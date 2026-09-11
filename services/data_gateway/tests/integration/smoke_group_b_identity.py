"""B4 smoke test — one applicant per person per company, keyed by email.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head (it
TRUNCATEs what it touches, and drops/recreates the identity index):

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_b_identity.py

Real database, real endpoints (HR auth overridden; storage, PDF text and the
scorer stubbed). The story: an install that already has duplicate people gets
the rule switched on by HR merging them, and from then on uploads find the
person instead of copying them.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []
INDEX_SQL = "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_applicants_company_email'"


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def seed(f) -> dict:
    now = datetime.now(tz=UTC)
    s: dict = {k: uuid.uuid4() for k in ("company", "other_co", "hr", "python", "nurse")}
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
        for rid, title in [(s["python"], "Python Developer"), (s["nurse"], "Staff Nurse")]:
            await db.execute(text(
                "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
                " created_at,updated_at) VALUES (:i,:c,:t,'mid','open',false,:n,:n)"),
                {"i": rid, "c": s["company"], "t": title, "n": now})

        async def person(key: str, name: str, email: str | None, rid: uuid.UUID | None,
                         company: uuid.UUID | None = None, parsed: str | None = None) -> None:
            aid = uuid.uuid4()
            s[key] = aid
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                " parsed_email,target_job_title,target_level,resume_text,resume_s3_key,status,"
                " created_at,updated_at)"
                " VALUES (:i,:c,:u,:fn,:em,:pe,'x','mid','cv',:k,'new',:n,:n)"),
                {"i": aid, "c": company or s["company"], "u": s["hr"], "fn": name, "em": email,
                 "pe": parsed, "k": f"applicants/{key}.pdf", "n": now})
            if rid is not None:
                await db.execute(text(
                    "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                    " target_job_title,created_at,updated_at)"
                    " VALUES (:i,:c,:r,:a,'new','x',:n,:n)"),
                    {"i": uuid.uuid4(), "c": s["company"], "r": rid, "a": aid, "n": now})

        # One person, uploaded twice before the rule existed: case and spacing differ.
        await person("asha", "Asha", "Asha@X.in", s["python"])
        await person("asha_dup", "Asha R", " asha@x.in ", s["nurse"])
        # The same address at another company is a different person there.
        await person("globex_asha", "Asha (Globex)", "asha@x.in", None, company=s["other_co"])
        # A second person uploaded twice.
        await person("meena", "Meena", "meena@x.in", s["python"])
        await person("meena_dup", "Meena K", "MEENA@x.in", s["nurse"])
        # A duplicate found only through an address kept aside off a CV. The
        # index does not cover it (only one row has it as ``email``).
        await person("ravi", "Ravi", "ravi@x.in", s["python"])
        await person("ravi_cv", "ravi_cv_final", None, s["nurse"], parsed="ravi@x.in")
        await db.commit()
    return s


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text("DROP INDEX IF EXISTS uq_applicants_company_email"))
        await c.execute(text("TRUNCATE applicants, companies, users, job_requisitions, enrolments,"
                             " stage_transitions CASCADE"))
    s = await seed(f)

    from app.requisitions import ensure_applicant_identity_index

    # ── 1. Duplicates on file: the rule waits, it does not fail ─────────────
    async with f() as db:
        on = await ensure_applicant_identity_index(db)
    check("with duplicates on file the rule is not switched on (and nothing fails)", on is False)

    import app.routers.hr_applicants as hra
    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app
    from app.scoring_client import ResumeScoreError

    deleted: list[str] = []
    score_email: dict = {"v": None, "fail": False}

    async def _score(**_: object) -> dict:
        if score_email["fail"]:
            raise ResumeScoreError("scorer down")
        return {"overall": 7, "breakdown": {}, "strengths": [], "concerns": [],
                "recommendation": "consider", "summary": "ok",
                "candidate_name": "Parsed Name", "candidate_email": score_email["v"]}

    async def _noop(*_: object, **__: object) -> None:
        return None

    async def _del(key: str) -> None:
        deleted.append(key)

    async def _text(_raw: bytes) -> str:
        return "cv text"

    hra.score_resume_remote = _score  # type: ignore[assignment]
    hra._upload_to_s3 = _noop  # type: ignore[assignment]
    hra._delete_from_s3 = _del  # type: ignore[assignment]
    hra._extract_pdf_text = _text  # type: ignore[assignment]
    hra._embed_applicant = _noop  # type: ignore[assignment]

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (s["hr"], s["company"])
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    pdf = ("cv.pdf", b"%PDF-1.4", "application/pdf")

    r = await ac.get("/hr/requisitions/review")
    groups = {c["email"]: c for c in r.json().get("merge_candidates", [])}
    check("the review screen proposes every duplicate, the CV-address one included",
          set(groups) == {"asha@x.in", "meena@x.in", "ravi@x.in"}, str(sorted(groups)))
    check("the other company's Asha is not proposed as the same person",
          str(s["globex_asha"]) not in groups.get("asha@x.in", {}).get("applicant_ids", []))

    # ── 2. HR merges them; the last blocking merge switches the rule on ────
    r1 = await ac.post("/hr/applicants/merge",
                       json={"survivor_id": str(s["asha"]), "absorbed_ids": [str(s["asha_dup"])]})
    async with f() as db:
        after_one = await db.scalar(text(INDEX_SQL))
    check("first merge succeeds", r1.status_code == 200, r1.text[:200])
    check("one duplicate left: still waiting", after_one is None)

    r2 = await ac.post("/hr/applicants/merge",
                       json={"survivor_id": str(s["meena"]), "absorbed_ids": [str(s["meena_dup"])]})
    async with f() as db:
        after_two = await db.scalar(text(INDEX_SQL))
    check("second merge succeeds", r2.status_code == 200, r2.text[:200])
    check("the last blocking merge switches the rule on by itself", after_two is not None)

    # Ravi's copy is known only by a kept-aside address, which never blocked the
    # rule — so this merge runs with the rule already live.
    r3 = await ac.post("/hr/applicants/merge",
                       json={"survivor_id": str(s["ravi_cv"]), "absorbed_ids": [str(s["ravi"])]})
    async with f() as db:
        ravi = (await db.execute(text("SELECT email, parsed_email FROM applicants WHERE id = :a"),
                                 {"a": s["ravi_cv"]})).mappings().first()
    check("a merge with the rule live succeeds", r3.status_code == 200, r3.text[:200])
    check("a survivor known only by its CV address takes it as its email",
          ravi is not None and ravi["email"] == "ravi@x.in" and ravi["parsed_email"] is None,
          str(ravi))

    # ── 3. The rule holds ──────────────────────────────────────────────────
    now = datetime.now(tz=UTC)

    async def insert(company: uuid.UUID, email: str) -> bool:
        try:
            async with f() as db:
                await db.execute(text(
                    "INSERT INTO applicants (id,company_id,full_name,email,target_job_title,"
                    " target_level,status,created_at,updated_at)"
                    " VALUES (:i,:c,'Copy',:em,'x','mid','new',:n,:n)"),
                    {"i": uuid.uuid4(), "c": company, "em": email, "n": now})
                await db.commit()
            return True
        except IntegrityError:
            return False

    check("a second Asha at the same company is refused, whatever the case",
          await insert(s["company"], "  ASHA@x.IN") is False)
    check("a new address at the same company is fine", await insert(s["company"], "new@x.in"))
    check("the same address at another company is still fine",
          await insert(s["other_co"], "ravi@x.in"))

    # ── 4. HR upload finds the person instead of copying them ─────────────
    async with f() as db:
        # Her current CV is what her Python application was scored against.
        await db.execute(text("UPDATE applicants SET resume_s3_key = 'k-old' WHERE id = :a"),
                         {"a": s["asha"]})
        await db.execute(text("UPDATE enrolments SET scored_resume_s3_key = 'k-old'"
                              " WHERE applicant_id = :a AND requisition_id = :r"),
                         {"a": s["asha"], "r": s["python"]})
        await db.commit()

    r409 = await ac.post("/hr/applicants", files={"file": pdf},
                         data={"full_name": "Someone", "target_job_title": "x",
                               "email": "asha@x.in", "requisition_id": str(s["python"])})
    check("uploading someone into an opening they are already in is refused",
          r409.status_code == 409 and "Asha" in r409.text, f"{r409.status_code} {r409.text[:120]}")

    score_email["fail"] = True
    rw = await ac.post("/hr/applicants", files={"file": pdf},
                       data={"full_name": "A. Typed Again", "target_job_title": "Welder",
                             "email": "ASHA@x.in"})
    score_email["fail"] = False
    async with f() as db:
        asha = (await db.execute(text(
            "SELECT full_name, resume_s3_key, (SELECT count(*) FROM enrolments e"
            "  WHERE e.applicant_id = a.id AND e.deleted_at IS NULL) AS n"
            " FROM applicants a WHERE id = :a"), {"a": s["asha"]})).mappings().first()
        people = await db.scalar(text(
            "SELECT count(*) FROM applicants WHERE company_id = :c AND deleted_at IS NULL"
            "   AND lower(btrim(email)) = 'asha@x.in'"), {"c": s["company"]})
    welder_key = asha["resume_s3_key"] if asha else None
    check("a returning person gets a new application, not a new record",
          rw.status_code == 201 and rw.json()["id"] == str(s["asha"]) and people == 1
          and asha is not None and asha["n"] == 3, f"{rw.status_code} {asha} people={people}")
    check("their name is kept, not replaced by what was typed this time",
          asha is not None and asha["full_name"] == "Asha", str(asha))
    check("the new CV gets a key of its own",
          welder_key not in (None, "k-old") and str(s["asha"]) in str(welder_key), str(welder_key))
    check("the CV an earlier application was scored against is kept", "k-old" not in deleted,
          str(deleted))

    rf = await ac.post("/hr/applicants", files={"file": pdf},
                       data={"full_name": "x", "target_job_title": "Fitter", "email": "asha@x.in"})
    check("the replaced CV nothing points at any more is deleted",
          rf.status_code == 201 and welder_key in deleted, f"{rf.status_code} {deleted}")

    # ── 5. Bulk upload + reconciler: a CV address already on file ──────────
    rb = await ac.post("/hr/applicants/bulk",
                       files=[("files", ("taken.pdf", b"%PDF-1.4", "application/pdf")),
                              ("files", ("free.pdf", b"%PDF-1.4", "application/pdf"))],
                       data={"target_job_title": "Electrician"})
    await ac.aclose()
    app.dependency_overrides.clear()
    check("bulk upload succeeds", rb.status_code == 201, rb.text[:200])

    import app.reconciliation as rec

    emails = iter(["asha@x.in", "free@x.in"])

    async def _rec_score(**_: object) -> dict:
        out = await _score()
        out["candidate_email"] = next(emails)
        return out

    rec.score_resume_remote = _rec_score  # type: ignore[assignment]
    async with f() as db:
        # Only the two bulk uploads are waiting to be read.
        await db.execute(text(
            "UPDATE enrolments e SET ats_overall = 5, scored_at = now()"
            "  FROM job_requisitions r WHERE r.id = e.requisition_id AND r.title <> 'Electrician'"
            "   AND e.ats_overall IS NULL"))
        # …and rows with no application at all are scored the legacy way.
        await db.execute(text(
            "UPDATE applicants SET ats_overall = 5 WHERE upload_batch_id IS NULL"
            "   AND ats_overall IS NULL"))
        await db.commit()
    async with f() as db:
        await rec._score_pass(db, rec.PassResult())
    async with f() as db:
        bulk = (await db.execute(text(
            "SELECT a.email, a.parsed_email FROM applicants a"
            "  JOIN enrolments e ON e.applicant_id = a.id"
            "  JOIN job_requisitions r ON r.id = e.requisition_id"
            " WHERE r.title = 'Electrician' ORDER BY a.created_at"))).mappings().all()
    got = sorted(((b["email"], b["parsed_email"]) for b in bulk), key=str)
    check("a CV address someone else has is kept aside; a free one becomes the email",
          got == sorted([(None, "asha@x.in"), ("free@x.in", None)], key=str), str(got))
    async with f() as db:
        on = await db.scalar(text(INDEX_SQL))
    check("…and the rule is still on (nothing forced a duplicate)", on is not None)

    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
