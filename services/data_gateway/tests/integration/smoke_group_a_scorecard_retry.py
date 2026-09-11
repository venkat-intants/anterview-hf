"""A1 smoke test — interviews without a scorecard, scorecards without a PDF.

Standalone script, not a pytest module. It drives the real reconciler passes
against a real feedback_billing, which makes a real LLM call (one interview's
worth) and uploads a real PDF, so it needs:

  * a throwaway PostgreSQL at head (it TRUNCATEs what it touches), and
  * feedback_billing running against that same database, with scorecard
    storage configured and an LLM key, at FEEDBACK_BILLING_URL.

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    # start feedback_billing on :8013 against intants_smoke with S3_* set, then:
    FEEDBACK_BILLING_URL=http://127.0.0.1:8013 PYTHONPATH=".;../.." \
      python tests/integration/smoke_group_a_scorecard_retry.py

Exits non-zero on any failed check.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.reconciliation as rec
import app.reminders as rem
from app.config import settings

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def seed(f) -> dict:
    now = datetime.now(tz=UTC)
    cid, hr, jid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    s = {k: uuid.uuid4() for k in ("owed", "abandoned", "fresh", "no_consent", "short")}
    cands = {k: uuid.uuid4() for k in s}
    async with f() as db:
        await db.execute(text("INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                              " VALUES (:i,'Acme','acme',true,:n,:n)"), {"i": cid, "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.test','HR Owner','x',:c,'en',true,false,false,:n,:n)"),
            {"i": hr, "c": cid, "n": now})
        await db.execute(text(
            "INSERT INTO jobs (id,title,description,level,created_at,updated_at)"
            " VALUES (:i,'Staff Nurse','Ward care, triage and patient handover.','entry',:n,:n)"),
            {"i": jid, "n": now})

        for key, status, done_ago, consent, answers in [
            ("owed",       "completed", timedelta(minutes=40), True,  3),  # the one to score
            ("abandoned",  "abandoned", timedelta(minutes=40), True,  3),  # reaper's: never
            ("fresh",      "completed", timedelta(minutes=3),  True,  3),  # live call may land
            ("no_consent", "completed", timedelta(minutes=40), False, 3),  # withdrawn
            ("short",      "completed", timedelta(minutes=40), True,  1),  # too few answers
        ]:
            u = cands[key]
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,preferred_language,"
                " is_active,notify_login_email,must_change_password,created_at,updated_at)"
                " VALUES (:i,:e,:fn,'x','en',true,false,false,:n,:n)"),
                {"i": u, "e": f"{key}@cand.test", "fn": f"Candidate {key.title()}", "n": now})
            if consent:
                await db.execute(text(
                    "INSERT INTO dpdp_consent_ledger (id,user_id,consent_type,granted,granted_at,"
                    " purpose,evidence) VALUES (:i,:u,'interview_voice_recording',true,:n,"
                    " 'interview','{}'::jsonb)"), {"i": uuid.uuid4(), "u": u, "n": now})
            await db.execute(text(
                "INSERT INTO sessions (id,user_id,job_id,language,status,started_at,completed_at,"
                " created_at,updated_at) VALUES (:i,:u,:j,'en',:st,:sa,:ca,:n,:n)"),
                {"i": s[key], "u": u, "j": jid, "st": status,
                 "sa": now - done_ago - timedelta(minutes=10), "ca": now - done_ago, "n": now})
            qa = [
                ("interviewer", "Walk me through how you triage three patients arriving at once."),
                ("candidate", "I check airway, breathing and circulation first, then pain and "
                              "vitals, and I escalate anyone deteriorating to the doctor."),
                ("interviewer", "How do you hand over a patient at the end of a shift?"),
                ("candidate", "I use SBAR: situation, background, assessment, recommendation, "
                              "and I confirm the incoming nurse has read the chart."),
                ("interviewer", "Tell me about a time you disagreed with a doctor."),
                ("candidate", "A dose looked high for the patient's weight, so I asked for it "
                              "to be rechecked before giving it, and it was corrected."),
            ]
            kept, answered = [], 0
            for speaker, line in qa:
                if speaker == "candidate":
                    if answered >= answers:
                        continue
                    answered += 1
                kept.append((speaker, line))
            for i, (speaker, line) in enumerate(kept, start=1):
                await db.execute(text(
                    "INSERT INTO turns (id,session_id,turn_number,speaker,text_content,created_at)"
                    " VALUES (:i,:s,:t,:sp,:tx,:n)"),
                    {"i": uuid.uuid4(), "s": s[key], "t": i, "sp": speaker, "tx": line, "n": now})

        # The owed interview came from an HR invite, so HR should hear it finished.
        await db.execute(text(
            "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
            " target_job_title,target_level,resume_text,status,created_at,updated_at)"
            " VALUES (:i,:c,:u,'Candidate Owed','owed@cand.test','Staff Nurse','entry','cv',"
            " 'shortlisted',:n,:n)"), {"i": (app := uuid.uuid4()), "c": cid, "u": hr, "n": now})
        await db.execute(text(
            "INSERT INTO interview_invites (id,company_id,applicant_id,job_id,created_by_user_id,"
            " token_hash,language,expires_at,session_id,status,consumed_at,created_at,updated_at)"
            " VALUES (:i,:c,:a,:j,:u,:th,'en',:x,:s,'consumed',:n,:n,:n)"),
            {"i": uuid.uuid4(), "c": cid, "a": app, "j": jid, "u": hr, "th": uuid.uuid4().hex,
             "x": now + timedelta(days=1), "s": s["owed"], "n": now})

        # Two scorecards without PDFs: one renderable, one with no name to put on it.
        named_sess, anon_sess, anon_user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'anon@cand.test','','x','en',true,false,false,:n,:n)"),
            {"i": anon_user, "n": now})
        pdf = {}
        for key, sess, user in [("named", named_sess, cands["abandoned"]),
                                ("anon", anon_sess, anon_user)]:
            await db.execute(text(
                "INSERT INTO sessions (id,user_id,job_id,status,created_at,updated_at)"
                " VALUES (:i,:u,:j,'completed',:n,:n)"),
                {"i": sess, "u": user, "j": jid, "n": now - timedelta(days=30)})
            pdf[key] = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO scorecards (scorecard_id,session_id,scores,composite_score,strengths,"
                " improvements,summary,lang,created_at) VALUES (:i,:s,CAST(:sc AS jsonb),6.5,"
                " CAST(:st AS jsonb),CAST(:im AS jsonb),'Steady and safe.','en',:c)"),
                {"i": pdf[key], "s": sess,
                 "sc": json.dumps({"communication": 7, "technical": 6,
                                   "problem_solving": 6, "confidence": 7}),
                 "st": json.dumps(["Structured handover"]),
                 "im": json.dumps([{"area": "Depth", "suggestion": "Cite protocols"}]),
                 "c": now - timedelta(minutes=30)})
        await db.commit()
    return {"sessions": s, "hr": hr, "pdf": pdf}


async def main() -> None:
    settings.feedback_billing_url = os.environ.get("FEEDBACK_BILLING_URL", "http://127.0.0.1:8013")
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text("TRUNCATE applicants, companies, users, jobs, sessions, turns,"
                             " scorecards, interview_invites, dpdp_consent_ledger, email_events,"
                             " notifications, reconciliation_state CASCADE"))
    s = await seed(f)
    print("\n--- seeded: 5 finished interviews, 2 PDF-less scorecards ---\n")

    async with f() as db:
        r = rec.PassResult()
        await rec._scorecard_pass(db, r)
        print(f"  scorecard pass -> interviews_scored={r.interviews_scored} failed={r.failed}")
        scored = {row[0] for row in (await db.execute(text(
            "SELECT session_id FROM scorecards"))).all()}

    ss = s["sessions"]
    check("the interview whose scoring was lost now has a scorecard",
          ss["owed"] in scored and r.interviews_scored == 1, f"{r}")
    check("an interview the reaper marked abandoned is never scored", ss["abandoned"] not in scored)
    check("a just-finished interview is left for the live call", ss["fresh"] not in scored)
    check("an interview without active consent is not processed", ss["no_consent"] not in scored)
    check("an interview too short to score is not scored", ss["short"] not in scored)

    async with f() as db:
        rationale = await db.scalar(text(
            "SELECT rationale->>'_domain_family' FROM scorecards WHERE session_id = :s"),
            {"s": ss["owed"]})
    check("the retry scored against a role-aware rubric, not generic axes",
          rationale == "healthcare_nursing", f"domain_family={rationale}")

    async with f() as db:
        r2 = rec.PassResult()
        await rec._scorecard_pass(db, r2)
        n = await db.scalar(text("SELECT count(*) FROM scorecards WHERE session_id = :s"),
                            {"s": ss["owed"]})
    check("a second pass does not score it again", r2.interviews_scored == 0 and n == 1,
          f"{r2} rows={n}")

    # The results email and HR's completion notice ride on the existing sweep.
    sweep = await rem.run_once(f)
    async with f() as db:
        told_hr = await db.scalar(text(
            "SELECT count(*) FROM notifications WHERE user_id = :u AND kind = 'interview_completed'"),
            {"u": s["hr"]})
        results = await db.scalar(text(
            "SELECT count(*) FROM email_events WHERE template = 'results_ready'"
            " AND to_email = 'owed@cand.test'"))
    check("HR is told the retried interview is complete", told_hr == 1, f"sweep={sweep}")
    check("the candidate gets their results email", results == 1)

    # ── PDFs ────────────────────────────────────────────────────────────
    async with f() as db:
        p = rec.PassResult()
        await rec._pdf_pass(db, p)
        print(f"  pdf pass -> pdfs_rendered={p.pdfs_rendered} failed={p.failed}")
        named_key = await db.scalar(text(
            "SELECT report_pdf_key FROM scorecards WHERE scorecard_id = :i"),
            {"i": s["pdf"]["named"]})
        anon = (await db.execute(text(
            "SELECT gave_up_at IS NOT NULL, last_error FROM reconciliation_state"
            " WHERE kind = :k AND ref_id = :i"), {"k": rec.KIND_PDF, "i": s["pdf"]["anon"]})).first()
    check("a scorecard whose PDF never landed now has one",
          named_key == f"scorecards/{s['pdf']['named']}/report.pdf", str(named_key))
    check("a scorecard with no name to print is parked, not retried",
          bool(anon) and anon[0] and "no_candidate_name" in anon[1], str(anon))

    from shared.s3 import s3_client  # noqa: PLC0415

    async with s3_client(
        endpoint=os.environ["S3_ENDPOINT_URL"], region="us-east-1",
        access_key=os.environ["S3_ACCESS_KEY_ID"], secret_key=os.environ["S3_SECRET_ACCESS_KEY"],
    ) as s3:
        head = await s3.head_object(Bucket=os.environ["S3_SCORECARD_BUCKET"], Key=named_key)
    check("the PDF is really in the bucket",
          head["ContentType"] == "application/pdf" and head["ContentLength"] > 1000,
          f"{head.get('ContentType')} {head.get('ContentLength')}")

    async with f() as db:
        p2 = rec.PassResult()
        await rec._pdf_pass(db, p2)
    check("a second PDF pass renders nothing", p2.pdfs_rendered == 0 and p2.failed == 0, str(p2))

    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 64)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
