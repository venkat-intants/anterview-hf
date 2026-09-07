"""Group C API smoke — the full builder flow over HTTP.

Standalone script; needs a live PostgreSQL with pgvector.

    docker run -d --name intants-pgv -e POSTGRES_PASSWORD=postgres \
        -e POSTGRES_DB=intants_smoke -p 55432:5432 pgvector/pgvector:pg16
    cd services/data_gateway
    export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke
    python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_c_api.py

Overrides only the auth dependency; the routers, the service layer and the SQL
are the real ones. Walks the sequence a builder actually performs: read the role
model, start a draft, add rounds, set criteria, validate, publish, then try to
edit the published version and be refused.

Exits non-zero on any failed check.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_db_session
from app.dependencies import get_hr_company
from app.main import app

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def main() -> None:
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE companies, users, applicants, jobs, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, round_criteria, round_results,"
            " exams, exam_rounds, exam_assignments, interview_invites, sessions,"
            " email_events, notifications CASCADE"))

    now = datetime.now(tz=UTC)
    cid, uid, rid, other_rid = (uuid.uuid4() for _ in range(4))
    exam_id, er = uuid.uuid4(), uuid.uuid4()
    other_cid, other_uid = uuid.uuid4(), uuid.uuid4()

    async with factory() as db:
        for c_id, name, slug in [(cid, "Acme", "acme"), (other_cid, "Globex", "globex")]:
            await db.execute(text(
                "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                " VALUES (:i,:n,:s,true,:t,:t)"), {"i": c_id, "n": name, "s": slug, "t": now})
        for u_id, c_id, em in [(uid, cid, "hr@acme.t"), (other_uid, other_cid, "hr@globex.t")]:
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,"
                " preferred_language,is_active,notify_login_email,must_change_password,"
                " created_at,updated_at)"
                " VALUES (:i,:e,'HR','x',:c,'en',true,false,false,:t,:t)"),
                {"i": u_id, "e": em, "c": c_id, "t": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,level,created_at,updated_at)"
            " VALUES (:i,:c,'Python Developer','mid',:t,:t)"), {"i": rid, "c": cid, "t": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,created_at,updated_at)"
            " VALUES (:i,:c,'Nurse',:t,:t)"), {"i": other_rid, "c": other_cid, "t": now})
        await db.execute(text(
            "INSERT INTO exams (id,company_id,title,created_by_user_id,created_at,updated_at)"
            " VALUES (:i,:c,'Screen',:u,:t,:t)"), {"i": exam_id, "c": cid, "u": uid, "t": now})
        await db.execute(text(
            "INSERT INTO exam_rounds (id,exam_id,company_id,round_number,title,position,"
            " status,created_at,updated_at)"
            " VALUES (:i,:e,:c,1,'Aptitude',0,'published',:t,:t)"),
            {"i": er, "e": exam_id, "c": cid, "t": now})
        await db.commit()

    async def _db_override():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_hr_company] = lambda: (uid, cid)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # ── the criteria picker ──────────────────────────────────────────
        print("\n--- role model ---")
        r = await c.get(f"/hr/requisitions/{rid}/role-model")
        check("GET role-model -> 200", r.status_code == 200, r.text[:200])
        rm = r.json()
        comps = rm["competencies"]
        check("competencies returned", len(comps) >= 3, str(len(comps)))
        check("each carries anchors so the rubric can be frozen (C8)",
              all(x["anchors"].get("mid") for x in comps), str(comps[0])[:160])
        check("each carries probe stems", all(x["probes"] for x in comps))
        check("weights sum to 1.0",
              abs(sum(x["weight"] for x in comps) - 1.0) < 1e-6,
              str(sum(x["weight"] for x in comps)))

        # ── build a draft ────────────────────────────────────────────────
        print("\n--- authoring ---")
        r = await c.post(f"/hr/requisitions/{rid}/workflows?name=Standard")
        check("POST workflow -> 201", r.status_code == 201, r.text[:200])
        wf = r.json()
        wf_id = wf["id"]
        check("starts at version 1 as a draft",
              wf["version"] == 1 and wf["status"] == "draft", str(wf)[:120])
        check("a draft is editable", wf["editable"] is True)
        check("automation settings default ON",
              wf["settings"]["auto_advance_rounds"] and wf["settings"]["auto_score_on_apply"],
              str(wf["settings"]))

        r = await c.post(f"/hr/requisitions/{rid}/workflows")
        check("a second draft -> 409", r.status_code == 409, str(r.status_code))

        picked = comps[:3]
        rounds = [
            {"title": "Aptitude", "kind": "mcq", "pass_threshold": 60,
             "exam_round_id": str(er), "criteria": [picked[1]]},
            {"title": "Technical", "kind": "coding", "pass_threshold": 50,
             "exam_round_id": str(er), "criteria": [picked[0], picked[2]]},
            {"title": "AI Interview", "kind": "ai_interview", "pass_threshold": 60,
             "criteria": picked},
            {"title": "HR Final", "kind": "human_review"},
        ]
        for body in rounds:
            r = await c.post(f"/hr/workflows/{wf_id}/rounds", json=body)
            check(f"POST round {body['title']} -> 201", r.status_code == 201, r.text[:200])
        wf = r.json()
        check("four rounds, in order",
              [x["title"] for x in wf["rounds"]] ==
              ["Aptitude", "Technical", "AI Interview", "HR Final"], str(wf["rounds"])[:120])
        check("the chain wired itself",
              wf["rounds"][0]["on_pass_next_round_id"] == wf["rounds"][1]["id"])
        check("the last round points nowhere", wf["rounds"][-1]["on_pass_next_round_id"] is None)
        check("criteria frozen with anchors",
              all(x.get("anchors") for x in wf["rounds"][2]["criteria"]),
              str(wf["rounds"][2]["criteria"])[:160])

        r = await c.post(f"/hr/workflows/{wf_id}/rounds",
                         json={"title": "Bad", "kind": "portfolio"})
        check("an unsupported round kind -> 422", r.status_code == 422, str(r.status_code))

        # ── validate ─────────────────────────────────────────────────────
        print("\n--- validation ---")
        r = await c.get(f"/hr/workflows/{wf_id}/validate")
        check("GET validate -> 200", r.status_code == 200, r.text[:200])
        rep = r.json()
        check("publishable", rep["publishable"] is True, str(rep["errors"]))
        check("coverage reported for every competency",
              len(rep["coverage"]) == len(comps), str(len(rep["coverage"])))
        check("warns about competencies no round assesses",
              any("no round assesses it" in w for w in rep["warnings"]) or len(comps) <= 3,
              str(rep["warnings"]))

        # ── reorder, edit, remove ────────────────────────────────────────
        print("\n--- structural edits ---")
        ids = [x["id"] for x in wf["rounds"]]
        r = await c.put(f"/hr/workflows/{wf_id}/rounds/order",
                        json={"round_ids": [ids[1], ids[0], ids[2], ids[3]]})
        check("PUT order -> 200", r.status_code == 200, r.text[:200])
        reordered = r.json()["rounds"]
        check("order applied", [x["title"] for x in reordered][:2] == ["Technical", "Aptitude"],
              str([x["title"] for x in reordered]))
        check("the chain follows the new order",
              reordered[0]["on_pass_next_round_id"] == reordered[1]["id"])

        r = await c.patch(f"/hr/workflows/{wf_id}/rounds/{ids[0]}",
                          json={"pass_threshold": 55, "title": "Aptitude (revised)"})
        check("PATCH round -> 200", r.status_code == 200, r.text[:200])
        apt = next(x for x in r.json()["rounds"] if x["id"] == ids[0])
        check("threshold updated", apt["pass_threshold"] == 55.0, str(apt["pass_threshold"]))

        r = await c.delete(f"/hr/workflows/{wf_id}/rounds/{ids[1]}")
        check("DELETE round -> 200", r.status_code == 200, r.text[:200])
        left = r.json()["rounds"]
        check("round removed", len(left) == 3, str(len(left)))
        check("the chain healed around the gap",
              left[0]["on_pass_next_round_id"] == left[1]["id"], str(left)[:200])
        rep = (await c.get(f"/hr/workflows/{wf_id}/validate")).json()
        check("still valid after the deletion", rep["publishable"], str(rep["errors"]))

        # ── settings (C9) ────────────────────────────────────────────────
        print("\n--- automation settings ---")
        r = await c.patch(f"/hr/workflows/{wf_id}",
                          json={"auto_advance_rounds": False, "hold_band": 15})
        check("PATCH settings -> 200", r.status_code == 200, r.text[:200])
        st = r.json()["settings"]
        check("settings applied", st["auto_advance_rounds"] is False and st["hold_band"] == 15,
              str(st))
        r = await c.patch(f"/hr/workflows/{wf_id}", json={"auto_reject_below": True})
        check("an unknown setting is ignored, never applied",
              r.status_code == 200 and "auto_reject_below" not in r.json()["settings"],
              str(r.json()["settings"]))
        await c.patch(f"/hr/workflows/{wf_id}", json={"auto_advance_rounds": True})

        # ── publish and immutability ─────────────────────────────────────
        print("\n--- publish ---")
        r = await c.post(f"/hr/workflows/{wf_id}/publish")
        check("POST publish -> 200", r.status_code == 200, r.text[:300])
        check("status is published", r.json()["status"] == "published")
        check("no longer editable", r.json()["editable"] is False)
        check("the validation report comes back with it", "validation" in r.json())

        r = await c.post(f"/hr/workflows/{wf_id}/rounds",
                         json={"title": "Sneaky", "kind": "human_review"})
        check("adding a round to a published workflow -> 409", r.status_code == 409,
              str(r.status_code))
        r = await c.patch(f"/hr/workflows/{wf_id}", json={"hold_band": 50})
        check("editing published settings -> 409", r.status_code == 409, str(r.status_code))
        r = await c.delete(f"/hr/workflows/{wf_id}")
        check("discarding a published workflow -> 409", r.status_code == 409, str(r.status_code))

        # ── clone to edit ────────────────────────────────────────────────
        print("\n--- clone ---")
        r = await c.post(f"/hr/workflows/{wf_id}/clone")
        check("POST clone -> 201", r.status_code == 201, r.text[:200])
        v2 = r.json()
        check("clone is v2 and a draft", v2["version"] == 2 and v2["status"] == "draft",
              str(v2)[:120])
        check("clone copied the rounds",
              [x["title"] for x in v2["rounds"]] == [x["title"] for x in left],
              str([x["title"] for x in v2["rounds"]]))
        check("clone copied the frozen anchors",
              all(cr.get("anchors") for rr in v2["rounds"] for cr in rr["criteria"]),
              "anchors lost in the clone")

        r = await c.get(f"/hr/requisitions/{rid}/workflows")
        vers = r.json()
        check("both versions listed", len(vers) == 2, str(vers))
        check("v1 still published, v2 draft",
              {v["version"]: v["status"] for v in vers} == {1: "published", 2: "draft"},
              str({v["version"]: v["status"] for v in vers}))

        # ── an invalid draft cannot be published ─────────────────────────
        print("\n--- publish is gated ---")
        r = await c.post(f"/hr/workflows/{v2['id']}/rounds",
                         json={"title": "No questions", "kind": "mcq", "pass_threshold": 50})
        check("a round with no questions can be added to a draft", r.status_code == 201)
        r = await c.post(f"/hr/workflows/{v2['id']}/publish")
        check("publishing it -> 422 with the reasons", r.status_code == 422, str(r.status_code))
        detail = r.json()["detail"]
        check("the errors say what to fix",
              any("needs questions" in e for e in detail["errors"]), str(detail["errors"]))
        check("v1 is still the published one after a failed publish",
              (await c.get(f"/hr/workflows/{wf_id}")).json()["status"] == "published")

        # ── tenant isolation ─────────────────────────────────────────────
        print("\n--- tenant isolation ---")
        r = await c.get(f"/hr/requisitions/{other_rid}/role-model")
        check("another company's requisition -> 404", r.status_code == 404, str(r.status_code))
        r = await c.get(f"/hr/requisitions/{other_rid}/workflows")
        check("its workflow list -> 404", r.status_code == 404, str(r.status_code))
        r = await c.get(f"/hr/workflows/{uuid.uuid4()}")
        check("an unknown workflow -> 404", r.status_code == 404, str(r.status_code))

        # ── the decision queue ───────────────────────────────────────────
        print("\n--- decision queue ---")
        r = await c.get(f"/hr/requisitions/{rid}/decision-queue")
        check("GET decision-queue -> 200", r.status_code == 200, r.text[:200])
        check("empty with nobody enrolled", r.json() == [], str(r.json()))
        r = await c.post(f"/hr/enrolments/{uuid.uuid4()}/release-hold", json={})
        check("releasing an unknown enrolment -> 404", r.status_code == 404, str(r.status_code))

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'=' * 64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 64)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
