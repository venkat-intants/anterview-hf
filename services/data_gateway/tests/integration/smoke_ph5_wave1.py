#!/usr/bin/env python3
"""PH5 Wave 1 end to end: the governed metric layer (definitions/funnel/members),
the 90-day hire check-in, and HR-added source — through the real API, against a
real Postgres.

    cd services/data_gateway
    export SMOKE_DATABASE_URL=postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph5_w1_smoke
    PYTHONPATH=".;../.." DATABASE_URL="$SMOKE_DATABASE_URL" DATABASE_SSL= \\
      REDIS_URL=redis://127.0.0.1:6379/0 python tests/integration/smoke_ph5_wave1.py

The smoke's own engine reads SMOKE_DATABASE_URL (default: the local ph5_w1_smoke
database), not DATABASE_URL, so point both at the same disposable database.
Keep REDIS_URL local: the service .env files point at the live Space's Redis.

Seeds its own companies; leaves them behind (unique slugs), like the other smokes.

Every application's flags (screened / assessed / interviewed / selected / hired /
rejected) are decided by the SEED PLAN below (``APPS``) and every expected count
is computed independently, in Python, from that plan — never copied from what the
API returns.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("SMOKE_DATABASE_URL", "postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph5_w1_smoke")
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f" — {detail}" if not cond and detail else ""))


# ---------------------------------------------------------------------------
# The seed plan for company A — one row per application. Every expected count
# used later is a Python computation over THIS list, not a number typed twice.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AppPlan:
    key: str
    source: str
    req: str  # "req1" | "req2"
    screened: bool
    assessed: bool
    interviewed: bool
    selected: bool
    hired: bool
    rejected: bool


APPS: tuple[AppPlan, ...] = (
    # A hire ~95 days ago: due a check-in, screened, assessed (exam), and
    # interviewed via a completed AI session.
    AppPlan("hire95", "referral", "req1", True, True, True, True, True, False),
    # A hire ~40 days ago: too recent for 'employed' on the check-in.
    AppPlan("hire40", "job_board", "req1", True, False, False, True, True, False),
    # A hire ~200 days ago: past the check-in window.
    AppPlan("hire200", "unknown", "req1", True, False, False, True, True, False),
    # Hired on the ledger, then the offer was declined after the fact — not a
    # standing hire, though the company did choose them (selected).
    AppPlan("reversed", "referral", "req1", True, False, False, True, False, False),
    # Never screened; an offer reached them and was declined (still selected);
    # interviewed via a submitted human scorecard.
    AppPlan("offer_declined", "referral", "req1", False, False, True, True, False, False),
    AppPlan("rejected1", "job_board", "req2", False, False, False, False, False, True),
    AppPlan("rejected2", "unknown", "req2", False, False, False, False, False, True),
    # Screened (shortlisted) but nothing beyond it.
    AppPlan("screened_only", "referral", "req2", True, False, False, False, False, False),
    # Just applied.
    AppPlan("new", "job_board", "req2", False, False, False, False, False, False),
    # An offer was withdrawn before it reached the candidate — not selected.
    AppPlan("offer_withdrawn", "job_board", "req2", False, False, False, False, False, False),
)

EXPECTED = {
    "applications": len(APPS),
    "screened": sum(a.screened for a in APPS),
    "assessed": sum(a.assessed for a in APPS),
    "interviewed": sum(a.interviewed for a in APPS),
    "selected": sum(a.selected for a in APPS),
    "hires": sum(a.hired for a in APPS),
    "rejections": sum(a.rejected for a in APPS),
    "unknown_apps": sum(a.source == "unknown" for a in APPS),
    "referral_apps": sum(a.source == "referral" for a in APPS),
    "job_board_apps": sum(a.source == "job_board" for a in APPS),
}

#: When each application landed — always well before its own decision/hire
#: date below, so `days_to_hire` (application -> hire) is never negative.
CREATED_DAYS_AGO: dict[str, int] = {
    "hire95": 120, "hire40": 60, "hire200": 220, "reversed": 20,
    "offer_declined": 30, "rejected1": 15, "rejected2": 15,
    "screened_only": 10, "new": 3, "offer_withdrawn": 20,
}


# ---------------------------------------------------------------------------
# Raw seeding helpers — mirror tests/integration/test_ph5_w1_metrics.py's own
# helpers exactly, so the SQL shape this smoke relies on is the same shape the
# integration suite already proved works.
# ---------------------------------------------------------------------------
async def _application(
    db, *, company: uuid.UUID, req: uuid.UUID, name: str, source: str, created_at: datetime,
) -> tuple[uuid.UUID, uuid.UUID]:
    applicant_id, enrolment_id = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO applicants (id, company_id, full_name, target_job_title, created_at)"
            " VALUES (:a, :c, :n, 'Engineer', :ts)"
        ),
        {"a": applicant_id, "c": company, "n": name, "ts": created_at},
    )
    await db.execute(
        text(
            "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id,"
            " target_job_title, status, source, created_at, updated_at)"
            " VALUES (:e, :c, :r, :a, 'Engineer', 'new', :src, :ts, :ts)"
        ),
        {"e": enrolment_id, "c": company, "r": req, "a": applicant_id, "src": source, "ts": created_at},
    )
    return applicant_id, enrolment_id


async def _transition(
    db, *, company: uuid.UUID, enrolment_id: uuid.UUID, to_status: str, occurred_at: datetime,
) -> None:
    needs_reason = to_status in ("hired", "rejected")
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, to_status, automated,"
            " occurred_at, reason_code, reason_label) VALUES (:c, :e, :s, false, :ts, :rc, :rl)"
        ),
        {
            "c": company, "e": enrolment_id, "s": to_status, "ts": occurred_at,
            "rc": "smoke_reason" if needs_reason else None,
            "rl": "Smoke reason" if needs_reason else None,
        },
    )


async def _hire(
    db, *, company: uuid.UUID, enrolment_id: uuid.UUID, hired_at: datetime,
    offer_outcome: str | None = None,
) -> None:
    await db.execute(
        text("UPDATE enrolments SET status = 'hired', offer_outcome = :oo WHERE id = :e"),
        {"e": enrolment_id, "oo": offer_outcome},
    )
    await _transition(db, company=company, enrolment_id=enrolment_id, to_status="hired", occurred_at=hired_at)


async def _reject(db, *, company: uuid.UUID, enrolment_id: uuid.UUID, occurred_at: datetime) -> None:
    await db.execute(text("UPDATE enrolments SET status = 'rejected' WHERE id = :e"), {"e": enrolment_id})
    await _transition(
        db, company=company, enrolment_id=enrolment_id, to_status="rejected", occurred_at=occurred_at,
    )


async def _screen(db, *, company: uuid.UUID, enrolment_id: uuid.UUID, occurred_at: datetime) -> None:
    await _transition(
        db, company=company, enrolment_id=enrolment_id, to_status="shortlisted", occurred_at=occurred_at,
    )


async def _exam_attempt(db, *, company: uuid.UUID, applicant_id: uuid.UUID, enrolment_id: uuid.UUID) -> None:
    exam_id, round_id, attempt_id, assignment_id = (uuid.uuid4() for _ in range(4))
    await db.execute(
        text(
            "INSERT INTO exams (id, company_id, title, kind, created_at, updated_at)"
            " VALUES (:e, :c, 'Smoke exam', 'mcq', now(), now())"
        ),
        {"e": exam_id, "c": company},
    )
    await db.execute(
        text(
            "INSERT INTO exam_rounds (id, company_id, exam_id, round_number, title, position,"
            " created_at, updated_at) VALUES (:r, :c, :e, 1, 'Round 1', 0, now(), now())"
        ),
        {"r": round_id, "c": company, "e": exam_id},
    )
    await db.execute(
        text(
            "INSERT INTO exam_assignments (id, company_id, exam_id, round_id, applicant_id,"
            " enrolment_id, token_hash, expires_at, created_at, updated_at)"
            " VALUES (:i, :c, :e, :r, :a, :en, :th, now() + interval '7 days', now(), now())"
        ),
        {
            "i": assignment_id, "c": company, "e": exam_id, "r": round_id, "a": applicant_id,
            "en": enrolment_id, "th": uuid.uuid4().hex,
        },
    )
    await db.execute(
        text(
            "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id,"
            " assignment_id, status, started_at, submitted_at, created_at, updated_at)"
            " VALUES (:i, :c, :e, :r, :a, :asn, 'submitted', now(), now(), now(), now())"
        ),
        {"i": attempt_id, "c": company, "e": exam_id, "r": round_id, "a": applicant_id, "asn": assignment_id},
    )


async def _ai_interview(db, *, company: uuid.UUID, applicant_id: uuid.UUID, enrolment_id: uuid.UUID) -> None:
    job_id, session_id, invite_id, guest_user = (uuid.uuid4() for _ in range(4))
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
        {"u": guest_user, "e": f"guest-{guest_user.hex[:8]}@ph5w1.test", "c": company},
    )
    await db.execute(
        text(
            "INSERT INTO jobs (id, title, description, level, created_at, updated_at)"
            " VALUES (:j, 'Engineer', 'd', 'mid', now(), now())"
        ),
        {"j": job_id},
    )
    await db.execute(
        text(
            "INSERT INTO sessions (id, user_id, job_id, status, started_at, completed_at,"
            " created_at, updated_at) VALUES (:s, :u, :j, 'completed', now(), now(), now(), now())"
        ),
        {"s": session_id, "u": guest_user, "j": job_id},
    )
    await db.execute(
        text(
            "INSERT INTO interview_invites (id, company_id, applicant_id, job_id, session_id,"
            " enrolment_id, token_hash, expires_at, status, created_at, updated_at)"
            " VALUES (:i, :c, :a, :j, :s, :e, :th, now() + interval '7 days', 'completed', now(), now())"
        ),
        {
            "i": invite_id, "c": company, "a": applicant_id, "j": job_id, "s": session_id,
            "e": enrolment_id, "th": uuid.uuid4().hex,
        },
    )


async def _human_scorecard(
    db, *, company: uuid.UUID, enrolment_id: uuid.UUID, round_id: uuid.UUID,
    interviewer: uuid.UUID, competency_id: str, score: int = 4,
) -> None:
    scorecard_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, status, created_at, updated_at)"
            " VALUES (:i, :c, :e, :r, :iv, 'in_progress', now(), now())"
        ),
        {"i": scorecard_id, "c": company, "e": enrolment_id, "r": round_id, "iv": interviewer},
    )
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
            " competency_id, score, not_assessed, updated_at)"
            " VALUES (:sc, :c, :r, :comp, :score, false, now())"
        ),
        {"sc": scorecard_id, "c": company, "r": round_id, "comp": competency_id, "score": score},
    )
    await db.execute(
        text("UPDATE interviewer_scorecards SET status = 'submitted', submitted_at = now() WHERE id = :i"),
        {"i": scorecard_id},
    )


async def _offer(
    db, *, company: uuid.UUID, enrolment_id: uuid.UUID, applicant_id: uuid.UUID,
    hr: uuid.UUID, admin: uuid.UUID, final: str = "sent", start_date: date | None = None,
) -> uuid.UUID:
    offer_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO offers (id, company_id, enrolment_id, applicant_id, job_title,"
            " base_salary, start_date, created_by_user_id)"
            " VALUES (:o, :c, :e, :a, 'Engineer', 1200000, :sd, :hr)"
        ),
        {"o": offer_id, "c": company, "e": enrolment_id, "a": applicant_id, "sd": start_date, "hr": hr},
    )
    if final == "draft":
        return offer_id
    await db.execute(
        text("UPDATE offers SET status = 'pending_approval', submitted_by_user_id = :u WHERE id = :o"),
        {"u": hr, "o": offer_id},
    )
    await db.execute(
        text("UPDATE offers SET status = 'approved', decided_by_user_id = :u WHERE id = :o"),
        {"u": admin, "o": offer_id},
    )
    if final == "approved":
        return offer_id
    if final == "withdrawn":
        await db.execute(
            text(
                "UPDATE offers SET status = 'withdrawn', withdrawn_by_user_id = :u,"
                " withdrawn_at = now() WHERE id = :o"
            ),
            {"u": hr, "o": offer_id},
        )
        return offer_id
    await db.execute(
        text(
            "UPDATE offers SET status = 'sent', token_hash = :t, sent_at = now(),"
            " expires_at = now() + interval '7 days' WHERE id = :o"
        ),
        {"t": uuid.uuid4().hex, "o": offer_id},
    )
    if final == "sent":
        return offer_id
    if final == "declined":
        await db.execute(
            text("UPDATE offers SET status = 'declined', responded_at = now() WHERE id = :o"),
            {"o": offer_id},
        )
        return offer_id
    # accepted
    await db.execute(
        text(
            "UPDATE offers SET status = 'accepted', responded_at = now(),"
            " accepted_name = 'Smoke Candidate' WHERE id = :o"
        ),
        {"o": offer_id},
    )
    return offer_id


async def _snapshot(db, *, company: uuid.UUID) -> tuple[dict[str, str], int]:
    """(enrolment_id -> status, count of stage_transitions rows) for one
    company — the "no write" guarantee's before/after fingerprint."""
    rows = (
        await db.execute(text("SELECT id, status FROM enrolments WHERE company_id = :c"), {"c": company})
    ).all()
    statuses = {str(r[0]): r[1] for r in rows}
    txns = int(
        await db.scalar(text("SELECT count(*) FROM stage_transitions WHERE company_id = :c"), {"c": company})
        or 0
    )
    return statuses, txns


async def main() -> None:  # noqa: PLR0915 — one linear script, read top to bottom
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    tag = uuid.uuid4().hex[:8]

    def _days_ago(n: int) -> datetime:
        return now - timedelta(days=n)

    # -----------------------------------------------------------------
    # Company A — the main scenario.
    # -----------------------------------------------------------------
    company_a = uuid.uuid4()
    hr_a, admin_a, interviewer_a, candidate_a = (uuid.uuid4() for _ in range(4))
    req1, req2 = uuid.uuid4(), uuid.uuid4()
    workflow_id, round_id = uuid.uuid4(), uuid.uuid4()
    competency_id = "communication"

    # -----------------------------------------------------------------
    # Company B — tenant isolation only.
    # -----------------------------------------------------------------
    company_b = uuid.uuid4()
    hr_b, admin_b = uuid.uuid4(), uuid.uuid4()
    req_b = uuid.uuid4()

    enrolments: dict[str, uuid.UUID] = {}
    applicants: dict[str, uuid.UUID] = {}

    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO companies (id, name, slug, is_active, created_at, updated_at)"
                " VALUES (:i, 'W1 Co A', :s, true, :n, :n), (:i2, 'W1 Co B', :s2, true, :n, :n)"
            ),
            {"i": company_a, "s": f"w1a-{tag}", "i2": company_b, "s2": f"w1b-{tag}", "n": now},
        )
        for uid, company, name in (
            (hr_a, company_a, "Hema HR"), (admin_a, company_a, "Sam Admin"),
            (interviewer_a, company_a, "Ivy Interviewer"), (candidate_a, None, "Cand"),
            (hr_b, company_b, "Bea HR"), (admin_b, company_b, "Bo Admin"),
        ):
            await db.execute(
                text(
                    "INSERT INTO users (id, email, full_name, company_id, created_at, updated_at)"
                    " VALUES (:i, :e, :n, :c, :ts, :ts)"
                ),
                {"i": uid, "e": f"{uid.hex[:10]}@ph5w1-{tag}.test", "n": name, "c": company, "ts": now},
            )
        # Titles must be distinct per company — uq_job_requisitions_company_title.
        for rid, company, title in (
            (req1, company_a, "Engineer"), (req2, company_a, "Engineer II"),
            (req_b, company_b, "Engineer"),
        ):
            await db.execute(
                text(
                    "INSERT INTO job_requisitions (id, company_id, title, status, created_at, updated_at)"
                    " VALUES (:r, :c, :t, 'open', now(), now())"
                ),
                {"r": rid, "c": company, "t": title},
            )
        # One workflow/round/criterion for company A — an FK target for the
        # human scorecard and exam round only; the metric layer never reads
        # workflow content itself.
        await db.execute(
            text(
                "INSERT INTO workflows (id, company_id, requisition_id, version, status,"
                " created_at, updated_at) VALUES (:w, :c, :r, 1, 'draft', now(), now())"
            ),
            {"w": workflow_id, "c": company_a, "r": req1},
        )
        await db.execute(
            text(
                "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
                " created_at, updated_at)"
                " VALUES (:rd, :c, :w, 0, 'Panel', 'human_review', now(), now())"
            ),
            {"rd": round_id, "c": company_a, "w": workflow_id},
        )
        await db.execute(
            text(
                "INSERT INTO round_criteria (id, company_id, round_id, competency_id,"
                " competency_name, weight, created_at)"
                " VALUES (:i, :c, :rd, :comp, 'Communication', 1.0, now())"
            ),
            {"i": uuid.uuid4(), "c": company_a, "rd": round_id, "comp": competency_id},
        )

        req_by_key = {"req1": req1, "req2": req2}
        for plan in APPS:
            created_at = _days_ago(CREATED_DAYS_AGO[plan.key])
            applicant_id, enrolment_id = await _application(
                db, company=company_a, req=req_by_key[plan.req], name=f"Applicant {plan.key}",
                source=plan.source, created_at=created_at,
            )
            applicants[plan.key] = applicant_id
            enrolments[plan.key] = enrolment_id

        # Hires with a known employment-start clock (via an accepted offer's
        # start_date), one per due-window bucket the check-in tests need.
        await _offer(
            db, company=company_a, enrolment_id=enrolments["hire95"], applicant_id=applicants["hire95"],
            hr=hr_a, admin=admin_a, final="accepted", start_date=_days_ago(95).date(),
        )
        await _hire(db, company=company_a, enrolment_id=enrolments["hire95"], hired_at=_days_ago(95))
        await _exam_attempt(db, company=company_a, applicant_id=applicants["hire95"], enrolment_id=enrolments["hire95"])
        await _ai_interview(db, company=company_a, applicant_id=applicants["hire95"], enrolment_id=enrolments["hire95"])

        await _offer(
            db, company=company_a, enrolment_id=enrolments["hire40"], applicant_id=applicants["hire40"],
            hr=hr_a, admin=admin_a, final="accepted", start_date=_days_ago(40).date(),
        )
        await _hire(db, company=company_a, enrolment_id=enrolments["hire40"], hired_at=_days_ago(40))

        await _offer(
            db, company=company_a, enrolment_id=enrolments["hire200"], applicant_id=applicants["hire200"],
            hr=hr_a, admin=admin_a, final="accepted", start_date=_days_ago(200).date(),
        )
        await _hire(db, company=company_a, enrolment_id=enrolments["hire200"], hired_at=_days_ago(200))

        # Hired on the ledger, then reversed — decided recently (~10 days ago).
        await _hire(
            db, company=company_a, enrolment_id=enrolments["reversed"], hired_at=_days_ago(10),
            offer_outcome="offer_declined",
        )

        # An offer reached them and was declined; interviewed via a human
        # scorecard (never touches the ledger's status).
        await _offer(
            db, company=company_a, enrolment_id=enrolments["offer_declined"],
            applicant_id=applicants["offer_declined"], hr=hr_a, admin=admin_a, final="declined",
        )
        await _human_scorecard(
            db, company=company_a, enrolment_id=enrolments["offer_declined"], round_id=round_id,
            interviewer=interviewer_a, competency_id=competency_id, score=4,
        )

        await _reject(db, company=company_a, enrolment_id=enrolments["rejected1"], occurred_at=_days_ago(5))
        await _reject(db, company=company_a, enrolment_id=enrolments["rejected2"], occurred_at=_days_ago(5))
        await _screen(db, company=company_a, enrolment_id=enrolments["screened_only"], occurred_at=_days_ago(5))
        # "new" gets no transition at all.
        await _offer(
            db, company=company_a, enrolment_id=enrolments["offer_withdrawn"],
            applicant_id=applicants["offer_withdrawn"], hr=hr_a, admin=admin_a, final="withdrawn",
        )

        # Company B: one hire, also ~95 days in — proves due()/funnel are
        # company-scoped, not just "the right age".
        b_applicant, b_enrolment = await _application(
            db, company=company_b, req=req_b, name="B Hire", source="direct", created_at=_days_ago(110),
        )
        await _offer(
            db, company=company_b, enrolment_id=b_enrolment, applicant_id=b_applicant,
            hr=hr_b, admin=admin_b, final="accepted", start_date=_days_ago(95).date(),
        )
        await _hire(db, company=company_b, enrolment_id=b_enrolment, hired_at=_days_ago(95))

        await db.commit()

    before_snapshot = None
    async with factory() as db:
        before_snapshot = await _snapshot(db, company=company_a)

    from shared.auth.base import User

    from app.bulk_ingest import StagedFile, create_batch
    from app.database import get_db_session
    from app.dependencies import get_current_user, get_hr_company, get_super_admin_company
    from app.main import app
    from app.metrics.definitions import _registry_hash  # noqa: PLC0415 — the smoke's own check
    from app.routers.admin_hr import get_company_admin_ctx
    from app.routers.hr_checkins import CHECKIN_NOTICE
    from app.routers.hr_metrics import get_definitions_ctx

    async def _db():  # noqa: ANN202
        async with factory() as session:
            yield session

    acting = {"hr": hr_a, "company": company_a}
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_hr_company] = lambda: (acting["hr"], acting["company"])
    app.dependency_overrides[get_super_admin_company] = lambda: (admin_a, company_a)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://smoke") as c:
        # ===================================================================
        # 2. Definitions
        # ===================================================================
        print("\nPH5-W1 — GET /hr/metrics/definitions")
        app.dependency_overrides[get_definitions_ctx] = lambda: (hr_a, company_a)
        r = await c.get("/hr/metrics/definitions")
        check("hr_manager gets 200 on definitions", r.status_code == 200, r.text[:200])
        body = r.json()
        check(
            "registry_hash matches the sha256 the lock file implies",
            body.get("registry_hash") == _registry_hash(), body.get("registry_hash"),
        )
        source_dim = next((d for d in body["dimensions"] if d["name"] == "source"), None)
        check(
            "the source dimension carries values",
            source_dim is not None and bool(source_dim.get("values")), str(source_dim)[:200],
        )
        check(
            "every metric carries a drillable numerator/denominator pair",
            bool(body["metrics"]) and all(
                isinstance(m.get("drillable"), dict)
                and "numerator" in m["drillable"] and "denominator" in m["drillable"]
                for m in body["metrics"]
            ),
        )

        app.dependency_overrides[get_definitions_ctx] = lambda: (admin_a, company_a)
        r = await c.get("/hr/metrics/definitions")
        check("super_admin also gets 200 on definitions", r.status_code == 200, r.text[:200])
        del app.dependency_overrides[get_definitions_ctx]

        # ===================================================================
        # 3. The funnel
        # ===================================================================
        print("\nPH5-W1 — GET /hr/analytics/funnel — the application cohort")
        r = await c.get("/hr/analytics/funnel")
        check("hr_manager gets 200 on the funnel", r.status_code == 200, r.text[:200])
        fm = r.json()["groups"][0]["metrics"]
        check("applications matches the seed plan", fm["applications"]["value"] == EXPECTED["applications"],
              str(fm["applications"]))
        check("screened matches the seed plan", fm["screened"]["value"] == EXPECTED["screened"],
              str(fm["screened"]))
        check("assessed matches the seed plan", fm["assessed"]["value"] == EXPECTED["assessed"],
              str(fm["assessed"]))
        check("interviewed matches the seed plan", fm["interviewed"]["value"] == EXPECTED["interviewed"],
              str(fm["interviewed"]))
        check("selected matches the seed plan", fm["selected"]["value"] == EXPECTED["selected"],
              str(fm["selected"]))
        check("hires matches the seed plan (the reversed hire is excluded)",
              fm["hires"]["value"] == EXPECTED["hires"], str(fm["hires"]))
        check("rejections matches the seed plan", fm["rejections"]["value"] == EXPECTED["rejections"],
              str(fm["rejections"]))

        print("\nPH5-W1 — group_by=source, including Unknown / untracked")
        r = await c.get("/hr/analytics/funnel", params={"group_by": "source"})
        by_source = {g["key"]: g for g in r.json()["groups"]}
        check(
            "the unknown source group reads as Unknown / untracked, with the right count",
            by_source.get("unknown", {}).get("label") == "Unknown / untracked"
            and by_source["unknown"]["metrics"]["applications"]["value"] == EXPECTED["unknown_apps"],
            str(by_source.get("unknown")),
        )
        check(
            "referral and job_board groups match the seed plan",
            by_source.get("referral", {}).get("metrics", {}).get("applications", {}).get("value")
            == EXPECTED["referral_apps"]
            and by_source.get("job_board", {}).get("metrics", {}).get("applications", {}).get("value")
            == EXPECTED["job_board_apps"],
            str({k: v["metrics"]["applications"]["value"] for k, v in by_source.items() if k}),
        )

        print("\nPH5-W1 — the decision cohort, windowed")
        r = await c.get(
            "/hr/analytics/funnel",
            params={"cohort": "decision", "from": (now - timedelta(days=60)).date().isoformat()},
        )
        dm = r.json()["groups"][0]["metrics"]
        # Within the last 60 days: hire40 (-40d), reversed (-10d), rejected1/2
        # (-5d) decided; hire95 (-95d) and hire200 (-200d) fall outside it.
        check(
            "the decision cohort, windowed to 60 days, sees only the recent decisions",
            dm["hires"]["value"] == 1 and dm["rejections"]["value"] == 2, str(dm),
        )

        print("\nPH5-W1 — the hire cohort, windowed")
        r = await c.get(
            "/hr/analytics/funnel",
            params={"cohort": "hire", "from": (now - timedelta(days=60)).date().isoformat()},
        )
        hm = r.json()["groups"][0]["metrics"]
        check(
            "the hire cohort, windowed to 60 days, sees only hire40",
            hm["hires"]["value"] == 1, str(hm["hires"]),
        )

        print("\nPH5-W1 — a foreign requisition_id yields zero, not an error")
        r = await c.get("/hr/analytics/funnel", params={"requisition_id": str(req_b)})
        check(
            "company B's requisition id, read by company A, gives zero applications",
            r.status_code == 200 and r.json()["groups"][0]["metrics"]["applications"]["value"] == 0,
            r.text[:200],
        )

        print("\nPH5-W1 — role gates on the funnel")
        del app.dependency_overrides[get_hr_company]
        app.dependency_overrides[get_current_user] = lambda: User(
            user_id=str(admin_a), full_name="Sam", email="sam@x.test", roles=["super_admin"])
        r = await c.get("/hr/analytics/funnel")
        check("super_admin gets 403 on the funnel", r.status_code == 403, r.text[:160])
        app.dependency_overrides[get_current_user] = lambda: User(
            user_id=str(interviewer_a), full_name="Ivy", email="ivy@x.test", roles=["interviewer"])
        r = await c.get("/hr/analytics/funnel")
        check("interviewer gets 403 on the funnel", r.status_code == 403, r.text[:160])
        app.dependency_overrides[get_current_user] = lambda: User(
            user_id=str(candidate_a), full_name="Cand", email="cand@x.test", roles=["candidate"])
        r = await c.get("/hr/analytics/funnel")
        check("candidate gets 403 on the funnel", r.status_code == 403, r.text[:160])
        del app.dependency_overrides[get_current_user]
        app.dependency_overrides[get_hr_company] = lambda: (acting["hr"], acting["company"])

        # ===================================================================
        # 4. The members drill-down
        # ===================================================================
        print("\nPH5-W1 — GET /hr/analytics/members")
        r = await c.get(
            "/hr/analytics/members", params={"metric": "application_to_hire", "part": "numerator"},
        )
        check("the drill-down returns 200", r.status_code == 200, r.text[:200])
        members = r.json()
        check(
            "the numerator's row count matches the governed hires count",
            members["total"] == EXPECTED["hires"] == len(members["rows"]), str(members)[:300],
        )

        async with factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT details FROM audit_log WHERE action = 'analytics.members_viewed'"
                        " AND actor_id = :hr ORDER BY event_ts DESC LIMIT 1"
                    ),
                    {"hr": hr_a},
                )
            ).mappings().first()
        applicant_names = [f"Applicant {p.key}" for p in APPS]
        details_text = str(row["details"]) if row else ""
        check(
            "an audit row was written for the drill-down, naming no candidate",
            row is not None and not any(name in details_text for name in applicant_names),
            details_text[:300],
        )
        check(
            "the audit row records the metric, part and total — not a candidate",
            row is not None and row["details"].get("metric") == "application_to_hire"
            and row["details"].get("part") == "numerator" and row["details"].get("total") == EXPECTED["hires"],
            str(row["details"]) if row else "",
        )

        r = await c.get(
            "/hr/analytics/members",
            params={"metric": "performance_90d", "part": "numerator", "cohort": "hire"},
        )
        check("performance_90d's drill-down is refused (a check-in outcome)", r.status_code == 422, r.text[:160])
        r = await c.get(
            "/hr/analytics/members",
            params={"metric": "retention_90d", "part": "numerator", "cohort": "hire"},
        )
        check("retention_90d's numerator drill-down is refused too", r.status_code == 422, r.text[:160])

        # ===================================================================
        # 5. Check-ins
        # ===================================================================
        print("\nPH5-W1 — GET /hr/checkins/due")
        r = await c.get("/hr/checkins/due")
        due_ids = {i["enrolment_id"] for i in r.json()}
        check(
            "due() lists the 95-day hire but not the 40- or 200-day hires, nor company B's",
            str(enrolments["hire95"]) in due_ids
            and str(enrolments["hire40"]) not in due_ids
            and str(enrolments["hire200"]) not in due_ids
            and str(b_enrolment) not in due_ids,
            str(due_ids),
        )

        print("\nPH5-W1 — record, correct, and the audit trail")
        r = await c.post(
            f"/hr/enrolments/{enrolments['hire95']}/checkins",
            json={"employment": "employed", "performance": "meets"},
        )
        check("recording employed/meets on the 95-day hire is accepted", r.status_code == 201, r.text[:200])
        checkin1_id = r.json()["checkin_id"]

        r = await c.get(f"/hr/enrolments/{enrolments['hire95']}/checkins")
        listing = r.json()
        check(
            "the window reads open, with the exact notice text",
            listing["window"]["open"] is True and listing["notice"] == CHECKIN_NOTICE,
            str(listing)[:300],
        )

        r = await c.post(
            f"/hr/enrolments/{enrolments['hire95']}/checkins",
            json={"employment": "left", "left_reason": "voluntary"},
        )
        check("a second record on the same hire is refused", r.status_code == 409, r.text[:160])

        r = await c.post(
            f"/hr/checkins/{checkin1_id}/correct",
            json={"employment": "left", "left_reason": "voluntary"},
        )
        check("correcting it succeeds", r.status_code == 200, r.text[:200])
        corrected = r.json()
        check(
            "the correction supersedes the original",
            corrected.get("supersedes_id") == checkin1_id and corrected.get("employment") == "left",
            str(corrected),
        )

        async with factory() as db:
            old_superseded = await db.scalar(
                text("SELECT superseded_at IS NOT NULL FROM hire_checkins WHERE id = :i"),
                {"i": uuid.UUID(checkin1_id)},
            )
            audit_rows = (
                await db.execute(
                    text(
                        "SELECT action, details FROM audit_log WHERE resource_type = 'hire_checkin'"
                        "   AND details->>'enrolment_id' = :e ORDER BY event_ts"
                    ),
                    {"e": str(enrolments["hire95"])},
                )
            ).all()
        check("the original row is now superseded", bool(old_superseded))
        forbidden = {"employed", "left", "meets", "below", "exceeds", "voluntary", "involuntary"}
        blob = " ".join(str(details) for _action, details in audit_rows)
        check(
            "the recorded and corrected audit rows carry no employment/performance value",
            len(audit_rows) == 2 and not any(v in blob for v in forbidden),
            blob[:300],
        )

        print("\nPH5-W1 — the window: too early, closed, and a reversed hire")
        r = await c.post(
            f"/hr/enrolments/{enrolments['hire40']}/checkins",
            json={"employment": "employed", "performance": "meets"},
        )
        check("'employed' before day 80 (the 40-day hire) is refused", r.status_code == 409, r.text[:200])
        r = await c.post(
            f"/hr/enrolments/{enrolments['hire40']}/checkins",
            json={"employment": "left", "left_reason": "voluntary"},
        )
        check("'left' has no lower bound, so it is accepted", r.status_code == 201, r.text[:200])

        r = await c.post(
            f"/hr/enrolments/{enrolments['hire200']}/checkins",
            json={"employment": "left", "left_reason": "voluntary"},
        )
        check("the 200-day hire is past the window and is refused", r.status_code == 409, r.text[:200])

        r = await c.post(
            f"/hr/enrolments/{enrolments['reversed']}/checkins",
            json={"employment": "left", "left_reason": "involuntary"},
        )
        check("the reversed hire is refused a check-in", r.status_code == 409, r.text[:200])

        print("\nPH5-W1 — tenant isolation on check-ins")
        acting["hr"], acting["company"] = hr_b, company_b
        r = await c.post(
            f"/hr/enrolments/{enrolments['hire95']}/checkins",
            json={"employment": "left", "left_reason": "voluntary"},
        )
        check("company B's HR gets 404 on company A's enrolment", r.status_code == 404, r.text[:160])
        acting["hr"], acting["company"] = hr_a, company_a

        # ===================================================================
        # 6. After the check-ins: coverage and suppression on the hire cohort
        # ===================================================================
        print("\nPH5-W1 — the hire-cohort funnel after the check-ins")
        r = await c.get("/hr/analytics/funnel", params={"cohort": "hire"})
        after = r.json()["groups"][0]["metrics"]
        check("checkin_coverage appears on the hire cohort", "checkin_coverage" in after, str(list(after)))
        check(
            "retention_90d is suppressed with a null value, the cohort being under 5",
            after["retention_90d"]["suppressed"] is True and after["retention_90d"]["value"] is None,
            str(after["retention_90d"]),
        )

        # ===================================================================
        # 7. Cross-consumer consistency
        # ===================================================================
        print("\nPH5-W1 — consistency across /hr/analytics, the funnel, the dashboards and the board")
        analytics = (await c.get("/hr/analytics")).json()
        funnel = (await c.get("/hr/analytics/funnel")).json()
        fm2 = funnel["groups"][0]["metrics"]
        dash1 = (await c.get(f"/hr/requisitions/{req1}/dashboard")).json()
        dash2 = (await c.get(f"/hr/requisitions/{req2}/dashboard")).json()
        dash_apps = dash1["progress"]["applications"] + dash2["progress"]["applications"]

        app.dependency_overrides[get_company_admin_ctx] = lambda: (admin_a, company_a)
        board = (await c.get("/admin/hiring-board")).json()
        del app.dependency_overrides[get_company_admin_ctx]
        board_hired = sum(o["hired"] for o in board["openings"])

        check(
            "/hr/analytics ever_hired, the funnel's hires and the board's summed hired all agree",
            analytics["conversion"]["ever_hired"] == fm2["hires"]["value"] == board_hired == EXPECTED["hires"],
            f"conversion={analytics['conversion']['ever_hired']} funnel={fm2['hires']['value']}"
            f" board={board_hired}",
        )
        check(
            "the requisition dashboards' applications, summed, equal the funnel's",
            dash_apps == fm2["applications"]["value"] == EXPECTED["applications"],
            f"dashboards={dash_apps} funnel={fm2['applications']['value']}",
        )

        # ===================================================================
        # 8. HR source on the bulk upload
        # ===================================================================
        print("\nPH5-W1 — HR source on POST /hr/applicants/bulk")
        r = await c.post(
            "/hr/applicants/bulk",
            data={"requisition_id": str(req1), "source": "bogus"},
            files=[("files", ("resume.pdf", b"%PDF-1.7\n%%EOF\n", "application/pdf"))],
        )
        check(
            "an unrecognised source is refused (422), before any file is touched",
            r.status_code == 422, r.text[:200],
        )

        # The success path needs S3 (or a resume parse) this environment does
        # not have; per the brief, the persistence of `source` is asserted
        # directly against app.bulk_ingest.create_batch and the row it
        # writes, rather than via a real multipart POST.
        batch_id = uuid.uuid4()
        async with factory() as db:
            await create_batch(
                db, batch_id=batch_id, company_id=company_a, requisition_id=req1, uploaded_by=hr_a,
                files=[StagedFile(uuid.uuid4(), "referral.pdf", None, 0, "smoke: no S3 locally")],
                source="referral",
            )
            await db.commit()
        async with factory() as db:
            persisted_source = await db.scalar(
                text("SELECT source FROM upload_batches WHERE id = :i"), {"i": batch_id},
            )
        check(
            "source='referral' persists on upload_batches (create_batch, direct)",
            persisted_source == "referral", str(persisted_source),
        )

    # =======================================================================
    # 9. No-write guarantee — every analytics/check-in read above must not
    # have moved a single enrolment's status or added a stage_transitions row.
    # =======================================================================
    async with factory() as db:
        after_snapshot = await _snapshot(db, company=company_a)
    check(
        "not one enrolment status changed across every read in this smoke",
        before_snapshot[0] == after_snapshot[0],
        f"before={before_snapshot[0]} after={after_snapshot[0]}",
    )
    check(
        "the stage_transitions ledger gained no rows either",
        before_snapshot[1] == after_snapshot[1],
        f"before={before_snapshot[1]} after={after_snapshot[1]}",
    )

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())

