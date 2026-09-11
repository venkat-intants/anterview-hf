"""A2/A3/A4 smoke test — deadline reminders, expiry notices and results emails.

Standalone smoke script, not a pytest module — it needs a live PostgreSQL with
pgvector and drives the real code paths end to end, so it is deliberately kept
out of the unit run.

It TRUNCATEs every table it touches. Point it at a throwaway database, never at
one with data you want to keep.

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

import app.reconciliation as rec
import app.reminders as rem

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def seed(f):
    now = datetime.now(tz=UTC)
    cid, uid, jid, eid, rid = (uuid.uuid4() for _ in range(5))
    wf_owner, req_id, wf_on, wf_off = (uuid.uuid4() for _ in range(4))
    apps = {k: uuid.uuid4() for k in (
        "soon", "verysoon", "lapsed", "done", "interview", "scored",
        "noemail", "wflapsed", "wfquiet", "consumed",
    )}
    ids = {"interview_invite": uuid.uuid4(), "consumed_invite": uuid.uuid4()}
    async with f() as db:
        await db.execute(text("INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                              " VALUES (:i,'Acme','acme',true,:n,:n)"), {"i": cid, "n": now})
        for u, email, name in [(uid, "hr@acme.test", "HR Owner"),
                               (wf_owner, "wf@acme.test", "Workflow Owner")]:
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
                " is_active,notify_login_email,must_change_password,created_at,updated_at)"
                " VALUES (:i,:e,:fn,'x',:c,'en',true,false,false,:n,:n)"),
                {"i": u, "e": email, "fn": name, "c": cid, "n": now})
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

        # A requisition with two workflow versions: one with reminders on, one off.
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,created_at,updated_at)"
            " VALUES (:i,:c,'Python Developer',:n,:n)"), {"i": req_id, "c": cid, "n": now})
        for wid, version, on in [(wf_on, 1, True), (wf_off, 2, False)]:
            await db.execute(text(
                "INSERT INTO workflows (id,company_id,requisition_id,version,reminders_enabled,"
                " created_by_user_id,created_at,updated_at) VALUES (:i,:c,:r,:v,:on,:u,:n,:n)"),
                {"i": wid, "c": cid, "r": req_id, "v": version, "on": on, "u": wf_owner, "n": now})

        for key, name, email in [
            ("soon", "Asha", "asha@cand.test"), ("verysoon", "Bharat", "bharat@cand.test"),
            ("lapsed", "Chitra", "chitra@cand.test"), ("done", "Dev", "dev@cand.test"),
            ("interview", "Esha", "esha@cand.test"), ("scored", "Farah", "farah@cand.test"),
            ("noemail", "Gita", None), ("wflapsed", "Hari", "hari@cand.test"),
            ("wfquiet", "Indu", "indu@cand.test"), ("consumed", "Jaya", "jaya@cand.test"),
        ]:
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                " target_job_title,target_level,resume_text,status,created_at,updated_at)"
                " VALUES (:i,:c,:u,:fn,:em,'Python Developer','mid','cv','new',:n,:n)"),
                {"i": apps[key], "c": cid, "u": uid, "fn": name, "em": email, "n": now})

        enrol = {}
        for key, wid in [("wflapsed", wf_on), ("wfquiet", wf_off)]:
            enrol[key] = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,target_job_title,"
                " workflow_id,created_at,updated_at) VALUES (:i,:c,:r,:a,'Python Developer',:w,:n,:n)"),
                {"i": enrol[key], "c": cid, "r": req_id, "a": apps[key], "w": wid, "n": now})

        # Exam assignments across the windows under test. `owner` None models a
        # link the workflow runner issued, which used to carry no creator.
        for key, expires, status, consumed, owner, en in [
            ("soon",     now + timedelta(hours=20),   "invited",   None, uid,  None),  # 24h band
            ("verysoon", now + timedelta(minutes=40), "invited",   None, uid,  None),  # 1h band only
            ("lapsed",   now - timedelta(hours=2),    "invited",   None, uid,  None),  # expiry notice
            ("done",     now + timedelta(hours=20),   "completed", now,  uid,  None),  # silence
            ("noemail",  now - timedelta(hours=3),    "invited",   None, uid,  None),  # HR still told
            ("wflapsed", now - timedelta(hours=1),    "invited",   None, None, enrol["wflapsed"]),
            ("wfquiet",  now + timedelta(minutes=30), "invited",   None, None, enrol["wfquiet"]),
        ]:
            await db.execute(text(
                "INSERT INTO exam_assignments (id,company_id,exam_id,round_id,applicant_id,"
                " created_by_user_id,enrolment_id,token_hash,expires_at,status,consumed_at,"
                " created_at,updated_at) VALUES (:i,:c,:e,:r,:a,:u,:en,:th,:x,:s,:cs,:n,:n)"),
                {"i": uuid.uuid4(), "c": cid, "e": eid, "r": rid, "a": apps[key], "u": owner,
                 "en": en, "th": uuid.uuid4().hex, "x": expires, "s": status, "cs": consumed,
                 "n": now})

        await db.execute(text(
            "INSERT INTO interview_invites (id,company_id,applicant_id,job_id,created_by_user_id,"
            " token_hash,language,expires_at,scheduled_at,status,created_at,updated_at)"
            " VALUES (:i,:c,:a,:j,:u,:th,'hi',:x,:s,'invited',:n,:n)"),
            {"i": ids["interview_invite"], "c": cid, "a": apps["interview"], "j": jid, "u": uid,
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

        # A finished interview still at 'consumed' — the completion sweep's input.
        sid2, g2 = uuid.uuid4(), uuid.uuid4()
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'jaya@cand.test','Jaya','x','en',true,false,false,:n,:n)"),
            {"i": g2, "n": now})
        await db.execute(text(
            "INSERT INTO sessions (id,user_id,job_id,status,created_at,updated_at)"
            " VALUES (:i,:u,:j,'completed',:n,:n)"), {"i": sid2, "u": g2, "j": jid, "n": now})
        await db.execute(text(
            "INSERT INTO interview_invites (id,company_id,applicant_id,job_id,created_by_user_id,"
            " token_hash,language,expires_at,session_id,status,consumed_at,created_at,updated_at)"
            " VALUES (:i,:c,:a,:j,:u,:th,'en',:x,:sid,'consumed',:n,:n,:n)"),
            {"i": ids["consumed_invite"], "c": cid, "a": apps["consumed"], "j": jid, "u": uid,
             "th": uuid.uuid4().hex, "x": now + timedelta(days=1), "sid": sid2, "n": now})
        await db.execute(text(
            "INSERT INTO scorecards (scorecard_id,session_id,scores,summary,lang,created_at)"
            " VALUES (:i,:s,CAST(:sc AS jsonb),'Done','en',:n)"),
            {"i": uuid.uuid4(), "s": sid2, "sc": json.dumps({"communication": 6}), "n": now})
        await db.commit()
    return {"company": cid, "hr": uid, "wf_owner": wf_owner, "applicants": apps, **ids}


async def emails(f) -> list[dict]:
    async with f() as db:
        return [dict(r) for r in (await db.execute(text(
            "SELECT template, to_email, lang, dedupe_key, subject FROM email_events "
            "ORDER BY template, to_email"))).mappings().all()]


async def notifications(f, user_id) -> list[dict]:
    async with f() as db:
        return [dict(r) for r in (await db.execute(text(
            "SELECT kind, title, body, dedupe_key FROM notifications WHERE user_id = :u"),
            {"u": user_id})).mappings().all()]


async def batch_checks(f, s) -> None:
    """The batch-finished notification against real SQL: a parked row and a
    manually rescored row must not hold the batch open, and it fires once."""
    now = datetime.now(tz=UTC)
    batch = uuid.uuid4()
    rows = {"scored": uuid.uuid4(), "parked": uuid.uuid4(), "rescored": uuid.uuid4(),
            "empty": uuid.uuid4()}
    async with f() as db:
        for key, pending, ats, cv in [
            ("scored", False, 7, "cv"),     # the loop finished it
            ("parked", True, None, "cv"),   # the loop gave up on it
            ("rescored", True, 6, "cv"),    # HR rescored it by hand; flag never cleared
            ("empty", True, None, "   "),   # nothing to score
        ]:
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,"
                " target_job_title,target_level,resume_text,status,pending_enrichment,"
                " ats_overall,upload_batch_id,created_at,updated_at)"
                " VALUES (:i,:c,:u,:fn,'Dev','mid',:cv,'new',:p,:ats,:b,:n,:n)"),
                {"i": rows[key], "c": s["company"], "u": s["hr"], "fn": key, "cv": cv,
                 "p": pending, "ats": ats, "b": batch, "n": now})
        await db.execute(text(
            "INSERT INTO reconciliation_state (kind,ref_id,attempts,last_error,last_attempt_at,"
            " gave_up_at) VALUES (:k,:r,8,'unreadable',:n,:n)"),
            {"k": rec.KIND_ATS, "r": rows["parked"], "n": now})
        await db.commit()

        first = await rec._notify_batch_done(db, batch, s["hr"])
        second = await rec._notify_batch_done(db, batch, s["hr"])

    got = [n for n in await notifications(f, s["hr"]) if n["kind"] == "bulk_upload"]
    check("a batch with a parked row is still reported finished", first is True)
    check("the batch is reported once, not per call", second is False and len(got) == 1,
          f"second={second} rows={len(got)}")
    check("the report counts the unreadable rows honestly",
          bool(got) and "2 of 4" in got[0]["body"] and "2 could not be read" in got[0]["body"],
          got[0]["body"] if got else "")

    # One still-outstanding row must keep it quiet.
    batch2 = uuid.uuid4()
    async with f() as db:
        await db.execute(text(
            "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,"
            " target_job_title,target_level,resume_text,status,pending_enrichment,"
            " upload_batch_id,created_at,updated_at)"
            " VALUES (:i,:c,:u,'waiting','Dev','mid','cv','new',true,:b,:n,:n)"),
            {"i": uuid.uuid4(), "c": s["company"], "u": s["hr"], "b": batch2, "n": now})
        await db.commit()
        check("a batch with a row still being read stays quiet",
              await rec._notify_batch_done(db, batch2, s["hr"]) is False)


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text("TRUNCATE applicants, companies, users, jobs, exams, exam_rounds,"
                             " exam_assignments, interview_invites, sessions, scorecards,"
                             " email_events, notifications, reconciliation_state,"
                             " job_requisitions, enrolments, workflows CASCADE"))
    s = await seed(f)
    print("\n--- seeded: 7 exam assignments, 3 invites, 2 scorecards, 2 workflows ---\n")

    r1 = await rem.run_once(f)
    print(f"  sweep 1 -> {r1}\n")
    ev = await emails(f)
    for e in ev:
        print(f"    {e['template']:20s} {str(e['to_email']):22s} {e['lang']}  {e['dedupe_key']}")
    print()

    def to(addr: str, template: str | None = None) -> list[dict]:
        return [e for e in ev if e["to_email"] == addr
                and (template is None or e["template"] == template)]

    kinds = [e["template"] for e in ev]
    check("24h exam reminder sent for a deadline 20h out",
          [e["dedupe_key"].rsplit(":", 1)[1] for e in to("asha@cand.test", "exam_reminder")]
          == ["24h"], str(to("asha@cand.test")))
    check("a deadline 40 min out gets the 1h reminder only — not both at once",
          [e["dedupe_key"].rsplit(":", 1)[1] for e in to("bharat@cand.test", "exam_reminder")]
          == ["1h"], str([e["dedupe_key"] for e in to("bharat@cand.test")]))
    check("candidate who already sat the exam gets nothing", not to("dev@cand.test"))
    check("lapsed link produces an expiry notice", bool(to("chitra@cand.test", "link_expired")))
    check("interview reminder sent", "interview_reminder" in kinds)
    check("interview reminder uses the invite's language",
          any(e["template"] == "interview_reminder" and e["lang"] == "hi" for e in ev),
          str([(e["template"], e["lang"]) for e in ev]))
    check("results-ready email sent for the scorecard",
          bool(to("farah@cand.test", "results_ready")))
    check("results email uses the invite language (te)",
          any(e["template"] == "results_ready" and e["lang"] == "te" for e in ev))
    check("every email has a subject", all(e["subject"].strip() for e in ev))
    check("workflow with reminders OFF: its candidate gets no reminder",
          not to("indu@cand.test"), str(to("indu@cand.test")))
    check("workflow with reminders ON: its lapsed candidate is still emailed",
          bool(to("hari@cand.test", "link_expired")))

    hr = await notifications(f, s["hr"])
    owner = await notifications(f, s["wf_owner"])
    lapsed_hr = [n for n in hr if n["kind"] == "link_expired"]
    check("HR owner notified about the lapsed link",
          any("Chitra" in n["title"] for n in lapsed_hr), str(lapsed_hr))
    check("HR is told about a lapse even when the candidate has no email",
          any("Gita" in n["title"] for n in lapsed_hr), str(lapsed_hr))
    check("a workflow-issued link's lapse reaches the workflow owner",
          any(n["kind"] == "link_expired" and "Hari" in n["title"] for n in owner), str(owner))
    check("every lapse notification carries its dedupe key",
          all(n["dedupe_key"] and n["dedupe_key"].startswith("link_expired:")
              for n in lapsed_hr))
    done = [n for n in hr if n["kind"] == "interview_completed"]
    check("completion announced to HR once, with its key",
          len(done) == 1 and done[0]["dedupe_key"] == f"interview_completed:{s['consumed_invite']}",
          str(done))

    # ── Idempotency: the sweep runs every five minutes ───────────────────
    before_e, before_n = len(ev), len(hr) + len(owner)
    r2 = await rem.run_once(f)
    r3 = await rem.run_once(f)
    after_e = len(await emails(f))
    after_n = len(await notifications(f, s["hr"])) + len(await notifications(f, s["wf_owner"]))
    print(f"  sweep 2 -> {r2}")
    print(f"  sweep 3 -> {r3}")
    check("repeat sweeps send no new email", after_e == before_e, f"{before_e} -> {after_e}")
    check("repeat sweeps add no new notifications", after_n == before_n,
          f"{before_n} -> {after_n}")
    check("sweep 2 reported zero sends", r2.total() == 0, str(r2))
    check("sweep 3 reported zero sends", r3.total() == 0, str(r3))

    async with f() as db:
        leftover = (await db.execute(
            text(rem._LAPSED_SQL),
            {"now": datetime.now(tz=UTC), "floor": datetime.now(tz=UTC) - timedelta(hours=48),
             "lim": 200},
        )).mappings().all()
    check("fully handled lapses drop out of the expiry query (no batch-slot hogging)",
          leftover == [], str([r["full_name"] for r in leftover]))

    # ── Reschedule: the new slot gets its own reminders ──────────────────
    async with f() as db:
        await rem.rearm_interview_reminders(db, s["interview_invite"])
        await db.execute(text(
            "UPDATE interview_invites SET scheduled_at = :t WHERE id = :i"),
            {"t": datetime.now(tz=UTC) + timedelta(hours=22), "i": s["interview_invite"]})
        await db.commit()
    await rem.run_once(f)
    ev2 = await emails(f)
    esha = [e["dedupe_key"] for e in ev2 if e["to_email"] == "esha@cand.test"]
    live_key = rem.interview_reminder_key(s["interview_invite"], "24h")
    check("a rescheduled interview is reminded about its new slot",
          esha.count(live_key) == 1 and any(":superseded:" in k for k in esha), str(esha))
    check("the reminder already sent is kept in the delivery log", len(esha) == 2, str(esha))

    await batch_checks(f, s)

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
