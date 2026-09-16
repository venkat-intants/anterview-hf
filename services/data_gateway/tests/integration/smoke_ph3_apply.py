"""PH3 smoke test — approval, scheduling, drafts and confirmation, against Postgres.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head
(it TRUNCATEs what it touches) and writes CVs to a temporary directory:

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph3_smoke \\
      DATABASE_SSL= python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_ph3_apply.py

What it proves, in order:

  * PH3-B2 — an unapproved opening is invisible and unappliable, and becomes
    both the moment its super admin approves it. This is the story's central
    acceptance criterion and it is checked through the REAL public endpoints
    rather than against the predicate in isolation.
  * PH3-B1 — the channel a tracked link carried reaches the enrolment.
  * PH3-B4c — a draft cannot be created without consent, and the consent ledger
    entry lands in the same breath as the first row holding the person's email.
  * PH3-B5 — a submission is refused until the candidate has confirmed what the
    CV parser read, and their correction is what gets stored.
  * PH3-B4b — a rejected candidate cannot reapply inside the cooldown, and can
    the moment HR overrides it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get(
    "SMOKE_DATABASE_URL", "postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph3_smoke"
)
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    mark = "PASS" if cond else "FAIL"
    print(f"  {mark}  {label}{(' — ' + detail) if detail and not cond else ''}")


def tiny_pdf(lines: list[str]) -> bytes:
    """A minimal one-page PDF pypdf can extract text from."""
    stream = "BT /F1 12 Tf 72 720 Td " + " ".join(
        f"({line}) Tj 0 -16 Td" for line in lines
    ) + " ET"
    raw = stream.encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(raw)).encode() + b" >>\nstream\n" + raw + b"\nendstream",
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
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(xref).encode() + b"\n%%EOF")
    return bytes(out)


CV = tiny_pdf([
    "Priya Sharma",
    "priya.sharma@example.com",
    "+91 98765 43210",
])


async def main() -> None:  # noqa: PLR0915 — one linear script, read top to bottom
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)

    async with eng.begin() as conn:
        # audit_log is NOT in this list, and that is the database refusing
        # rather than an omission: it carries an append-only trigger that
        # rejects TRUNCATE outright (DPDP audit integrity). Everything else
        # cascades from companies.
        await conn.execute(text(
            "TRUNCATE applicants, companies, users, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, round_criteria,"
            " round_results, application_drafts, dpdp_consent_ledger,"
            " user_roles CASCADE"))
    # roles is reference data seeded by the migrations — candidate, admin,
    # super_admin, hr_manager, guest_candidate, platform_owner — and is NOT
    # truncated above. Nothing to create here.

    cid = uuid.uuid4()
    hr_uid, admin_uid = uuid.uuid4(), uuid.uuid4()
    req = uuid.uuid4()

    async with factory() as db:
        await db.execute(text(
            "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
            " VALUES (:i,'Acme','acme',true,:n,:n)"), {"i": cid, "n": now})
        for uid, email in ((hr_uid, "hr@acme.test"), (admin_uid, "admin@acme.test")):
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,"
                " preferred_language,is_active,notify_login_email,must_change_password,"
                " created_at,updated_at)"
                " VALUES (:i,:e,'Staff','x',:c,'en',true,false,false,:n,:n)"),
                {"i": uid, "e": email, "c": cid, "n": now})
        await db.execute(text(
            "INSERT INTO user_roles (user_id, role_id, assigned_at)"
            " SELECT :u, id, :n FROM roles WHERE name = 'super_admin'"),
            {"u": admin_uid, "n": now})
        # A brand-new opening: NOT approved, which is the default since PH3-B2.
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,level,jd_text,status,"
            " from_backfill,public_apply_enabled,created_by_user_id,owner_user_id,"
            " approval_status,created_at,updated_at)"
            " VALUES (:i,:c,'Platform Engineer','senior','Run the platform.','open',"
            " false,true,:u,:u,'draft',:n,:n)"),
            {"i": req, "c": cid, "u": hr_uid, "n": now})
        await db.execute(text(
            "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
            " auto_score_on_apply,auto_assign_first_round,auto_advance_rounds,"
            " reminders_enabled,hold_band,created_at,updated_at,published_at)"
            " VALUES (gen_random_uuid(),:c,:r,1,'published',true,true,true,true,10,:n,:n,:n)"),
            {"c": cid, "r": req, "n": now})
        await db.commit()

    from app.config import settings

    store = pathlib.Path(tempfile.mkdtemp(prefix="intants-smoke-ph3-"))
    settings.storage_local_dir = str(store)
    settings.s3_access_key_id = ""
    settings.app_env = "development"

    from app.database import get_db_session
    from app.dependencies import get_hr_company, get_super_admin_company
    from app.main import app
    from app.rate_limit import rate_limit  # noqa: F401 - imported for the override below

    async def _db():  # noqa: ANN202
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_hr_company] = lambda: (hr_uid, cid)
    app.dependency_overrides[get_super_admin_company] = lambda: (admin_uid, cid)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://smoke") as client:
        # ── PH3-B2: unapproved means invisible ────────────────────────
        print("\nPH3-B2 — an unapproved opening is not public")
        r = await client.get(f"/apply/{req}")
        check("the posting 404s while unapproved", r.status_code == 404, str(r.status_code))

        r = await client.get("/careers/acme")
        listed = [i["requisition_id"] for i in r.json().get("items", [])] if r.status_code == 200 else []
        check("the careers board does not list it", str(req) not in listed)

        r = await client.post(
            f"/apply/{req}/draft",
            json={"email": "priya@example.com", "consent_granted": True},
        )
        check("a draft cannot be started either", r.status_code == 404, str(r.status_code))

        # HR cannot simply switch it on.
        r = await client.patch(
            f"/hr/requisitions/{req}", json={"public_apply_enabled": True}
        )
        check("HR cannot open it to the public unapproved", r.status_code == 409,
              f"{r.status_code} {r.text[:120]}")

        # ── The approval round trip ───────────────────────────────────
        print("\nPH3-B2 — submit, then the super admin approves")
        r = await client.post(
            f"/hr/requisitions/{req}/approval/submit", json={"note": "Backfill for Priya"}
        )
        check("HR submits for approval", r.status_code == 200 and
              r.json()["approval_status"] == "pending_approval",
              f"{r.status_code} {r.text[:140]}")

        r = await client.get("/hr/requisitions/approvals/pending")
        queued = [i["id"] for i in r.json()] if r.status_code == 200 else []
        check("it appears in the super admin's queue", str(req) in queued,
              f"{r.status_code} {r.text[:140]}")

        r = await client.post(
            f"/hr/requisitions/{req}/approval/approve", json={"note": "Approved"}
        )
        check("the super admin approves it", r.status_code == 200 and
              r.json()["approval_status"] == "approved",
              f"{r.status_code} {r.text[:140]}")

        r = await client.get(f"/apply/{req}?src=linkedin")
        check("the posting is now live", r.status_code == 200, str(r.status_code))
        check("PH3-B1: the tracked channel is echoed back normalised",
              r.status_code == 200 and r.json().get("source") == "job_board"
              and r.json().get("source_detail") == "linkedin",
              r.text[:140])

        # ── PH3-B4c: consent first ────────────────────────────────────
        print("\nPH3-B4c — a draft cannot be saved without consent")
        r = await client.post(
            f"/apply/{req}/draft",
            json={"email": "priya@example.com", "consent_granted": False},
        )
        check("refused without consent", r.status_code == 422, str(r.status_code))

        async with factory() as db:
            rows = await db.scalar(text("SELECT count(*) FROM application_drafts"))
        check("and nothing was stored", rows == 0, f"{rows} draft rows")

        r = await client.post(
            f"/apply/{req}/draft",
            json={"email": "priya@example.com", "consent_granted": True, "src": "linkedin"},
        )
        check("accepted with consent", r.status_code == 201, f"{r.status_code} {r.text[:140]}")
        token = r.json()["resume_token"] if r.status_code == 201 else ""

        async with factory() as db:
            ledger = await db.scalar(text(
                "SELECT count(*) FROM dpdp_consent_ledger"
                " WHERE consent_type = 'application_data' AND granted"))
            drafts = await db.scalar(text("SELECT count(*) FROM application_drafts"))
            stored_token = await db.scalar(text("SELECT token_hash FROM application_drafts"))
            applicants = await db.scalar(text("SELECT count(*) FROM applicants"))
        check("the consent ledger entry exists", ledger == 1, f"{ledger} rows")
        check("exactly one draft exists", drafts == 1, f"{drafts} rows")
        check("only the token HASH is stored", stored_token != token and len(stored_token or "") == 64)
        check("a draft did NOT create an applicant", applicants == 0, f"{applicants} applicants")

        # ── Resume it ─────────────────────────────────────────────────
        print("\nPH3-B4c — resume from the link")
        r = await client.get(f"/apply/draft/{token}")
        check("the link reopens the draft", r.status_code == 200 and
              r.json()["email"] == "priya@example.com", f"{r.status_code} {r.text[:140]}")

        r = await client.patch(
            f"/apply/draft/{token}", json={"current_company": "Globex", "phone": "0000"}
        )
        check("progress saves", r.status_code == 200 and
              r.json()["current_company"] == "Globex", r.text[:140])

        r = await client.get(f"/apply/draft/{token}")
        check("progress survives a reload", r.status_code == 200 and
              r.json()["current_company"] == "Globex", r.text[:140])

        r = await client.get("/apply/draft/not-a-real-token")
        check("a bogus token 404s", r.status_code == 404, str(r.status_code))

        # ── PH3-B5: the confirmation step ─────────────────────────────
        print("\nPH3-B5 — confirm what the CV said")
        r = await client.post(f"/apply/draft/{token}/submit")
        check("submission refused with no CV", r.status_code == 422, str(r.status_code))

        r = await client.post(
            f"/apply/draft/{token}/resume-upload",
            files={"resume": ("priya.pdf", CV, "application/pdf")},
        )
        uploaded = r.json() if r.status_code == 200 else {}
        check("the CV uploads", r.status_code == 200, f"{r.status_code} {r.text[:140]}")
        check("the parser read the name off it",
              uploaded.get("parsed", {}).get("full_name") == "Priya Sharma",
              str(uploaded.get("parsed")))
        check("the parser read the email off it",
              uploaded.get("parsed", {}).get("email") == "priya.sharma@example.com",
              str(uploaded.get("parsed")))
        check("it is not confirmed yet", uploaded.get("confirmed") is False)

        r = await client.post(f"/apply/draft/{token}/submit")
        check("submission refused until confirmed", r.status_code == 422,
              f"{r.status_code} {r.text[:140]}")

        r = await client.post(f"/apply/draft/{token}/confirm", json={"full_name": ""})
        check("confirming an empty name is refused", r.status_code == 422, str(r.status_code))

        # The candidate CORRECTS what the parser read. Theirs must win.
        r = await client.post(
            f"/apply/draft/{token}/confirm",
            json={"full_name": "Priya S. Sharma", "years_experience": 7},
        )
        check("confirming with a correction succeeds", r.status_code == 200 and
              r.json()["confirmed"] is True, f"{r.status_code} {r.text[:140]}")

        # ── Submit ────────────────────────────────────────────────────
        print("\nPH3-B4c — the draft becomes an application")
        r = await client.post(f"/apply/draft/{token}/submit")
        body = r.json() if r.status_code in (200, 201) else {}
        check("the application is created", r.status_code == 201 and
              body.get("already_applied") is False, f"{r.status_code} {r.text[:200]}")

        async with factory() as db:
            row = (await db.execute(text(
                "SELECT a.full_name, a.full_name_source, a.years_experience,"
                "       a.details_confirmed_at, e.source, e.source_detail"
                "  FROM applicants a JOIN enrolments e ON e.applicant_id = a.id"))
            ).mappings().first()
            draft_status = await db.scalar(text("SELECT status FROM application_drafts"))
        check("the CANDIDATE's name was stored, not the CV's",
              row and row["full_name"] == "Priya S. Sharma", str(row))
        check("and it is marked as authored, so the scorer will not overwrite it",
              row and row["full_name_source"] == "candidate", str(row))
        check("their correction was stored", row and row["years_experience"] == 7, str(row))
        check("the confirmation survives the draft",
              row and row["details_confirmed_at"] is not None, str(row))
        check("PH3-B1: the channel reached the enrolment",
              row and row["source"] == "job_board" and row["source_detail"] == "linkedin",
              str(row))
        check("the draft is closed", draft_status == "submitted", str(draft_status))

        r = await client.get(f"/apply/draft/{token}")
        check("the link stops working once submitted", r.status_code == 404, str(r.status_code))

        # ── PH3-B4b: cooldown ─────────────────────────────────────────
        print("\nPH3-B4b — reapplication cooldown")
        async with factory() as db:
            enr = await db.scalar(text("SELECT id FROM enrolments LIMIT 1"))
            await db.execute(text(
                "UPDATE job_requisitions SET reapply_cooldown_days = 90 WHERE id = :r"),
                {"r": req})
            await db.execute(text("UPDATE enrolments SET deleted_at = :n WHERE id = :e"),
                             {"n": now, "e": enr})
            await db.execute(text(
                "INSERT INTO stage_transitions (company_id,enrolment_id,from_status,"
                " to_status,automated,occurred_at) VALUES (:c,:e,'new','rejected',false,:n)"),
                {"c": cid, "e": enr, "n": datetime.now(tz=UTC) - timedelta(days=10)})
            await db.commit()

        r = await client.post(
            f"/apply/{req}",
            data={"full_name": "Priya S. Sharma", "email": "priya@example.com",
                  "consent_granted": "true"},
            files={"resume": ("priya.pdf", CV, "application/pdf")},
        )
        check("a rejected candidate is refused inside the window",
              r.status_code == 409, f"{r.status_code} {r.text[:160]}")
        check("and is told the date they may reapply",
              r.status_code == 409 and "20" in r.json().get("detail", ""),
              r.text[:160])

        r = await client.post(f"/hr/enrolments/{enr}/reapply-override",
                              json={"reason": "Different role, strong CV"})
        check("HR can override the cooldown", r.status_code == 200,
              f"{r.status_code} {r.text[:160]}")

        r = await client.post(
            f"/apply/{req}",
            data={"full_name": "Priya S. Sharma", "email": "priya@example.com",
                  "consent_granted": "true"},
            files={"resume": ("priya.pdf", CV, "application/pdf")},
        )
        check("and they can then apply", r.status_code == 201,
              f"{r.status_code} {r.text[:160]}")

    await eng.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        raise SystemExit(1)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
