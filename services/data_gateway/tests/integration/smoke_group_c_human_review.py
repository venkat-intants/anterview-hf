"""C4 smoke test — a human_review round can be reviewed, through the API.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head:

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_c_human_review.py

The runner could always record a reviewer's verdict, and the runner smoke
exercised it by calling the function. Nothing a PERSON could reach did: no
endpoint, no screen, and the decision queue listed only held or finished
candidates — so someone parked on a review round was invisible on the one page
built to act on them, and the workflow stopped there.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []
PROFILE = [
    {"id": "portfolio", "name": "Portfolio quality", "kind": "technical", "weight": 0.5},
    {"id": "communication", "name": "Communication", "kind": "behavioural", "weight": 0.3},
    {"id": "ownership", "name": "Ownership", "kind": "behavioural", "weight": 0.2},
]


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE applicants, companies, users, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, round_criteria,"
            " round_results, notifications CASCADE"))

    cid, uid, rid, aid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with f() as db:
        await db.execute(text(
            "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
            " VALUES (:i,'Acme','acme',true,:t,:t)"), {"i": cid, "t": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.test','HR','x',:c,'en',true,false,false,:t,:t)"),
            {"i": uid, "c": cid, "t": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,created_at,updated_at)"
            " VALUES (:i,:c,'Designer',:t,:t)"), {"i": rid, "c": cid, "t": now})
        await db.execute(text(
            "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
            " target_job_title,target_level,resume_text,ats_overall,status,created_at,updated_at)"
            " VALUES (:i,:c,:u,'Ira','ira@x.test','Designer','mid','cv',8,'new',:t,:t)"),
            {"i": aid, "c": cid, "u": uid, "t": now})
        await db.commit()

    from app.workflow_runner import enrol_applicant, on_shortlisted
    from app.workflows import add_round, create_draft, publish

    async with f() as db:
        wf = await create_draft(db, company_id=cid, requisition_id=rid, created_by=uid,
                                name="Portfolio then panel")
        await db.commit()
        # Appended in order; add_round chains each to the next.
        r1 = await add_round(db, company_id=cid, workflow_id=wf, title="Portfolio review",
                             kind="human_review", criteria=PROFILE)
        await add_round(db, company_id=cid, workflow_id=wf, title="Panel",
                        kind="human_review", criteria=PROFILE)
        await db.commit()
        rep = await publish(db, company_id=cid, workflow_id=wf, profile_competencies=PROFILE)
        await db.commit()
        check("a two-review workflow publishes", rep.publishable, str(rep.errors))

        out = await enrol_applicant(db, company_id=cid, applicant_id=aid, requisition_id=rid,
                                    target_job_title="Designer", actor_user_id=uid)
        await db.commit()
        enrolment = uuid.UUID(str(out.enrolment_id))
        await on_shortlisted(db, enrolment_id=enrolment, actor_user_id=uid)
        await db.commit()

    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (uid, cid)
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    # ── The queue shows them, with the checklist ────────────────────────────
    q = (await ac.get(f"/hr/requisitions/{rid}/decision-queue")).json()
    ira = next((r for r in q if r["full_name"] == "Ira"), None)
    check("a candidate waiting on a review round is in the decision queue",
          ira is not None, str(q))
    check("…marked as awaiting a review, with the round named",
          ira is not None and ira["awaiting_review"] is True
          and ira["review_round_title"] == "Portfolio review", str(ira))
    check("…and carrying that round's competencies as the reviewer's checklist",
          ira is not None
          and sorted(c["name"] for c in ira["review_criteria"])
          == ["Communication", "Ownership", "Portfolio quality"], str(ira))

    # ── The verdict ─────────────────────────────────────────────────────────
    r = await ac.post(f"/hr/enrolments/{enrolment}/round-review",
                      json={"passed": True, "note": "Strong, varied portfolio"})
    body = r.json() if r.status_code == 200 else {}
    check("passing the review advances the candidate",
          r.status_code == 200 and body.get("to_round") == "Panel", f"{r.status_code} {body}")
    async with f() as db:
        rr = (await db.execute(text(
            "SELECT graded_by, grader_user_id, passed, evidence, score FROM round_results"
            " WHERE enrolment_id = :e AND round_id = :r AND superseded_at IS NULL"),
            {"e": enrolment, "r": r1})).mappings().first()
    check("the result is recorded as a person's, with their note and no score",
          rr is not None and rr["graded_by"] == "human" and rr["grader_user_id"] == uid
          and rr["passed"] is True and rr["evidence"] == "Strong, varied portfolio"
          and rr["score"] is None, str(rr))

    r = await ac.post(f"/hr/enrolments/{enrolment}/round-review",
                      json={"passed": False, "note": "Panel was not convinced"})
    async with f() as db:
        state = (await db.execute(text(
            "SELECT status, held_reason FROM enrolments WHERE id = :e"),
            {"e": enrolment})).mappings().first()
        rejected = await db.scalar(text(
            "SELECT count(*) FROM enrolments WHERE status = 'rejected'"))
    check("not passing holds the candidate rather than rejecting them (D-05)",
          r.status_code == 200 and state["status"] == "held" and rejected == 0, str(state))

    q2 = (await ac.get(f"/hr/requisitions/{rid}/decision-queue")).json()
    held = next((x for x in q2 if x["full_name"] == "Ira"), None)
    check("and they stay in the queue, now as held",
          held is not None and held["held"] is True and held["awaiting_review"] is False,
          str(held))

    # ── What the endpoint refuses ───────────────────────────────────────────
    async with f() as db:
        other = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,created_at,updated_at)"
            " VALUES (:i,:c,'Welder',:t,:t)"), {"i": other, "c": cid, "t": now})
        await db.commit()
    r = await ac.post(f"/hr/enrolments/{uuid.uuid4()}/round-review", json={"passed": True})
    check("an unknown application is a 404", r.status_code == 404, str(r.status_code))

    await ac.aclose()
    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
