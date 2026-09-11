"""B3 smoke test — the review screen can actually fix what the backfill guessed.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head (it
TRUNCATEs what it touches):

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_b_review.py

Drives the real endpoints (HR auth overridden, database real). The opening under
test is shaped the way the Group B backfill really shapes them: every candidate's
title normalises to the same key — which is what made split-by-title useless.
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


async def seed(f) -> dict:
    now = datetime.now(tz=UTC)
    s: dict = {k: uuid.uuid4() for k in ("company", "other_co", "hr", "python", "analyst",
                                         "data_analyst", "wf_req", "wf", "other_req")}
    async with f() as db:
        for cid, slug in [(s["company"], "acme"), (s["other_co"], "globex")]:
            await db.execute(text(
                "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                " VALUES (:i,:s,:s,true,:n,:n)"), {"i": cid, "s": slug, "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.test','HR','x',:c,'en',true,false,false,:n,:n)"),
            {"i": s["hr"], "c": s["company"], "n": now})
        for rid, cid, title, backfill in [
            (s["python"], s["company"], "Python Developer", True),
            (s["analyst"], s["company"], "Analyst (Data)", True),
            (s["data_analyst"], s["company"], "Data Analyst", False),
            (s["wf_req"], s["company"], "Nurse", True),
            (s["other_req"], s["other_co"], "Globex Nurse", False),
        ]:
            await db.execute(text(
                "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
                " created_at,updated_at) VALUES (:i,:c,:t,'mid','open',:b,:n,:n)"),
                {"i": rid, "c": cid, "t": title, "b": backfill, "n": now})
        await db.execute(text(
            "INSERT INTO workflows (id,company_id,requisition_id,version,status,created_at,"
            " updated_at) VALUES (:i,:c,:r,1,'draft',:n,:n)"),
            {"i": s["wf"], "c": s["company"], "r": s["wf_req"], "n": now})

        async def person(name: str, *reqs_titles: tuple) -> dict:
            aid = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                " target_job_title,target_level,resume_text,status,created_at,updated_at)"
                " VALUES (:i,:c,:u,:fn,:em,'x','mid','cv','new',:n,:n)"),
                {"i": aid, "c": s["company"], "u": s["hr"], "fn": name,
                 "em": f"{name.lower().replace(' ', '.')}@x.test", "n": now})
            enrs = {}
            for rid, title in reqs_titles:
                eid = uuid.uuid4()
                await db.execute(text(
                    "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                    " target_job_title,created_at,updated_at)"
                    " VALUES (:i,:c,:r,:a,'new',:t,:n,:n)"),
                    {"i": eid, "c": s["company"], "r": rid, "a": aid, "t": title, "n": now})
                enrs[rid] = eid
            return {"id": aid, "enrolments": enrs}

        # Three spellings that normalise identically — how the backfill groups.
        s["ravi"] = await person("Ravi", (s["python"], "Python Developer"))
        s["meena"] = await person("Meena", (s["python"], "python developer"))
        s["arjun"] = await person("Arjun", (s["python"], "Python  Developer"))
        # One job spelled two unrelated ways.
        s["kavya"] = await person("Kavya", (s["analyst"], "Analyst (Data)"))
        s["lakshmi"] = await person("Lakshmi", (s["data_analyst"], "Data Analyst"))
        # Someone in an opening that has a workflow.
        s["nisha"] = await person("Nisha", (s["wf_req"], "Nurse"))
        # A person with no application at all.
        await person("Unfiled Person")
        # A retired application in the analyst opening must not count.
        await db.execute(text(
            "UPDATE enrolments SET deleted_at = :n WHERE id = :e"),
            {"n": now, "e": (await person("Gone", (s["analyst"], "Data analyst (old)")))
                           ["enrolments"][s["analyst"]]})
        await db.commit()
    return s


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text("TRUNCATE applicants, companies, users, job_requisitions, enrolments,"
                             " stage_transitions, workflows CASCADE"))
    # audit_log is append-only (it refuses TRUNCATE), so this run's entries are
    # told apart by the company they name instead.
    s = await seed(f)

    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (s["hr"], s["company"])
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    # ── The review list ───────────────────────────────────────────────────────
    r = await ac.get("/hr/requisitions/review")
    rev = r.json()
    analyst = next(x for x in rev["backfilled_requisitions"] if x["id"] == str(s["analyst"]))
    check("a retired application does not count towards the opening",
          analyst["enrolments"] == 1 and analyst["distinct_source_titles"] == 1, str(analyst))
    # Two: the person who never had an application, and "Gone", whose only
    # application was retired — neither sits in any opening now.
    check("applicants filed under no opening are counted, not lost",
          rev.get("unfiled_applicants") == 2, str(rev.get("unfiled_applicants")))

    # ── Split: the old way cannot work on a backfilled opening ────────────────
    r = await ac.post(f"/hr/requisitions/{s['python']}/split",
                      json={"source_titles": ["python developer"], "new_title": "Data Engineer"})
    check("split-by-title on a backfilled opening selects everyone and is refused",
          r.status_code == 409 and "every candidate" in r.text, r.text[:200])

    meena = s["meena"]["enrolments"][s["python"]]
    r = await ac.post(f"/hr/requisitions/{s['python']}/split",
                      json={"enrolment_ids": [str(meena)], "new_title": "Data Engineer"})
    check("split by candidate separates what split by title could not",
          r.status_code == 200 and r.json()["moved"] == 1 and r.json()["left_behind"] == 2,
          r.text[:200])
    new_req = r.json().get("requisition_id")
    async with f() as db:
        row = (await db.execute(text(
            "SELECT e.requisition_id, r.from_backfill FROM enrolments e"
            " JOIN job_requisitions r ON r.id = e.requisition_id WHERE e.id = :e"),
            {"e": meena})).first()
        split_ledger = await db.scalar(text(
            "SELECT count(*) FROM stage_transitions WHERE enrolment_id = :e"
            " AND reason LIKE '%(split)%' AND NOT automated"), {"e": meena})
    check("the chosen candidate is in the new opening, which is not flagged for review",
          row is not None and str(row[0]) == new_req and row[1] is False, str(row))
    check("the move is in their history, as HR's doing", split_ledger == 1)

    r = await ac.post(f"/hr/requisitions/{s['python']}/split",
                      json={"enrolment_ids": [str(uuid.uuid4())], "new_title": "Anything"})
    check("a candidate who is not in the opening is refused (stale page)",
          r.status_code == 409 and "not in this opening" in r.text, r.text[:200])
    r = await ac.post(f"/hr/requisitions/{s['python']}/split", json={"new_title": "Anything"})
    check("a split that names nobody is refused up front", r.status_code == 422)

    # ── Merge two openings that are one job ───────────────────────────────────
    r = await ac.post(f"/hr/requisitions/{s['analyst']}/merge",
                      json={"into_requisition_id": str(s["data_analyst"])})
    check("merging two openings moves everyone", r.status_code == 200
          and r.json()["moved"] == 1, r.text[:200])
    kavya = s["kavya"]["enrolments"][s["analyst"]]
    async with f() as db:
        moved_to = await db.scalar(text("SELECT requisition_id FROM enrolments WHERE id = :e"),
                                   {"e": kavya})
        retired = (await db.execute(text(
            "SELECT status, deleted_at IS NOT NULL FROM job_requisitions WHERE id = :r"),
            {"r": s["analyst"]})).first()
        merge_ledger = await db.scalar(text(
            "SELECT count(*) FROM stage_transitions WHERE enrolment_id = :e"
            " AND reason LIKE '%(merged from%'"), {"e": kavya})
        audited = await db.scalar(text(
            "SELECT count(*) FROM audit_log WHERE action = 'requisition.merge'"
            " AND details->>'company_id' = :c"), {"c": str(s["company"])})
    check("the candidate is now in the surviving opening", moved_to == s["data_analyst"])
    check("the emptied opening is closed and retired", retired is not None
          and retired[0] == "closed" and retired[1] is True, str(retired))
    check("the move is in their history, and the merge is audited",
          merge_ledger == 1 and audited == 1, f"ledger={merge_ledger} audit={audited}")
    r = await ac.post("/hr/requisitions", json={"title": "Analyst (Data)"})
    check("the retired opening's title is free to use again", r.status_code == 201, r.text[:200])

    # ── Merge refusals ────────────────────────────────────────────────────────
    r = await ac.post(f"/hr/requisitions/{s['wf_req']}/merge",
                      json={"into_requisition_id": str(s["data_analyst"])})
    check("an opening with its own workflow cannot be merged away",
          r.status_code == 409 and "workflow" in r.text, r.text[:200])
    # Ravi also applies to Data Analyst: he would hold two applications.
    async with f() as db:
        await db.execute(text(
            "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
            " target_job_title,created_at,updated_at)"
            " VALUES (:i,:c,:r,:a,'new','Data Analyst',now(),now())"),
            {"i": uuid.uuid4(), "c": s["company"], "r": s["data_analyst"], "a": s["ravi"]["id"]})
        await db.commit()
    r = await ac.post(f"/hr/requisitions/{s['python']}/merge",
                      json={"into_requisition_id": str(s["data_analyst"])})
    check("someone who applied to both blocks the merge, by name",
          r.status_code == 409 and "Ravi" in r.text, r.text[:200])
    r = await ac.post(f"/hr/requisitions/{s['python']}/merge",
                      json={"into_requisition_id": str(s["python"])})
    check("an opening cannot be merged into itself", r.status_code == 409)
    r = await ac.post(f"/hr/requisitions/{s['python']}/merge",
                      json={"into_requisition_id": str(s["other_req"])})
    check("another company's opening is not a merge target",
          r.status_code == 409 and "not found" in r.text, r.text[:200])

    # ── Confirm ───────────────────────────────────────────────────────────────
    r = await ac.post(f"/hr/requisitions/{s['python']}/confirm")
    async with f() as db:
        confirmed = await db.scalar(text(
            "SELECT from_backfill FROM job_requisitions WHERE id = :r"), {"r": s["python"]})
        audited = await db.scalar(text(
            "SELECT count(*) FROM audit_log WHERE action = 'requisition.confirm'"
            " AND details->>'company_id' = :c"), {"c": str(s["company"])})
    check("confirming takes the opening out of the review queue, audited",
          r.status_code == 200 and confirmed is False and audited == 1,
          f"{r.status_code} {confirmed} {audited}")
    r = await ac.post(f"/hr/requisitions/{s['other_req']}/confirm")
    check("another company's opening cannot be confirmed", r.status_code == 404)

    await ac.aclose()
    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 64)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
