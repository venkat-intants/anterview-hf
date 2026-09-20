"""D5 smoke test — publishing picks up candidates who applied early.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head:

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_d_publish_waiting.py

An opening advertised before its workflow was built collects applicants with no
workflow attached. The runner's comment said they would "join a workflow when
one goes live"; nothing did it, so shortlisting them afterwards started nothing.
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
            " notifications CASCADE"))

    cid, uid, rid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # PH4-O6: publishing needs a version approved by the company's super admin
    # — a different account from the one that authored it.
    sa_uid = uuid.uuid4()
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
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'superadmin@acme.test','Super Admin','x',:c,'en',true,false,false,:t,:t)"),
            {"i": sa_uid, "c": cid, "t": now})
        await db.execute(text(
            "INSERT INTO user_roles (user_id, role_id, assigned_at)"
            " SELECT :u, id, :t FROM roles WHERE name = 'super_admin'"),
            {"u": sa_uid, "t": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,status,public_apply_enabled,"
            " created_at,updated_at) VALUES (:i,:c,'Welder','open',true,:t,:t)"),
            {"i": rid, "c": cid, "t": now})
        await db.commit()

    from app.workflow_runner import enrol_applicant
    from app.workflows import add_round, create_draft

    people: dict[str, uuid.UUID] = {}
    async with f() as db:
        # They apply while nothing is live; one is shortlisted by hand.
        for name, status in [("Asha", "new"), ("Bilal", "shortlisted"), ("Chandra", "rejected")]:
            aid = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                " target_job_title,target_level,resume_text,status,created_at,updated_at)"
                " VALUES (:i,:c,:u,:n,:e,'Welder','mid','cv','new',:t,:t)"),
                {"i": aid, "c": cid, "u": uid, "n": name, "e": f"{name}@x.test", "t": now})
            out = await enrol_applicant(db, company_id=cid, applicant_id=aid, requisition_id=rid,
                                        target_job_title="Welder")
            people[name] = uuid.UUID(str(out.enrolment_id))
            if status != "new":
                await db.execute(text("UPDATE enrolments SET status = :s WHERE id = :e"),
                                 {"s": status, "e": people[name]})
        await db.commit()

        wf = await create_draft(db, company_id=cid, requisition_id=rid, created_by=uid)
        await db.commit()
        await add_round(db, company_id=cid, workflow_id=wf, title="Final review",
                        kind="human_review")
        await db.commit()

    from app.database import get_db_session
    from app.dependencies import get_hr_company, get_super_admin_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (uid, cid)
    app.dependency_overrides[get_super_admin_company] = lambda: (sa_uid, cid)
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    report = (await ac.get(f"/hr/workflows/{wf}/validate")).json()
    check("validation says who is waiting, before publishing",
          report.get("waiting") == {"shortlisted": 1, "applied": 1}, str(report.get("waiting")))

    # PH4-O6: a version must be reviewed and approved before it goes live.
    r = await ac.post(f"/hr/workflows/{wf}/publish")
    check("publishing an unapproved version is refused (409)", r.status_code == 409,
          str(r.status_code))
    r = await ac.post(f"/hr/workflows/{wf}/submit-review", json={})
    check("submit for review -> 200", r.status_code == 200, r.text[:200])
    r = await ac.post(f"/admin/workflow-reviews/{wf}/approve", json={})
    check("super admin approves -> 200", r.status_code == 200, r.text[:200])

    r = await ac.post(f"/hr/workflows/{wf}/publish")
    body = r.json() if r.status_code == 200 else {}
    check("publishing succeeds and reports what it did",
          r.status_code == 200 and body.get("attached_candidates") == 2
          and body.get("started_candidates") == 1, f"{r.status_code} {str(body)[:200]}")

    async with f() as db:
        rows = {r[0]: (r[1], r[2]) for r in (await db.execute(text(
            "SELECT a.full_name, e.workflow_id, e.current_round_id FROM enrolments e"
            " JOIN applicants a ON a.id = e.applicant_id"))).all()}
        # A change INTO shortlisted. Bilal's round move records his current
        # status ('shortlisted' -> 'shortlisted'), which is not a shortlisting.
        shortlisted_moves = await db.scalar(text(
            "SELECT count(*) FROM stage_transitions WHERE to_status = 'shortlisted'"
            "   AND from_status IS DISTINCT FROM 'shortlisted'"))
    check("the waiting applicants join the version just published",
          rows["Asha"][0] == wf and rows["Bilal"][0] == wf, str(rows))
    check("the one a person already shortlisted starts the first round",
          rows["Bilal"][1] is not None, str(rows["Bilal"]))
    check("the one nobody has shortlisted waits for the shortlist",
          rows["Asha"][1] is None, str(rows["Asha"]))
    check("a decided candidate is left alone", rows["Chandra"][0] is None, str(rows["Chandra"]))
    check("publishing shortlisted nobody itself (D-05)", shortlisted_moves == 0,
          f"moves={shortlisted_moves}")

    after = (await ac.get(f"/hr/workflows/{wf}/validate")).json()
    check("once attached, nobody is reported as waiting",
          after.get("waiting") == {"shortlisted": 0, "applied": 0}, str(after.get("waiting")))

    await ac.aclose()
    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
