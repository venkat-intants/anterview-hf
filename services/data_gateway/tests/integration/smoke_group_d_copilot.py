"""The workflow copilot's tools and its commit path — Group D (D6/D7).

Two halves, both against real SQL:

* the TOOL HANDLERS, invoked through the real ``ToolRegistry`` with a real
  ``ToolContext``. No model is involved — the copilot's judgement is not what
  needs testing here; what needs testing is that the handlers read the right
  rows, refuse the wrong ones, and resolve competency ids against the taxonomy
  instead of trusting whatever the model passed.
* the APPLY ENDPOINT, which is what a committed proposal actually fires. This
  is the only path by which anything an agent produced becomes a stored object,
  so it is the one that has to be atomic.

    docker run -d --name intants-pgv -e POSTGRES_PASSWORD=postgres \
      -e POSTGRES_DB=intants_smoke -p 55432:5432 pgvector/pgvector:pg16
    cd services/data_gateway
    export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke
    python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_d_copilot.py

Exits non-zero on any failed check.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from shared.agents import ToolContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents import workflow_tools  # noqa: F401 — registers the tools
from app.agents.tools import registry
from app.database import get_db_session
from app.dependencies import get_hr_company
from app.main import app

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def call(name, args, ctx):
    """Invoke a tool the way the runtime does, and parse what the model sees."""
    result = await registry.invoke(name, args, ctx, call_id=uuid.uuid4().hex[:8])
    data = json.loads(result.content) if result.content else {}
    return result, data


async def main() -> None:
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)

    async with eng.begin() as c:
        await c.execute(
            text(
                "TRUNCATE companies, users, applicants, job_requisitions, enrolments,"
                " stage_transitions, workflows, workflow_rounds, round_criteria,"
                " round_results, exams, exam_rounds, exam_sections, exam_questions CASCADE"
            )
        )

    cid, other_cid = uuid.uuid4(), uuid.uuid4()
    hr_uid = uuid.uuid4()
    req_id, other_req = uuid.uuid4(), uuid.uuid4()
    exam_id, exam_round = uuid.uuid4(), uuid.uuid4()

    async with factory() as db:
        for c_id, slug in ((cid, "acme"), (other_cid, "globex")):
            await db.execute(
                text(
                    "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                    " VALUES (:i,:n,:s,true,:t,:t)"
                ),
                {"i": c_id, "n": slug.title(), "s": slug, "t": now},
            )
        await db.execute(
            text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,"
                " preferred_language,is_active,notify_login_email,must_change_password,"
                " created_at,updated_at)"
                " VALUES (:i,'hr@acme.test','HR','x',:c,'en',true,false,false,:t,:t)"
            ),
            {"i": hr_uid, "c": cid, "t": now},
        )
        await db.execute(
            text(
                "INSERT INTO job_requisitions (id,company_id,title,level,jd_text,"
                " created_by_user_id,status,from_backfill,created_at,updated_at)"
                " VALUES (:i,:c,'Backend Engineer','mid','We need someone to build APIs.',"
                " :u,'open',false,:t,:t)"
            ),
            {"i": req_id, "c": cid, "u": hr_uid, "t": now},
        )
        # Another tenant's opening, to prove the context cannot be pointed at it.
        await db.execute(
            text(
                "INSERT INTO job_requisitions (id,company_id,title,level,status,"
                " from_backfill,created_at,updated_at)"
                " VALUES (:i,:c,'Their Role','mid','open',false,:t,:t)"
            ),
            {"i": other_req, "c": other_cid, "t": now},
        )
        await db.execute(
            text(
                "INSERT INTO exams (id,company_id,created_by_user_id,title,pass_threshold,"
                " allow_retake,auto_advance_on_pass,status,kind,created_at,updated_at)"
                " VALUES (:i,:c,:u,'Python Basics',60,false,false,'published','mcq',:t,:t)"
            ),
            {"i": exam_id, "c": cid, "u": hr_uid, "t": now},
        )
        await db.execute(
            text(
                "INSERT INTO exam_rounds (id,exam_id,company_id,title,round_number,"
                " pass_threshold,advances_to_interview,status,position,created_at,updated_at)"
                " VALUES (:i,:e,:c,'Round 1',1,60,true,'published',0,:t,:t)"
            ),
            {"i": exam_round, "e": exam_id, "c": cid, "t": now},
        )
        await db.commit()

    async def _db_override():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_hr_company] = lambda: (hr_uid, cid)

    # ── The tools ────────────────────────────────────────────────────────────
    async with factory() as db:
        ctx = ToolContext(
            actor_id=str(hr_uid),
            role="hr_manager",
            company_id=str(cid),
            resources={"db": db, "requisition_id": str(req_id)},
        )

        print("\n--- reading the opening under design ---")
        res, data = await call("get_opening_under_design", {}, ctx)
        check("get_opening_under_design succeeds", res.ok, res.error or "")
        check("returns the right opening", data.get("opening", {}).get("title") == "Backend Engineer",
              str(data.get("opening"))[:120])
        comps = data.get("competencies", [])
        check("competencies carry their rating scale", bool(comps) and all(
            c.get("weak") and c.get("adequate") and c.get("strong") for c in comps),
            str(comps[:1])[:200])
        check("the JD is included but bounded",
              len(data["opening"].get("job_description_excerpt") or "") <= 1500)
        check("cites the opening and the role model",
              {c.kind for c in res.citations} == {"job", "role_profile"},
              str([c.kind for c in res.citations]))

        print("\n--- no workflow yet ---")
        _res, data = await call("get_current_workflow", {}, ctx)
        check("reports that none exists", data.get("exists") is False, str(data)[:120])

        print("\n--- exams available to attach ---")
        _res, data = await call("list_available_exams", {}, ctx)
        rounds = data.get("exam_rounds", [])
        check("lists this company's exam rounds", len(rounds) == 1, str(rounds))
        check("gives the id the round would attach by",
              rounds and rounds[0]["exam_round_id"] == str(exam_round), str(rounds))

        print("\n--- drafting a whole process ---")
        good_ids = [comps[0]["id"], comps[1]["id"]]
        res, data = await call(
            "draft_hiring_workflow",
            {
                "name": "Screen then interview",
                "rounds": [
                    {"title": "Screening test", "kind": "mcq", "pass_threshold": 60,
                     "competency_ids": [good_ids[0]]},
                    {"title": "Conversation", "kind": "ai_interview", "pass_threshold": 65,
                     "competency_ids": good_ids},
                    {"title": "Final read", "kind": "human_review"},
                ],
                "rationale": "cheap and broad first",
            },
            ctx,
        )
        check("one proposal, not three", len(res.proposals) == 1, str(len(res.proposals)))
        proposal = res.proposals[0]
        check("it is a workflow proposal", proposal.kind == "workflow", proposal.kind)
        check("it commits to the atomic apply endpoint",
              proposal.commit.path == f"/hr/requisitions/{req_id}/workflows/apply",
              proposal.commit.path)
        body = proposal.commit.body
        check("all three rounds are in the body", len(body["rounds"]) == 3, str(len(body["rounds"])))
        check("human_review carries no threshold",
              body["rounds"][2]["pass_threshold"] is None, str(body["rounds"][2]))
        check("the frozen rubric came from the taxonomy, not the model",
              all(c["anchors"]["high"] for c in body["rounds"][1]["criteria"]),
              str(body["rounds"][1]["criteria"])[:200])
        check("probes travel with it too",
              all(c["probes"] for c in body["rounds"][1]["criteria"]))
        check("no risk note — a draft reaches nobody", proposal.risk_note is None)

        print("\n--- what the model cannot smuggle in ---")
        res, data = await call(
            "draft_hiring_workflow",
            {
                "rounds": [
                    {"title": "Invented", "kind": "ai_interview", "pass_threshold": 55,
                     "competency_ids": ["totally_made_up", good_ids[0]]},
                    {"title": "Nonsense", "kind": "psychic_reading", "pass_threshold": 50},
                ]
            },
            ctx,
        )
        kept = res.proposals[0].commit.body["rounds"]
        check("an unknown round type is dropped, not passed through",
              len(kept) == 1 and kept[0]["kind"] == "ai_interview", str(kept)[:160])
        check("an invented competency is dropped",
              [c["id"] for c in kept[0]["criteria"]] == [good_ids[0]],
              str(kept[0]["criteria"])[:160])
        check("and the drop is reported rather than silent",
              any("ignored competency" in n for n in data.get("adjustments", [])),
              str(data.get("adjustments")))

        # A threshold is ALWAYS a percentage. 7.5 meaning "7.5/10" is the exact
        # confusion that once held candidates who had passed.
        res, _data = await call(
            "draft_hiring_workflow",
            {"rounds": [{"title": "X", "kind": "ai_interview", "pass_threshold": 250}]}, ctx
        )
        check("an out-of-range threshold is clamped to a percentage",
              res.proposals[0].commit.body["rounds"][0]["pass_threshold"] == 100.0,
              str(res.proposals[0].commit.body["rounds"][0]))

        print("\n--- the tenancy boundary ---")
        foreign_ctx = ToolContext(
            actor_id=str(hr_uid), role="hr_manager", company_id=str(cid),
            resources={"db": db, "requisition_id": str(other_req)},
        )
        res, _data = await call("get_opening_under_design", {}, foreign_ctx)
        check("another company's opening is not readable even when injected",
              res.ok is False, str(res.content)[:120])

        res, _data = await call("draft_hiring_workflow", {"rounds": [
            {"title": "X", "kind": "human_review"}]}, foreign_ctx)
        check("and nothing can be drafted against it", res.ok is False, str(res.content)[:120])

        print("\n--- a super admin cannot reach these at all ---")
        sa_ctx = ToolContext(actor_id=str(hr_uid), role="super_admin", company_id=str(cid),
                             resources={"db": db, "requisition_id": str(req_id)})
        res, _data = await call("draft_hiring_workflow", {"rounds": []}, sa_ctx)
        check("invoke refuses on role regardless of surface", res.ok is False, str(res.error))

    # ── The commit path ──────────────────────────────────────────────────────
    tr = ASGITransport(app=app)
    async with AsyncClient(transport=tr, base_url="http://t") as c:
        print("\n--- committing the proposal ---")
        r = await c.post(f"/hr/requisitions/{req_id}/workflows/apply", json=body)
        check("POST apply -> 201", r.status_code == 201, r.text[:200])
        wf = r.json()
        check("the draft has all three rounds", len(wf["rounds"]) == 3, str(len(wf["rounds"])))
        check("it is a DRAFT, not published", wf["status"] == "draft", wf["status"])
        check("rounds are in the order proposed",
              [x["title"] for x in wf["rounds"]] == ["Screening test", "Conversation", "Final read"],
              str([x["title"] for x in wf["rounds"]]))
        check("the chain was wired as it was built",
              wf["rounds"][0]["on_pass_next_round_id"] == wf["rounds"][1]["id"])
        check("the rubric survived the round trip with anchors",
              all(cr.get("anchors") for cr in wf["rounds"][1]["criteria"]),
              str(wf["rounds"][1]["criteria"])[:160])

        print("\n--- and it is not publishable on the agent's say-so ---")
        r = await c.get(f"/hr/workflows/{wf['id']}/validate")
        report = r.json()
        check("the mcq round still needs questions attached",
              any("needs questions" in e for e in report["errors"]), str(report["errors"]))
        check("so a copilot-designed process cannot go live unreviewed",
              report["publishable"] is False)

        print("\n--- applying twice ---")
        r = await c.post(f"/hr/requisitions/{req_id}/workflows/apply", json=body)
        check("a second apply -> 409, not a duplicate draft", r.status_code == 409, r.text[:160])
        async with factory() as db:
            n = await db.scalar(
                text("SELECT count(*) FROM workflows WHERE requisition_id = :r"), {"r": req_id}
            )
        check("still exactly one workflow", int(n) == 1, str(n))

        print("\n--- atomicity ---")
        # Discard, then apply a payload whose LAST round is invalid. Nothing
        # should survive: a half-built process that looks complete is worse
        # than none at all.
        await c.delete(f"/hr/workflows/{wf['id']}")
        bad = {"name": "Broken", "rounds": [
            {"title": "One", "kind": "human_review"},
            {"title": "Two", "kind": "human_review"},
        ]}
        bad["rounds"].extend([{"title": f"R{i}", "kind": "human_review"} for i in range(3, 16)])
        r = await c.post(f"/hr/requisitions/{req_id}/workflows/apply", json=bad)
        check("a payload over the round cap -> 422", r.status_code == 422, str(r.status_code))
        async with factory() as db:
            n = await db.scalar(
                text(
                    "SELECT count(*) FROM workflows WHERE requisition_id = :r"
                    "   AND deleted_at IS NULL"
                ),
                {"r": req_id},
            )
        check("nothing was left behind", int(n) == 0, str(n))

        print("\n--- another tenant's opening over HTTP ---")
        r = await c.post(f"/hr/requisitions/{other_req}/workflows/apply", json=body)
        check("apply against another company -> 404", r.status_code == 404, str(r.status_code))

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'=' * 60}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 60)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
