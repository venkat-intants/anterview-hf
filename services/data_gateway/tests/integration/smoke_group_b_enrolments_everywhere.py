"""B1/B4/B5 step 1 smoke test — every applicant on an opening, every score on
its enrolment.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL (it
TRUNCATEs what it touches and moves the alembic head back and forth):

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      PYTHONPATH=".;../.." python tests/integration/smoke_group_b_enrolments_everywhere.py

What it does, in order:
  1. Rewinds to the revision before the catch-up migration, seeds the shapes
     that migration exists for, then upgrades and checks the result.
  2. Drives the real HR single and bulk upload endpoints (storage, PDF parsing
     and the scorer stubbed; the database real).
  3. Runs the real reconciler score pass and checks where scores land.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


def alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": URL}
    subprocess.run([sys.executable, "-m", "alembic", *args], check=True, env=env,
                   capture_output=True)


SCORE = {"overall": 7, "breakdown": {"skills": 7}, "strengths": ["x"], "concerns": ["y"],
         "recommendation": "consider", "summary": "ok"}


async def seed_before_catch_up(f) -> dict:
    now = datetime.now(tz=UTC)
    cid, hr = uuid.uuid4(), uuid.uuid4()
    ids = {k: uuid.uuid4() for k in ("orphan_scored", "orphan_spaced", "orphan_new_title",
                                     "multi", "req_nurse", "req_analyst", "enr_old", "enr_new")}
    async with f() as db:
        await db.execute(text("INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                              " VALUES (:i,'Acme','acme',true,:n,:n)"), {"i": cid, "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.test','HR','x',:c,'en',true,false,false,:n,:n)"),
            {"i": hr, "c": cid, "n": now})
        # An opening that already exists (the Group B backfill made it).
        for rid, title in [(ids["req_nurse"], "Staff Nurse"), (ids["req_analyst"], "Data Analyst")]:
            await db.execute(text(
                "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
                " created_at,updated_at) VALUES (:i,:c,:t,'mid','open',true,:n,:n)"),
                {"i": rid, "c": cid, "t": title, "n": now})

        async def applicant(key: str, title: str, ats: int | None, **extra) -> None:
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                " target_job_title,target_level,resume_text,resume_s3_key,status,ats_overall,"
                " ats_summary,created_at,updated_at)"
                " VALUES (:i,:c,:u,:fn,:em,:t,'mid','cv text',:k,'new',:o,:su,:ca,:n)"),
                {"i": ids[key], "c": cid, "u": hr, "fn": key, "em": f"{key}@x.test", "t": title,
                 "k": f"applicants/{cid}/{ids[key]}.pdf", "o": ats,
                 "su": "legacy summary" if ats is not None else None,
                 "ca": extra.get("created", now), "n": now})

        # HR uploads made after the Group B migration: no enrolment.
        await applicant("orphan_scored", "Staff Nurse", 8)       # existing opening
        await applicant("orphan_spaced", "  staff   NURSE ", None)  # same opening, spacing
        await applicant("orphan_new_title", "Welder", None)      # no opening yet
        # A returning public applicant: an old backfilled application, and a newer
        # one whose score only ever reached the applicant row.
        await applicant("multi", "Data Analyst", 6)
        for key, req, title, when, ats in [
            ("enr_old", ids["req_nurse"], "Staff Nurse", now - timedelta(days=5), 5),
            ("enr_new", ids["req_analyst"], "Data Analyst", now, None),
        ]:
            await db.execute(text(
                "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                " target_job_title,target_level,ats_overall,created_at,updated_at)"
                " VALUES (:i,:c,:r,:a,'new',:t,'mid',:o,:ca,:ca)"),
                {"i": ids[key], "c": cid, "r": req, "a": ids["multi"], "t": title,
                 "o": ats, "ca": when})
        await db.commit()
    return {"company": cid, "hr": hr, **ids}


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)

    # ── 1. The catch-up migration ─────────────────────────────────────────
    alembic("upgrade", "head")
    alembic("downgrade", "a7c9e1b3d5f8")
    async with eng.begin() as c:
        await c.execute(text("TRUNCATE applicants, companies, users, job_requisitions,"
                             " enrolments, stage_transitions, reconciliation_state CASCADE"))
    s = await seed_before_catch_up(f)
    alembic("upgrade", "head")
    print("\n--- catch-up migration applied ---\n")

    async with f() as db:
        enr = {r["applicant_id"]: dict(r) for r in (await db.execute(text(
            "SELECT e.applicant_id, e.requisition_id, e.ats_overall, e.scored_resume_s3_key,"
            "       r.title, r.from_backfill"
            "  FROM enrolments e JOIN job_requisitions r ON r.id = e.requisition_id"
            " WHERE e.applicant_id <> :m"), {"m": s["multi"]})).mappings().all()}
        ledger = await db.scalar(text(
            "SELECT count(*) FROM stage_transitions t JOIN enrolments e ON e.id = t.enrolment_id"
            " WHERE e.applicant_id IN (:a, :b, :c)"),
            {"a": s["orphan_scored"], "b": s["orphan_spaced"], "c": s["orphan_new_title"]})
        multi = {r["id"]: dict(r) for r in (await db.execute(text(
            "SELECT id, ats_overall, scored_resume_s3_key FROM enrolments WHERE applicant_id = :a"),
            {"a": s["multi"]})).mappings().all()}

    check("an applicant HR uploaded after the migration now has an enrolment",
          s["orphan_scored"] in enr and s["orphan_new_title"] in enr, str(list(enr)))
    check("it joins the opening its title already belongs to",
          enr[s["orphan_scored"]]["requisition_id"] == s["req_nurse"])
    check("case and spacing differences land on the same opening (no fuzzy match)",
          enr[s["orphan_spaced"]]["requisition_id"] == s["req_nurse"])
    check("a title with no opening gets one, flagged for HR's review",
          enr[s["orphan_new_title"]]["title"] == "Welder"
          and enr[s["orphan_new_title"]]["from_backfill"] is True)
    check("its score and scored CV are copied onto the enrolment",
          enr[s["orphan_scored"]]["ats_overall"] == 8
          and enr[s["orphan_scored"]]["scored_resume_s3_key"] is not None)
    check("every new enrolment gets a ledger floor", ledger == 3, f"ledger={ledger}")
    check("a score that belongs to the latest application is copied to it",
          multi[s["enr_new"]]["ats_overall"] == 6
          and multi[s["enr_new"]]["scored_resume_s3_key"] is not None, str(multi))
    check("an older application's own score is left as it was",
          multi[s["enr_old"]]["ats_overall"] == 5, str(multi))

    # ── 2. HR uploads, through the real endpoints ─────────────────────────
    import app.routers.hr_applicants as hra
    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app

    scored_titles: list[str] = []

    async def _score(**kw: object) -> dict:
        scored_titles.append(str(kw["job_title"]))
        return SCORE

    async def _noop(*_: object, **__: object) -> None:
        return None

    async def _text(_raw: bytes) -> str:
        return "cv text"

    hra.score_resume_remote = _score  # type: ignore[assignment]
    hra._upload_to_s3 = _noop  # type: ignore[assignment]
    hra._extract_pdf_text = _text  # type: ignore[assignment]
    hra._embed_applicant = _noop  # type: ignore[assignment]

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (s["hr"], s["company"])
    app.dependency_overrides[get_db_session] = _db
    pdf = ("cv.pdf", b"%PDF-1.4", "application/pdf")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r1 = await ac.post("/hr/applicants", files={"file": pdf},
                           data={"full_name": "Asha", "target_job_title": "STAFF nurse"})
        r2 = await ac.post("/hr/applicants", files={"file": pdf},
                           data={"full_name": "Ravi", "target_job_title": "Anything typed",
                                 "requisition_id": str(s["req_nurse"])})
        r3 = await ac.post("/hr/applicants/bulk",
                           files=[("files", ("a.pdf", b"%PDF-1.4", "application/pdf")),
                                  ("files", ("b.pdf", b"%PDF-1.4", "application/pdf"))],
                           data={"target_job_title": "Electrician"})
        r4 = await ac.post("/hr/applicants", files={"file": pdf},
                           data={"full_name": "X", "target_job_title": "Nurse",
                                 "requisition_id": str(uuid.uuid4())})
    app.dependency_overrides.clear()

    check("single upload succeeds", r1.status_code == 201, r1.text[:200])
    check("single upload into a chosen opening succeeds", r2.status_code == 201, r2.text[:200])
    check("bulk upload succeeds", r3.status_code == 201, r3.text[:200])
    check("an opening from another company (or none) is refused", r4.status_code == 404,
          str(r4.status_code))
    async with f() as db:
        rows = {r["full_name"]: dict(r) for r in (await db.execute(text(
            "SELECT a.full_name, a.target_job_title AS a_title, e.requisition_id,"
            "       e.target_job_title, e.ats_overall, e.scored_resume_s3_key, a.resume_s3_key,"
            "       (SELECT t.automated FROM stage_transitions t WHERE t.enrolment_id = e.id"
            "         ORDER BY t.occurred_at LIMIT 1) AS automated,"
            "       (SELECT t.actor_user_id FROM stage_transitions t WHERE t.enrolment_id = e.id"
            "         ORDER BY t.occurred_at LIMIT 1) AS actor"
            "  FROM applicants a JOIN enrolments e ON e.applicant_id = a.id"
            " WHERE a.full_name IN ('Asha','Ravi')"))).mappings().all()}
        electrician = await db.scalar(text(
            "SELECT count(*) FROM enrolments e JOIN job_requisitions r ON r.id = e.requisition_id"
            " WHERE r.title = 'Electrician' AND r.from_backfill"))
    asha, ravi = rows.get("Asha", {}), rows.get("Ravi", {})
    check("a single upload is filed under the opening its title resolves to",
          asha.get("requisition_id") == s["req_nurse"], str(asha))
    check("a chosen opening wins over the typed title",
          ravi.get("requisition_id") == s["req_nurse"]
          and ravi.get("a_title") == "Staff Nurse", str(ravi))
    check("the upload's score lands on the enrolment, with the CV it scored",
          asha.get("ats_overall") == 7
          and asha.get("scored_resume_s3_key") == asha.get("resume_s3_key"), str(asha))
    check("the ledger records the upload as HR's doing, not the system's",
          asha.get("automated") is False and asha.get("actor") == s["hr"], str(asha))
    check("every file in a bulk upload is filed under one new opening",
          electrician == 2, f"electrician={electrician}")

    # ── 3. The reconciler scores applications, against their own role ─────
    import app.reconciliation as rec

    scored_titles.clear()
    rec.score_resume_remote = _score  # type: ignore[assignment]
    async with f() as db:
        result = rec.PassResult()
        await rec._score_pass(db, result)
    async with f() as db:
        welder = (await db.execute(text(
            "SELECT e.ats_overall, e.scored_resume_s3_key, e.scored_at, a.ats_overall AS a_ats"
            "  FROM enrolments e JOIN applicants a ON a.id = e.applicant_id"
            " WHERE a.id = :a"), {"a": s["orphan_new_title"]})).mappings().first()
        bulk_scored = await db.scalar(text(
            "SELECT count(*) FROM enrolments e JOIN job_requisitions r ON r.id = e.requisition_id"
            " WHERE r.title = 'Electrician' AND e.ats_overall IS NOT NULL"
            "   AND e.scored_resume_s3_key IS NOT NULL"))
    check("the reconciler scores each application against its own opening",
          "Welder" in scored_titles and "Electrician" in scored_titles, str(scored_titles))
    check("the score, scored CV and time land on the enrolment",
          welder is not None and welder["ats_overall"] == 7
          and welder["scored_resume_s3_key"] is not None and welder["scored_at"] is not None,
          str(welder))
    check("the applicant row mirrors its latest application's score",
          welder is not None and welder["a_ats"] == 7)
    check("bulk-uploaded applications are scored on their enrolments", bulk_scored == 2,
          f"bulk_scored={bulk_scored}")
    async with f() as db:
        again = rec.PassResult()
        await rec._score_pass(db, again)
    check("a second pass has nothing left to score", again.scored == 0, str(again.as_dict()))

    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 64)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
