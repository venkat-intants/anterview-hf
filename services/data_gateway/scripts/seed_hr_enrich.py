"""Populate EVERY HR company's dashboard with realistic demo data.

The main demo seed (seed_demo.py) only fills 'Acme Technologies'. Other companies
created during the session (e.g. an HR who signed up their own company) show an
empty HR console — no ATS scores, no interviews, no activity. This script gives
each such company a full hiring story so whichever HR account is used looks alive:

  • 9 ATS-scored applicants spread across the pipeline (new → shortlisted →
    interviewed → hired → rejected)
  • exam attempts (some passed) against the company's first exam
  • 3 completed avatar interviews (guest user + session + scorecard + invite)
  • a notification feed for each HR manager

Idempotent (deterministic uuid5 ids). Skips 'acme-technologies-demo' (already rich).
Run:  ./.venv/Scripts/python.exe scripts/seed_hr_enrich.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ENV = pathlib.Path(__file__).resolve().parents[1] / ".env"
_env: dict[str, str] = {}
for _line in ENV.read_text(encoding="utf-8").splitlines():
    _m = re.match(r"^([A-Z0-9_]+)=(.*)$", _line.strip())
    if _m:
        _env[_m.group(1)] = _m.group(2)
DATABASE_URL = _env["DATABASE_URL"]
DATABASE_SSL = _env.get("DATABASE_SSL", "")

NS = uuid.uuid5(uuid.NAMESPACE_DNS, "demo.intants.com")
NOW = datetime.now(tz=UTC)
TOKEN = "demo-token-" + "0" * 28


def did(key: str) -> uuid.UUID:
    return uuid.uuid5(NS, key)


def ago(days: float = 0, minutes: float = 0) -> datetime:
    return NOW - timedelta(days=days, minutes=minutes)


# (name, email, title, level, ats, status, recommendation)
ROSTER = [
    ("Aisha Khan", "aisha.khan@example.com", "Frontend Engineer", "mid", 88, "shortlisted", "strong_yes"),
    ("Vivek Iyer", "vivek.iyer@example.com", "Backend Engineer", "senior", 91, "interviewed", "strong_yes"),
    ("Sneha Reddy", "sneha.reddy@example.com", "Data Analyst", "mid", 76, "shortlisted", "yes"),
    ("Rahul Gupta", "rahul.gupta@example.com", "Frontend Engineer", "entry", 64, "new", "maybe"),
    ("Ananya Singh", "ananya.singh@example.com", "Product Manager", "senior", 83, "interviewed", "yes"),
    ("Mohammed Ali", "mohammed.ali@example.com", "DevOps Engineer", "mid", 72, "new", "maybe"),
    ("Pooja Patel", "pooja.patel@example.com", "Data Analyst", "entry", 58, "rejected", "no"),
    ("Karthik Rao", "karthik.rao@example.com", "Backend Engineer", "mid", 80, "hired", "strong_yes"),
    ("Divya Nair", "divya.nair@example.com", "Frontend Engineer", "mid", 69, "shortlisted", "maybe"),
]
INTERVIEW_PLAN = [(1, 8.2, 6), (4, 7.0, 4), (7, 8.6, 11)]  # roster idx, composite, days ago
EXAM_PLAN = [(0, 88), (8, 75), (3, 50)]  # roster idx, score%


async def enrich(c: object, cid: uuid.UUID, slug: str, hr_id: uuid.UUID, job_id: uuid.UUID) -> None:
    pfx = str(cid)

    # ---- exam to attach attempts to (first one in the company, if any) ----
    exam_row = (await c.execute(  # type: ignore[attr-defined]
        text("SELECT id FROM exams WHERE company_id=:c AND deleted_at IS NULL ORDER BY created_at LIMIT 1"),
        {"c": str(cid)},
    )).fetchone()
    exam_id = exam_row[0] if exam_row else None

    appl_ids: list[uuid.UUID] = []
    for i, (name, email, title, level, ats, status, rec) in enumerate(ROSTER):
        aid = did(f"{pfx}:appl:{i}")
        appl_ids.append(aid)
        breakdown = {"skills_match": min(100, ats + 4), "experience": max(30, ats - 8),
                     "education": min(100, ats + 2), "projects": max(35, ats - 5)}
        await c.execute(  # type: ignore[attr-defined]
            text("INSERT INTO applicants (id, company_id, created_by_user_id, full_name, email,"
                 " target_job_title, target_level, status, ats_overall, ats_breakdown, ats_strengths,"
                 " ats_concerns, ats_recommendation, ats_summary, resume_text, created_at, updated_at)"
                 " VALUES (:id,:c,:hr,:name,:email,:title,:level,:status,:ats,CAST(:bd AS jsonb),"
                 " CAST(:st AS jsonb),CAST(:cn AS jsonb),:rec,:sum,:resume,:ts,:ts)"
                 " ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status, ats_overall=EXCLUDED.ats_overall,"
                 " ats_breakdown=EXCLUDED.ats_breakdown, updated_at=EXCLUDED.updated_at"),
            {"id": str(aid), "c": str(cid), "hr": str(hr_id), "name": name, "email": email,
             "title": title, "level": level, "status": status, "ats": ats,
             "bd": json.dumps(breakdown),
             "st": json.dumps(["Relevant projects", "Clear communication", f"Solid {title} basics"]),
             "cn": json.dumps(["Limited large-scale experience"] if ats < 80 else ["Long notice period"]),
             "rec": rec, "sum": f"{name} scores {ats}/100 for {title} ({level}).",
             "resume": f"{name} — {level} {title}.", "ts": ago(days=14 - i)},
        )

    # ---- exam attempts (only if the company has an exam) ----
    if exam_id is not None:
        for n, (idx, pct) in enumerate(EXAM_PLAN):
            passed = pct >= 60
            await c.execute(  # type: ignore[attr-defined]
                text("INSERT INTO exam_attempts (id, company_id, exam_id, applicant_id, attempt_no,"
                     " score_raw, score_max, score_percent, passed, status, started_at, submitted_at,"
                     " created_at, updated_at) VALUES (:id,:c,:e,:a,1,:sr,10,:pct,:p,'submitted',"
                     " :start,:sub,:sub,:sub) ON CONFLICT (id) DO UPDATE SET score_percent=EXCLUDED.score_percent,"
                     " passed=EXCLUDED.passed"),
                {"id": str(did(f"{pfx}:attempt:{n}")), "c": str(cid), "e": str(exam_id),
                 "a": str(appl_ids[idx]), "sr": round(pct / 10), "pct": pct, "p": passed,
                 "start": ago(days=9 - n, minutes=18), "sub": ago(days=9 - n)},
            )

    # ---- completed interviews (guest user + session + scorecard + invite) ----
    for n, (idx, composite, d) in enumerate(INTERVIEW_PLAN):
        name, email = ROSTER[idx][0], ROSTER[idx][1]
        guest_id = did(f"{pfx}:guest:{n}")
        await c.execute(  # type: ignore[attr-defined]
            text("INSERT INTO users (id, email, full_name, preferred_language, is_active,"
                 " must_change_password, company_id, headline, bio, employment_status, desired_roles,"
                 " location, created_at, updated_at) VALUES (:id,:email,:name,'en',true,false,:c,"
                 " :hl,:bio,'employed',:dr,'India',:ts,:ts) ON CONFLICT (email) DO UPDATE SET"
                 " full_name=EXCLUDED.full_name, company_id=EXCLUDED.company_id"),
            {"id": str(guest_id), "email": f"guest+{slug}+{email}", "name": name, "c": str(cid),
             "hl": f"{ROSTER[idx][2]} · interviewed", "bio": f"Interviewed for {ROSTER[idx][2]}. Strong examples.",
             "dr": ROSTER[idx][2], "ts": ago(days=d + 1)},
        )
        real_guest = (await c.execute(  # type: ignore[attr-defined]
            text("SELECT id FROM users WHERE email=:e"), {"e": f"guest+{slug}+{email}"})).scalar_one()
        await c.execute(  # type: ignore[attr-defined]
            text("INSERT INTO user_roles (user_id, role_id, assigned_at) SELECT :u, id, :ts FROM roles"
                 " WHERE name='guest_candidate' ON CONFLICT DO NOTHING"),
            {"u": str(real_guest), "ts": ago(days=d + 1)},
        )
        sid = did(f"{pfx}:sess:{n}")
        await c.execute(  # type: ignore[attr-defined]
            text("INSERT INTO sessions (id, user_id, job_id, language, status, started_at, completed_at,"
                 " duration_seconds, metadata, presenter_id, created_at, updated_at) VALUES (:id,:u,:j,"
                 " 'en','completed',:start,:done,600,'{}'::jsonb,'raj',:start,:start)"
                 " ON CONFLICT (id) DO UPDATE SET status='completed'"),
            {"id": str(sid), "u": str(real_guest), "j": str(job_id),
             "start": ago(days=d, minutes=12), "done": ago(days=d)},
        )
        scores = {"communication": min(10, round(composite + 0.4)), "technical": max(3, round(composite - 0.6)),
                  "problem_solving": round(composite), "confidence": min(10, round(composite + 0.2))}
        await c.execute(  # type: ignore[attr-defined]
            text("INSERT INTO scorecards (scorecard_id, session_id, scores, composite_score, strengths,"
                 " improvements, summary, lang, scorer_model, scorer_version, created_at) VALUES (:id,:sid,"
                 " CAST(:sc AS jsonb),:comp,CAST(:st AS jsonb),CAST(:imp AS jsonb),:sum,'en',"
                 " 'gemini-flash-lite-latest','1.0',:ts) ON CONFLICT (session_id) DO UPDATE SET"
                 " composite_score=EXCLUDED.composite_score"),
            {"id": str(did(f"scorecard:{sid}")), "sid": str(sid), "sc": json.dumps(scores),
             "comp": round(composite, 2), "st": json.dumps(["Clear answers", "Good examples", "Handles follow-ups"]),
             "imp": json.dumps([{"area": "Depth", "suggestion": "Quantify impact with metrics."}]),
             "sum": f"Composite {composite:.1f}/10.", "ts": ago(days=d)},
        )
        await c.execute(  # type: ignore[attr-defined]
            text("INSERT INTO interview_invites (id, company_id, applicant_id, job_id, guest_user_id,"
                 " session_id, created_by_user_id, token_hash, language, avatar_id, expires_at, scheduled_at,"
                 " consumed_at, status, created_at, updated_at) VALUES (:id,:c,:a,:j,:g,:sid,:hr,:tok,'en',"
                 " 'raj',:exp,:sched,:done,'completed',:ts,:ts) ON CONFLICT (id) DO UPDATE SET status='completed'"),
            {"id": str(did(f"{pfx}:invite:{n}")), "c": str(cid), "a": str(appl_ids[idx]), "j": str(job_id),
             "g": str(real_guest), "sid": str(sid), "hr": str(hr_id), "tok": TOKEN + slug + str(n),
             "exp": ago(days=d - 2), "sched": ago(days=d, minutes=20), "done": ago(days=d), "ts": ago(days=d + 1)},
        )
        # link the applicant to the candidate user (HR "View full profile")
        await c.execute(  # type: ignore[attr-defined]
            text("UPDATE applicants SET user_id=:g WHERE id=:a"),
            {"g": str(real_guest), "a": str(appl_ids[idx])},
        )

    # ---- notifications for every HR manager in this company ----
    hr_rows = (await c.execute(  # type: ignore[attr-defined]
        text("SELECT u.id FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r"
             " ON r.id=ur.role_id AND r.name='hr_manager' WHERE u.company_id=:c AND u.deleted_at IS NULL"),
        {"c": str(cid)},
    )).all()
    feed = [
        ("applicant_scored", "New applicant scored", "Vivek Iyer scored 91/100 for Backend Engineer.", "/hr/applicants", 6, False),
        ("interview_completed", "Interview completed", "Karthik Rao finished the avatar interview — 8.6/10.", "/hr/interviews", 11, False),
        ("invite_sent", "Interview invite sent", "Invite sent to Aisha Khan (Frontend Engineer).", "/hr/interviews", 1, True),
    ]
    for (uid_row,) in hr_rows:
        for i, (kind, title, body, link, d, read) in enumerate(feed):
            await c.execute(  # type: ignore[attr-defined]
                text("INSERT INTO notifications (id, user_id, kind, title, body, link, read_at, created_at)"
                     " VALUES (:id,:u,:k,:t,:b,:l,:r,:ts) ON CONFLICT (id) DO NOTHING"),
                {"id": str(did(f"{pfx}:notif:{uid_row}:{i}")), "u": str(uid_row), "k": kind, "t": title,
                 "b": body, "l": link, "r": ago(days=d) if read else None, "ts": ago(days=d)},
            )


async def main() -> None:
    connect_args = {"ssl": DATABASE_SSL, "statement_cache_size": 0} if DATABASE_SSL else {}
    eng = create_async_engine(DATABASE_URL, connect_args=connect_args)
    async with eng.begin() as c:
        job_id = (await c.execute(
            text("SELECT id FROM jobs WHERE is_active = true ORDER BY created_at LIMIT 1"))).scalar()
        if job_id is None:
            print("No active job found — cannot seed interviews. Aborting.")
            return
        companies = (await c.execute(text(
            "SELECT DISTINCT co.id, co.name, co.slug, "
            " (SELECT u.id FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r"
            "  ON r.id=ur.role_id AND r.name='hr_manager' WHERE u.company_id=co.id AND u.deleted_at IS NULL"
            "  ORDER BY u.created_at LIMIT 1) AS hr_id"
            " FROM companies co WHERE co.deleted_at IS NULL AND co.slug <> 'acme-technologies-demo'"
            " AND EXISTS (SELECT 1 FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r"
            "   ON r.id=ur.role_id AND r.name='hr_manager' WHERE u.company_id=co.id AND u.deleted_at IS NULL)"
            " ORDER BY co.name"))).all()
        for cid, name, slug, hr_id in companies:
            await enrich(c, cid, slug, hr_id, job_id)
            print(f"  enriched: {name} (slug={slug})")
    await eng.dispose()
    print("\nDone — every HR company now has a populated dashboard.")


if __name__ == "__main__":
    asyncio.run(main())
