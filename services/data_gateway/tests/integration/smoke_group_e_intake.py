"""Public applications and deferred enrichment — Group E (E4/E5).

The two halves of "how does a candidate get into the system":

* the PUBLIC APPLY endpoint, unauthenticated, which is the first route in this
  product that stores a stranger's PII. The checks that matter most are the
  refusals: no consent, no application; an opening that is not public is
  indistinguishable from one that does not exist; and a second submission
  cannot fragment one person across two records.
* DEFERRED ENRICHMENT, where both intake paths now store the resume and let the
  Group A reconciler read it. What is tested here is that the row is left in
  exactly the state the reconciler looks for — because if it is not, resumes
  are silently never scored and the symptom is "the ATS stopped working".

Storage is REAL, not stubbed: the run points ``STORAGE_LOCAL_DIR`` at a
temporary directory and asserts the CV actually lands on disk. That is the same
path a developer with no bucket takes, so testing it against a stub would have
left the one storage configuration anybody runs locally unexercised.

    docker run -d --name intants-pgv -e POSTGRES_PASSWORD=postgres \
      -e POSTGRES_DB=intants_smoke -p 55432:5432 pgvector/pgvector:pg16
    cd services/data_gateway
    export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke
    python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_e_intake.py

Exits non-zero on any failed check.
"""

from __future__ import annotations

import asyncio
import pathlib
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db_session
from app.dependencies import get_hr_company
from app.main import app

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


def tiny_pdf(line: str) -> bytes:
    """A minimal one-page PDF pypdf can extract text from."""
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (
        b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref).encode() + b"\n%%EOF"
    )
    return bytes(out)


CV = tiny_pdf("Priya Sharma priya.sharma@example.com Backend Engineer Python")


def form(name="Priya Sharma", email="priya@example.com", consent="true"):
    return {"full_name": name, "email": email, "consent_granted": consent}


def files(pdf=CV, filename="cv.pdf", ctype="application/pdf"):
    return {"resume": (filename, pdf, ctype)}


async def main() -> None:
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)

    async with eng.begin() as c:
        await c.execute(
            text(
                "TRUNCATE companies, users, applicants, job_requisitions, enrolments,"
                " stage_transitions, workflows, workflow_rounds, round_criteria,"
                " round_results CASCADE"
            )
        )
        # `roles` is seeded by the migrations and deliberately not truncated —
        # guest_candidate has to be there for the guest user this flow mints.

    cid, other_cid, hr_uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # open+public, open+private, paused+public, open+public but already closed,
    # and one belonging to another tenant.
    r_public, r_private, r_paused, r_expired, r_foreign = (uuid.uuid4() for _ in range(5))
    r_second = uuid.uuid4()  # a second public opening at the same company

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
        rows = [
            (r_public, cid, "Backend Engineer", "open", True, None),
            (r_private, cid, "Internal Only", "open", False, None),
            (r_paused, cid, "Paused Role", "paused", True, None),
            (r_expired, cid, "Closed Yesterday", "open", True, now - timedelta(days=1)),
            (r_second, cid, "Data Engineer", "open", True, None),
            (r_foreign, other_cid, "Their Role", "open", True, None),
        ]
        for rid, c_id, title, st, public, closes in rows:
            await db.execute(
                text(
                    "INSERT INTO job_requisitions (id,company_id,title,level,jd_text,"
                    " owner_user_id,created_by_user_id,status,public_apply_enabled,"
                    " closes_at,from_backfill,created_at,updated_at)"
                    " VALUES (:i,:c,:t,'mid','Build and maintain APIs.',:u,:u,:s,:p,:cl,"
                    " false,:n,:n)"
                ),
                {"i": rid, "c": c_id, "t": title, "u": hr_uid if c_id == cid else None,
                 "s": st, "p": public, "cl": closes, "n": now},
            )
        await db.commit()

    async def _db_override():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_hr_company] = lambda: (hr_uid, cid)

    # Real storage, on disk. settings is patched rather than the helpers, so
    # the code under test is the same branch a laptop with no bucket runs.
    store = pathlib.Path(tempfile.mkdtemp(prefix="intants-smoke-cv-"))
    settings.storage_local_dir = str(store)
    settings.s3_access_key_id = ""
    settings.app_env = "development"

    def stored() -> list[pathlib.Path]:
        return sorted(p for p in store.rglob("*.pdf") if p.is_file())

    tr = ASGITransport(app=app)
    async with AsyncClient(transport=tr, base_url="http://t") as c:
        print("\n--- the posting ---")
        r = await c.get(f"/apply/{r_public}")
        check("GET a public opening -> 200", r.status_code == 200, r.text[:160])
        posting = r.json()
        check("carries the title and company", posting["title"] == "Backend Engineer"
              and posting["company_name"] == "Acme", str(posting)[:160])
        check("and nothing about who else applied",
              not ({"total_enrolments", "hired", "funnel"} & set(posting)), str(posting.keys()))

        for label, rid in (
            ("an opening that never opted in", r_private),
            ("a paused opening", r_paused),
            ("an opening past its closing date", r_expired),
            ("an id that is not a requisition", uuid.uuid4()),
        ):
            r = await c.get(f"/apply/{rid}")
            check(f"{label} -> 404", r.status_code == 404, str(r.status_code))

        # NOT a tenancy check — there is no caller company on a public board, and
        # a posting another company chose to publish is meant to be visible.
        # What matters is that the TENANT is derived from the opening rather
        # than from anything the applicant sends.
        r = await c.get(f"/apply/{r_foreign}")
        check("another company's PUBLIC opening is visible — it is a job board",
              r.status_code == 200, str(r.status_code))
        check("and it reports that company, not ours",
              r.json()["company_name"] == "Globex", str(r.json())[:120])

        print("\n--- consent is not optional ---")
        r = await c.post(f"/apply/{r_public}", data=form(consent="false"), files=files())
        check("applying without consent -> 422", r.status_code == 422, r.text[:160])
        async with factory() as db:
            n = await db.scalar(text("SELECT count(*) FROM applicants"))
        check("and nothing was stored", int(n) == 0, str(n))
        check("not even the CV", stored() == [], str(stored()))

        r = await c.post(f"/apply/{r_public}", data={"full_name": "X Y", "email": "x@y.co"},
                         files=files())
        check("consent omitted entirely -> 422", r.status_code == 422, str(r.status_code))

        print("\n--- a real application ---")
        r = await c.post(f"/apply/{r_public}", data=form(), files=files())
        check("POST apply -> 201", r.status_code == 201, r.text[:200])
        out = r.json()
        check("it is not reported as a duplicate", out["already_applied"] is False, str(out))
        check("an enrolment was created", out["enrolment_id"] is not None, str(out))
        applicant_id = out["applicant_id"]

        async with factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT full_name, email, company_id, target_job_title, status,"
                        "       pending_enrichment, ats_overall, resume_text, user_id"
                        "  FROM applicants WHERE id = CAST(:i AS uuid)"
                    ),
                    {"i": applicant_id},
                )
            ).mappings().first()
        check("filed under the right company", str(row["company_id"]) == str(cid))
        check("targeted at the opening's role",
              row["target_job_title"] == "Backend Engineer", row["target_job_title"])
        check("the resume text was extracted", "Priya" in (row["resume_text"] or ""),
              (row["resume_text"] or "")[:60])
        check("starts as 'new', not shortlisted", row["status"] == "new", row["status"])

        print("\n--- E5: stored, not read ---")
        check("flagged for the reconciler", row["pending_enrichment"] is True)
        check("and carries no score yet", row["ats_overall"] is None, str(row["ats_overall"]))
        check("the CV is on disk, not just recorded", len(stored()) == 1, str(stored()))
        check("stored under the applicant's own key",
              bool(stored()) and applicant_id in str(stored()[0]), str(stored()))
        check("and it is the bytes we sent",
              bool(stored()) and stored()[0].read_bytes() == CV)

        print("\n--- DPDP ---")
        check("a guest user was minted for the ledger FK", row["user_id"] is not None)
        async with factory() as db:
            consent = (
                await db.execute(
                    text(
                        "SELECT consent_type, granted, purpose, evidence"
                        "  FROM dpdp_consent_ledger WHERE user_id = :u"
                    ),
                    {"u": row["user_id"]},
                )
            ).mappings().first()
        check("consent was recorded", consent is not None)
        check("as granted, for recruitment",
              consent and consent["granted"] and consent["purpose"] == "recruitment",
              str(consent and dict(consent))[:160])
        ev = (consent or {}).get("evidence") or {}
        check("the evidence names the opening", ev.get("requisition_id") == str(r_public), str(ev))
        # The column's contract says it never holds raw PII.
        check("the IP is hashed, not stored",
              len(str(ev.get("ip_hash", ""))) == 64, str(ev.get("ip_hash"))[:40])
        blob = str(ev)
        check("and no raw PII rode along",
              "priya@example.com" not in blob and "Priya" not in blob, blob[:160])

        print("\n--- one person, many openings (D-06) ---")
        r = await c.post(f"/apply/{r_public}", data=form(), files=files())
        check("re-applying to the same opening is not an error", r.status_code == 201,
              str(r.status_code))
        check("it reports the existing application", r.json()["already_applied"] is True,
              str(r.json()))
        async with factory() as db:
            n_app = await db.scalar(text("SELECT count(*) FROM applicants"))
            n_enr = await db.scalar(
                text("SELECT count(*) FROM enrolments WHERE requisition_id = :r"),
                {"r": r_public},
            )
        check("no duplicate applicant", int(n_app) == 1, str(n_app))
        check("no duplicate enrolment", int(n_enr) == 1, str(n_enr))

        r = await c.post(f"/apply/{r_second}", data=form(), files=files())
        check("the same person may apply to a DIFFERENT opening", r.status_code == 201,
              r.text[:160])
        check("and it is the same applicant record",
              r.json()["applicant_id"] == applicant_id, str(r.json()))
        async with factory() as db:
            n_app = await db.scalar(text("SELECT count(*) FROM applicants"))
            n_enr = await db.scalar(text("SELECT count(*) FROM enrolments"))
            consents = await db.scalar(text("SELECT count(*) FROM dpdp_consent_ledger"))
        check("still one person", int(n_app) == 1, str(n_app))
        check("now with two enrolments", int(n_enr) == 2, str(n_enr))
        check("and consent was not re-recorded", int(consents) == 1, str(consents))

        print("\n--- bad uploads ---")
        r = await c.post(f"/apply/{r_public}", data=form(email="new@example.com"),
                         files=files(pdf=b"not a pdf at all", ctype="application/pdf"))
        check("a file that is not a PDF -> 422", r.status_code == 422, r.text[:160])
        r = await c.post(f"/apply/{r_public}", data=form(email="new2@example.com"),
                         files=files(ctype="text/plain"))
        check("a non-PDF content type -> 400", r.status_code == 400, str(r.status_code))
        r = await c.post(f"/apply/{r_public}", data=form(email="new3@example.com"),
                         files=files(pdf=b""))
        check("an empty file -> 400", r.status_code == 400, str(r.status_code))
        r = await c.post(f"/apply/{r_public}", data=form(email="not-an-email"), files=files())
        check("a malformed email -> 422", r.status_code == 422, str(r.status_code))
        r = await c.post(f"/apply/{r_private}", data=form(email="new4@example.com"),
                         files=files())
        check("applying to a private opening -> 404", r.status_code == 404, str(r.status_code))

        async with factory() as db:
            n_app = await db.scalar(text("SELECT count(*) FROM applicants"))
        check("no failed attempt left a row behind", int(n_app) == 1, str(n_app))

        print("\n--- the reconciler will find them ---")
        async with factory() as db:
            due = await db.scalar(
                text(
                    "SELECT count(*) FROM applicants a"
                    " WHERE a.ats_overall IS NULL AND a.deleted_at IS NULL"
                    "   AND a.resume_text IS NOT NULL AND length(trim(a.resume_text)) > 0"
                )
            )
        check("the deferred row is due for scoring", int(due) == 1, str(due))

    app.dependency_overrides.clear()
    shutil.rmtree(store, ignore_errors=True)
    await eng.dispose()
    print(f"\n{'=' * 60}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 60)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
