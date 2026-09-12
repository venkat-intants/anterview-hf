"""C8 worker wiring — the interview worker reads back a round's frozen rubric.

Standalone script; needs a live PostgreSQL with pgvector.

    docker run -d --name intants-pgv -e POSTGRES_PASSWORD=postgres         -e POSTGRES_DB=intants_smoke -p 55432:5432 pgvector/pgvector:pg16
    cd services/data_gateway
    export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke
    python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_c_worker_rubric.py

Exercises interview_core's own module but drives it from here, because that
service's suite cannot be collected without livekit installed and the authoring
code under test lives on this side. The SQL, the join chain and every fallback
path are the real ones.

Exits non-zero on any failed check.
"""
from __future__ import annotations

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.workflows import add_round, create_draft, publish

# Both services ship a package called `app`, so interview_core's cannot be
# put on sys.path here without shadowing data_gateway's. frozen_rubric.py
# imports nothing from its own `app` package (only shared.intelligence and
# sqlalchemy), so loading it straight off disk runs the real module without
# the collision.
_WORKER_MODULE = 'd:\\anterview-hf-main\\anterview-hf-main\\services\\interview_core\\app\\worker\\frozen_rubric.py'
_spec = importlib.util.spec_from_file_location('worker_frozen_rubric', _WORKER_MODULE)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
load_frozen_rubric = _mod.load_frozen_rubric

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []

RUBRIC = [
    {"id": "python", "name": "Python proficiency", "kind": "technical", "weight": 0.5,
     "anchors": {"low": "cannot write a loop", "mid": "working scripts",
                 "high": "idiomatic, tested"},
     "probes": ["walk me through something you built"]},
    {"id": "solving", "name": "Problem solving", "kind": "technical", "weight": 0.3,
     "anchors": {"low": "needs the answer", "mid": "gets there with hints",
                 "high": "decomposes unprompted"},
     "probes": ["describe a bug that took a while"]},
    {"id": "comms", "name": "Communication", "kind": "communication", "weight": 0.2,
     "anchors": {"low": "hard to follow", "mid": "clear enough",
                 "high": "explains to a non-expert"},
     "probes": ["explain a decision to a non-engineer"]},
]


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE companies, users, applicants, jobs, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, round_criteria, round_results,"
            " interview_invites, sessions CASCADE"))

    now = datetime.now(tz=UTC)
    cid, uid, rid, aid, jid = (uuid.uuid4() for _ in range(5))
    sid_wf, sid_practice = uuid.uuid4(), uuid.uuid4()

    async with f() as db:
        await db.execute(text(
            "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
            " VALUES (:i,'Acme','acme',true,:t,:t)"), {"i": cid, "t": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@a.t','HR','x',:c,'en',true,false,false,:t,:t)"),
            {"i": uid, "c": cid, "t": now})
        await db.execute(text(
            "INSERT INTO jobs (id,title,description,level,created_at,updated_at)"
            " VALUES (:i,'Python Developer','d','mid',:t,:t)"), {"i": jid, "t": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,created_at,updated_at)"
            " VALUES (:i,:c,'Python Developer',:t,:t)"), {"i": rid, "c": cid, "t": now})
        await db.execute(text(
            "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
            " target_job_title,target_level,status,created_at,updated_at)"
            " VALUES (:i,:c,:u,'Asha','a@x.t','Python Developer','mid','shortlisted',:t,:t)"),
            {"i": aid, "c": cid, "u": uid, "t": now})
        await db.commit()

        wf = await create_draft(db, company_id=cid, requisition_id=rid, created_by=uid)
        await db.commit()
        rnd = await add_round(db, company_id=cid, workflow_id=wf, title="AI Interview",
                              kind="ai_interview", pass_threshold=60, criteria=RUBRIC)
        await db.commit()
        await publish(db, company_id=cid, workflow_id=wf, profile_competencies=RUBRIC)
        await db.commit()

        enr = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
            " target_job_title,workflow_id,current_round_id,created_at,updated_at)"
            " VALUES (:i,:c,:r,:a,'shortlisted','Python Developer',:w,:cr,:t,:t)"),
            {"i": enr, "c": cid, "r": rid, "a": aid, "w": wf, "cr": rnd, "t": now})
        cand = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'asha@x.t','Asha','x','en',true,false,false,:t,:t)"),
            {"i": cand, "t": now})
        # Two sessions: one produced by a workflow invite, one a practice run.
        for s in (sid_wf, sid_practice):
            await db.execute(text(
                "INSERT INTO sessions (id,user_id,job_id,status,created_at,updated_at)"
                " VALUES (:i,:u,:j,'in_progress',:t,:t)"),
                {"i": s, "u": cand, "j": jid, "t": now})
        await db.execute(text(
            "INSERT INTO interview_invites (id,company_id,applicant_id,job_id,enrolment_id,"
            " created_by_user_id,token_hash,language,expires_at,session_id,status,"
            " created_at,updated_at)"
            " VALUES (:i,:c,:a,:j,:e,:u,:th,'en',:x,:s,'consumed',:t,:t)"),
            {"i": uuid.uuid4(), "c": cid, "a": aid, "j": jid, "e": enr, "u": uid,
             "th": uuid.uuid4().hex, "x": now + timedelta(days=1), "s": sid_wf, "t": now})
        await db.commit()

    print("\n--- a session belonging to a workflow round ---")
    prof = await load_frozen_rubric(f, sid_wf, job_title="Python Developer")
    check("rubric found for a workflow session", prof is not None)
    if prof:
        check("profile id names the workflow and round",
              prof.profile_id == f"frozen:{wf}:{rnd}", prof.profile_id)
        check("exactly this round's competencies",
              sorted(c.id for c in prof.competencies) == ["comms", "python", "solving"],
              str([c.id for c in prof.competencies]))
        check("weights renormalised to the round",
              abs(sum(c.weight for c in prof.competencies) - 1.0) < 1e-9)
        check("frozen anchors came back, not generic ones",
              any(c.anchors.high == "idiomatic, tested" for c in prof.competencies))
        check("frozen probes came back",
              any("walk me through" in p for c in prof.competencies for p in c.probes))
        # The whole point: the interview plan follows the round, not the role.
        from shared.intelligence import plan_interview
        plans = plan_interview(prof, 10)
        probed = {t.competency_id for t in plans if t.competency_id}
        check("the 10-turn plan probes only what the round promised",
              probed <= {"python", "solving", "comms"} and probed, str(probed))

    print("\n--- a practice session with no workflow ---")
    prof2 = await load_frozen_rubric(f, sid_practice, job_title="Python Developer")
    check("no rubric, so the worker derives as before", prof2 is None)

    print("\n--- degradation, not failure ---")
    check("a malformed session id returns None rather than raising",
          await load_frozen_rubric(f, "not-a-uuid", job_title="X") is None)
    check("an unknown session returns None",
          await load_frozen_rubric(f, uuid.uuid4(), job_title="X") is None)

    async with f() as db:
        # The workflow is archived first: a PUBLISHED one refuses this, which is
        # the point of migration f3b5d7a9c1e4. Archiving is how a version stops
        # being live, and it is what makes this "a round whose criteria are
        # gone" rather than "an edit nobody should be able to make".
        await db.execute(text("UPDATE workflows SET status = 'archived' WHERE id = :i"),
                         {"i": wf})
        await db.execute(text("DELETE FROM round_criteria WHERE round_id = :r"), {"r": rnd})
        await db.commit()
    check("a round stripped of its criteria falls back rather than breaking",
          await load_frozen_rubric(f, sid_wf, job_title="Python Developer") is None)

    bad = create_async_engine("postgresql+asyncpg://postgres:wrong@127.0.0.1:55432/nope")
    check("a database failure returns None rather than stopping the interview",
          await load_frozen_rubric(
              async_sessionmaker(bad, expire_on_commit=False), sid_wf, job_title="X") is None)
    await bad.dispose()

    await eng.dispose()
    print(f"\n{'=' * 60}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 60)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
