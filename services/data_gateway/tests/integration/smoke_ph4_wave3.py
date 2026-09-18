#!/usr/bin/env python3
"""PH4 Wave 3 end to end: interview scheduling and loops (A2), panel workload
and calibration (O5) — through the real API.

    cd services/data_gateway
    PYTHONPATH=".;../.." DATABASE_URL=postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_dev \\
      DATABASE_SSL= python tests/integration/smoke_ph4_wave3.py

Seeds its own company; leaves it behind (unique slugs), like the other smokes.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get(
    "SMOKE_DATABASE_URL", "postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_dev"
)
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f" — {detail}" if not cond and detail else ""))


async def main() -> None:  # noqa: PLR0915 — one linear script, read top to bottom
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    tag = uuid.uuid4().hex[:8]
    cid = uuid.uuid4()
    hr, admin, iv1, iv2, iv3 = (uuid.uuid4() for _ in range(5))
    cand1, cand2 = uuid.uuid4(), uuid.uuid4()
    req = uuid.uuid4()
    # Three days out, 04:00 UTC = 09:30 in India.
    base = (now + timedelta(days=3)).replace(hour=4, minute=0, second=0, microsecond=0)

    def at(minutes: int) -> str:
        return (base + timedelta(minutes=minutes)).isoformat()

    async with factory() as db:
        await db.execute(text(
            "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
            " VALUES (:i,:s,:s,true,:n,:n)"), {"i": cid, "s": f"w3-{tag}", "n": now})
        staff = ((hr, "hr_manager", "Hema HR"), (admin, "super_admin", "Sam Admin"),
                 (iv1, "interviewer", "Ivan One"), (iv2, "interviewer", "Iris Two"),
                 (iv3, "interviewer", "Ines Three"))
        for uid, role, name in staff:
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
                " is_active,notify_login_email,must_change_password,created_at,updated_at)"
                " VALUES (:i,:e,:fn,'x',:c,'en',true,false,false,:n,:n)"),
                {"i": uid, "e": f"{uid.hex[:10]}@{tag}.test", "fn": name, "c": cid, "n": now})
            await db.execute(text(
                "INSERT INTO user_roles (user_id, role_id, assigned_at)"
                " SELECT :u, id, :n FROM roles WHERE name = :r"), {"u": uid, "r": role, "n": now})
        for uid, lang in ((cand1, "hi"), (cand2, "en")):
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,preferred_language,is_active,"
                " notify_login_email,must_change_password,created_at,updated_at)"
                " VALUES (:i,:e,'Cand','x',:l,true,false,false,:n,:n)"),
                {"i": uid, "e": f"c-{uid.hex[:10]}@{tag}.test", "l": lang, "n": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
            " public_apply_enabled,created_by_user_id,owner_user_id,approval_status,"
            " approval_decided_at,created_at,updated_at)"
            " VALUES (:i,:c,'Platform Engineer','senior','open',false,false,:u,:u,'approved',:n,:n,:n)"),
            {"i": req, "c": cid, "u": hr, "n": now})
        await db.commit()

    from shared.auth.base import User

    from app.database import get_db_session
    from app.dependencies import (
        get_current_user,
        get_hr_company,
        get_interviewer_company,
        get_super_admin_company,
    )
    from app.main import app

    async def _db():  # noqa: ANN202
        async with factory() as session:
            yield session

    acting = {"iv": iv1, "cand": cand1}
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_hr_company] = lambda: (hr, cid)
    app.dependency_overrides[get_super_admin_company] = lambda: (admin, cid)
    app.dependency_overrides[get_interviewer_company] = lambda: (acting["iv"], cid)
    # The same type the real get_current_user returns (security review F1: an
    # ORM User here once hid a 500 on every candidate route).
    app.dependency_overrides[get_current_user] = lambda: User(
        user_id=str(acting["cand"]), full_name="Candidate", email="c@x.test", roles=["candidate"])

    crit = [{"id": "problem_solving", "name": "Problem Solving", "weight": 0.5},
            {"id": "communication", "name": "Communication", "weight": 0.5}]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://smoke") as c:
        # ── A live workflow with a human round, two candidates on it ────────
        r = await c.post(f"/hr/requisitions/{req}/workflows", json={})
        wf = r.json()["id"]
        r = await c.post(f"/hr/workflows/{wf}/rounds",
                         json={"title": "Panel", "kind": "human_review", "criteria": crit,
                               "deadline_days": 5})
        rnd = r.json()["rounds"][0]["id"]
        await c.post(f"/hr/workflows/{wf}/submit-review", json={"note": None})
        await c.post(f"/admin/workflow-reviews/{wf}/approve", json={"note": None})
        r = await c.post(f"/hr/workflows/{wf}/publish")
        check("the workflow is live", r.status_code == 200, f"{r.status_code} {r.text[:160]}")

        e1, e2, e3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        a1, a2, a3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with factory() as db:
            for a, e, u, name, email in (
                (a1, e1, cand1, "Asha Rao", f"asha@{tag}.test"),
                (a2, e2, cand2, "Ravi Kumar", f"ravi@{tag}.test"),
                (a3, e3, None, "[redacted]", None),
            ):
                await db.execute(text(
                    "INSERT INTO applicants (id,company_id,user_id,full_name,email,target_job_title)"
                    " VALUES (:i,:c,:u,:n,:m,'Platform Engineer')"),
                    {"i": a, "c": cid, "u": u, "n": name, "m": email})
                await db.execute(text(
                    "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                    " target_job_title,workflow_id,current_round_id,created_at,updated_at)"
                    " VALUES (:i,:c,:r,:a,'shortlisted','Platform Engineer',:w,:rd,:n,:n)"),
                    {"i": e, "c": cid, "r": req, "a": a, "w": uuid.UUID(wf), "rd": uuid.UUID(rnd),
                     "n": now})
            await db.commit()

            async def ledger() -> int:
                async with factory() as s:
                    return int(await s.scalar(text(
                        "SELECT count(*) FROM stage_transitions WHERE enrolment_id = ANY(:e)"),
                        {"e": [e1, e2]}) or 0)
        ledger_before = await ledger()

        print("\nPH4-A2 — availability")
        for iv in (iv1, iv2):
            r = await c.post(f"/hr/interviewers/{iv}/availability",
                             json={"starts_at": at(-30), "ends_at": at(450)})
            check("HR sets an interviewer's availability", r.status_code == 201, r.text[:160])
        r = await c.post(f"/hr/interviewers/{iv1}/availability",
                         json={"starts_at": at(400), "ends_at": at(500)})
        check("an overlapping window is refused, in words", r.status_code == 409
              and "overlaps" in r.json().get("detail", ""), r.text[:160])
        r = await c.post(f"/hr/interviewers/{iv1}/availability",
                         json={"starts_at": "2026-12-01T10:00:00", "ends_at": at(600)})
        check("a time without a timezone is refused, not guessed", r.status_code == 422, r.text[:160])

        print("\nPH4-A2 — a loop of several sessions")
        r = await c.post(f"/hr/enrolments/{e1}/loops",
                         json={"title": "Onsite", "candidate_timezone": "Asia/Kolkata",
                               "buffer_minutes": 15})
        loop1 = r.json().get("id")
        check("HR creates a loop for the candidate", r.status_code == 201 and loop1, r.text[:160])
        r = await c.post(f"/hr/enrolments/{e3}/loops", json={"title": "x"})
        check("nothing is scheduled for an erased candidate", r.status_code == 409, r.text[:160])
        r = await c.post(f"/hr/enrolments/{e1}/loops", json={"candidate_timezone": "Mars/Base"})
        check("an unknown timezone is refused", r.status_code == 422, r.text[:160])

        r = await c.post(f"/hr/loops/{loop1}/sessions",
                         json={"round_id": rnd, "title": "System design", "duration_minutes": 60,
                               "interviewer_user_ids": [str(iv1)], "starts_at": at(0),
                               "location": "Room 4"})
        check("a session with its own interviewer and duration", r.status_code == 201, r.text[:200])
        sess = {s["title"]: s for s in r.json()["sessions"]}
        s_a = sess["System design"]
        check("the session carries its interviewer's scorecard",
              s_a["interviewers"][0]["scorecard_id"] is not None, str(s_a))

        r = await c.post(f"/hr/loops/{loop1}/sessions",
                         json={"round_id": rnd, "title": "Coding", "duration_minutes": 60,
                               "interviewer_user_ids": [str(iv2)], "starts_at": at(30)})
        check("an overlapping session for the candidate is refused", r.status_code == 409
              and "candidate" in r.json()["detail"].lower(), r.text[:200])
        r = await c.post(f"/hr/loops/{loop1}/sessions",
                         json={"round_id": rnd, "title": "Coding", "duration_minutes": 60,
                               "interviewer_user_ids": [str(iv2)], "starts_at": at(65)})
        check("a session inside the buffer is refused", r.status_code == 409, r.text[:200])
        r = await c.post(f"/hr/loops/{loop1}/sessions",
                         json={"round_id": rnd, "title": "Coding", "duration_minutes": 60,
                               "interviewer_user_ids": [str(iv2)], "starts_at": at(75)})
        check("a second session the same day, after the gap", r.status_code == 201, r.text[:200])
        s_b = next(s for s in r.json()["sessions"] if s["title"] == "Coding")

        r = await c.post(f"/hr/loops/{loop1}/sessions",
                         json={"round_id": rnd, "title": "Late chat", "duration_minutes": 30,
                               "interviewer_user_ids": [str(iv1)], "starts_at": at(600)})
        check("a time the interviewer has not offered needs HR to say so",
              r.status_code == 409 and "available" in r.json()["detail"], r.text[:200])
        r = await c.post(f"/hr/loops/{loop1}/sessions",
                         json={"round_id": rnd, "title": "Late chat", "duration_minutes": 30,
                               "interviewer_user_ids": [str(iv1)], "starts_at": at(600),
                               "allow_outside_availability": True})
        check("…and is allowed when HR does", r.status_code == 201, r.text[:200])
        s_c = next(s for s in r.json()["sessions"] if s["title"] == "Late chat")

        r = await c.post(f"/hr/enrolments/{e2}/loops", json={"title": "Ravi"})
        loop_r = r.json()["id"]
        r = await c.post(f"/hr/loops/{loop_r}/sessions",
                         json={"round_id": rnd, "duration_minutes": 45,
                               "interviewer_user_ids": [str(iv1)], "starts_at": at(15)})
        check("an interviewer cannot be double-booked across candidates",
              r.status_code == 409 and "interviewer" in r.json()["detail"].lower(), r.text[:200])

        print("\nPH4-A2 — the itinerary")
        r = await c.post(f"/hr/loops/{loop1}/send")
        check("HR sends the loop", r.status_code == 200 and r.json()["sent"] == "itinerary",
              r.text[:200])
        r = await c.get(f"/hr/loops/{loop1}")
        check("the loop has an overall status", r.json()["status"] == "scheduled", r.text[:120])
        check("each session has its own status",
              {s["status"] for s in r.json()["sessions"]} == {"scheduled"}, r.text[:200])
        async with factory() as db:
            mails = (await db.execute(text(
                "SELECT template, lang, subject FROM email_events WHERE related_id = :l"),
                {"l": uuid.UUID(loop1)})).all()
            notes = await db.scalar(text(
                "SELECT count(*) FROM notifications WHERE user_id = ANY(:u)"
                " AND kind = 'interview_scheduled'"), {"u": [iv1, iv2]})
        check("the candidate gets ONE itinerary, in their language",
              len(mails) == 1 and mails[0][0] == "interview_itinerary" and mails[0][1] == "hi",
              str(mails))
        check("the interviewers are told, with the time", (notes or 0) >= 3, str(notes))

        acting["cand"] = cand1
        r = await c.get("/users/me/interview-loops")
        mine = r.json()
        check("the candidate sees the whole loop from their applications",
              len(mine) == 1 and len(mine[0]["sessions"]) == 3, r.text[:200])
        check("…with interviewer names but no scorecards",
              "scorecard" not in r.text and "Ivan One" in r.text, r.text[:200])
        r = await c.get(f"/users/me/interview-loops/{loop1}/calendar.ics")
        check("a calendar file for the schedule",
              r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
              and r.text.count("BEGIN:VEVENT") == 3 and "Z\r\n" in r.text, r.text[:200])
        r = await c.post(f"/users/me/interview-loops/{loop1}/book",
                         json={"session_id": s_a["id"], "starts_at": at(200)})
        check("a candidate cannot move a booked interview", r.status_code == 409, r.text[:160])
        r = await c.patch(f"/users/me/interview-loops/{loop1}/sessions/{s_a['id']}",
                          json={"starts_at": at(200)})
        check("there is no candidate route to reschedule", r.status_code in (404, 405),
              str(r.status_code))
        acting["cand"] = cand2
        r = await c.get(f"/users/me/interview-loops/{loop1}/calendar.ics")
        check("another candidate cannot read this schedule", r.status_code == 404, str(r.status_code))

        print("\nPH4-A2 — HR changes and cancels")
        r = await c.patch(f"/hr/sessions/{s_b['id']}", json={"starts_at": at(90)})
        check("HR moves a session", r.status_code == 200, r.text[:200])
        async with factory() as db:
            due = await db.scalar(text(
                "SELECT sc.due_at FROM interview_session_interviewers si"
                " JOIN interviewer_scorecards sc ON sc.id = si.scorecard_id"
                " WHERE si.session_id = :s"), {"s": uuid.UUID(s_b["id"])})
            moved = await db.scalar(text(
                "SELECT count(*) FROM email_events WHERE related_id = :s"
                " AND template = 'interview_session_update'"), {"s": uuid.UUID(s_b["id"])})
        r = await c.get(f"/hr/loops/{loop1}/calendar.ics")
        sequences = set(re.findall(r"SEQUENCE:(\d+)", r.text))
        check("moving a sent session raises the calendar SEQUENCE, so calendars update",
              r.status_code == 200 and sequences and min(int(x) for x in sequences) >= 2,
              str(sequences))
        check("the scorecard is due after the NEW time",
              due == base + timedelta(minutes=150) + timedelta(days=2), str(due))
        check("the candidate is told the new time", moved == 1, str(moved))
        r = await c.post(f"/hr/sessions/{s_c['id']}/outcome",
                         json={"outcome": "cancelled", "reason": "Interviewer unwell"})
        check("HR cancels a session", r.status_code == 200 and r.json()["loop_status"] == "scheduled",
              r.text[:200])
        r = await c.post(f"/hr/sessions/{s_a['id']}/outcome", json={"outcome": "completed"})
        check("a session cannot be marked done before it starts", r.status_code == 409, r.text[:120])
        async with factory() as db:
            audits = dict((await db.execute(text(
                "SELECT action, count(*) FROM audit_log WHERE (action LIKE 'session.%'"
                "   OR action LIKE 'loop.%' OR action LIKE 'availability.%')"
                "  AND (details->>'company_id') = :c GROUP BY action"),
                {"c": str(cid)})).all())
            reason_leak = await db.scalar(text(
                "SELECT count(*) FROM audit_log WHERE details::text LIKE '%unwell%'"))
        check("every scheduling change is audited",
              {"loop.created", "session.created", "loop.sent", "session.rescheduled",
               "session.cancelled", "availability.added"} <= set(audits), str(audits))
        check("…without the words of a reason", reason_leak == 0, str(reason_leak))

        print("\nPH4-A2 — the candidate picks their own time")
        r = await c.post(f"/hr/enrolments/{e2}/loops", json={"title": "Pick", "self_schedule": True})
        loop2 = r.json()["id"]
        r = await c.post(f"/hr/loops/{loop2}/sessions",
                         json={"round_id": rnd, "title": "Panel chat", "duration_minutes": 45,
                               "interviewer_user_ids": [str(iv1), str(iv2)]})
        check("a session can wait for the candidate's choice",
              r.status_code == 201 and r.json()["sessions"][0]["status"] == "awaiting_slot",
              r.text[:200])
        s_p = r.json()["sessions"][0]
        r = await c.post(f"/hr/loops/{loop2}/send")
        check("the candidate is asked to choose", r.json().get("sent") == "slot_request", r.text[:200])
        acting["cand"] = cand2
        r = await c.get(f"/users/me/interview-loops/{loop2}/sessions/{s_p['id']}/slots")
        slots = r.json()["slots"]
        busy = {at(m) for m in (0, 30)}  # iv1 is in System design 04:00–05:00
        check("offered slots avoid both interviewers' bookings",
              bool(slots) and not (busy & set(slots)), str(slots[:5]))
        acting["cand"] = cand1
        r = await c.get(f"/users/me/interview-loops/{loop2}/sessions/{s_p['id']}/slots")
        check("nobody else can see those slots", r.status_code == 404, str(r.status_code))
        acting["cand"] = cand2
        r = await c.post(f"/users/me/interview-loops/{loop2}/book",
                         json={"session_id": s_p["id"], "starts_at": at(0),
                               "timezone": "Europe/London"})
        check("a time that was never offered cannot be booked", r.status_code == 409, r.text[:160])
        r = await c.post(f"/users/me/interview-loops/{loop2}/book",
                         json={"session_id": s_p["id"], "starts_at": slots[0],
                               "timezone": "Europe/London"})
        check("the candidate books an offered slot",
              r.status_code == 200 and r.json()["all_booked"], r.text[:200])
        r = await c.post(f"/users/me/interview-loops/{loop2}/book",
                         json={"session_id": s_p["id"], "starts_at": slots[1]})
        check("…once", r.status_code == 409, r.text[:160])
        async with factory() as db:
            tz = await db.scalar(text("SELECT candidate_timezone FROM interview_loops WHERE id = :l"),
                                 {"l": uuid.UUID(loop2)})
            itin = await db.scalar(text(
                "SELECT count(*) FROM email_events WHERE related_id = :l"
                " AND template = 'interview_itinerary'"), {"l": uuid.UUID(loop2)})
        check("the candidate's own timezone is captured", tz == "Europe/London", str(tz))
        check("booking the last slot sends the coordinated itinerary", itin == 1, str(itin))
        r = await c.post(f"/hr/loops/{loop2}/sessions",
                         json={"round_id": rnd, "title": "Spare", "duration_minutes": 30,
                               "interviewer_user_ids": [str(iv2)]})
        spare = next(x for x in r.json()["sessions"] if x["title"] == "Spare")
        r = await c.post(f"/hr/sessions/{spare['id']}/outcome", json={"outcome": "cancelled"})
        check("a session still awaiting its slot can be cancelled", r.status_code == 200,
              r.text[:200])

        # A loop whose every session is cancelled one by one is itself cancelled.
        r = await c.post(f"/hr/enrolments/{e1}/loops", json={"title": "Short-lived"})
        lone = r.json()["id"]
        r = await c.post(f"/hr/loops/{lone}/sessions",
                         json={"round_id": rnd, "title": "Only one", "duration_minutes": 30,
                               "interviewer_user_ids": [str(iv2)], "starts_at": at(1500),
                               "allow_outside_availability": True})
        only = r.json()["sessions"][0]
        r = await c.post(f"/hr/sessions/{only['id']}/outcome", json={"outcome": "cancelled"})
        check("a loop left with no session to hold is cancelled, not 'scheduled' forever",
              r.status_code == 200 and r.json()["loop_status"] == "cancelled", r.text[:200])

        # Cancelling releases a scorecard only that interview was holding open;
        # one another live session still needs is kept.
        r = await c.post(f"/hr/enrolments/{e1}/loops", json={"title": "Guest panel"})
        guest = r.json()["id"]
        r = await c.post(f"/hr/loops/{guest}/sessions",
                         json={"round_id": rnd, "title": "Guest chat", "duration_minutes": 30,
                               "interviewer_user_ids": [str(iv3)], "starts_at": at(1700),
                               "allow_outside_availability": True})
        g = r.json()["sessions"][0]
        g_card = g["interviewers"][0]["scorecard_id"]
        r = await c.post(f"/hr/sessions/{g['id']}/outcome", json={"outcome": "cancelled"})
        async with factory() as db:
            g_state = await db.scalar(text(
                "SELECT status FROM interviewer_scorecards WHERE id = :i"), {"i": uuid.UUID(g_card)})
            kept = await db.scalar(text(
                "SELECT sc.status FROM interview_session_interviewers si"
                " JOIN interviewer_scorecards sc ON sc.id = si.scorecard_id"
                " WHERE si.session_id = :s"), {"s": uuid.UUID(only["id"])})
            told = await db.scalar(text(
                "SELECT count(*) FROM notifications WHERE user_id = :u"
                " AND title = 'An interview assignment was withdrawn'"), {"u": iv3})
        check("cancelling an interview releases the scorecard only it was holding",
              r.status_code == 200 and g_state == "withdrawn", f"{r.status_code} {g_state}")
        check("…and tells that interviewer", told == 1, str(told))
        check("a scorecard another live session still needs is kept", kept == "assigned",
              str(kept))

        # An AI interview's schedule is part of the same record (A2 #25).
        r = await c.post("/hr/interviews", json={"applicant_id": str(a2), "enrolment_id": str(e2),
                                                 "scheduled_at": at(2000)})
        invite = r.json().get("invite_id")
        check("HR invites the candidate to an AI interview", r.status_code == 201, r.text[:160])
        r = await c.patch(f"/hr/interviews/{invite}", json={"scheduled_at": at(2100)})
        check("…moves it", r.status_code == 200, r.text[:160])
        r = await c.post(f"/hr/interviews/{invite}/revoke")
        check("…and revokes it", r.status_code == 200, r.text[:160])
        async with factory() as db:
            invite_audit = {row[0]: row[1] for row in (await db.execute(text(
                "SELECT action, details FROM audit_log WHERE resource_id = :i"
                " AND action LIKE 'interview_invite.%'"), {"i": uuid.UUID(invite)})).all()}
        check("creating, moving and revoking an AI interview are each audited",
              set(invite_audit) == {"interview_invite.created", "interview_invite.rescheduled",
                                    "interview_invite.revoked"}, str(sorted(invite_audit)))
        check("…against this company, with where the time moved from and to",
              all(d.get("company_id") == str(cid) for d in invite_audit.values())
              and invite_audit.get("interview_invite.rescheduled", {}).get("to", "")[:16]
              == at(2100)[:16], str(invite_audit.get("interview_invite.rescheduled")))

        print("\nPH4-A2 — interviewers see their own")
        acting["iv"] = iv2
        r = await c.get("/interviewer/sessions",
                        params={"start": at(-120), "end": at(2000)})
        titles = {s["title"] for s in r.json()}
        check("an interviewer sees only the sessions they sit on",
              titles == {"Coding", "Panel chat"}, str(titles))
        r = await c.post("/interviewer/availability", json={"starts_at": at(1440),
                                                            "ends_at": at(1500)})
        check("an interviewer sets their own availability", r.status_code == 201, r.text[:160])
        async with factory() as db:
            other_window = await db.scalar(text(
                "SELECT id FROM interviewer_availability WHERE user_id = :u LIMIT 1"), {"u": iv1})
        r = await c.delete(f"/interviewer/availability/{other_window}")
        check("…but cannot remove anyone else's", r.status_code == 404, str(r.status_code))

        print("\nPH4-O5 — workload")
        r = await c.put(f"/hr/interviewers/{iv1}/capacity", json={"max_sessions_per_day": 1})
        check("HR sets an interviewer's daily limit", r.status_code == 200, r.text[:160])
        r = await c.get("/hr/panel/workload", params={"start": at(-120), "end": at(2880)})
        rows = {x["user_id"]: x for x in r.json()["interviewers"]}
        one = rows[str(iv1)]
        check("workload counts sessions and open scorecards",
              one["sessions"] == 2 and one["open_scorecards"] >= 2, str(one))
        check("over-allocation is flagged, not refused", "over_allocated" in one["flags"], str(one))
        check("a panel session counts for each interviewer",
              rows[str(iv2)]["sessions"] == 2, str(rows[str(iv2)]))
        check("a cancelled session no longer counts against availability",
              one["outside_availability"] == 0, str(one))

        print("\nPH4-O5 — calibration")
        async with factory() as db:
            cards = dict((await db.execute(text(
                "SELECT interviewer_user_id, id FROM interviewer_scorecards"
                " WHERE enrolment_id = :e AND status = 'assigned'"), {"e": e1})).all())
        # The HR manager joins this candidate's panel BEFORE anyone submits, so
        # until they submit their own they must not see their peers' scores.
        r = await c.post(f"/hr/enrolments/{e1}/scorecards",
                         json={"round_id": rnd, "interviewer_user_ids": [str(hr)]})
        check("an HR manager can sit on the panel", r.status_code == 201, r.text[:160])
        hr_card = r.json()["created"][0]["scorecard_id"]
        for who, score in ((iv1, 5), (iv2, 3)):
            acting["iv"] = who
            r = await c.post(f"/interviewer/scorecards/{cards[who]}/submit",
                             json={"scores": [{"competency_id": k["id"], "score": score}
                                              for k in crit], "summary": "ok"})
            check(f"an interviewer submits ({score})", r.status_code == 200, r.text[:160])
        async with factory() as db:
            before = await db.scalar(text(
                "SELECT count(*) FROM interviewer_scorecard_scores WHERE scorecard_id = ANY(:s)"),
                {"s": list(cards.values())})
        r = await c.get("/hr/panel/calibration", params={"requisition_id": str(req)})
        check("a panellist who has not submitted sees none of their peers' scores",
              r.status_code == 200 and r.json()["interviewers"] == [], r.text[:200])
        acting["iv"] = hr
        r = await c.post(f"/interviewer/scorecards/{hr_card}/submit",
                         json={"scores": [{"competency_id": k["id"], "score": 4} for k in crit],
                               "summary": "ok"})
        check("the HR manager submits their own", r.status_code == 200, r.text[:160])
        r = await c.get("/hr/panel/calibration", params={"requisition_id": str(req)})
        report = {x["user_id"]: x for x in r.json()["interviewers"]}
        one = report.get(str(iv1), {})
        check("with one candidate, every figure is withheld — it would BE their score",
              one.get("suppressed") is True and one.get("mean") is None
              and one.get("mean_delta") is None and one.get("by_competency") == {}
              and one.get("distribution") is None and one.get("candidates") == 1, str(one))
        check("the report says how few candidates it rests on",
              r.json()["rules"]["min_candidates"] == 5, str(r.json()["rules"]))
        check("calibration never names a candidate", "Asha" not in r.text and str(e1) not in r.text)
        narrow = await c.get("/hr/panel/calibration",
                             params={"start": at(-60), "end": at(60), "round_id": rnd})
        check("a narrow window cannot isolate a candidate", narrow.status_code == 422,
              narrow.text[:160])
        year = await c.get("/hr/panel/calibration",
                           params={"start": at(-180 * 24 * 60), "end": at(0)})
        check("calibration looks back 180 days, as the panel offers", year.status_code == 200,
              year.text[:160])
        async with factory() as db:
            after = await db.scalar(text(
                "SELECT count(*) FROM interviewer_scorecard_scores WHERE scorecard_id = ANY(:s)"),
                {"s": list(cards.values())})
            viewed = await db.scalar(text(
                "SELECT count(*) FROM audit_log WHERE action = 'panel.calibration.viewed'"
                " AND resource_id = :c"), {"c": cid})
            status = dict((await db.execute(text(
                "SELECT id, status FROM enrolments WHERE id = ANY(:e)"), {"e": [e1, e2]})).all())
        check("submitted scorecards are untouched by calibration", before == after == 4,
              f"{before} {after}")
        # Three reports were served: the full panel, one candidate, and 180 days.
        check("looking at calibration is audited, every time", viewed == 3, str(viewed))
        check("nothing in scheduling or calibration moved a candidate",
              await ledger() == ledger_before and set(status.values()) == {"shortlisted"},
              str(status))

        print("\nPH4-A2 — a final decision closes the schedule")
        r = await c.post(f"/hr/loops/{loop2}/sessions",
                         json={"round_id": rnd, "title": "Follow-up", "duration_minutes": 30,
                               "interviewer_user_ids": [str(iv1)]})
        follow = next(x for x in r.json()["sessions"] if x["title"] == "Follow-up")
        r = await c.post(f"/hr/enrolments/{e2}/decision",
                         json={"decision": "rejected", "reason": "Not the right fit",
                               "reason_code": "role_fit"})
        check("HR records a final decision", r.status_code == 200, r.text[:200])
        async with factory() as db:
            left = await db.scalar(text(
                "SELECT count(*) FROM interview_sessions WHERE enrolment_id = :e"
                " AND status IN ('scheduled', 'awaiting_slot')"), {"e": e2})
            loop_status = await db.scalar(text(
                "SELECT status FROM interview_loops WHERE id = :l"), {"l": uuid.UUID(loop2)})
            told = await db.scalar(text(
                "SELECT count(*) FROM notifications WHERE user_id = ANY(:u)"
                " AND title LIKE 'Interview cancelled: a decision%'"), {"u": [iv1, iv2]})
        check("…which cancels every interview not yet held", left == 0, str(left))
        check("…closes the loop", loop_status == "cancelled", str(loop_status))
        check("…and tells the interviewers", (told or 0) >= 2, str(told))
        acting["cand"] = cand2
        r = await c.get(f"/users/me/interview-loops/{loop2}/sessions/{follow['id']}/slots")
        check("a decided candidate is offered no slots", r.json().get("slots") == [], r.text[:160])
        r = await c.post(f"/users/me/interview-loops/{loop2}/book",
                         json={"session_id": follow["id"], "starts_at": at(900)})
        check("…and cannot book one", r.status_code == 409, r.text[:160])

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
