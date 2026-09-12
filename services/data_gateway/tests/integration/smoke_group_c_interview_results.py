"""C6/C7 smoke test — a scored workflow interview reaches the runner.

Standalone script, not a pytest module. It drives the real reminder sweep
against a real PostgreSQL, so it needs a throwaway database at head (it
TRUNCATEs what it touches). No LLM, no other service: the scorecards are seeded.

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_c_interview_results.py

Exits non-zero on any failed check.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.reminders as rem

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


CRITERIA = [("triage", "Triage", 0.4), ("handover", "Handover", 0.3),
            ("escalation", "Escalation", 0.3)]


async def seed(f) -> dict:
    now = datetime.now(tz=UTC)
    cid, owner, req, wf, jid = (uuid.uuid4() for _ in range(5))
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    async with f() as db:
        await db.execute(text("INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                              " VALUES (:i,'Acme','acme',true,:n,:n)"), {"i": cid, "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.test','HR Owner','x',:c,'en',true,false,false,:n,:n)"),
            {"i": owner, "c": cid, "n": now})
        await db.execute(text(
            "INSERT INTO jobs (id,title,description,level,created_at,updated_at)"
            " VALUES (:i,'Staff Nurse','Ward care.','entry',:n,:n)"), {"i": jid, "n": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,created_at,updated_at)"
            " VALUES (:i,:c,'Staff Nurse',:n,:n)"), {"i": req, "c": cid, "n": now})
        await db.execute(text(
            "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
            " created_by_user_id,published_at,created_at,updated_at)"
            " VALUES (:i,:c,:r,1,'draft',:u,:n,:n,:n)"),
            {"i": wf, "c": cid, "r": req, "u": owner, "n": now})
        # Round two first, so round one can point at it.
        for rid, pos, title, nxt in [(r2, 1, "Panel interview", None),
                                     (r1, 0, "Screening interview", r2)]:
            await db.execute(text(
                "INSERT INTO workflow_rounds (id,company_id,workflow_id,position,title,kind,"
                " pass_threshold,on_pass_next_round_id,created_at,updated_at)"
                " VALUES (:i,:c,:w,:p,:t,'ai_interview',60,:nx,:n,:n)"),
                {"i": rid, "c": cid, "w": wf, "p": pos, "t": title, "nx": nxt, "n": now})
        for comp_id, name, weight in CRITERIA:
            await db.execute(text(
                "INSERT INTO round_criteria (id,company_id,round_id,competency_id,competency_name,"
                " competency_kind,weight,created_at) VALUES (:i,:c,:r,:ci,:cn,'technical',:w,:n)"),
                {"i": uuid.uuid4(), "c": cid, "r": r1, "ci": comp_id, "cn": name, "w": weight,
                 "n": now})

        # Published only once its rounds and criteria exist: a published
        # workflow is immutable at the database (migration f3b5d7a9c1e4), which
        # is the same order the API enforces.
        await db.execute(text("UPDATE workflows SET status = 'published' WHERE id = :i"),
                         {"i": wf})

        people = {}
        for key, name, status, via_workflow, composite, comps in [
            # Headline composite 5.0 would hold her; her round's own criteria
            # (8, 7, 6 weighted 0.4/0.3/0.3 = 7.1) are what must decide.
            ("asha", "Asha Rao", "shortlisted", True, 5.0,
             {"triage": 8, "handover": 7, "escalation": 6}),
            ("bharat", "Bharat Iyer", "shortlisted", True, 4.0, None),
            ("chitra", "Chitra Das", "shortlisted", False, 9.0, None),  # hand-made invite
            ("dev", "Dev Menon", "held", True, 9.0, None),             # a person holds him
        ]:
            app, enr, guest, sess, sc, inv = (uuid.uuid4() for _ in range(6))
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                " target_job_title,target_level,resume_text,status,created_at,updated_at)"
                " VALUES (:i,:c,:u,:fn,:em,'Staff Nurse','entry','cv',:st,:n,:n)"),
                {"i": app, "c": cid, "u": owner, "fn": name, "em": f"{key}@cand.test",
                 "st": "held" if status == "held" else "shortlisted", "n": now})
            await db.execute(text(
                "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                " target_job_title,workflow_id,current_round_id,held_at,created_at,updated_at)"
                " VALUES (:i,:c,:r,:a,:st,'Staff Nurse',:w,:cr,:h,:n,:n)"),
                {"i": enr, "c": cid, "r": req, "a": app, "st": status, "w": wf, "cr": r1,
                 "h": now if status == "held" else None, "n": now})
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,preferred_language,"
                " is_active,notify_login_email,must_change_password,created_at,updated_at)"
                " VALUES (:i,:e,:fn,'x','en',true,false,false,:n,:n)"),
                {"i": guest, "e": f"guest-{key}@cand.test", "fn": name, "n": now})
            await db.execute(text(
                "INSERT INTO sessions (id,user_id,job_id,status,created_at,updated_at)"
                " VALUES (:i,:u,:j,'completed',:n,:n)"), {"i": sess, "u": guest, "j": jid, "n": now})
            rationale: dict = {}
            if comps:
                rationale["_competencies"] = {
                    cid_: {"name": nm, "weight": w, "score": comps[cid_], "evidence": ""}
                    for cid_, nm, w in CRITERIA
                }
            await db.execute(text(
                "INSERT INTO scorecards (scorecard_id,session_id,scores,composite_score,rationale,"
                " summary,lang,created_at) VALUES (:i,:s,CAST(:sc AS jsonb),:cs,"
                " CAST(:ra AS jsonb),'Seeded.','en',:n)"),
                {"i": sc, "s": sess, "cs": composite, "ra": json.dumps(rationale),
                 "sc": json.dumps({"communication": 7, "technical": 6,
                                   "problem_solving": 6, "confidence": 7}), "n": now})
            # Still 'consumed': the sweep's completion stage has to close it
            # first, in the same pass — that ordering is part of what is tested.
            await db.execute(text(
                "INSERT INTO interview_invites (id,company_id,applicant_id,job_id,created_by_user_id,"
                " token_hash,language,expires_at,session_id,status,consumed_at,enrolment_id,"
                " created_at,updated_at) VALUES (:i,:c,:a,:j,:u,:th,'en',:x,:s,'consumed',:n,:e,"
                " :n,:n)"),
                {"i": inv, "c": cid, "a": app, "j": jid, "u": owner, "th": uuid.uuid4().hex,
                 "x": now + timedelta(days=1), "s": sess, "n": now,
                 "e": enr if via_workflow else None})
            people[key] = {"applicant": app, "enrolment": enr, "scorecard": sc, "invite": inv}
        await db.commit()
    return {"r1": r1, "r2": r2, "people": people}


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text("TRUNCATE applicants, companies, users, jobs, sessions, scorecards,"
                             " interview_invites, job_requisitions, enrolments, workflows,"
                             " workflow_rounds, round_criteria, round_results, stage_transitions,"
                             " email_events, notifications CASCADE"))
    s = await seed(f)
    p = s["people"]
    print("\n--- seeded: a two-round interview workflow, 4 scored interviews ---\n")

    sweep = await rem.run_once(f)
    print(f"  sweep 1 -> {sweep}\n")

    async def result_for(key: str):  # noqa: ANN202
        async with f() as db:
            return (await db.execute(text(
                "SELECT round_id, percent, passed, graded_by, attempt_ref, criterion_scores"
                "  FROM round_results WHERE enrolment_id = :e AND superseded_at IS NULL"),
                {"e": p[key]["enrolment"]})).mappings().all()

    async def enrolment(key: str):  # noqa: ANN202
        async with f() as db:
            return (await db.execute(text(
                "SELECT status, current_round_id, held_reason FROM enrolments WHERE id = :e"),
                {"e": p[key]["enrolment"]})).mappings().first()

    a, ae = await result_for("asha"), await enrolment("asha")
    check("a scored interview is recorded as the round's result",
          len(a) == 1 and a[0]["round_id"] == s["r1"] and a[0]["graded_by"] == "ai"
          and a[0]["attempt_ref"] == p["asha"]["scorecard"], str(a))
    check("the round's own criteria decide, not the headline composite",
          bool(a) and float(a[0]["percent"]) == 71.0 and a[0]["passed"],
          f"percent={a[0]['percent'] if a else None}")
    check("a passing candidate advances to the next round", ae["current_round_id"] == s["r2"],
          str(dict(ae)))
    async with f() as db:
        invites = (await db.execute(text(
            "SELECT status, enrolment_id FROM interview_invites WHERE applicant_id = :a"
            " ORDER BY created_at"), {"a": p["asha"]["applicant"]})).mappings().all()
    check("the finished invite was closed before advancing",
          invites[0]["status"] == "completed", str([dict(i) for i in invites]))
    check("the next interview round gets a working invite (not stranded)",
          len(invites) == 2 and invites[1]["status"] == "invited"
          and invites[1]["enrolment_id"] == p["asha"]["enrolment"],
          str([dict(i) for i in invites]))

    b, be = await result_for("bharat"), await enrolment("bharat")
    check("a below-threshold interview is recorded as not passed",
          len(b) == 1 and float(b[0]["percent"]) == 40.0 and not b[0]["passed"], str(b))
    check("a below-threshold candidate is held, not rejected (D-05)",
          be["status"] == "held" and "40%" in (be["held_reason"] or ""), str(dict(be)))
    check("a held candidate stays on the round for a person to decide",
          be["current_round_id"] == s["r1"])

    check("a hand-made HR invite does not drive the workflow", await result_for("chitra") == [])
    de = await enrolment("dev")
    check("a candidate a person already holds is not moved",
          await result_for("dev") == [] and de["current_round_id"] == s["r1"]
          and de["status"] == "held")
    check("the sweep counted the two results it recorded", sweep.workflow_results == 2,
          str(sweep))

    sweep2 = await rem.run_once(f)
    async with f() as db:
        total = await db.scalar(text("SELECT count(*) FROM round_results"))
        rejected = await db.scalar(text(
            "SELECT count(*) FROM enrolments WHERE status = 'rejected'"))
    check("a second sweep records nothing again", sweep2.workflow_results == 0 and total == 2,
          f"{sweep2} rows={total}")
    check("nobody was rejected by an automated path", rejected == 0)

    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 64)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
