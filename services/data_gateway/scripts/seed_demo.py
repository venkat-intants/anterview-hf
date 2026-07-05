"""Seed a coherent, presentation-ready demo dataset into the shared DB.

Creates clean, known login accounts for every role and a self-consistent
company story so every dashboard (candidate, HR, company admin, platform
owner) looks alive:

    platform owner  superadmin@demo.intants.com   Demo@12345   (the Intants core)
    company admin   companyadmin@demo.intants.com Demo@12345   (Acme Technologies)
    platform admin  admin.demo@demo.intants.com   Demo@12345
    HR manager      hr@demo.intants.com           Demo@12345   (Acme Technologies)
    HR manager      hr2@demo.intants.com          Demo@12345   (Acme Technologies)
    candidate       candidate@demo.intants.com    Demo@12345

Idempotent: every row uses a deterministic uuid5 id, so re-running upserts the
same rows instead of duplicating. It only ADDS demo rows — it never deletes or
mutates existing data. NOT for production.

Run from the data_gateway dir with its venv:

    ./.venv/Scripts/python.exe scripts/seed_demo.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# ---------------------------------------------------------------------------
# Config — read straight from .env (avoids importing app.config, which can crash
# on env drift when a newly-required Settings field is missing).
# ---------------------------------------------------------------------------
ENV = pathlib.Path(__file__).resolve().parents[1] / ".env"
_env: dict[str, str] = {}
for _line in ENV.read_text(encoding="utf-8").splitlines():
    _m = re.match(r"^([A-Z0-9_]+)=(.*)$", _line.strip())
    if _m:
        _env[_m.group(1)] = _m.group(2)
DATABASE_URL = _env["DATABASE_URL"]
DATABASE_SSL = _env.get("DATABASE_SSL", "")

NS = uuid.uuid5(uuid.NAMESPACE_DNS, "demo.intants.com")


def did(key: str) -> uuid.UUID:
    """Deterministic id for a demo row (stable across re-runs)."""
    return uuid.uuid5(NS, key)


NOW = datetime.now(tz=UTC)


def ago(days: float = 0, hours: float = 0, minutes: float = 0) -> datetime:
    return NOW - timedelta(days=days, hours=hours, minutes=minutes)


PW_HASH = bcrypt.hashpw(b"Demo@12345", bcrypt.gensalt(12)).decode()
TOKEN_HASH = "demo-token-" + "0" * 32  # opaque placeholder; links aren't redeemed in the demo

COMPANY_ID = did("company:acme")

# ---------------------------------------------------------------------------
# Static demo content
# ---------------------------------------------------------------------------

DEMO_JOBS = [
    ("Frontend Engineer", "Build delightful React UIs with TypeScript and a sharp eye for UX.",
     "mid", "en", "technical", "Engineering",
     {"required": ["React", "TypeScript", "CSS", "Testing"], "nice_to_have": ["Vite", "Accessibility"]}),
    ("Backend Engineer", "Design resilient FastAPI services, data models and APIs at scale.",
     "senior", "en", "technical", "Engineering",
     {"required": ["Python", "FastAPI", "PostgreSQL", "System Design"], "nice_to_have": ["Redis", "Kafka"]}),
    ("Data Analyst", "Turn raw product data into clear, decision-ready insight.",
     "mid", "en", "screening", "Analytics",
     {"required": ["SQL", "Statistics", "Dashboards"], "nice_to_have": ["Python", "dbt"]}),
    ("Product Manager", "Own the roadmap, talk to users, ship outcomes not features.",
     "senior", "en", "hr", "Product",
     {"required": ["Discovery", "Prioritisation", "Stakeholder Mgmt"], "nice_to_have": ["SQL", "Analytics"]}),
    ("DevOps Engineer", "Automate delivery, observability and infra-as-code end to end.",
     "mid", "en", "technical", "Platform",
     {"required": ["Docker", "CI/CD", "Kubernetes", "Linux"], "nice_to_have": ["Terraform", "AWS"]}),
]
# index -> deterministic job id
JOB_IDS = [did(f"job:{j[0]}") for j in DEMO_JOBS]

# Applicants: (name, email, target_title, level, ats, status, recommendation)
DEMO_APPLICANTS = [
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

# candidate's own practice interviews: (job_idx, status, composite, days_ago)
CANDIDATE_SESSIONS = [
    (0, "completed", 7.8, 18),
    (1, "completed", 6.9, 14),
    (2, "completed", 8.3, 9),
    (3, "completed", 7.1, 5),
    (4, "completed", 5.8, 2),
    (0, "in_progress", None, 0),
]

FE_QUESTIONS = [
    ("Which hook lets a function component hold local state?",
     ["useState", "useMemo", "useRef", "useContext"], 0),
    ("What does the CSS property `flex: 1` primarily control?",
     ["Grow factor", "Font size", "Z-index", "Opacity"], 0),
    ("In React, what is the correct way to render a list?",
     ["Map with a stable key", "for-loop in JSX", "while-loop", "forEach push"], 0),
    ("Which is a TypeScript utility type?",
     ["Partial<T>", "Maybe<T>", "Option<T>", "Result<T>"], 0),
    ("What triggers a component re-render?",
     ["State or props change", "Console log", "CSS hover", "A comment"], 0),
    ("Which is best for memoising an expensive computed value?",
     ["useMemo", "useEffect", "useState", "useId"], 0),
    ("What does a Promise represent?",
     ["A future value", "A CSS rule", "A DOM node", "A TS interface"], 0),
    ("Accessible buttons should always have…",
     ["A discernible label", "An inline style", "A fixed width", "A z-index"], 0),
]
APT_QUESTIONS = [
    ("If a train travels 60 km in 45 min, its speed is…",
     ["80 km/h", "60 km/h", "45 km/h", "90 km/h"], 0),
    ("Find the odd one out.",
     ["Square", "Circle", "Triangle", "Cube"], 3),
    ("Next in series: 2, 6, 12, 20, …",
     ["30", "28", "26", "24"], 0),
    ("25% of 240 is…", ["60", "48", "72", "50"], 0),
    ("Synonym of 'concise'.", ["Brief", "Vague", "Lengthy", "Loud"], 0),
    ("If MONDAY is coded 123456, what is DAY?", ["456", "356", "256", "654"], 0),
]


async def main() -> None:  # noqa: C901, PLR0915 — a flat seed script reads best top-to-bottom
    connect_args = {"ssl": DATABASE_SSL, "statement_cache_size": 0} if DATABASE_SSL else {}
    eng = create_async_engine(DATABASE_URL, connect_args=connect_args)

    async with eng.begin() as c:
        role_rows = (await c.execute(text("SELECT name, id FROM roles"))).all()
        role_id = {r[0]: r[1] for r in role_rows}

        async def upsert_user(
            key: str, email: str, name: str, role: str, *, company: uuid.UUID | None = None,
            created_days: float = 30, lang: str = "en", linkedin: str | None = None,
            github: str | None = None, resume: str | None = None, with_pw: bool = True,
        ) -> uuid.UUID:
            uid = did(key)
            await c.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, full_name, preferred_language,"
                    " is_active, must_change_password, company_id, linkedin_url, github_url,"
                    " resume_text, created_at, updated_at) VALUES (:id, :email, :pw, :name, :lang,"
                    " true, false, :cid, :li, :gh, :resume, :ts, :ts) "
                    "ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash,"
                    " full_name = EXCLUDED.full_name, company_id = EXCLUDED.company_id,"
                    " is_active = true, must_change_password = false"
                ),
                {"id": str(uid), "email": email, "pw": PW_HASH if with_pw else None,
                 "name": name, "lang": lang, "cid": str(company) if company else None,
                 "li": linkedin, "gh": github, "resume": resume, "ts": ago(days=created_days)},
            )
            # email is the conflict key; fetch the real id (may pre-exist).
            real = (await c.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})).scalar_one()
            await c.execute(
                text("INSERT INTO user_roles (user_id, role_id, assigned_at) VALUES (:u, :r, :ts)"
                     " ON CONFLICT DO NOTHING"),
                {"u": real, "r": role_id[role], "ts": ago(days=created_days)},
            )
            await c.execute(
                text("INSERT INTO dpdp_consent_ledger (id, user_id, consent_type, granted,"
                     " granted_at, purpose, evidence) VALUES (:id, :u, 'account_processing', true,"
                     " :ts, 'registration', CAST(:ev AS jsonb)) ON CONFLICT (id) DO NOTHING"),
                {"id": str(did(f"consent:{key}")), "u": real, "ts": ago(days=created_days),
                 "ev": json.dumps({"version": "1.0", "source": "seed_demo"})},
            )
            return real

        async def set_profile(uid: uuid.UUID, **fields: object) -> None:
            """Set editable profile columns on a demo user (post-migration)."""
            if not fields:
                return
            cols = ", ".join(f"{k} = :{k}" for k in fields)
            await c.execute(
                text(f"UPDATE users SET {cols}, updated_at = now() WHERE id = :uid"),
                {**fields, "uid": str(uid)},
            )

        # ---- Company ----
        await c.execute(
            text("INSERT INTO companies (id, name, slug, is_active, created_at, updated_at)"
                 " VALUES (:id, :name, :slug, true, :ts, :ts)"
                 " ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, is_active = true"),
            {"id": str(COMPANY_ID), "name": "Acme Technologies", "slug": "acme-technologies-demo",
             "ts": ago(days=60)},
        )
        company_id = (await c.execute(
            text("SELECT id FROM companies WHERE slug = 'acme-technologies-demo'"))).scalar_one()

        # ---- Users (one clean login per role) ----
        # platform_owner = the Intants core ("super super admin"); company_id NULL.
        super_id = await upsert_user("user:super", "superadmin@demo.intants.com", "Aarav Mehta", "platform_owner")
        admin_id = await upsert_user("user:admin", "admin.demo@demo.intants.com", "Platform Admin", "admin")
        # super_admin = company super admin (one per company), scoped to Acme.
        company_admin_id = await upsert_user(
            "user:companyadmin", "companyadmin@demo.intants.com", "Neha Kapoor", "super_admin",
            company=company_id)
        hr_id = await upsert_user("user:hr", "hr@demo.intants.com", "Priya Sharma", "hr_manager", company=company_id)
        hr2_id = await upsert_user("user:hr2", "hr2@demo.intants.com", "Karan Nair", "hr_manager", company=company_id)

        # The platform owner is ALSO a platform 'admin' so it can open the
        # analytics dashboards — a "complete" owner (mirrors the production
        # migration which grants support@intants.com both roles).
        await c.execute(
            text("INSERT INTO user_roles (user_id, role_id, assigned_at) VALUES (:u, :r, :ts)"
                 " ON CONFLICT DO NOTHING"),
            {"u": str(super_id), "r": role_id["admin"], "ts": ago(days=30)},
        )
        cand_id = await upsert_user(
            "user:candidate", "candidate@demo.intants.com", "Rohan Verma", "candidate",
            created_days=40, linkedin="https://linkedin.com/in/rohanverma",
            github="https://github.com/rohanverma",
            resume=("Frontend engineer with 4 years building React + TypeScript products. "
                    "Shipped a design system, led migration to Vite, mentors juniors."),
        )
        _ = (super_id, admin_id, company_admin_id)

        # candidate's current resume version (so the Resume page shows a real file,
        # not the empty state) — mirrors users.resume_text.
        await c.execute(
            text("INSERT INTO resumes (id, user_id, filename, resume_text, resume_s3_key,"
                 " is_current, uploaded_at, created_at) VALUES (:id, :uid, :fn, :txt, :key,"
                 " true, :ts, :ts) ON CONFLICT (id) DO NOTHING"),
            {"id": str(did("resume:candidate")), "uid": str(cand_id),
             "fn": "Rohan_Verma_Frontend.pdf",
             "txt": ("Frontend engineer with 4 years building React + TypeScript products. "
                     "Shipped a design system, led migration to Vite, mentors juniors."),
             "key": f"resumes/{cand_id}/{did('resume:candidate')}.pdf", "ts": ago(days=20)},
        )

        # Rich demo profiles (so the profile pages look real).
        await set_profile(
            cand_id,
            headline="Frontend Engineer · React + TypeScript",
            bio=("Frontend engineer with 4 years building React + TypeScript products. "
                 "Shipped a company-wide design system, led the migration to Vite, and "
                 "mentors junior developers. Looking for a senior front-end role."),
            employment_status="employed",
            desired_roles="Frontend Engineer, Full-stack Developer, UI Engineer",
            location="Bengaluru, KA", phone="+91 98765 43210",
        )
        await set_profile(
            hr_id,
            headline="Talent Acquisition Lead",
            bio=("I run structured, fair hiring across engineering and product at Acme "
                 "Technologies. Big on candidate experience and bias-controlled rubrics."),
            official_email="priya.sharma@acme.tech", location="Hyderabad, TS",
            phone="+91 90000 11111",
        )
        await set_profile(
            hr2_id, headline="Technical Recruiter",
            official_email="karan.nair@acme.tech", location="Hyderabad, TS",
        )

        # ---- Jobs ----
        for i, (title, desc, level, lang, itype, dept, comp) in enumerate(DEMO_JOBS):
            await c.execute(
                text("INSERT INTO jobs (id, title, description, level, language, nos_codes,"
                     " competencies, is_active, interview_type, company_name, department,"
                     " created_at, updated_at) VALUES (:id, :title, :desc, :level, :lang,"
                     " CAST(:nos AS text[]), CAST(:comp AS jsonb), true, :itype, 'Acme Technologies', :dept, :ts, :ts)"
                     " ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title,"
                     " description = EXCLUDED.description, is_active = true"),
                {"id": str(JOB_IDS[i]), "title": title, "desc": desc, "level": level, "lang": lang,
                 "nos": [], "comp": json.dumps(comp), "itype": itype, "dept": dept,
                 "ts": ago(days=50 - i)},
            )

        # ---- Applicants ----
        applicant_ids: list[uuid.UUID] = []
        for idx, (name, email, ttitle, level, ats, status, rec) in enumerate(DEMO_APPLICANTS):
            aid = did(f"applicant:{email}")
            applicant_ids.append(aid)
            breakdown = {"skills_match": min(100, ats + 4), "experience": max(30, ats - 8),
                         "education": min(100, ats + 2), "projects": max(35, ats - 5)}
            strengths = ["Relevant project portfolio", "Strong communication", f"Solid {ttitle} fundamentals"]
            concerns = ["Limited large-scale experience"] if ats < 80 else ["Notice period may be long"]
            summary = (f"{name} scores {ats}/100 for {ttitle} ({level}). "
                       f"{'Strong fit — fast-track.' if ats >= 80 else 'Reasonable fit — worth a screen.' if ats >= 65 else 'Below bar for this role.'}")
            await c.execute(
                text("INSERT INTO applicants (id, company_id, created_by_user_id, full_name, email,"
                     " target_job_title, target_level, status, ats_overall, ats_breakdown,"
                     " ats_strengths, ats_concerns, ats_recommendation, ats_summary, resume_text,"
                     " created_at, updated_at) VALUES (:id, :cid, :hr, :name, :email, :title, :level,"
                     " :status, :ats, CAST(:bd AS jsonb), CAST(:st AS jsonb), CAST(:cn AS jsonb), :rec, :sum, :resume, :ts, :ts)"
                     " ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status,"
                     " ats_overall = EXCLUDED.ats_overall, updated_at = EXCLUDED.updated_at"),
                {"id": str(aid), "cid": str(company_id), "hr": str(hr_id), "name": name, "email": email,
                 "title": ttitle, "level": level, "status": status, "ats": ats,
                 "bd": json.dumps(breakdown), "st": json.dumps(strengths), "cn": json.dumps(concerns),
                 "rec": rec, "sum": summary,
                 "resume": f"{name} — {level} {ttitle}. Experienced practitioner with shipped projects.",
                 "ts": ago(days=14 - idx)},
            )

        # ---- Exams + questions ----
        async def make_exam(key: str, title: str, desc: str, ttitle: str, thresh: int,
                            tlimit: int | None, qs: list[tuple], created_days: float) -> uuid.UUID:
            eid = did(key)
            await c.execute(
                text("INSERT INTO exams (id, company_id, created_by_user_id, title, description,"
                     " target_job_title, pass_threshold, time_limit_seconds, allow_retake, status,"
                     " created_at, updated_at) VALUES (:id, :cid, :hr, :title, :desc, :tt, :th,"
                     " :tl, false, 'published', :ts, :ts) ON CONFLICT (id) DO UPDATE SET"
                     " title = EXCLUDED.title, status = 'published'"),
                {"id": str(eid), "cid": str(company_id), "hr": str(hr_id), "title": title,
                 "desc": desc, "tt": ttitle, "th": thresh, "tl": tlimit, "ts": ago(days=created_days)},
            )
            for pos, (prompt, options, correct) in enumerate(qs):
                qid = did(f"{key}:q{pos}")
                await c.execute(
                    text("INSERT INTO exam_questions (id, exam_id, company_id, prompt, options,"
                         " correct_index, points, position, created_at, updated_at) VALUES (:id, :eid,"
                         " :cid, :p, CAST(:opt AS jsonb), :ci, 1, :pos, :ts, :ts)"
                         " ON CONFLICT (id) DO UPDATE SET prompt = EXCLUDED.prompt,"
                         " options = EXCLUDED.options, correct_index = EXCLUDED.correct_index"),
                    {"id": str(qid), "eid": str(eid), "cid": str(company_id), "p": prompt,
                     "opt": json.dumps(options), "ci": correct, "pos": pos, "ts": ago(days=created_days)},
                )
            return eid

        fe_exam = await make_exam("exam:fe", "Frontend Fundamentals",
                                  "Core React, TypeScript and CSS screening.", "Frontend Engineer",
                                  60, 1200, FE_QUESTIONS, 20)
        apt_exam = await make_exam("exam:apt", "Aptitude Screen",
                                   "General reasoning and numerical aptitude.", None,
                                   50, 900, APT_QUESTIONS, 16)

        # ---- Exam assignments + graded attempts ----
        async def make_attempt(exam_key: str, eid: uuid.UUID, qs: list[tuple], applicant_idx: int,
                               pct: int, days_ago: float) -> None:
            aid = applicant_ids[applicant_idx]
            asg = did(f"assign:{exam_key}:{applicant_idx}")
            await c.execute(
                text("INSERT INTO exam_assignments (id, company_id, exam_id, applicant_id,"
                     " created_by_user_id, token_hash, expires_at, status, created_at, updated_at)"
                     " VALUES (:id, :cid, :eid, :aid, :hr, :tok, :exp, 'completed', :ts, :ts)"
                     " ON CONFLICT (id) DO UPDATE SET status = 'completed'"),
                {"id": str(asg), "cid": str(company_id), "eid": str(eid), "aid": str(aid),
                 "hr": str(hr_id), "tok": TOKEN_HASH + exam_key + str(applicant_idx),
                 "exp": ago(days=days_ago - 3), "ts": ago(days=days_ago)},
            )
            n = len(qs)
            n_correct = round(n * pct / 100)
            answers, graded = {}, {}
            for pos, (_p, options, correct) in enumerate(qs):
                qid = str(did(f"{exam_key}:q{pos}"))
                chosen = correct if pos < n_correct else (correct + 1) % len(options)
                answers[qid] = chosen
                graded[qid] = {"correct_index": correct, "points": 1}
            score_raw = n_correct
            passed = pct >= (60 if exam_key == "exam:fe" else 50)
            att = did(f"attempt:{exam_key}:{applicant_idx}")
            await c.execute(
                text("INSERT INTO exam_attempts (id, company_id, exam_id, applicant_id, assignment_id,"
                     " attempt_no, answers, graded_snapshot, score_raw, score_max, score_percent,"
                     " passed, status, started_at, submitted_at, created_at, updated_at) VALUES"
                     " (:id, :cid, :eid, :aid, :asg, 1, CAST(:ans AS jsonb), CAST(:gr AS jsonb), :sr, :sm, :pct, :passed,"
                     " 'submitted', :start, :sub, :sub, :sub) ON CONFLICT (id) DO UPDATE SET"
                     " score_percent = EXCLUDED.score_percent, passed = EXCLUDED.passed"),
                {"id": str(att), "cid": str(company_id), "eid": str(eid), "aid": str(aid),
                 "asg": str(asg), "ans": json.dumps(answers), "gr": json.dumps(graded),
                 "sr": score_raw, "sm": n, "pct": pct, "passed": passed,
                 "start": ago(days=days_ago, minutes=18), "sub": ago(days=days_ago)},
            )

        await make_attempt("exam:fe", fe_exam, FE_QUESTIONS, 0, 88, 12)
        await make_attempt("exam:fe", fe_exam, FE_QUESTIONS, 8, 75, 10)
        await make_attempt("exam:fe", fe_exam, FE_QUESTIONS, 3, 50, 8)
        await make_attempt("exam:apt", apt_exam, APT_QUESTIONS, 2, 83, 9)

        # ---- Helper: a graded interview (session + scorecard) ----
        async def make_scorecard(session_id: uuid.UUID, composite: float, days_ago: float,
                                 lang: str = "en") -> None:
            base = composite
            scores = {"communication": min(10, round(base + 0.4)), "technical": max(3, round(base - 0.6)),
                      "problem_solving": round(base), "confidence": min(10, round(base + 0.2))}
            strengths = ["Clear, structured answers", "Strong worked examples", "Good follow-up handling"]
            improvements = [
                {"area": "Depth", "suggestion": "Quantify impact with concrete metrics."},
                {"area": "Concision", "suggestion": "Lead with the answer, then explain."},
                {"area": "Edge cases", "suggestion": "Call out trade-offs proactively."},
            ]
            verdict = ("Strong performance — recommend advancing." if composite >= 7.5
                       else "Solid, hireable with minor gaps." if composite >= 6.5
                       else "Promising but needs more preparation.")
            await c.execute(
                text("INSERT INTO scorecards (scorecard_id, session_id, scores, composite_score,"
                     " strengths, improvements, summary, lang, scorer_model, scorer_version,"
                     " report_pdf_key, created_at) VALUES (:id, :sid, CAST(:sc AS jsonb), :comp, CAST(:st AS jsonb),"
                     " CAST(:imp AS jsonb), :sum, :lang, 'gemini-flash-lite-latest', '1.0', :pdf, :ts)"
                     " ON CONFLICT (session_id) DO UPDATE SET composite_score = EXCLUDED.composite_score,"
                     " scores = EXCLUDED.scores"),
                {"id": str(did(f"scorecard:{session_id}")), "sid": str(session_id),
                 "sc": json.dumps(scores), "comp": round(composite, 2), "st": json.dumps(strengths),
                 "imp": json.dumps(improvements), "sum": f"{verdict} Composite {composite:.1f}/10.",
                 "lang": lang, "pdf": f"scorecards/{session_id}.pdf", "ts": ago(days=days_ago)},
            )

        async def make_session(session_id: uuid.UUID, user_id: uuid.UUID, job_id: uuid.UUID,
                               status: str, days_ago: float, *, presenter: str = "anna",
                               with_turns: bool = False) -> None:
            completed = status == "completed"
            await c.execute(
                text("INSERT INTO sessions (id, user_id, job_id, language, status, started_at,"
                     " completed_at, duration_seconds, metadata, presenter_id, created_at, updated_at)"
                     " VALUES (:id, :uid, :jid, 'en', :status, :start, :done, :dur, '{}'::jsonb,"
                     " :pres, :start, :start) ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status,"
                     " completed_at = EXCLUDED.completed_at"),
                {"id": str(session_id), "uid": str(user_id), "jid": str(job_id), "status": status,
                 "start": ago(days=days_ago, minutes=12), "done": ago(days=days_ago) if completed else None,
                 "dur": 600 if completed else None, "pres": presenter},
            )
            if with_turns:
                convo = [
                    ("interviewer", "Welcome! To start, tell me about a project you're proud of."),
                    ("candidate", "I led a migration of our build to Vite, cutting CI time by 40%."),
                    ("interviewer", "Nice — what was the hardest part of that migration?"),
                    ("candidate", "Reworking legacy module resolution without breaking SSR."),
                    ("interviewer", "How did you validate it was safe to ship?"),
                    ("candidate", "Canary deploy plus a visual-regression suite on key routes."),
                ]
                for tn, (speaker, txt) in enumerate(convo, start=1):
                    await c.execute(
                        text("INSERT INTO turns (id, session_id, turn_number, speaker, text_content,"
                             " latency_ms, created_at) VALUES (:id, :sid, :tn, :sp, :tx, :lat, :ts)"
                             " ON CONFLICT (session_id, turn_number) DO NOTHING"),
                        {"id": str(did(f"turn:{session_id}:{tn}")), "sid": str(session_id), "tn": tn,
                         "sp": speaker, "tx": txt, "lat": 900 + tn * 60,
                         "ts": ago(days=days_ago, minutes=12 - tn)},
                    )

        # ---- Candidate's own practice interviews ----
        for n, (job_idx, status, composite, d) in enumerate(CANDIDATE_SESSIONS):
            sid = did(f"sess:cand:{n}")
            await make_session(sid, cand_id, JOB_IDS[job_idx], status, d, with_turns=(n in (0, 2)))
            if composite is not None:
                await make_scorecard(sid, composite, d)

        # ---- Company interviews: interviewed/hired applicants get a graded interview ----
        # (guest_candidate user + session + scorecard + completed interview invite)
        interview_plan = [(1, 8.2, 6), (4, 7.0, 4), (7, 8.6, 11)]  # applicant_idx, composite, days_ago
        for applicant_idx, composite, d in interview_plan:
            name, email, ttitle, *_ = DEMO_APPLICANTS[applicant_idx]
            guest_id = await upsert_user(
                f"guest:{email}", f"guest+{email}", name, "guest_candidate",
                company=company_id, created_days=d + 1, with_pw=False)
            job_id = JOB_IDS[next((i for i, j in enumerate(DEMO_JOBS) if j[0] == ttitle), 0)]
            # Link the applicant to its candidate user so HR's "View full profile"
            # works, and give that guest a real-looking profile.
            await c.execute(
                text("UPDATE applicants SET user_id = :g, updated_at = now() WHERE id = :a"),
                {"g": str(guest_id), "a": str(applicant_ids[applicant_idx])},
            )
            await set_profile(
                guest_id, headline=f"{ttitle} · interviewed at Acme",
                bio=(f"Interviewed for {ttitle} at Acme Technologies. Clear communicator "
                     "with strong, concrete worked examples."),
                employment_status="employed", desired_roles=ttitle, location="India",
            )
            sid = did(f"sess:appl:{applicant_idx}")
            await make_session(sid, guest_id, job_id, "completed", d, presenter="raj", with_turns=True)
            await make_scorecard(sid, composite, d)
            await c.execute(
                text("INSERT INTO interview_invites (id, company_id, applicant_id, job_id,"
                     " guest_user_id, session_id, created_by_user_id, token_hash, language, avatar_id,"
                     " expires_at, scheduled_at, consumed_at, status, created_at, updated_at) VALUES"
                     " (:id, :cid, :aid, :jid, :guid, :sid, :hr, :tok, 'en', 'raj', :exp, :sched,"
                     " :done, 'completed', :ts, :ts) ON CONFLICT (id) DO UPDATE SET status = 'completed',"
                     " session_id = EXCLUDED.session_id"),
                {"id": str(did(f"invite:{applicant_idx}")), "cid": str(company_id),
                 "aid": str(applicant_ids[applicant_idx]), "jid": str(job_id), "guid": str(guest_id),
                 "sid": str(sid), "hr": str(hr_id), "tok": TOKEN_HASH + "inv" + str(applicant_idx),
                 "exp": ago(days=d - 2), "sched": ago(days=d, minutes=20), "done": ago(days=d),
                 "ts": ago(days=d + 1)},
            )

        # A couple of still-pending invites (not yet taken) for the HR interviews list.
        for applicant_idx, d in [(0, 1), (2, 2)]:
            name, email, ttitle, *_ = DEMO_APPLICANTS[applicant_idx]
            job_id = JOB_IDS[next((i for i, j in enumerate(DEMO_JOBS) if j[0] == ttitle), 0)]
            await c.execute(
                text("INSERT INTO interview_invites (id, company_id, applicant_id, job_id,"
                     " created_by_user_id, token_hash, language, avatar_id, expires_at, scheduled_at,"
                     " status, created_at, updated_at) VALUES (:id, :cid, :aid, :jid, :hr, :tok, 'en',"
                     " 'anna', :exp, :sched, 'invited', :ts, :ts) ON CONFLICT (id) DO NOTHING"),
                {"id": str(did(f"invite:pending:{applicant_idx}")), "cid": str(company_id),
                 "aid": str(applicant_ids[applicant_idx]), "jid": str(job_id), "hr": str(hr_id),
                 "tok": TOKEN_HASH + "pend" + str(applicant_idx), "exp": ago(days=-2),
                 "sched": ago(days=-1), "ts": ago(days=d)},
            )

        # ---- Notifications (bell) ----
        notifs = [
            (cand_id, "welcome", "Welcome to Anterview 👋", "Pick a role and start your first mock interview.", "/jobs", 39, True),
            (cand_id, "interview_completed", "Your scorecard is ready", "Data Analyst mock — composite 8.3/10.", "/scorecards", 9, False),
            (cand_id, "interview_completed", "Your scorecard is ready", "DevOps mock — composite 5.8/10.", "/scorecards", 2, False),
            (hr_id, "applicant_scored", "New applicant scored", "Vivek Iyer scored 91/100 for Backend Engineer.", "/hr/applicants", 6, False),
            (hr_id, "interview_completed", "Interview completed", "Karthik Rao finished the avatar interview — 8.6/10.", "/hr/interviews", 11, False),
            (hr_id, "invite_sent", "Interview invite sent", "Invite sent to Aisha Khan (Frontend Engineer).", "/hr/interviews", 1, True),
        ]
        for i, (uid, kind, title, body, link, d, read) in enumerate(notifs):
            await c.execute(
                text("INSERT INTO notifications (id, user_id, kind, title, body, link, read_at,"
                     " created_at) VALUES (:id, :uid, :kind, :title, :body, :link, :read, :ts)"
                     " ON CONFLICT (id) DO NOTHING"),
                {"id": str(did(f"notif:{uid}:{i}")), "uid": str(uid), "kind": kind, "title": title,
                 "body": body, "link": link, "read": ago(days=d) if read else None, "ts": ago(days=d)},
            )

    await eng.dispose()

    print("\n  Demo data seeded successfully.\n")
    print("  Login (all passwords: Demo@12345)")
    print("  ---------------------------------------------------------")
    print("  platform owner  superadmin@demo.intants.com")
    print("  company admin   companyadmin@demo.intants.com  (Acme Technologies)")
    print("  platform admin  admin.demo@demo.intants.com")
    print("  HR manager      hr@demo.intants.com    (Acme Technologies)")
    print("  HR manager      hr2@demo.intants.com   (Acme Technologies)")
    print("  candidate       candidate@demo.intants.com")
    print("  ---------------------------------------------------------")
    print("  Company 'Acme Technologies': 9 applicants, 2 exams, 4 graded")
    print("  exam attempts, 3 completed + 2 pending interviews.")
    print("  Candidate: 5 completed mock interviews w/ scorecards + 1 live.\n")


if __name__ == "__main__":
    asyncio.run(main())
