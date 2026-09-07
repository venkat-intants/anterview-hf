"""A2/A3/A4 smoke test — deadline reminders, expiry notices and results emails.

Standalone smoke script, not a pytest module — it needs a live PostgreSQL with
pgvector and drives the real code paths end to end, so it is deliberately kept
out of the unit run.

    docker run -d --name intants-pgv -e POSTGRES_PASSWORD=postgres         -e POSTGRES_DB=intants_smoke -p 55432:5432 pgvector/pgvector:pg16
    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke         python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_a_reminders.py

Exits non-zero on any failed check, so it can gate a release.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.reminders as rem

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def seed(f):
    now = datetime.now(tz=UTC)
    cid, uid, jid, eid, rid = (uuid.uuid4() for _ in range(5))
    apps = {k: uuid.uuid4() for k in ("soon", "verysoon", "lapsed", "done", "interview", "scored")}
    async with f() as db:
        await db.execute(text("INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                              " VALUES (:i,'Acme','acme',true,:n,:n)"), {"i": cid, "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.test','HR Owner','x',:c,'en',true,false,false,:n,:n)"),
            {"i": uid, "c": cid, "n": now})
        await db.execute(text(
            "INSERT INTO jobs (id,title,description,level,created_at,updated_at)"
            " VALUES (:i,'Python Developer','desc','mid',:n,:n)"), {"i": jid, "n": now})
        await db.execute(text(
            "INSERT INTO exams (id,company_id,title,created_by_user_id,created_at,updated_at)"
            " VALUES (:i,:c,'Screening Exam',:u,:n,:n)"), {"i": eid, "c": cid, "u": uid, "n": now})
        await db.execute(text(
            "INSERT INTO exam_rounds (id,exam_id,company_id,round_number,title,position,"
            " created_at,updated_at) VALUES (:i,:e,:c,1,'Aptitude Round',0,:n,:n)"),
            {"i": rid, "e": eid, "c": cid, "n": now})

        for key, name in [("soon", "Asha"), ("verysoon", "Bharat"), ("lapsed", "Chitra"),
                          ("done", "Dev"), ("interview", "Esha"), ("scored", "Farah")]:
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                " target_job_title,target_level,resume_text,status,created_at,updated_at)"
                " VALUES (:i,:c,:u,:fn,:em,'Python Developer','mid','cv','new',:n,:n)"),
                {"i": apps[key], "c": cid, "u": uid, "fn": name,
                 "em": f"{name.lower()}@cand.test", "n": now})

        # Exam assignments across the windows under test.
        for key, expires, status, consumed in [
            ("soon",     now + timedelta(hours=20), "invited", None),   # 24h window only
            ("verysoon", now + timedelta(minutes=40), "invited", None), # both windows
            ("lapsed",   now - timedelta(hours=2), "invited", None),    # expiry notice
            ("done",     now + timedelta(hours=20), "completed", now),  # already sat: silence
        ]:
            await db.execute(text(
                "INSERT INTO exam_assignments (id,company_id,exam_id,round_id,applicant_id,"
                " created_by_user_id,token_hash,expires_at,status,consumed_at,created_at,updated_at)"
                " VALUES (:i,:c,:e,:r,:a,:u,:th,:x,:s,:cs,:n,:n)"),
                {"i": uuid.uuid4(), "c": cid, "e": eid, "r": rid, "a": apps[key], "u": uid,
                 "th": uuid.uuid4().hex, "x": expires, "s": status, "cs": consumed, "n": now})

        await db.execute(text(
            "INSERT INTO interview_invites (id,company_id,applicant_id,job_id,created_by_user_id,"
            " token_hash,language,expires_at,scheduled_at,status,created_at,updated_at)"
            " VALUES (:i,:c,:a,:j,:u,:th,'hi',:x,:s,'invited',:n,:n)"),
            {"i": uuid.uuid4(), "c": cid, "a": apps["interview"], "j": jid, "u": uid,
             "th": uuid.uuid4().hex, "x": now + timedelta(days=2),
             "s": now + timedelta(hours=20), "n": now})

        # A completed interview with a scorecard — nobody has told the candidate.
        sid, scid = uuid.uuid4(), uuid.uuid4()
        cand_uid = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'farah@cand.test','Farah','x','te',true,false,false,:n,:n)"),
            {"i": cand_uid, "n": now})
        await db.execute(text(
            "INSERT INTO sessions (id,user_id,job_id,status,created_at,updated_at)"
            " VALUES (:i,:u,:j,'completed',:n,:n)"),
            {"i": sid, "u": cand_uid, "j": jid, "n": now})
        await db.execute(text(
            "INSERT INTO interview_invites (id,company_id,applicant_id,job_id,created_by_user_id,"
            " token_hash,language,expires_at,session_id,status,consumed_at,created_at,updated_at)"
            " VALUES (:i,:c,:a,:j,:u,:th,'te',:x,:sid,'completed',:n,:n,:n)"),
            {"i": uuid.uuid4(), "c": cid, "a": apps["scored"], "j": jid, "u": uid,
             "th": uuid.uuid4().hex, "x": now + timedelta(days=1), "sid": sid, "n": now})
        await db.execute(text(
            "INSERT INTO scorecards (scorecard_id,session_id,scores,summary,lang,created_at)"
            " VALUES (:i,:s,CAST(:sc AS jsonb),'Solid interview','en',:n)"),
            {"i": scid, "s": sid, "sc": json.dumps({"communication": 7}), "n": now})
        await db.commit()
    return {"company": cid, "hr": uid, "applicants": apps}


async def emails(f) -> list[dict]:
    async with f() as db:
        return [dict(r) for r in (await db.execute(text(
            "SELECT template, to_email, lang, dedupe_key, subject FROM email_events "
            "ORDER BY template, to_email"))).mappings().all()]


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text("TRUNCATE applicants, companies, users, jobs, exams, exam_rounds,"
                             " exam_assignments, interview_invites, sessions, scorecards,"
                             " email_events, notifications, reconciliation_state CASCADE"))
    await seed(f)
    print("\n--- seeded: 4 exam assignments, 2 invites, 1 scorecard ---\n")

    r1 = await rem.run_once(f)
    print(f"  sweep 1 -> {r1}\n")
    ev = await emails(f)
    for e in ev:
        print(f"    {e['template']:20s} {e['to_email']:22s} {e['lang']}  {e['dedupe_key']}")
    print()

    kinds = [e["template"] for e in ev]
    check("24h exam reminder sent", kinds.count("exam_reminder") >= 1)
    check("candidate inside both windows gets exactly two exam reminders",
          len([e for e in ev if e["template"] == "exam_reminder"
               and e["to_email"] == "bharat@cand.test"]) == 2,
          str([e["dedupe_key"] for e in ev if e["to_email"] == "bharat@cand.test"]))
    check("candidate who already sat the exam gets nothing",
          not any(e["to_email"] == "dev@cand.test" for e in ev))
    check("lapsed link produces an expiry notice",
          any(e["template"] == "link_expired" and e["to_email"] == "chitra@cand.test" for e in ev))
    check("interview reminder sent", "interview_reminder" in kinds)
    check("interview reminder uses the invite's language",
          any(e["template"] == "interview_reminder" and e["lang"] == "hi" for e in ev),
          str([(e["template"], e["lang"]) for e in ev]))
    check("results-ready email sent for the scorecard",
          any(e["template"] == "results_ready" and e["to_email"] == "farah@cand.test" for e in ev))
    check("results email uses the invite language (te)",
          any(e["template"] == "results_ready" and e["lang"] == "te" for e in ev))
    check("every email has a subject", all(e["subject"].strip() for e in ev))

    async with f() as db:
        n = (await db.execute(text(
            "SELECT kind, title FROM notifications WHERE user_id = "
            "(SELECT id FROM users WHERE email='hr@acme.test')"))).mappings().all()
        check("HR owner notified about the lapsed link",
              any(r["kind"] == "link_expired" for r in n), str([dict(r) for r in n]))

    # ── Idempotency: the sweep runs hourly ───────────────────────────────
    before = len(ev)
    r2 = await rem.run_once(f)
    r3 = await rem.run_once(f)
    after = len(await emails(f))
    print(f"  sweep 2 -> {r2}")
    print(f"  sweep 3 -> {r3}")
    check("repeat sweeps send nothing new", after == before, f"{before} -> {after}")
    check("sweep 2 reported zero sends", r2.total() == 0, str(r2))
    check("sweep 3 reported zero sends", r3.total() == 0, str(r3))

    async with f() as db:
        dupes = await db.scalar(text(
            "SELECT count(*) FROM (SELECT dedupe_key FROM email_events "
            "WHERE dedupe_key IS NOT NULL GROUP BY dedupe_key HAVING count(*) > 1) d"))
        check("no duplicate dedupe keys in the outbox", dupes == 0, f"dupes={dupes}")

    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 64)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
