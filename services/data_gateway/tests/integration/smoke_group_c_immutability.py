"""C1 smoke test — a published workflow cannot change, at the database.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head:

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_c_immutability.py

app/workflows.py already refuses structural edits to a published version. This
checks the guard UNDER it: raw SQL — a fix by hand, a future importer — cannot
move a threshold beneath candidates who already sat the round. And that the
legitimate paths (publish, clone to version n+1, archive) still work, because a
guard that blocks those would be found at the worst possible moment.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def refused(f, sql: str, params: dict) -> str | None:
    """Run a statement expecting the database to refuse it. Returns the message."""
    try:
        async with f() as db:
            await db.execute(text(sql), params)
            await db.commit()
    except (DBAPIError, IntegrityError) as exc:
        return str(exc)
    return None


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE applicants, companies, users, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, round_criteria CASCADE"))

    company, hr, req = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with f() as db:
        await db.execute(text(
            "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
            " VALUES (:i,'acme','acme',true,:n,:n)"), {"i": company, "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.test','HR','x',:c,'en',true,false,false,:n,:n)"),
            {"i": hr, "c": company, "n": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
            " created_at,updated_at) VALUES (:i,:c,'Python Developer','mid','open',false,:n,:n)"),
            {"i": req, "c": company, "n": now})
        await db.commit()

    from app.workflows import add_round, clone_for_edit, create_draft, publish, set_round_criteria

    competencies = [
        {"id": "python", "name": "Python", "kind": "technical", "weight": 0.5},
        {"id": "problem_solving", "name": "Problem Solving", "kind": "cognitive", "weight": 0.3},
        {"id": "communication", "name": "Communication", "kind": "behavioural", "weight": 0.2},
    ]
    async with f() as db:
        wf1 = await create_draft(db, company_id=company, requisition_id=req, created_by=hr,
                                 name="v1")
        rd1 = await add_round(db, company_id=company, workflow_id=wf1, title="Interview",
                              kind="ai_interview", pass_threshold=60)
        await set_round_criteria(db, company_id=company, round_id=rd1, criteria=competencies)
        await db.commit()
        report = await publish(db, company_id=company, workflow_id=wf1,
                               profile_competencies=competencies)
        await db.commit()
    check("a draft publishes", report.publishable, str(getattr(report, "errors", None)))

    # ── The guard ───────────────────────────────────────────────────────────
    msg = await refused(f, "UPDATE workflows SET hold_band = 40 WHERE id = :i", {"i": wf1})
    check("a published workflow's settings cannot be changed by raw SQL",
          msg is not None and "cannot be edited" in msg, str(msg)[:160])

    msg = await refused(f, "UPDATE workflow_rounds SET pass_threshold = 10 WHERE id = :i",
                        {"i": rd1})
    check("nor a round's pass threshold",
          msg is not None and "cannot change" in msg, str(msg)[:160])

    msg = await refused(f, "DELETE FROM round_criteria WHERE round_id = :r", {"r": rd1})
    check("nor its criteria", msg is not None and "cannot change" in msg, str(msg)[:160])

    msg = await refused(f, "INSERT INTO workflow_rounds (id,company_id,workflow_id,position,"
                           " title,kind,created_at,updated_at)"
                           " VALUES (:i,:c,:w,9,'Sneaky','mcq',now(),now())",
                        {"i": uuid.uuid4(), "c": company, "w": wf1})
    check("nor a round added to it", msg is not None and "cannot change" in msg, str(msg)[:160])

    msg = await refused(f, "UPDATE workflows SET status = 'draft' WHERE id = :i", {"i": wf1})
    check("and it cannot be reopened as a draft",
          msg is not None and "create a new version" in msg, str(msg)[:160])

    msg = await refused(f, "DELETE FROM workflows WHERE id = :i", {"i": wf1})
    check("nor deleted outright", msg is not None and "cannot be deleted" in msg, str(msg)[:160])

    msg = await refused(f, "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
                           " created_at,updated_at) VALUES (:i,:c,:r,99,'published',now(),now())",
                        {"i": uuid.uuid4(), "c": company, "r": req})
    check("one opening cannot have two live workflows",
          msg is not None and "uq_workflows_one_published" in msg, str(msg)[:160])

    # ── The legitimate path still works ─────────────────────────────────────
    async with f() as db:
        wf2 = await clone_for_edit(db, company_id=company, workflow_id=wf1, created_by=hr)
        await db.commit()
    async with f() as db:
        rounds = (await db.execute(text(
            "SELECT id, title, pass_threshold FROM workflow_rounds"
            " WHERE workflow_id = :w AND deleted_at IS NULL"), {"w": wf2})).mappings().all()
        crit = await db.scalar(text(
            "SELECT count(*) FROM round_criteria rc JOIN workflow_rounds wr ON wr.id = rc.round_id"
            " WHERE wr.workflow_id = :w"), {"w": wf2})
    check("editing a published workflow clones it, with its rounds and criteria",
          len(rounds) == 1 and crit == 3, f"{rounds} criteria={crit}")

    async with f() as db:
        await db.execute(text(
            "UPDATE workflow_rounds SET pass_threshold = 75, updated_at = now()"
            " WHERE workflow_id = :w"), {"w": wf2})
        await db.commit()
        moved = await db.scalar(text(
            "SELECT pass_threshold FROM workflow_rounds WHERE workflow_id = :w"), {"w": wf2})
    check("the new draft is editable", moved == 75, str(moved))

    async with f() as db:
        report2 = await publish(db, company_id=company, workflow_id=wf2,
                                profile_competencies=competencies)
        await db.commit()
        states = {str(r[0]): r[1] for r in (await db.execute(text(
            "SELECT id, status FROM workflows WHERE requisition_id = :r"), {"r": req})).all()}
    check("publishing the new version archives the old one",
          report2.publishable and states[str(wf1)] == "archived"
          and states[str(wf2)] == "published", str(states))

    async with f() as db:
        v1_threshold = await db.scalar(text(
            "SELECT pass_threshold FROM workflow_rounds WHERE workflow_id = :w"), {"w": wf1})
    check("and version 1 still says what it said when people started on it",
          v1_threshold == 60, str(v1_threshold))

    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
