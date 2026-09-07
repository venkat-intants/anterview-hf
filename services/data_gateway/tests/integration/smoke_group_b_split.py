"""Splitting a mis-grouped opening — the other half of the review screen.

Merge fixes duplicate PEOPLE; split fixes duplicate OPENINGS. The backfill
groups applicants on a normalised title, so "Python Developer" and
"Senior Python Developer" stay separate but "Python Developer" and
"python  developer" become one — and occasionally two genuinely different jobs
are spelled alike and get folded together. This is how a human takes them back
apart.

Everything below runs against real SQL. The guard worth reading carefully is
the mid-workflow one: a candidate already running the old opening's workflow is
NOT moved, because re-filing them would either strand them at a round that is
no longer theirs or silently re-scope what they are being assessed against.

    docker run -d --name intants-pgv -e POSTGRES_PASSWORD=postgres \
      -e POSTGRES_DB=intants_smoke -p 55432:5432 pgvector/pgvector:pg16
    cd services/data_gateway
    export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke
    python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_b_split.py

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


# Five applicants under three spellings, all folded into one opening. Two of
# them ("Senior ...") are the ones that do not belong.
SPELLINGS = [
    ("Ananya", "Python Developer"),
    ("Bhavya", "python  developer"),
    ("Chetan", "PYTHON DEVELOPER"),
    ("Divya", "Senior Python Developer"),
    ("Eshan", "senior python developer"),
]


async def main() -> None:
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)

    async with eng.begin() as c:
        await c.execute(
            text(
                "TRUNCATE companies, users, applicants, job_requisitions, enrolments,"
                " stage_transitions, workflows, workflow_rounds, round_criteria,"
                # audit_log is deliberately absent: an append-only trigger
                # refuses TRUNCATE on it (DPDP audit integrity), so the count
                # below is scoped to this run's own requisition instead.
                " round_results CASCADE"
            )
        )

    cid, hr_uid, req_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    other_cid = uuid.uuid4()
    enrolments: dict[str, uuid.UUID] = {}

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
                "INSERT INTO job_requisitions (id,company_id,title,level,owner_user_id,"
                " created_by_user_id,status,from_backfill,created_at,updated_at)"
                " VALUES (:i,:c,'Python Developer','mid',:u,:u,'open',true,:t,:t)"
            ),
            {"i": req_id, "c": cid, "u": hr_uid, "t": now},
        )
        for name, spelling in SPELLINGS:
            aid, eid = uuid.uuid4(), uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                    " target_job_title,target_level,status,created_at,updated_at)"
                    " VALUES (:i,:c,:u,:n,:e,:tt,'mid','new',:t,:t)"
                ),
                {"i": aid, "c": cid, "u": hr_uid, "n": name,
                 "e": f"{name.lower()}@x.test", "tt": spelling, "t": now},
            )
            await db.execute(
                text(
                    "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                    " target_job_title,created_at,updated_at)"
                    " VALUES (:i,:c,:r,:a,'new',:tt,:t,:t)"
                ),
                {"i": eid, "c": cid, "r": req_id, "a": aid, "tt": spelling, "t": now},
            )
            enrolments[name] = eid
        await db.commit()

    async def _db_override():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_hr_company] = lambda: (hr_uid, cid)

    tr = ASGITransport(app=app)
    async with AsyncClient(transport=tr, base_url="http://t") as c:
        print("\n--- the review screen sees the fold ---")
        r = await c.get("/hr/requisitions/review")
        check("GET review -> 200", r.status_code == 200, r.text[:200])
        row = r.json()["backfilled_requisitions"][0]
        check("all five candidates landed in one opening", row["enrolments"] == 5, str(row))
        check("every distinct spelling is reported, not the normalised groups",
              row["distinct_source_titles"] == 5, str(row))

        print("\n--- what the server refuses ---")
        r = await c.post(
            f"/hr/requisitions/{req_id}/split",
            json={"source_titles": ["Nothing Like This"], "new_title": "Ghost Role"},
        )
        check("titles nobody applied under -> 409", r.status_code == 409, r.text[:160])

        r = await c.post(
            f"/hr/requisitions/{req_id}/split",
            json={"source_titles": [s for _, s in SPELLINGS], "new_title": "Everything"},
        )
        check("moving every candidate out -> 409", r.status_code == 409, r.text[:160])
        check("and it says to rename instead", "rename" in r.text.lower(), r.text[:160])

        r = await c.post(
            f"/hr/requisitions/{req_id}/split",
            json={"source_titles": ["Senior Python Developer"], "new_title": " "},
        )
        check("a blank new title -> 422", r.status_code == 422, str(r.status_code))

        print("\n--- a candidate part-way through the workflow blocks the split ---")
        wf_id = uuid.uuid4()
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
                    " created_by_user_id,created_at,updated_at)"
                    " VALUES (:i,:c,:r,1,'published',:u,:t,:t)"
                ),
                {"i": wf_id, "c": cid, "r": req_id, "u": hr_uid, "t": now},
            )
            await db.execute(
                text("UPDATE enrolments SET workflow_id = :w WHERE id = :e"),
                {"w": wf_id, "e": enrolments["Divya"]},
            )
            await db.commit()

        r = await c.post(
            f"/hr/requisitions/{req_id}/split",
            json={"source_titles": ["Senior Python Developer"],
                  "new_title": "Senior Python Developer"},
        )
        check("a mid-workflow candidate refuses the split", r.status_code == 409, r.text[:160])
        check("and the refusal names them", "Divya" in r.text, r.text[:200])

        async with factory() as db:
            still = await db.scalar(
                text("SELECT count(*) FROM enrolments WHERE requisition_id = :r"), {"r": req_id}
            )
            made = await db.scalar(
                text("SELECT count(*) FROM job_requisitions WHERE company_id = :c"), {"c": cid}
            )
        check("nothing moved on the refused split", int(still) == 5, str(still))
        # The insert of the new requisition happens BEFORE the move, so a
        # refusal that left one behind would be a half-applied split.
        check("and no orphan opening was left behind", int(made) == 1, str(made))

        print("\n--- the split itself ---")
        async with factory() as db:
            await db.execute(
                text("UPDATE enrolments SET workflow_id = NULL WHERE id = :e"),
                {"e": enrolments["Divya"]},
            )
            await db.commit()

        r = await c.post(
            f"/hr/requisitions/{req_id}/split",
            json={"source_titles": ["Senior Python Developer", "senior python developer"],
                  "new_title": "Senior Python Developer"},
        )
        check("POST split -> 200", r.status_code == 200, r.text[:200])
        out = r.json()
        check("both senior candidates moved", out["moved"] == 2, str(out))
        check("the other three stayed", out["left_behind"] == 3, str(out))
        new_id = out["requisition_id"]

        async with factory() as db:
            names = (
                await db.execute(
                    text(
                        "SELECT a.full_name FROM enrolments e JOIN applicants a"
                        " ON a.id = e.applicant_id WHERE e.requisition_id = :r ORDER BY 1"
                    ),
                    {"r": uuid.UUID(new_id)},
                )
            ).scalars().all()
            flag = await db.scalar(
                text("SELECT from_backfill FROM job_requisitions WHERE id = :i"),
                {"i": uuid.UUID(new_id)},
            )
            audit = await db.scalar(
                text(
                    "SELECT count(*) FROM audit_log"
                    " WHERE action = 'requisition.split' AND resource_id = :i"
                ),
                {"i": uuid.UUID(new_id)},
            )
        check("exactly the two senior applicants moved", names == ["Divya", "Eshan"], str(names))
        # A human chose this one, so it must not reappear in the review queue.
        check("the new opening is not flagged as backfill", flag is False, str(flag))
        check("the split is audit-logged", int(audit) == 1, str(audit))

        print("\n--- title collision + tenancy ---")
        r = await c.post(
            f"/hr/requisitions/{req_id}/split",
            json={"source_titles": ["python  developer"], "new_title": "Senior Python Developer"},
        )
        check("colliding with an open opening -> 409", r.status_code == 409, r.text[:160])

        r = await c.post(
            f"/hr/requisitions/{uuid.uuid4()}/split",
            json={"source_titles": ["Python Developer"], "new_title": "Somewhere Else"},
        )
        check("an unknown opening -> 404", r.status_code == 404, str(r.status_code))

        # Another tenant's opening must be indistinguishable from one that does
        # not exist — a 403 would confirm it is there.
        foreign = uuid.uuid4()
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO job_requisitions (id,company_id,title,level,status,"
                    " from_backfill,created_at,updated_at)"
                    " VALUES (:i,:c,'Their Role','mid','open',false,:t,:t)"
                ),
                {"i": foreign, "c": other_cid, "t": now},
            )
            await db.commit()
        r = await c.post(
            f"/hr/requisitions/{foreign}/split",
            json={"source_titles": ["Their Role"], "new_title": "Mine Now"},
        )
        check("another company's opening -> 404, not 403", r.status_code == 404, str(r.status_code))

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'=' * 60}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 60)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
