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


def _executor_draft_predicate() -> str:
    """The erasure executor's own draft-matching WHERE clause, lifted from its
    source rather than re-typed here.

    Re-typing it is how this test came to validate a predicate that production
    had already replaced: the copy kept the old third route, which was
    algebraically a duplicate of the first, and would have gone on passing
    while the real query regressed.
    """
    executor = (
        pathlib.Path(__file__).resolve().parents[4]
        / "services" / "admin_ops" / "app" / "erasure_executor.py"
    ).read_text(encoding="utf-8")
    start = executor.index('"DELETE FROM application_drafts d"')
    end = executor.index("),", start)
    fragment = executor[start:end]
    # The statement is assembled from adjacent string literals with comments
    # between them; join the literals and drop the DELETE head.
    import ast as _ast
    parts = [
        _ast.literal_eval(line.strip().rstrip(","))
        for line in fragment.splitlines()
        if line.strip().startswith('"')
    ]
    sql = "".join(parts)
    return sql.split(" WHERE ", 1)[1]


def _tok(token: str) -> dict[str, str]:
    """The resume token travels in a header now, never the URL — it is a live
    credential to a person's name, phone, employer, answers and CV, and a path
    puts it in every access log between here and the browser."""
    return {"X-Draft-Token": token}


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
    # A SECOND company with its own opening, and a real account that already
    # owns the address our candidate drafts with. users.email is unique across
    # the whole platform, so both of these collide with a draft-created user
    # unless that user's stored address is synthetic.
    other_cid, other_hr, other_req = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    taken_email = "priya@example.com"

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

        # ── The second company, already accepting applications ───────────
        await db.execute(text(
            "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
            " VALUES (:i,'Globex','globex',true,:n,:n)"), {"i": other_cid, "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,"
            " preferred_language,is_active,notify_login_email,must_change_password,"
            " created_at,updated_at)"
            " VALUES (:i,'hr@globex.test','Staff','x',:c,'en',true,false,false,:n,:n)"),
            {"i": other_hr, "c": other_cid, "n": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,level,jd_text,status,"
            " from_backfill,public_apply_enabled,created_by_user_id,owner_user_id,"
            " approval_status,approval_decided_at,created_at,updated_at)"
            " VALUES (:i,:c,'Data Engineer','mid','Move data.','open',"
            " false,true,:u,:u,'approved',:n,:n,:n)"),
            {"i": other_req, "c": other_cid, "u": other_hr, "n": now})
        await db.execute(text(
            "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
            " auto_score_on_apply,auto_assign_first_round,auto_advance_rounds,"
            " reminders_enabled,hold_band,created_at,updated_at,published_at)"
            " VALUES (gen_random_uuid(),:c,:r,1,'published',true,true,true,true,10,:n,:n,:n)"),
            {"c": other_cid, "r": other_req, "n": now})

        # A REAL registered account that already holds the candidate's address.
        # users.email is globally unique, so this is the row a draft-created
        # user would collide with if it stored the real address.
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,"
            " preferred_language,is_active,notify_login_email,must_change_password,"
            " created_at,updated_at)"
            " VALUES (gen_random_uuid(),:e,'Priya Elsewhere','x',NULL,'en',true,"
            " false,false,:n,:n)"),
            {"e": taken_email, "n": now})
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
        r = await client.get("/apply/draft", headers=_tok(token))
        check("the link reopens the draft", r.status_code == 200 and
              r.json()["email"] == "priya@example.com", f"{r.status_code} {r.text[:140]}")

        r = await client.patch(
            "/apply/draft", headers=_tok(token),
            json={"current_company": "Globex", "phone": "0000"},
        )
        check("progress saves", r.status_code == 200 and
              r.json()["current_company"] == "Globex", r.text[:140])

        r = await client.get("/apply/draft", headers=_tok(token))
        check("progress survives a reload", r.status_code == 200 and
              r.json()["current_company"] == "Globex", r.text[:140])

        r = await client.get("/apply/draft", headers=_tok("not-a-real-token"))
        check("a bogus token 404s", r.status_code == 404, str(r.status_code))

        # ── PH3-B5: the confirmation step ─────────────────────────────
        print("\nPH3-B5 — confirm what the CV said")
        r = await client.post("/apply/draft/submit", headers=_tok(token))
        check("submission refused with no CV", r.status_code == 422, str(r.status_code))

        r = await client.post(
            "/apply/draft/resume-upload", headers=_tok(token),
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

        r = await client.post("/apply/draft/submit", headers=_tok(token))
        check("submission refused until confirmed", r.status_code == 422,
              f"{r.status_code} {r.text[:140]}")

        r = await client.post("/apply/draft/confirm", headers=_tok(token), json={"full_name": ""})
        check("confirming an empty name is refused", r.status_code == 422, str(r.status_code))

        # The candidate CORRECTS what the parser read. Theirs must win.
        r = await client.post(
            "/apply/draft/confirm", headers=_tok(token),
            json={"full_name": "Priya S. Sharma", "years_experience": 7},
        )
        check("confirming with a correction succeeds", r.status_code == 200 and
              r.json()["confirmed"] is True, f"{r.status_code} {r.text[:140]}")

        # ── Submit ────────────────────────────────────────────────────
        print("\nPH3-B4c — the draft becomes an application")
        r = await client.post("/apply/draft/submit", headers=_tok(token))
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

        r = await client.get("/apply/draft", headers=_tok(token))
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
            # PH4-O4: a move into 'rejected' needs a reason_code (and its
            # paired label) — the ledger trigger refuses one without.
            await db.execute(text(
                "INSERT INTO stage_transitions (company_id,enrolment_id,from_status,"
                " to_status,automated,reason_code,reason_label,occurred_at)"
                " VALUES (:c,:e,'new','rejected',false,'other','Other',:n)"),
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

        # ── REGRESSION: users.email is globally unique ────────────────
        # Found by code review, not by the first version of this test — which
        # passed only because it used one company and a fresh address. The
        # draft path used to store the candidate's REAL address on the users
        # row it mints, so the second company anyone ever drafted at (or the
        # first, if that address already belonged to any account anywhere)
        # hit the unique index and surfaced as an unrecoverable 503.
        print("\nREGRESSION — a draft-created user must not collide on users.email")

        r = await client.post(
            f"/apply/{other_req}/draft",
            json={"email": taken_email, "consent_granted": True},
        )
        check("can draft at a second company with the same address",
              r.status_code == 201, f"{r.status_code} {r.text[:160]}")
        second_token = r.json()["resume_token"] if r.status_code == 201 else ""

        check("that draft actually opens", bool(second_token) and (
            await client.get("/apply/draft", headers=_tok(second_token))).status_code == 200)

        async with factory() as db:
            real = await db.scalar(text(
                "SELECT count(*) FROM users WHERE email = :e"), {"e": taken_email})
            synthetic = await db.scalar(text(
                "SELECT count(*) FROM users WHERE email LIKE 'guest+%@applicants.invalid'"))
        check("the pre-existing real account is untouched", real == 1, f"{real} rows")
        check("draft users get a synthetic address instead",
              synthetic >= 1, f"{synthetic} synthetic users")

        # Drafting AGAIN mints a SEPARATE identity, and that is the fix rather
        # than a regression. This test used to assert the opposite — that the
        # same identity was reused, found by matching the supplied address —
        # and that reuse was exactly the hole a security review found: it is
        # what let an unauthenticated caller reach somebody else's draft.
        #
        # The cost is real and worth stating: one person who saves twice ends
        # up with two guest rows, two drafts and two consent records. Each
        # records a genuine consent act by whoever pressed the button, each
        # draft is reachable only by its own link, and all of them expire on
        # the same 30-day clock. That is a tidiness price for an access-control
        # property, and it is the right way round.
        async with factory() as db:
            before = await db.scalar(text("SELECT count(*) FROM users"))
        r = await client.post(
            f"/apply/{other_req}/draft",
            json={"email": taken_email, "consent_granted": True},
        )
        async with factory() as db:
            after = await db.scalar(text("SELECT count(*) FROM users"))
        check("re-drafting mints a fresh identity rather than reusing one",
              r.status_code == 201 and after == before + 1, f"{before} -> {after}")

        # The property that actually matters: neither draft can see the other.
        second_tok = r.json()["resume_token"] if r.status_code == 201 else ""
        first = await client.get("/apply/draft", headers=_tok(second_token))
        second = await client.get("/apply/draft", headers=_tok(second_tok))
        check("and the two drafts are separate, each reachable only by its own link",
              second_token != second_tok
              and first.status_code == 200 and second.status_code == 200,
              f"{first.status_code}/{second.status_code}")

        # ── SECURITY REGRESSION: the email is not an authenticator ────
        # Found by security review, not by the tests. start_draft used to
        # resolve the email in an unauthenticated request body to an existing
        # identity and, if that person had a live draft, rotate its token and
        # return its contents. Anyone with the apply link (not a secret) plus a
        # candidate's address could read their details, get a working token,
        # alter the draft, replace the CV and submit in their name — and the
        # victim's own link died silently.
        #
        # This walks the actual attack, through the real endpoints.
        print("\nSECURITY — a stranger who knows the email gets nothing")

        victim_email = "victim@example.com"
        r = await client.post(
            f"/apply/{other_req}/draft",
            json={"email": victim_email, "consent_granted": True},
        )
        victim_token = r.json()["resume_token"] if r.status_code == 201 else ""
        check("the victim starts a draft", r.status_code == 201, r.text[:140])

        # They fill in real details — this is what an attacker would be after.
        await client.patch(
            "/apply/draft", headers=_tok(victim_token),
            json={"full_name": "Victim Real Name", "phone": "+91 90000 00001",
                  "current_company": "Confidential Employer Ltd"},
        )

        # THE ATTACK: same opening, same email, no token, no login.
        r = await client.post(
            f"/apply/{other_req}/draft",
            json={"email": victim_email, "consent_granted": True},
        )
        attacker = r.json() if r.status_code == 201 else {}
        attacker_token = attacker.get("resume_token", "")
        leaked = str(attacker.get("draft", {}))

        check("the attacker learns NOTHING about the victim",
              "Victim Real Name" not in leaked
              and "+91 90000 00001" not in leaked
              and "Confidential Employer" not in leaked,
              leaked[:200])

        check("the attacker's token does NOT open the victim's draft",
              attacker_token != victim_token)
        if attacker_token:
            r = await client.get("/apply/draft", headers=_tok(attacker_token))
            got = r.json() if r.status_code == 200 else {}
            check("and what it does open is empty, not theirs",
                  got.get("full_name") in (None, ""), str(got)[:160])

        # The victim's own link must still work — the old code rotated it away.
        r = await client.get("/apply/draft", headers=_tok(victim_token))
        check("the victim's link still works (no silent denial of service)",
              r.status_code == 200 and r.json().get("full_name") == "Victim Real Name",
              f"{r.status_code} {r.text[:140]}")

        # ── The data principal can erase their own draft ──────────────
        print("\nDPDP — a draft-only candidate can erase their own data")
        # Resolved by the victim's OWN token, not by their address. Both the
        # victim and the attacker hold a draft carrying this email — that is the
        # whole point of the section above — so "the most recent draft with this
        # address" is the ATTACKER's, whose identity is quite correctly left
        # alone. Asking by address made this assertion check the wrong user and
        # report a failure that was not there.
        async with factory() as db:
            from app import application_drafts as _drafts

            victim_row = await _drafts.load(db, raw_token=victim_token)
            victim_uid = victim_row["user_id"] if victim_row else None
        check("the victim's draft is resolvable before deletion",
              victim_uid is not None)
        r = await client.delete("/apply/draft", headers=_tok(victim_token))
        check("deleting a draft by its own link works", r.status_code == 204,
              str(r.status_code))
        r = await client.get("/apply/draft", headers=_tok(victim_token))
        check("and it is really gone", r.status_code == 404, str(r.status_code))

        # The guest identity goes with it. The self-serve delete removed the
        # draft and left behind the users row, its user_roles grant and its
        # dpdp_consent_ledger entry — the exact unbounded growth the retention
        # pass exists to stop, on the one path a person takes deliberately.
        # Asserted here rather than in a unit test because the DELETE's three
        # NOT EXISTS guards are only real against a real Postgres.
        async with factory() as db:
            left = await db.scalar(
                text("SELECT count(*) FROM users WHERE id = :u"), {"u": victim_uid}
            )
            roles = await db.scalar(
                text("SELECT count(*) FROM user_roles WHERE user_id = :u"),
                {"u": victim_uid},
            )
        check("and the guest identity it anchored goes with it",
              left == 0, f"users rows left: {left}")
        check("and its role grant with it", roles == 0, f"user_roles left: {roles}")

        # ── ERASURE AFTER ACTIVATION — the case that silently failed ──
        # A guest drafts, submits, then activates into an account they already
        # had. _link_to_existing moved applicants.user_id and the consent ledger
        # but NOT application_drafts.user_id, so both erasure hooks — which key
        # on user_id — missed the draft. It survived a COMPLETED erasure with
        # the person's name, phone, employer and CV still in it, and the CV
        # object was never collected for deletion.
        #
        # Source-inspection tests cannot see this: it is an interaction between
        # two modules and a database. So this exercises the real repair against
        # real rows, and then runs the executor's own matching logic over them.
        print("\nDPDP — a draft survives activation and is still erasable")

        from app.apply_activation import _link_to_existing

        real_uid = uuid.uuid4()
        guest_uid = uuid.uuid4()
        drafted = uuid.uuid4()
        async with factory() as db:
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,"
                " preferred_language,is_active,notify_login_email,must_change_password,"
                " created_at,updated_at)"
                " VALUES (:i,'returning@example.com','Returning Person','x',:c,'en',"
                " true,false,false,:n,:n)"), {"i": real_uid, "c": other_cid, "n": now})
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,"
                " preferred_language,is_active,notify_login_email,must_change_password,"
                " created_at,updated_at)"
                " VALUES (:i,:e,'','x',:c,'en',true,false,false,:n,:n)"),
                {"i": guest_uid, "e": f"guest+{guest_uid}@applicants.invalid",
                 "c": other_cid, "n": now})
            await db.execute(text(
                "INSERT INTO application_drafts (id,company_id,requisition_id,user_id,"
                " token_hash,email,full_name,phone,resume_s3_key,status,expires_at,"
                " created_at,updated_at)"
                " VALUES (:i,:c,:r,:u,:h,'returning@example.com','Returning Person',"
                " '+91 90000 00002','drafts/x/y.pdf','submitted',:exp,:n,:n)"),
                {"i": drafted, "c": other_cid, "r": other_req, "u": guest_uid,
                 "h": "hash-" + str(drafted), "exp": now + timedelta(days=30), "n": now})
            await db.commit()

        async with factory() as db:
            await _link_to_existing(
                db, guest_user_id=guest_uid, target_user_id=real_uid, now=now
            )
            await db.commit()

        async with factory() as db:
            moved = await db.scalar(text(
                "SELECT user_id FROM application_drafts WHERE id = :i"), {"i": drafted})
        check("activation re-points the draft to the real account",
              str(moved) == str(real_uid), f"{moved} != {real_uid}")

        # Now the executor's own matching, run verbatim against these rows.
        # The predicate is READ OUT OF THE EXECUTOR rather than copied here.
        # The copy that used to live in this file still carried the old third
        # route — the one that was algebraically a duplicate of the first — so
        # it would have kept passing while the real query regressed. A test
        # that validates a stale copy of the thing it is testing is worse than
        # no test.
        where = _executor_draft_predicate()
        async with factory() as db:
            reachable = await db.scalar(
                text(f"SELECT count(*) FROM application_drafts d WHERE {where}"),
                {"uid": real_uid},
            )
            keys = (await db.execute(
                text(
                    "SELECT d.resume_s3_key FROM application_drafts d"
                    f" WHERE d.resume_s3_key IS NOT NULL AND ({where})"
                ),
                {"uid": real_uid},
            )).scalars().all()
        check("erasure reaches the draft after activation", reachable == 1,
              f"{reachable} drafts matched")
        check("and collects its CV for deletion", list(keys) == ["drafts/x/y.pdf"],
              str(list(keys)))

        # Belt and braces: even if the re-point had NOT happened, the address
        # route must still find it. This is the property that stops the
        # executor depending on a repair elsewhere being correct.
        async with factory() as db:
            await db.execute(text(
                "UPDATE application_drafts SET user_id = :g WHERE id = :i"),
                {"g": guest_uid, "i": drafted})
            await db.commit()
            still = await db.scalar(
                text(f"SELECT count(*) FROM application_drafts d WHERE {where}"),
                {"uid": real_uid},
            )
        check("and still reaches it even if the re-point never happened",
              still == 1, f"{still} drafts matched by address alone")

    await eng.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        raise SystemExit(1)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
