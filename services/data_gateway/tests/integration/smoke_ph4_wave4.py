#!/usr/bin/env python3
"""PH4 Wave 4 end to end: offers (A3), documents and preboarding (A4), the HRMS
handoff — through the real API, with real object storage (MinIO locally).

    cd services/data_gateway
    PYTHONPATH=".;../.." DATABASE_URL=postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_w4 \\
      DATABASE_SSL= S3_ENDPOINT=http://127.0.0.1:9000 S3_ACCESS_KEY_ID=... \\
      python tests/integration/smoke_ph4_wave4.py

Seeds its own company; leaves it behind (unique slugs), like the other smokes.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("SMOKE_DATABASE_URL", "postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_w4")
PASS: list[str] = []
FAIL: list[str] = []

PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n"


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f" — {detail}" if not cond and detail else ""))


async def main() -> None:  # noqa: PLR0915 — one linear script, read top to bottom
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    tag = uuid.uuid4().hex[:8]
    cid = uuid.uuid4()
    hr, hr2, admin, cand = (uuid.uuid4() for _ in range(4))
    req = uuid.uuid4()
    a1, a2, a3, e1, e2, e3 = (uuid.uuid4() for _ in range(6))

    async with factory() as db:
        await db.execute(text("INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                              " VALUES (:i,'Acme Wave4',:s,true,:n,:n)"),
                         {"i": cid, "s": f"w4-{tag}", "n": now})
        for uid, role, name in ((hr, "hr_manager", "Hema HR"), (hr2, "hr_manager", "Owen HR"),
                                (admin, "super_admin", "Sam Admin")):
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
                " is_active,notify_login_email,must_change_password,created_at,updated_at)"
                " VALUES (:i,:e,:fn,'x',:c,'en',true,false,false,:n,:n)"),
                {"i": uid, "e": f"{uid.hex[:10]}@{tag}.test", "fn": name, "c": cid, "n": now})
            await db.execute(text("INSERT INTO user_roles (user_id, role_id, assigned_at)"
                                  " SELECT :u, id, :n FROM roles WHERE name = :r"),
                             {"u": uid, "r": role, "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,preferred_language,is_active,"
            " notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,:e,'Asha','x','hi',true,false,false,:n,:n)"),
            {"i": cand, "e": f"asha-{tag}@cand.test", "n": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
            " public_apply_enabled,created_by_user_id,owner_user_id,approval_status,"
            " approval_decided_at,created_at,updated_at)"
            " VALUES (:i,:c,'Platform Engineer','senior','open',false,false,:u,:u,'approved',:n,:n,:n)"),
            {"i": req, "c": cid, "u": hr, "n": now})
        for a, u, name, email in ((a1, cand, "Asha Rao", f"asha-{tag}@cand.test"),
                                  (a2, None, "Ravi Kumar", f"ravi-{tag}@cand.test"),
                                  (a3, None, "Meena Iyer", f"meena-{tag}@cand.test")):
            await db.execute(text("INSERT INTO applicants (id,company_id,user_id,full_name,email,"
                                  "target_job_title) VALUES (:i,:c,:u,:n,:m,'Platform Engineer')"),
                             {"i": a, "c": cid, "u": u, "n": name, "m": email})
        for e, a, st in ((e1, a1, "hired"), (e2, a2, "hired"), (e3, a3, "shortlisted")):
            await db.execute(text(
                "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                " target_job_title,created_at,updated_at)"
                " VALUES (:i,:c,:r,:a,:s,'Platform Engineer',:n,:n)"),
                {"i": e, "c": cid, "r": req, "a": a, "s": st, "n": now})
        await db.commit()

    from shared.auth.base import User

    from app.database import get_db_session
    from app.dependencies import get_current_user, get_hr_company, get_super_admin_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with factory() as session:
            yield session

    acting = {"hr": hr, "admin": admin}
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_hr_company] = lambda: (acting["hr"], cid)
    app.dependency_overrides[get_super_admin_company] = lambda: (acting["admin"], cid)
    app.dependency_overrides[get_current_user] = lambda: User(
        user_id=str(cand), full_name="Asha", email="asha@x.test", roles=["candidate"])

    async def last_mail(template: str, related: uuid.UUID | str) -> str:
        async with factory() as db:
            body = await db.scalar(text(
                "SELECT body_text FROM email_events WHERE template = :t AND related_id = :r"
                " ORDER BY created_at DESC LIMIT 1"), {"t": template, "r": uuid.UUID(str(related))})
        return body or ""

    def token_in(body: str) -> str:
        m = re.search(r"/offer#([A-Za-z0-9_-]+)", body)
        return m.group(1) if m else ""

    async def ledger() -> int:
        async with factory() as db:
            return int(await db.scalar(text("SELECT count(*) FROM stage_transitions"
                                            " WHERE enrolment_id = ANY(:e)"), {"e": [e1, e2]}) or 0)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://smoke") as c:
        ledger_before = await ledger()
        print("\nPH4-A3 — templates and drafting")
        r = await c.post("/hr/offer-templates",
                         json={"name": "Standard FT", "currency": "INR", "pay_period": "annual",
                               "probation_months": 6, "notice_period_days": 60, "valid_days": 7,
                               "benefits": "Health cover", "terms": "Standard terms apply."})
        tpl = r.json().get("id")
        check("HR saves an offer template", r.status_code == 201, r.text[:160])
        r = await c.post("/hr/offer-templates", json={"name": "standard ft"})
        check("template names are unique per company", r.status_code == 409, r.text[:160])

        r = await c.post(f"/hr/enrolments/{e3}/offers", json={"base_salary": 100})
        check("no offer without a decision to hire", r.status_code == 409
              and "hire" in r.json()["detail"], r.text[:200])
        r = await c.post(f"/hr/enrolments/{e1}/offers",
                         json={"template_id": tpl, "base_salary": 2400000,
                               "start_date": str(date.today() + timedelta(days=30)),
                               "location": "Pune"})
        offer = r.json()
        o1 = offer.get("id")
        check("an offer is drafted from a template for a hired application",
              r.status_code == 201 and offer["status"] == "draft"
              and offer["probation_months"] == 6 and offer["benefits"] == "Health cover"
              and offer["base_salary"] == "2400000.00", r.text[:240])
        r = await c.post(f"/hr/enrolments/{e1}/offers", json={"base_salary": 1})
        check("one offer in play per application", r.status_code == 409, r.text[:160])
        r = await c.patch(f"/hr/offers/{o1}", json={"base_salary": 2500000, "bonus": "10% target"})
        check("a draft is editable", r.status_code == 200 and r.json()["base_salary"] == "2500000.00",
              r.text[:160])
        r = await c.post(f"/hr/offers/{o1}/send")
        check("an unapproved offer cannot be sent", r.status_code == 409, r.text[:160])

        print("\nPH4-A3 — approval (D4-2)")
        r = await c.post(f"/hr/offers/{o1}/submit")
        check("HR submits for approval", r.status_code == 200
              and r.json()["status"] == "pending_approval", r.text[:160])
        r = await c.patch(f"/hr/offers/{o1}", json={"base_salary": 9999999})
        check("an offer under approval is frozen", r.status_code == 409, r.text[:160])
        acting["admin"] = hr
        r = await c.post(f"/admin/offers/{o1}/approve", json={})
        check("nobody approves their own offer", r.status_code == 403, r.text[:160])
        acting["admin"] = admin
        r = await c.get("/admin/offer-approvals")
        check("the super admin sees it waiting", any(x["id"] == o1 for x in r.json()), r.text[:160])
        r = await c.post(f"/admin/offers/{o1}/reject", json={"note": "too short"})
        check("sending back needs a real note", r.status_code == 422, r.text[:120])
        r = await c.post(f"/admin/offers/{o1}/reject",
                         json={"note": "Please align the bonus with band B."})
        check("the super admin sends it back", r.json().get("status") == "rejected", r.text[:160])
        r = await c.patch(f"/hr/offers/{o1}", json={"bonus": "8% target"})
        check("a sent-back offer is editable again", r.status_code == 200, r.text[:160])
        await c.post(f"/hr/offers/{o1}/submit")
        r = await c.post(f"/admin/offers/{o1}/approve", json={"note": "Good to go"})
        check("…and approved", r.json().get("status") == "approved", r.text[:160])
        async with factory() as db:
            decided = (await db.execute(text("SELECT decided_by_user_id, decided_at FROM offers"
                                             " WHERE id = :o"), {"o": uuid.UUID(o1)})).one()
        check("approval records who and when", decided[0] == admin and decided[1] is not None)

        print("\nPH4-A3 — delivery and the candidate's answer")
        r = await c.post(f"/hr/offers/{o1}/send")
        check("HR sends the approved offer", r.json().get("status") == "sent", r.text[:160])
        mail = await last_mail("offer_ready", o1)
        tok = token_in(mail)
        check("the candidate is emailed a link with the token in the fragment", bool(tok), mail[:200])
        async with factory() as db:
            lang = await db.scalar(text("SELECT lang FROM email_events WHERE template = 'offer_ready'"
                                        " AND related_id = :r"), {"r": uuid.UUID(o1)})
        check("…in their language", lang == "hi", str(lang))
        r = await c.get("/offer", headers={"X-Offer-Token": tok})
        view = r.json()
        check("the candidate reads their offer", r.status_code == 200 and view["status"] == "sent"
              and view["base_salary"] == "2500000.00" and view["company"] == "Acme Wave4", r.text[:200])
        check("…without the approval trail", "approval_note" not in r.text and "decided" not in r.text)
        r = await c.get("/offer", headers={"X-Offer-Token": "not-a-real-token"})
        r2 = await c.get("/offer")
        check("a wrong or missing link says one thing", r.status_code == r2.status_code == 404
              and r.json() == r2.json(), f"{r.text} {r2.text}")
        r = await c.post("/offer/accept", headers={"X-Offer-Token": tok},
                         json={"code": "000000", "full_name": "Asha Rao"})
        check("accepting needs the emailed code", r.status_code == 422, r.text[:160])
        r = await c.post("/offer/code", headers={"X-Offer-Token": tok}, json={"purpose": "accept"})
        check("the candidate asks for a code", r.status_code == 200, r.text[:160])
        code_mail = await last_mail("offer_code", o1)
        code = (re.search(r"\b(\d{6})\b", code_mail) or [None, ""])[1]
        check("the code arrives by email, without a link", bool(code) and "http" not in code_mail,
              code_mail[:120])
        wrong = "111111" if code != "111111" else "222222"
        r = await c.post("/offer/accept", headers={"X-Offer-Token": tok},
                         json={"code": wrong, "full_name": "Asha Rao"})
        async with factory() as db:
            attempts = await db.scalar(text("SELECT attempts FROM offer_codes WHERE offer_id = :o"
                                            " ORDER BY created_at DESC LIMIT 1"), {"o": uuid.UUID(o1)})
        check("a wrong code is refused and still counts", r.status_code == 422 and attempts == 1,
              f"{r.status_code} {attempts}")
        r = await c.post("/offer/decline", headers={"X-Offer-Token": tok},
                         json={"code": code, "reason": "x"})
        check("an accept code cannot decline", r.status_code == 422, r.text[:160])
        r = await c.post("/offer/accept", headers={"X-Offer-Token": tok},
                         json={"code": code, "full_name": "Asha Rao"})
        check("the candidate accepts with the code", r.status_code == 200
              and r.json()["status"] == "accepted", r.text[:200])
        r = await c.post("/offer/code", headers={"X-Offer-Token": tok}, json={"purpose": "decline"})
        check("an accepted offer cannot then be declined", r.status_code == 409, r.text[:160])
        async with factory() as db:
            state = (await db.execute(text(
                "SELECT e.status, e.offer_outcome, o.responded_at, o.accepted_name"
                "  FROM enrolments e JOIN offers o ON o.enrolment_id = e.id WHERE o.id = :o"),
                {"o": uuid.UUID(o1)})).one()
            consent = await db.scalar(text(
                "SELECT count(*) FROM dpdp_consent_ledger WHERE user_id = :u"
                " AND consent_type = 'preboarding_documents' AND granted"), {"u": cand})
            invited = await db.scalar(text(
                "SELECT count(*) FROM email_events WHERE template = 'offer_account'"
                " AND to_user_id = :u"), {"u": cand})
        check("someone who already has an account is not invited to make one", invited == 0,
              str(invited))
        check("the outcome sits beside the decision, which is unchanged",
              state[0] == "hired" and state[1] == "offer_accepted", str(state))
        check("acceptance records when and the name typed", state[2] is not None
              and state[3] == "Asha Rao", str(state))
        check("accepting records consent for the documents that follow", consent == 1, str(consent))
        r = await c.get("/hr/pipeline", params={"status": "hired", "limit": 200})
        prow = next((i for i in r.json().get("items", []) if i.get("enrolment_id") == str(e1)), {})
        check("HR's pipeline shows the offer's outcome beside the hire",
              prow.get("status") == "hired" and prow.get("offer_outcome") == "offer_accepted",
              str(prow)[:200])

        print("\nPH4-A4 — documents")
        r = await c.post(f"/hr/requisitions/{req}/document-requirements",
                         json={"name": "Passport", "doc_type": "identity", "mandatory": True,
                               "requires_expiry": True})
        passport = r.json().get("id")
        check("HR requires a mandatory document with an expiry", r.status_code == 201, r.text[:160])
        r = await c.post(f"/hr/requisitions/{req}/document-requirements",
                         json={"name": "Photo", "doc_type": "photo", "mandatory": False})
        check("…and an optional one", r.status_code == 201, r.text[:160])
        link_only = {"X-Offer-Token": tok}
        r = await c.get("/offer/documents", headers=link_only)
        check("the link alone does not open the documents (H1)", r.status_code == 401, r.text[:160])
        r = await c.post(f"/offer/documents/{passport}", headers=link_only,
                         files={"file": ("p.pdf", PDF, "application/pdf")},
                         data={"expires_on": str(date.today() + timedelta(days=900))})
        check("…nor uploads one", r.status_code == 401, r.text[:160])
        r = await c.post("/offer/documents/code", headers=link_only)
        check("the candidate asks for a code to open their documents", r.status_code == 200,
              r.text[:160])
        dcode = (re.search(r"\b(\d{6})\b", await last_mail("offer_code", o1)) or [None, ""])[1]
        bad = "111111" if dcode != "111111" else "222222"
        r = await c.post("/offer/documents/session", headers=link_only, json={"code": bad})
        check("a wrong code opens nothing", r.status_code == 422, r.text[:160])
        r = await c.post("/offer/documents/session", headers=link_only, json={"code": dcode})
        sess = r.json().get("session_token", "")
        check("the right code opens an hour-long session", r.status_code == 200 and bool(sess),
              r.text[:160])
        async with factory() as db:
            leaked = await db.scalar(text("SELECT count(*) FROM email_events"
                                          " WHERE body_text LIKE :s"), {"s": f"%{sess}%"})
        check("…whose token is never emailed", leaked == 0, str(leaked))
        h = {"X-Offer-Token": tok, "X-Offer-Session": sess}
        r = await c.get("/offer/documents", headers=h)
        items = {i["name"]: i for i in r.json()["items"]}
        check("the candidate sees what is outstanding", items["Passport"]["state"] == "outstanding"
              and r.json()["outstanding"] == ["Passport"], r.text[:200])
        r = await c.post(f"/offer/documents/{passport}", headers=h,
                         files={"file": ("passport.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")},
                         data={"expires_on": str(date.today() + timedelta(days=900))})
        check("a file that is not a PDF, JPEG or PNG is refused, whatever it is called",
              r.status_code == 422 and "PDF, JPEG or PNG" in r.json()["detail"], r.text[:160])
        r = await c.post(f"/offer/documents/{passport}", headers=h,
                         files={"file": ("passport.pdf", PDF, "application/pdf")})
        check("a document with an expiry needs its date", r.status_code == 422, r.text[:160])
        r = await c.post(f"/offer/documents/{passport}", headers=h,
                         files={"file": ("../../passport scan.pdf", PDF, "application/pdf")},
                         data={"expires_on": str(date.today() + timedelta(days=900))})
        doc1 = r.json().get("document_id")
        check("the candidate uploads it", r.status_code == 201, r.text[:200])
        r = await c.post(f"/offer/documents/{passport}", headers=h,
                         files={"file": ("p.pdf", PDF, "application/pdf")},
                         data={"expires_on": str(date.today() + timedelta(days=900))})
        check("…and cannot replace it while it is with HR", r.status_code == 409, r.text[:160])

        r = await c.get(f"/hr/offers/{o1}/documents")
        hr_items = {i["name"]: i for i in r.json()["items"]}
        check("HR sees the submitted document", hr_items["Passport"]["state"] == "submitted"
              and hr_items["Passport"]["document"]["file_name"] == "passport scan.pdf", r.text[:240])
        r = await c.post(f"/hr/documents/{doc1}/download")
        url = r.json().get("url", "")
        check("HR opens it only through a short signed link (SigV4, five minutes)",
              r.status_code == 200 and "X-Amz-Expires=300" in url
              and "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url, url[-200:])
        async with httpx.AsyncClient() as raw:
            got = await raw.get(url)
        check("…which serves the exact file, as a download", got.status_code == 200
              and got.content == PDF and "attachment" in got.headers.get("content-disposition", ""),
              f"{got.status_code} {got.headers.get('content-disposition')}")
        r = await c.post(f"/hr/documents/{doc1}/review", json={"action": "reject", "note": "no"})
        check("a rejection needs a reason the candidate can act on", r.status_code == 422, r.text[:120])
        r = await c.post(f"/hr/documents/{doc1}/review",
                         json={"action": "reject", "note": "The photo page is cut off."})
        check("HR rejects it with a reason", r.json().get("state") == "rejected", r.text[:160])
        r = await c.get("/offer/documents", headers=h)
        mine = {i["name"]: i for i in r.json()["items"]}["Passport"]
        check("the candidate sees it rejected, and why",
              mine["state"] == "rejected" and "cut off" in (mine["document"]["review_note"] or ""),
              str(mine))
        check("…and is emailed", "cut off" in await last_mail("document_update", doc1))
        check("the candidate is told of every upload, so one they did not send is noticed",
              "अपलोड नहीं किया" in await last_mail("document_received", doc1))

        print("\nPH4-A3 — the lifetime lock on wrong codes (M1)")
        async with factory() as db:
            await db.execute(text("UPDATE offers SET code_failures = 19 WHERE id = :o"),
                             {"o": uuid.UUID(o1)})
            await db.commit()
        await c.post("/offer/documents/code", headers=link_only)
        r = await c.post("/offer/documents/session", headers=link_only, json={"code": bad})
        r2 = await c.post("/offer/documents/code", headers=link_only)
        check("the twentieth wrong code locks the offer", r.status_code == 422
              and r2.status_code == 423, f"{r.status_code} {r2.status_code} {r2.text[:120]}")
        async with factory() as db:
            told = await db.scalar(text(
                "SELECT count(*) FROM notifications WHERE title LIKE 'Offer locked%'"
                " AND user_id = :u"), {"u": hr})
        check("…and HR is told", told == 1, str(told))
        r = await c.post(f"/hr/offers/{o1}/resend")
        check("HR re-sends the offer, which unlocks it", r.status_code == 200, r.text[:160])
        tok = token_in(await last_mail("offer_ready", o1))
        old = await c.get("/offer/documents", headers=h)
        check("…retires the old link and closes its sessions", old.status_code in (401, 404),
              str(old.status_code))
        link_only = {"X-Offer-Token": tok}
        await c.post("/offer/documents/code", headers=link_only)
        dcode = (re.search(r"\b(\d{6})\b", await last_mail("offer_code", o1)) or [None, ""])[1]
        r = await c.post("/offer/documents/session", headers=link_only, json={"code": dcode})
        h = {"X-Offer-Token": tok, "X-Offer-Session": r.json().get("session_token", "")}
        check("…and the candidate opens their documents again with a new code",
              r.status_code == 200, r.text[:160])
        r = await c.post(f"/offer/documents/{passport}", headers=h,
                         files={"file": ("passport2.pdf", PDF + b"%v2\n", "application/pdf")},
                         data={"expires_on": str(date.today() + timedelta(days=900))})
        doc2 = r.json().get("document_id")
        check("the candidate uploads a replacement", r.status_code == 201
              and r.json()["version"] == 2, r.text[:160])
        r = await c.get("/hr/offers", params={"status": "preboarding"})
        row = next((i for i in r.json().get("items", []) if i["id"] == o1), None)
        check("HR sees the offer among those in preboarding, with its progress",
              r.status_code == 200 and row is not None
              and row["documents"]["awaiting_review"] >= 1
              and row["documents"]["mandatory_verified"] < row["documents"]["mandatory_total"],
              r.text[:200])
        check("…and no compensation in the list", row is not None and "base_salary" not in row,
              str(row)[:200])
        r = await c.get("/hr/offers", params={"status": "nonsense"})
        check("an unknown filter is refused in words", r.status_code == 422, r.text[:160])
        acting["hr_company"] = uuid.uuid4()
        app.dependency_overrides[get_hr_company] = lambda: (acting["hr"], acting["hr_company"])
        r = await c.get("/hr/offers")
        check("another company's HR sees none of these offers",
              r.status_code == 200 and r.json()["total"] == 0, r.text[:160])
        app.dependency_overrides[get_hr_company] = lambda: (acting["hr"], cid)
        r = await c.post(f"/hr/offers/{o1}/preboarding-complete")
        check("preboarding cannot complete with the passport unverified", r.status_code == 409
              and "Passport" in r.json()["detail"], r.text[:200])
        r = await c.post(f"/hr/documents/{doc2}/review", json={"action": "verify"})
        check("HR verifies the replacement", r.json().get("state") == "verified", r.text[:160])
        async with factory() as db:
            reviewed = (await db.execute(text("SELECT reviewed_by_user_id, reviewed_at FROM"
                                              " candidate_documents WHERE id = :d"),
                                         {"d": uuid.UUID(doc2)})).one()
            old = await db.scalar(text("SELECT superseded_by_id FROM candidate_documents"
                                       " WHERE id = :d"), {"d": uuid.UUID(doc1)})
        check("verification records who and when", reviewed[0] == hr and reviewed[1] is not None)
        check("the rejected version is kept, superseded", str(old) == doc2, str(old))

        print("\nPH4-A4 — preboarding and the HRMS handoff")
        r = await c.post(f"/hr/offers/{o1}/hrms-export")
        check("no handoff before preboarding is complete", r.status_code == 409, r.text[:160])
        r = await c.post(f"/hr/offers/{o1}/preboarding-complete")
        check("preboarding completes with every mandatory document verified (the photo is optional)",
              r.status_code == 200 and r.json()["preboarding_completed_at"], r.text[:200])
        r = await c.post(f"/hr/offers/{o1}/hrms-export")
        export = r.json()
        from app.offer_security import verify_export

        check("HR prepares a signed HRMS payload", r.status_code == 200
              and export["algorithm"] == "HMAC-SHA256"
              and verify_export(export["payload"], export["signature"]), r.text[:200])
        check("…carrying only what an HRMS needs",
              not any(k in r.text for k in ("storage_key", "review_note", "approval_note",
                                            "cut off", "score")), r.text[:200])
        async with factory() as db:
            audit = dict((await db.execute(text(
                "SELECT action, count(*) FROM audit_log WHERE (details->>'company_id') = :c"
                "   AND (action LIKE 'offer.%' OR action LIKE 'document.%') GROUP BY action"),
                {"c": str(cid)})).all())
        check("every offer and document action is audited",
              {"offer.created", "offer.submitted", "offer.approved", "offer.rejected", "offer.sent",
               "offer.viewed", "offer.accepted", "document.uploaded", "document.rejected",
               "document.verified", "document.downloaded", "offer.hrms_exported"} <= set(audit),
              str(sorted(audit)))

        print("\nPH4-A3 — withdraw, the portal, and the hire")
        r = await c.post(f"/hr/enrolments/{e2}/offers", json={"base_salary": 1800000})
        o2 = r.json()["id"]
        acting["hr"] = hr2
        await c.post(f"/hr/offers/{o2}/submit")
        acting["hr"] = hr
        await c.post(f"/admin/offers/{o2}/approve", json={})
        await c.post(f"/hr/offers/{o2}/send")
        tok2 = token_in(await last_mail("offer_ready", o2))
        r = await c.post(f"/hr/offers/{o2}/withdraw", json={"note": "Role put on hold."})
        check("HR withdraws an offer before it is answered", r.json().get("status") == "withdrawn",
              r.text[:160])
        r = await c.get("/offer", headers={"X-Offer-Token": tok2})
        check("the candidate's link now says it was withdrawn", r.json().get("status") == "withdrawn",
              r.text[:160])
        check("…and they are emailed", bool(await last_mail("offer_update", o2)))
        r = await c.post("/offer/code", headers={"X-Offer-Token": tok2}, json={"purpose": "accept"})
        check("a withdrawn offer cannot be answered", r.status_code == 409, r.text[:160])

        r = await c.get("/users/me/offers")
        check("the signed-in candidate sees their offer", any(x["id"] == o1 for x in r.json())
              and all(x["id"] != o2 for x in r.json()), r.text[:200])
        r = await c.post(f"/users/me/offers/{o1}/link")
        fresh = (r.json().get("url") or "").split("#")[-1]
        old_view = await c.get("/offer", headers={"X-Offer-Token": tok})
        new_view = await c.get("/offer", headers={"X-Offer-Token": fresh})
        check("the portal mints a fresh link and retires the old one",
              old_view.status_code == 404 and new_view.status_code == 200, f"{old_view.status_code}")
        r = await c.post(f"/users/me/offers/{o2}/link")
        check("…but not for somebody else's offer", r.status_code == 404, r.text[:120])

        r = await c.post(f"/hr/enrolments/{e1}/status", json={"status": "shortlisted"})
        check("a hire cannot slide back into the pipeline", r.status_code == 409
              and "rejection" in r.json().get("detail", ""), r.text[:200])
        check("nothing in the offer journey wrote the stage ledger", await ledger() == ledger_before)

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
