"""PH5 Wave 1 / C2 — the governed metric layer, against real Postgres.

Flag semantics per stage (including the legacy-attribution edge cases and a
reversed hire), cohort windows, source grouping with the unknown bucket,
small-cell suppression, tenant isolation, permissions on all three routes,
the members drill-down and its audit row, and the CROSS-CONSUMER CONSISTENCY
guarantee: the same cohort, read through five different consumers, must
agree.

Registry validation and the lock file are covered offline in
``tests/unit/test_ph5_w1_metrics_definitions.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import issue_access_token
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.metrics.compute import CohortWindow, FunnelFilters, compute_funnel, compute_members

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A session whose writes roll back at the end of the test — for tests
    that call `compute_funnel`/`compute_members` directly, in-process."""
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
    )
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def committed_db() -> AsyncIterator[AsyncSession]:
    """A session that COMMITS — for tests that also drive the ASGI app,
    which reads through its OWN connection and cannot see an uncommitted
    write on this one. This is our own throwaway ``ph5_w1_mx`` database
    (never shared Neon), so nothing here needs cleaning up afterwards: every
    test uses fresh random ids.
    """
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
    )
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0,
    ) as ac, app.router.lifespan_context(app):
        yield ac


def _token(user_id: uuid.UUID, roles: list[str]) -> str:
    return issue_access_token(
        str(user_id), roles, settings.jwt_secret, issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )


def _auth(user_id: uuid.UUID, roles: list[str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id, roles)}"}


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
@dataclass
class Seed:
    company: uuid.UUID
    hr: uuid.UUID
    admin: uuid.UUID
    interviewer: uuid.UUID
    candidate: uuid.UUID
    requisition: uuid.UUID
    round_id: uuid.UUID
    competency_id: str = "communication"
    _n: int = field(default=0)

    def next_tag(self) -> str:
        self._n += 1
        return f"{self.company.hex[:6]}-{self._n}"


async def _seed_company(db: AsyncSession, *, tag: str | None = None) -> Seed:
    tag = tag or uuid.uuid4().hex[:8]
    company = uuid.uuid4()
    hr, admin, interviewer, candidate = (uuid.uuid4() for _ in range(4))
    requisition = uuid.uuid4()
    workflow_id = uuid.uuid4()
    round_id = uuid.uuid4()

    await db.execute(
        text("INSERT INTO companies (id, name, slug) VALUES (:c, :n, :s)"),
        {"c": company, "n": f"W1 {tag}", "s": f"w1-{tag}"},
    )
    for uid, email, roles_note in (
        (hr, f"hr-{tag}@w1.test", "hr_manager"),
        (admin, f"admin-{tag}@w1.test", "super_admin"),
        (interviewer, f"iv-{tag}@w1.test", "interviewer"),
        (candidate, f"cand-{tag}@w1.test", "candidate"),
    ):
        await db.execute(
            text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
            {"u": uid, "e": email, "c": company if roles_note != "candidate" else None},
        )
    await db.execute(
        text(
            "INSERT INTO job_requisitions (id, company_id, title, status, created_at, updated_at)"
            " VALUES (:r, :c, 'Engineer', 'open', now(), now())"
        ),
        {"r": requisition, "c": company},
    )
    # 'draft': the workflow's own review status is irrelevant to this suite —
    # nothing here reads `workflows.status` — and the lifecycle trigger
    # refuses an INSERT at any other status ("must be created as an
    # unreviewed draft").
    await db.execute(
        text(
            "INSERT INTO workflows (id, company_id, requisition_id, version, status,"
            " created_at, updated_at)"
            " VALUES (:w, :c, :r, 1, 'draft', now(), now())"
        ),
        {"w": workflow_id, "c": company, "r": requisition},
    )
    await db.execute(
        text(
            "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
            " created_at, updated_at)"
            " VALUES (:rd, :c, :w, 0, 'Panel', 'human_review', now(), now())"
        ),
        {"rd": round_id, "c": company, "w": workflow_id},
    )
    await db.execute(
        text(
            "INSERT INTO round_criteria (id, company_id, round_id, competency_id,"
            " competency_name, weight, created_at)"
            " VALUES (:id, :c, :rd, 'communication', 'Communication', 1.0, now())"
        ),
        {"id": uuid.uuid4(), "c": company, "rd": round_id},
    )
    return Seed(
        company=company, hr=hr, admin=admin, interviewer=interviewer, candidate=candidate,
        requisition=requisition, round_id=round_id,
    )


async def _application(
    db: AsyncSession, seed: Seed, *, source: str = "direct", status: str = "new",
    created_at: datetime | None = None, offer_outcome: str | None = None,
) -> uuid.UUID:
    applicant_id = uuid.uuid4()
    enrolment_id = uuid.uuid4()
    tag = seed.next_tag()
    created_at = created_at or datetime.now(tz=UTC)
    await db.execute(
        text(
            "INSERT INTO applicants (id, company_id, full_name, target_job_title, created_at)"
            " VALUES (:a, :c, :n, 'Engineer', :ts)"
        ),
        {"a": applicant_id, "c": seed.company, "n": f"Applicant {tag}", "ts": created_at},
    )
    await db.execute(
        text(
            "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id,"
            " target_job_title, status, source, offer_outcome, created_at, updated_at)"
            " VALUES (:e, :c, :r, :a, 'Engineer', :st, :src, :oo, :ts, :ts)"
        ),
        {
            "e": enrolment_id, "c": seed.company, "r": seed.requisition, "a": applicant_id,
            "st": status, "src": source, "oo": offer_outcome, "ts": created_at,
        },
    )
    return enrolment_id


async def _transition(
    db: AsyncSession, seed: Seed, enrolment_id: uuid.UUID, to_status: str,
    occurred_at: datetime | None = None,
) -> None:
    # PH4-O4: a move into hired/rejected needs a (reason_code, reason_label)
    # pair — any free text satisfies the trigger, which only checks presence.
    needs_reason = to_status in ("hired", "rejected")
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, to_status, automated,"
            " occurred_at, reason_code, reason_label) VALUES (:c, :e, :s, false, :ts, :rc, :rl)"
        ),
        {
            "c": seed.company, "e": enrolment_id, "s": to_status,
            "ts": occurred_at or datetime.now(tz=UTC),
            "rc": "test_reason" if needs_reason else None,
            "rl": "Test reason" if needs_reason else None,
        },
    )


async def _exam_attempt(
    db: AsyncSession, seed: Seed, applicant_id: uuid.UUID, *,
    assignment_enrolment_id: uuid.UUID | None | Any = "unset",
) -> None:
    """A submitted exam attempt. ``assignment_enrolment_id``: an enrolment id
    links it directly; ``None`` gives it an assignment with NO enrolment link
    (the legacy-attribution case); the sentinel default gives it NO
    assignment at all (also legacy-attribution, per application_progress)."""
    exam_id, round_id, attempt_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO exams (id, company_id, title, kind, created_at, updated_at)"
            " VALUES (:e, :c, 'Test exam', 'mcq', now(), now())"
        ),
        {"e": exam_id, "c": seed.company},
    )
    await db.execute(
        text(
            "INSERT INTO exam_rounds (id, company_id, exam_id, round_number, title, position,"
            " created_at, updated_at) VALUES (:r, :c, :e, 1, 'Round 1', 0, now(), now())"
        ),
        {"r": round_id, "c": seed.company, "e": exam_id},
    )
    assignment_id = None
    if assignment_enrolment_id != "unset":
        assignment_id = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO exam_assignments (id, company_id, exam_id, round_id, applicant_id,"
                " enrolment_id, token_hash, expires_at, created_at, updated_at)"
                " VALUES (:i, :c, :e, :r, :a, :en, :th, now() + interval '7 days', now(), now())"
            ),
            {
                "i": assignment_id, "c": seed.company, "e": exam_id, "r": round_id,
                "a": applicant_id, "en": assignment_enrolment_id, "th": uuid.uuid4().hex,
            },
        )
    await db.execute(
        text(
            "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id,"
            " assignment_id, status, started_at, submitted_at, created_at, updated_at)"
            " VALUES (:i, :c, :e, :r, :a, :asn, 'submitted', now(), now(), now(), now())"
        ),
        {
            "i": attempt_id, "c": seed.company, "e": exam_id, "r": round_id, "a": applicant_id,
            "asn": assignment_id,
        },
    )


async def _round_result(db: AsyncSession, seed: Seed, enrolment_id: uuid.UUID) -> None:
    await db.execute(
        text(
            "INSERT INTO round_results (id, company_id, enrolment_id, round_id, graded_by,"
            " created_at) VALUES (:i, :c, :e, :r, 'deterministic', now())"
        ),
        {"i": uuid.uuid4(), "c": seed.company, "e": enrolment_id, "r": seed.round_id},
    )


async def _ai_interview_completed(
    db: AsyncSession, seed: Seed, applicant_id: uuid.UUID,
    enrolment_id: uuid.UUID | None | Any = "unset",
) -> None:
    job_id = uuid.uuid4()
    session_id = uuid.uuid4()
    invite_id = uuid.uuid4()
    guest_user = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"
        ),
        {"u": guest_user, "e": f"guest-{guest_user.hex[:8]}@w1.test", "c": seed.company},
    )
    # `jobs` is global (candidate self-serve practice) — no company_id.
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
    enrolment_val = None if enrolment_id == "unset" else enrolment_id
    await db.execute(
        text(
            "INSERT INTO interview_invites (id, company_id, applicant_id, job_id, session_id,"
            " enrolment_id, token_hash, expires_at, status, created_at, updated_at)"
            " VALUES (:i, :c, :a, :j, :s, :e, :th, now() + interval '7 days', 'completed',"
            " now(), now())"
        ),
        {
            "i": invite_id, "c": seed.company, "a": applicant_id, "j": job_id, "s": session_id,
            "e": enrolment_val, "th": uuid.uuid4().hex,
        },
    )


async def _human_scorecard(
    db: AsyncSession, seed: Seed, enrolment_id: uuid.UUID, *, interviewer: uuid.UUID | None = None,
    status: str = "submitted", score: int | None = 4, scorecard_id: uuid.UUID | None = None,
    corrects_id: uuid.UUID | None = None,
) -> uuid.UUID:
    scorecard_id = scorecard_id or uuid.uuid4()
    interviewer = interviewer or seed.interviewer
    correction_reason = "fixing a typo in the score" if corrects_id is not None else None
    # The scores trigger refuses an INSERT once the parent is 'submitted' —
    # insert as 'in_progress', add the scores, THEN move to the requested
    # status, exactly the order a real interviewer's draft-then-submit flow
    # produces.
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, status, corrects_id, correction_reason, created_at, updated_at)"
            " VALUES (:i, :c, :e, :r, :iv, 'in_progress', :corrects, :reason, now(), now())"
        ),
        {
            "i": scorecard_id, "c": seed.company, "e": enrolment_id, "r": seed.round_id,
            "iv": interviewer, "corrects": corrects_id, "reason": correction_reason,
        },
    )
    if score is not None:
        await db.execute(
            text(
                "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
                " competency_id, score, not_assessed, updated_at)"
                " VALUES (:sc, :c, :r, :comp, :score, false, now())"
            ),
            {
                "sc": scorecard_id, "c": seed.company, "r": seed.round_id,
                "comp": seed.competency_id, "score": score,
            },
        )
    if status == "submitted":
        await db.execute(
            text(
                "UPDATE interviewer_scorecards SET status = 'submitted', submitted_at = now()"
                " WHERE id = :i"
            ),
            {"i": scorecard_id},
        )
    return scorecard_id


async def _offer(
    db: AsyncSession, seed: Seed, enrolment_id: uuid.UUID, applicant_id: uuid.UUID, *,
    final: str = "sent", start_date: Any = None,
) -> uuid.UUID:
    offer_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO offers (id, company_id, enrolment_id, applicant_id, job_title,"
            " base_salary, start_date, created_by_user_id)"
            " VALUES (:o, :c, :e, :a, 'Engineer', 1200000, :sd, :hr)"
        ),
        {"o": offer_id, "c": seed.company, "e": enrolment_id, "a": applicant_id, "sd": start_date,
         "hr": seed.hr},
    )
    if final == "draft":
        return offer_id
    await db.execute(
        text("UPDATE offers SET status = 'pending_approval', submitted_by_user_id = :u WHERE id = :o"),
        {"u": seed.hr, "o": offer_id},
    )
    await db.execute(
        text("UPDATE offers SET status = 'approved', decided_by_user_id = :u WHERE id = :o"),
        {"u": seed.admin, "o": offer_id},
    )
    if final == "approved":
        return offer_id
    if final == "withdrawn":
        # Withdrawn from 'approved' — never reached the candidate. A second,
        # LIVE offer can then be created for the same enrolment, since
        # withdrawn is not one of the "live" statuses the partial unique
        # index (one offer in play per application) counts.
        await db.execute(
            text(
                "UPDATE offers SET status = 'withdrawn', withdrawn_by_user_id = :u,"
                " withdrawn_at = now() WHERE id = :o"
            ),
            {"u": seed.hr, "o": offer_id},
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
    if final == "expired":
        await db.execute(
            text("UPDATE offers SET expires_at = now() - interval '1 day' WHERE id = :o"),
            {"o": offer_id},
        )
        await db.execute(text("UPDATE offers SET status = 'expired' WHERE id = :o"), {"o": offer_id})
        return offer_id
    # accepted
    await db.execute(
        text(
            "UPDATE offers SET status = 'accepted', responded_at = now(),"
            " accepted_name = 'Test Candidate' WHERE id = :o"
        ),
        {"o": offer_id},
    )
    return offer_id


async def _hire_checkin(
    db: AsyncSession, seed: Seed, enrolment_id: uuid.UUID, *, employment: str = "employed",
    performance: str | None = "meets", left_reason: str | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind, employment,"
            " left_reason, performance, recorded_by_user_id, recorded_at, created_at, updated_at)"
            " VALUES (:i, :c, :e, '90_day', :emp, :lr, :perf, :hr, now(), now(), now())"
        ),
        {
            "i": uuid.uuid4(), "c": seed.company, "e": enrolment_id, "emp": employment,
            "lr": left_reason, "perf": performance, "hr": seed.hr,
        },
    )


async def _hire(db: AsyncSession, seed: Seed, enrolment_id: uuid.UUID, hired_at: datetime | None = None,
                offer_outcome: str | None = None) -> None:
    hired_at = hired_at or datetime.now(tz=UTC)
    await db.execute(
        text("UPDATE enrolments SET status = 'hired', offer_outcome = :oo WHERE id = :e"),
        {"e": enrolment_id, "oo": offer_outcome},
    )
    await _transition(db, seed, enrolment_id, "hired", occurred_at=hired_at)


# ===========================================================================
# Flag semantics
# ===========================================================================
async def test_screened_reads_the_ledger(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    screened = await _application(db, seed)
    await _transition(db, seed, screened, "shortlisted")
    await _application(db, seed)  # unscreened

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    m = result.groups[0].metrics
    assert m["applications"]["value"] == 2
    assert m["screened"]["value"] == 1


async def test_assessed_without_a_screen_still_counts(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    enrolment = await _application(db, seed)
    applicant_id = (
        await db.execute(text("SELECT applicant_id FROM enrolments WHERE id = :e"), {"e": enrolment})
    ).scalar_one()
    await _exam_attempt(db, seed, applicant_id, assignment_enrolment_id=enrolment)

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    m = result.groups[0].metrics
    assert m["assessed"]["value"] == 1
    assert m["screened"]["value"] == 0


async def test_assessed_via_round_result_with_no_exam(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    enrolment = await _application(db, seed)
    await _round_result(db, seed, enrolment)

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    assert result.groups[0].metrics["assessed"]["value"] == 1


async def test_legacy_attribution_credits_the_only_application(db: AsyncSession) -> None:
    """An exam attempt whose assignment has NO enrolment link is credited to
    the application when the person has exactly one."""
    seed = await _seed_company(db)
    enrolment = await _application(db, seed)
    applicant_id = (
        await db.execute(text("SELECT applicant_id FROM enrolments WHERE id = :e"), {"e": enrolment})
    ).scalar_one()
    await _exam_attempt(db, seed, applicant_id, assignment_enrolment_id=None)

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    assert result.groups[0].metrics["assessed"]["value"] == 1


async def test_legacy_attribution_does_not_bleed_across_two_applications(db: AsyncSession) -> None:
    """The PH5-C2 fix: with TWO live applications, an unlinked attempt credits
    neither — it is no longer a guess which one it belongs to."""
    seed = await _seed_company(db)
    applicant_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
            " VALUES (:a, :c, 'Two Apps', 'Engineer')"
        ),
        {"a": applicant_id, "c": seed.company},
    )
    # (requisition_id, applicant_id) is unique — a second LIVE application for
    # the same person needs a second opening, exactly as "someone applies to
    # two roles" does in production.
    second_requisition = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO job_requisitions (id, company_id, title, status, created_at, updated_at)"
            " VALUES (:r, :c, 'Second Role', 'open', now(), now())"
        ),
        {"r": second_requisition, "c": seed.company},
    )
    for requisition_id in (seed.requisition, second_requisition):
        await db.execute(
            text(
                "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id,"
                " target_job_title, status, source, created_at, updated_at)"
                " VALUES (:e, :c, :r, :a, 'Engineer', 'new', 'direct', now(), now())"
            ),
            {"e": uuid.uuid4(), "c": seed.company, "r": requisition_id, "a": applicant_id},
        )
    await _exam_attempt(db, seed, applicant_id, assignment_enrolment_id=None)

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    assert result.groups[0].metrics["assessed"]["value"] == 0
    assert result.groups[0].metrics["applications"]["value"] == 2


async def test_interviewed_is_not_the_ledgers_interviewed_status(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    enrolment = await _application(db, seed, status="interviewed")
    await _transition(db, seed, enrolment, "interviewed")

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    assert result.groups[0].metrics["interviewed"]["value"] == 0


async def test_interviewed_via_ai_session(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    enrolment = await _application(db, seed)
    applicant_id = (
        await db.execute(text("SELECT applicant_id FROM enrolments WHERE id = :e"), {"e": enrolment})
    ).scalar_one()
    await _ai_interview_completed(db, seed, applicant_id, enrolment_id=enrolment)

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    assert result.groups[0].metrics["interviewed"]["value"] == 1


async def test_interviewed_via_human_scorecard_only(db: AsyncSession) -> None:
    """No AI session, no ledger 'interviewed' — just a submitted human
    scorecard. Still interviewed."""
    seed = await _seed_company(db)
    enrolment = await _application(db, seed)
    await _human_scorecard(db, seed, enrolment)

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    assert result.groups[0].metrics["interviewed"]["value"] == 1


async def test_a_withdrawn_correction_leaves_the_original_as_current(db: AsyncSession) -> None:
    """CURRENT_SCORECARD_SQL's 'last word' rule.

    A correction is opened (a new draft, ``in_progress``) against a submitted
    scorecard, which supersedes it — then the correction itself is withdrawn
    BEFORE it is ever submitted (the trigger allows ``status`` to change only
    while a scorecard is not yet submitted or withdrawn, matching the design
    note "a correction was withdrawn with its interviewer's removal": the
    draft is abandoned, never completed). The ORIGINAL, submitted score
    counts as current again.
    """
    seed = await _seed_company(db)
    enrolment = await _application(db, seed)
    original = await _human_scorecard(db, seed, enrolment, score=3)
    correction = uuid.uuid4()
    # Supersede FIRST, then insert the correction: the live partial unique
    # index (round_id, enrolment_id, interviewer_user_id) refuses the new row
    # while the original is still live — the same order app.hire_checkins
    # .correct() uses.
    await db.execute(
        text("UPDATE interviewer_scorecards SET superseded_at = now(), superseded_by_id = :n"
             " WHERE id = :o"),
        {"n": correction, "o": original},
    )
    await _human_scorecard(
        db, seed, enrolment, score=5, corrects_id=original, scorecard_id=correction,
        status="in_progress",
    )
    await db.execute(
        text("UPDATE interviewer_scorecards SET status = 'withdrawn', withdrawn_at = now(),"
             " withdrawn_reason = 'interviewer left the panel before finishing this correction'"
             " WHERE id = :c"),
        {"c": correction},
    )

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    assert result.groups[0].metrics["interviewed"]["value"] == 1

    # hire_interviewer_score reads the ORIGINAL's score (3), not the withdrawn
    # correction's (5), once this application is a standing hire. Four filler
    # hires at the SAME score keep the cohort at n=5 (the suppression floor):
    # if the bug this guards against reappeared (the correction's score
    # counted instead), the mean would read 3.4, not 3.0 — still visible, not
    # suppressed to null.
    await _hire(db, seed, enrolment)
    for _ in range(4):
        filler = await _application(db, seed)
        await _human_scorecard(db, seed, filler, score=3)
        await _hire(db, seed, filler)

    hire_result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
    )
    score = hire_result.groups[0].metrics["hire_interviewer_score"]
    assert score["suppressed"] is False
    assert score["value"] == 3.0


async def test_a_reversed_hire_is_not_hired(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    enrolment = await _application(db, seed)
    await _hire(db, seed, enrolment, offer_outcome="offer_declined")

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    m = result.groups[0].metrics
    assert m["hires"]["value"] == 0
    # But the company DID choose them — selected still holds via ever_hired.
    assert m["selected"]["value"] == 1


async def test_a_standing_hire_is_hired(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    enrolment = await _application(db, seed)
    await _hire(db, seed, enrolment)

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    assert result.groups[0].metrics["hires"]["value"] == 1


async def test_a_declined_offer_is_selected_but_not_accepted(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    enrolment = await _application(db, seed)
    applicant_id = (
        await db.execute(text("SELECT applicant_id FROM enrolments WHERE id = :e"), {"e": enrolment})
    ).scalar_one()
    await _offer(db, seed, enrolment, applicant_id, final="declined")

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    m = result.groups[0].metrics
    assert m["selected"]["value"] == 1
    assert m["applications"]["value"] == 1


# ===========================================================================
# Double-counting regressions — every flag is EXISTS, never a row count
# ===========================================================================
async def test_multiple_rows_per_application_never_double_count(db: AsyncSession) -> None:
    """One application with TWO submitted exam attempts, TWO current human
    scorecards from TWO different interviewers, and TWO offers (one
    withdrawn, one sent) — every flag is an EXISTS predicate over its base
    table, never a COUNT, so `applications`/`assessed`/`interviewed`/
    `selected` must each still read exactly 1, not 2."""
    seed = await _seed_company(db)
    enrolment = await _application(db, seed)
    applicant_id = (
        await db.execute(text("SELECT applicant_id FROM enrolments WHERE id = :e"), {"e": enrolment})
    ).scalar_one()

    # Two submitted exam attempts, both directly linked to this enrolment.
    await _exam_attempt(db, seed, applicant_id, assignment_enrolment_id=enrolment)
    await _exam_attempt(db, seed, applicant_id, assignment_enrolment_id=enrolment)

    # Two CURRENT (submitted, live) human scorecards from two interviewers.
    second_interviewer = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
        {"u": second_interviewer, "e": f"iv2-{second_interviewer.hex[:8]}@w1.test", "c": seed.company},
    )
    await _human_scorecard(db, seed, enrolment, interviewer=seed.interviewer, score=4)
    await _human_scorecard(db, seed, enrolment, interviewer=second_interviewer, score=2)

    # Two offers: one withdrawn (never reached the candidate), one sent.
    await _offer(db, seed, enrolment, applicant_id, final="withdrawn")
    await _offer(db, seed, enrolment, applicant_id, final="sent")

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    m = result.groups[0].metrics
    assert m["applications"]["value"] == 1
    assert m["assessed"]["value"] == 1
    assert m["interviewed"]["value"] == 1
    assert m["selected"]["value"] == 1


# ===========================================================================
# checkin_due — the employment-start clock (mirrors app.hire_checkins exactly)
# ===========================================================================
async def test_checkin_due_uses_the_accepted_offer_start_date(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    due = await _application(db, seed)
    applicant_due = (
        await db.execute(text("SELECT applicant_id FROM enrolments WHERE id = :e"), {"e": due})
    ).scalar_one()
    await _offer(
        db, seed, due, applicant_due, final="accepted",
        start_date=(datetime.now(tz=UTC) - timedelta(days=95)).date(),
    )
    await _hire(db, seed, due, hired_at=datetime.now(tz=UTC) - timedelta(days=95))

    not_due = await _application(db, seed)
    applicant_not_due = (
        await db.execute(text("SELECT applicant_id FROM enrolments WHERE id = :e"), {"e": not_due})
    ).scalar_one()
    await _offer(
        db, seed, not_due, applicant_not_due, final="accepted",
        start_date=(datetime.now(tz=UTC) - timedelta(days=70)).date(),
    )
    await _hire(db, seed, not_due, hired_at=datetime.now(tz=UTC) - timedelta(days=70))

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
    )
    m = result.groups[0].metrics
    # checkin_coverage's DENOMINATOR is checkin_due — the population, with no
    # check-in recorded for either hire yet.
    assert m["checkin_coverage"]["denominator"] == 1


async def test_checkin_due_falls_back_to_the_ledgers_hire_date(db: AsyncSession) -> None:
    """No offer at all (or none accepted) — the ledger's first move into
    hired is the employment-start clock, exactly as app.hire_checkins.
    employment_start falls back."""
    seed = await _seed_company(db)
    due = await _application(db, seed)
    await _hire(db, seed, due, hired_at=datetime.now(tz=UTC) - timedelta(days=95))
    not_due = await _application(db, seed)
    await _hire(db, seed, not_due, hired_at=datetime.now(tz=UTC) - timedelta(days=70))

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
    )
    assert result.groups[0].metrics["checkin_coverage"]["denominator"] == 1


async def test_checkin_recorded_covers_the_due_hire(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    enrolment = await _application(db, seed)
    await _hire(db, seed, enrolment, hired_at=datetime.now(tz=UTC) - timedelta(days=95))
    await _hire_checkin(db, seed, enrolment, employment="employed", performance="meets")

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
    )
    m = result.groups[0].metrics
    assert m["checkin_coverage"]["numerator"] == 1
    assert m["checkin_coverage"]["denominator"] == 1
    # retention_90d's denominator (checkin_recorded, operational) is never
    # nulled; its NUMERATOR is an outcome and is suppressed at n=1 < 5 — see
    # test_checkin_outcome_suppression_also_nulls_the_numerator for that rule
    # asserted directly.
    assert m["retention_90d"]["denominator"] == 1


async def test_time_to_hire_and_interviewer_score_stay_visible_when_suppressed(
    db: AsyncSession,
) -> None:
    """C2-13: neither `time_to_hire_days` nor `hire_interviewer_score` reads a
    check-in flag, so — unlike `retention_90d`/`performance_90d` — a small
    cohort still shows its real value, only flagged `suppressed: true`."""
    seed = await _seed_company(db)
    enrolment = await _application(db, seed, created_at=datetime.now(tz=UTC) - timedelta(days=20))
    await _human_scorecard(db, seed, enrolment, score=4)
    await _hire(db, seed, enrolment, hired_at=datetime.now(tz=UTC) - timedelta(days=10))

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
    )
    m = result.groups[0].metrics
    assert m["time_to_hire_days"]["n"] == 1
    assert m["time_to_hire_days"]["suppressed"] is True
    assert m["time_to_hire_days"]["value"] == pytest.approx(10.0, abs=0.1)
    assert m["hire_interviewer_score"]["n"] == 1
    assert m["hire_interviewer_score"]["suppressed"] is True
    assert m["hire_interviewer_score"]["value"] == 4.0


async def test_time_to_hire_days_is_the_median_not_the_mean(db: AsyncSession) -> None:
    """Evidence audit follow-up: no test before this one could tell a median
    from any other percentile, because every ``time_to_hire_days`` population
    in the suite was one row or all rows with the same measure — swapping
    ``_plan_metric``'s ``percentile_cont(0.5)`` for ``percentile_cont(0.9)``
    (or any other value) would have passed every existing assertion.

    Five DIFFERENT times to hire (2, 4, 10, 20, 40 days) fix that: the median
    is 10, the mean is 15.2, and the 90th percentile (Postgres's default
    linear interpolation over these five sorted values) is 32 — three
    different numbers, so asserting exactly 10 actually pins down
    ``percentile_cont(0.5)`` specifically, not merely "some aggregate"."""
    seed = await _seed_company(db)
    now = datetime.now(tz=UTC)
    for days in (2, 4, 10, 20, 40):
        enrolment = await _application(db, seed, created_at=now - timedelta(days=days))
        await _hire(db, seed, enrolment, hired_at=now)

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
    )
    m = result.groups[0].metrics
    assert m["time_to_hire_days"]["n"] == 5
    assert m["time_to_hire_days"]["suppressed"] is False
    median = m["time_to_hire_days"]["value"]
    assert median == pytest.approx(10.0, abs=0.1)
    assert median != pytest.approx(15.2, abs=0.5)  # the mean
    assert median != pytest.approx(32.0, abs=0.5)  # the 90th percentile


# ===========================================================================
# Cohorts
# ===========================================================================
async def test_application_cohort_filters_by_created_at(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    old = datetime.now(tz=UTC) - timedelta(days=40)
    await _application(db, seed, created_at=old)
    recent = datetime.now(tz=UTC) - timedelta(days=1)
    await _application(db, seed, created_at=recent)

    window = CohortWindow(
        basis="application", from_=(datetime.now(tz=UTC) - timedelta(days=10)).date(), to_=None,
    )
    result = await compute_funnel(db, company_id=seed.company, cohort=window, filters=FunnelFilters())
    assert result.groups[0].metrics["applications"]["value"] == 1


async def test_hire_cohort_filters_by_hired_at_not_created_at(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    enrolment = await _application(db, seed, created_at=datetime.now(tz=UTC) - timedelta(days=200))
    await _hire(db, seed, enrolment, hired_at=datetime.now(tz=UTC) - timedelta(days=1))

    window = CohortWindow(
        basis="hire", from_=(datetime.now(tz=UTC) - timedelta(days=10)).date(), to_=None,
    )
    result = await compute_funnel(db, company_id=seed.company, cohort=window, filters=FunnelFilters())
    assert result.groups[0].metrics["hires"]["value"] == 1


async def _reject(
    db: AsyncSession, seed: Seed, enrolment_id: uuid.UUID, occurred_at: datetime | None = None,
) -> None:
    await db.execute(
        text("UPDATE enrolments SET status = 'rejected', updated_at = now() WHERE id = :e"),
        {"e": enrolment_id},
    )
    await _transition(db, seed, enrolment_id, "rejected", occurred_at=occurred_at)


async def test_decision_cohort_counts_only_applications_decided_in_the_window(
    db: AsyncSession,
) -> None:
    """The `decision` cohort basis has no pytest of its own before this — only
    the smoke test covers it. All four applications here were created long
    before the window; only when each was DECIDED (the ledger's first move
    to hired or rejected) determines whether the `decision` cohort counts it,
    and the RATE's denominator is that same decided population, not every
    application that ever existed."""
    seed = await _seed_company(db)
    old_created = datetime.now(tz=UTC) - timedelta(days=200)
    within_window = datetime.now(tz=UTC) - timedelta(days=10)

    rejected_in_window = await _application(db, seed, created_at=old_created)
    await _reject(db, seed, rejected_in_window, occurred_at=datetime.now(tz=UTC) - timedelta(days=5))

    hired_in_window = await _application(db, seed, created_at=old_created)
    await _hire(db, seed, hired_in_window, hired_at=datetime.now(tz=UTC) - timedelta(days=3))

    rejected_outside_window = await _application(db, seed, created_at=old_created)
    await _reject(db, seed, rejected_outside_window, occurred_at=old_created)

    await _application(db, seed, created_at=old_created)  # never decided ('new')

    window = CohortWindow(basis="decision", from_=within_window.date(), to_=None)
    result = await compute_funnel(db, company_id=seed.company, cohort=window, filters=FunnelFilters())
    m = result.groups[0].metrics

    # Only the two decided INSIDE the window are in the cohort at all —
    # neither the one decided too early nor the one never decided counts.
    assert m["applications"]["value"] == 2
    assert m["hires"]["value"] == 1
    assert m["rejections"]["value"] == 1

    # The rate's denominator is the DECIDED population (2), not all four
    # applications that exist for this company.
    rate = m["application_to_hire"]
    assert rate["denominator"] == 2
    assert rate["numerator"] == 1
    assert rate["value"] == pytest.approx(50.0)


# ===========================================================================
# Source grouping, including Unknown / untracked
# ===========================================================================
async def test_unknown_source_is_its_own_group(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    await _application(db, seed, source="referral")
    await _application(db, seed, source="unknown")

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(), group_by="source",
    )
    by_key = {g.key: g for g in result.groups}
    assert by_key["unknown"].label == "Unknown / untracked"
    assert by_key["unknown"].metrics["applications"]["value"] == 1
    assert by_key["referral"].metrics["applications"]["value"] == 1
    assert result.groups[0].key is None and result.groups[0].label == "All"


# ===========================================================================
# Suppression
# ===========================================================================
async def test_small_pipeline_cohorts_are_suppressed_but_stay_visible(db: AsyncSession) -> None:
    """C2-13 (lead policy): null-on-suppression is a check-in-data-only rule.
    An ordinary hiring-pipeline rate below the floor still carries its real
    `value`/`numerator` — `suppressed` is an ADVISORY flag only, so a small
    company's `/hr/analytics` keeps showing real numbers."""
    seed = await _seed_company(db)
    for _ in range(4):
        e = await _application(db, seed)
        await _hire(db, seed, e)

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    rate = result.groups[0].metrics["application_to_hire"]
    assert rate["denominator"] == 4
    assert rate["suppressed"] is True
    assert rate["value"] == 100.0
    assert rate["numerator"] == 4


async def test_checkin_outcome_suppression_also_nulls_the_numerator(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    enrolment = await _application(db, seed)
    await _hire(db, seed, enrolment, hired_at=datetime.now(tz=UTC) - timedelta(days=95))
    await _hire_checkin(db, seed, enrolment, employment="employed", performance="meets")

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
    )
    retention = result.groups[0].metrics["retention_90d"]
    assert retention["denominator"] == 1
    assert retention["suppressed"] is True
    assert retention["value"] is None
    assert retention["numerator"] is None  # PH5-C1: an outcome flag nulls this too


async def test_a_visible_group_cannot_reveal_a_suppressed_ones_cell_by_subtraction(
    db: AsyncSession,
) -> None:
    """Security review T2-1b, the single-response subtraction attack.

    "job_board" is 4-of-5 retained — NOT suppressed on its own (n=5). "referral"
    is 0-of-1 — suppressed (n=1 < 5). "All" is the sum, 4-of-6. If job_board's
    exact cell shipped while referral's did not, a reader recovers referral's
    hidden cell as (All numerator - job_board numerator, All denominator -
    job_board denominator) = (0, 1) exactly. So BOTH group rows must be
    suppressed once ANY of them is — "All" is exempt, since it is the
    subtraction's target, not one of its inputs.
    """
    seed = await _seed_company(db)
    hired_95_days_ago = datetime.now(tz=UTC) - timedelta(days=95)
    for i in range(5):
        e = await _application(db, seed, source="job_board")
        await _hire(db, seed, e, hired_at=hired_95_days_ago)
        await _hire_checkin(
            db, seed, e,
            employment="employed" if i < 4 else "left",
            performance="meets" if i < 4 else None,
            left_reason=None if i < 4 else "voluntary",
        )
    e_referral = await _application(db, seed, source="referral")
    await _hire(db, seed, e_referral, hired_at=hired_95_days_ago)
    await _hire_checkin(
        db, seed, e_referral, employment="left", performance=None, left_reason="voluntary",
    )

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
        group_by="source",
    )
    by_key = {g.key: g for g in result.groups}

    all_retention = by_key[None].metrics["retention_90d"]
    assert all_retention["numerator"] == 4
    assert all_retention["denominator"] == 6
    assert all_retention["suppressed"] is False
    assert all_retention["value"] == pytest.approx(66.7, abs=0.1)

    job_board = by_key["job_board"].metrics["retention_90d"]
    referral = by_key["referral"].metrics["retention_90d"]
    # Both hidden, even though job_board's own n=5 would not, on its own,
    # have been below the floor.
    assert job_board["suppressed"] is True
    assert job_board["value"] is None
    assert job_board["numerator"] is None
    assert job_board["denominator"] == 5  # denominator (coverage) is never hidden
    assert referral["suppressed"] is True
    assert referral["value"] is None
    assert referral["numerator"] is None
    assert referral["denominator"] == 1


async def test_counts_are_never_suppressed(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    await _application(db, seed)
    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    assert result.groups[0].metrics["applications"]["value"] == 1
    assert "suppressed" not in result.groups[0].metrics["applications"]


# ===========================================================================
# Tenant isolation
# ===========================================================================
async def test_company_b_sees_nothing_of_companys_as(db: AsyncSession) -> None:
    seed_a = await _seed_company(db)
    seed_b = await _seed_company(db)
    await _application(db, seed_a)
    await _application(db, seed_a)

    result_b = await compute_funnel(
        db, company_id=seed_b.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    assert result_b.groups[0].metrics["applications"]["value"] == 0


async def test_a_foreign_requisition_id_yields_zero_not_an_error(db: AsyncSession) -> None:
    seed_a = await _seed_company(db)
    seed_b = await _seed_company(db)
    await _application(db, seed_a)

    result = await compute_funnel(
        db, company_id=seed_b.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(requisition_id=seed_a.requisition),
    )
    assert result.groups[0].metrics["applications"]["value"] == 0


# ===========================================================================
# `statement_timeout` is restored, not leaked into the caller's transaction
# ===========================================================================
async def test_statement_timeout_is_restored_after_compute_funnel(db: AsyncSession) -> None:
    """The requisition dashboard, a copilot turn and the watcher loop each
    keep the SAME session/transaction open across many more queries after
    calling into the metric layer — `SET LOCAL`'s own scope is "until this
    transaction ends", not "until this function returns", so without an
    explicit restore the override would silently re-time everything after
    it too. A distinctive prior value (not Postgres's default, not the
    layer's own 10s) proves the RESTORE, not merely that nothing broke.
    """
    seed = await _seed_company(db)
    await db.execute(text("SET LOCAL statement_timeout = '54321ms'"))
    await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    restored = await db.scalar(text("SELECT current_setting('statement_timeout')"))
    assert restored == "54321ms"


async def test_statement_timeout_is_restored_after_compute_members(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    await db.execute(text("SET LOCAL statement_timeout = '54321ms'"))
    await compute_members(
        db, company_id=seed.company, metric_name="applications", part="numerator",
        cohort=CohortWindow(basis="application"), filters=FunnelFilters(),
    )
    restored = await db.scalar(text("SELECT current_setting('statement_timeout')"))
    assert restored == "54321ms"


# A failed read aborts the transaction; restoring the timeout then would raise
# "current transaction is aborted" and hide the real error (a statement
# timeout, say). The restore happens on success only, so the caller sees the
# original failure. The failure here is a column the stand-in CTE lacks.
_BROKEN_WITH = "WITH app_facts AS (SELECT 1 AS nothing) "


async def test_a_failed_funnel_read_surfaces_its_own_error(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.metrics.compute as compute_mod

    seed = await _seed_company(db)
    monkeypatch.setattr(compute_mod, "_with_clause", lambda _basis: _BROKEN_WITH)
    with pytest.raises(Exception) as caught:
        await compute_funnel(
            db, company_id=seed.company, cohort=CohortWindow(basis="application"),
            filters=FunnelFilters(),
        )
    assert "does not exist" in str(caught.value)
    assert "current transaction is aborted" not in str(caught.value)


async def test_a_failed_members_read_surfaces_its_own_error(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.metrics.compute as compute_mod

    seed = await _seed_company(db)
    monkeypatch.setattr(compute_mod, "_with_clause", lambda _basis: _BROKEN_WITH)
    with pytest.raises(Exception) as caught:
        await compute_members(
            db, company_id=seed.company, metric_name="applications", part="numerator",
            cohort=CohortWindow(basis="application"), filters=FunnelFilters(),
        )
    assert "does not exist" in str(caught.value)
    assert "current transaction is aborted" not in str(caught.value)


# ===========================================================================
# Permissions (HTTP)
# ===========================================================================
async def test_hr_manager_gets_200_on_all_three_routes(
    committed_db: AsyncSession, client: AsyncClient,
) -> None:
    seed = await _seed_company(committed_db)
    await _application(committed_db, seed)
    await committed_db.commit()

    headers = _auth(seed.hr, ["hr_manager"])
    assert (await client.get("/hr/metrics/definitions", headers=headers)).status_code == 200
    assert (await client.get("/hr/analytics/funnel", headers=headers)).status_code == 200
    resp = await client.get(
        "/hr/analytics/members", headers=headers,
        params={"metric": "applications", "part": "numerator"},
    )
    assert resp.status_code == 200


async def test_super_admin_gets_403_on_funnel_and_members_but_200_on_definitions(
    committed_db: AsyncSession, client: AsyncClient,
) -> None:
    seed = await _seed_company(committed_db)
    await committed_db.commit()

    headers = _auth(seed.admin, ["super_admin"])
    assert (await client.get("/hr/metrics/definitions", headers=headers)).status_code == 200
    assert (await client.get("/hr/analytics/funnel", headers=headers)).status_code == 403
    resp = await client.get(
        "/hr/analytics/members", headers=headers,
        params={"metric": "applications", "part": "numerator"},
    )
    assert resp.status_code == 403


async def test_interviewer_gets_403_everywhere(
    committed_db: AsyncSession, client: AsyncClient,
) -> None:
    seed = await _seed_company(committed_db)
    await committed_db.commit()

    headers = _auth(seed.interviewer, ["interviewer"])
    assert (await client.get("/hr/metrics/definitions", headers=headers)).status_code == 403
    assert (await client.get("/hr/analytics/funnel", headers=headers)).status_code == 403
    resp = await client.get(
        "/hr/analytics/members", headers=headers,
        params={"metric": "applications", "part": "numerator"},
    )
    assert resp.status_code == 403


async def test_candidate_gets_403_everywhere(
    committed_db: AsyncSession, client: AsyncClient,
) -> None:
    seed = await _seed_company(committed_db)
    await committed_db.commit()

    headers = _auth(seed.candidate, ["candidate"])
    assert (await client.get("/hr/metrics/definitions", headers=headers)).status_code == 403
    assert (await client.get("/hr/analytics/funnel", headers=headers)).status_code == 403
    resp = await client.get(
        "/hr/analytics/members", headers=headers,
        params={"metric": "applications", "part": "numerator"},
    )
    assert resp.status_code == 403


# ===========================================================================
# The members drill-down and its audit row
# ===========================================================================
async def test_members_drilldown_returns_rows_and_writes_an_audit_row(
    committed_db: AsyncSession, client: AsyncClient,
) -> None:
    seed = await _seed_company(committed_db)
    enrolment = await _application(committed_db, seed, source="referral")
    await committed_db.commit()

    headers = _auth(seed.hr, ["hr_manager"])
    resp = await client.get(
        "/hr/analytics/members", headers=headers,
        params={"metric": "applications", "part": "numerator"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["metric"] == "applications"
    assert body["total"] == 1
    assert body["truncated"] is False
    assert body["rows"][0]["enrolment_id"] == str(enrolment)
    assert body["rows"][0]["source"] == "referral"

    row = (
        await committed_db.execute(
            text(
                "SELECT actor_id, action, resource_type, details FROM audit_log"
                " WHERE action = 'analytics.members_viewed' AND actor_id = :hr"
                " ORDER BY event_ts DESC LIMIT 1"
            ),
            {"hr": seed.hr},
        )
    ).mappings().one()
    assert row["action"] == "analytics.members_viewed"
    details_text = str(row["details"])
    assert "referral" not in details_text  # the source of ONE row, not logged as a fact about it
    assert row["details"]["metric"] == "applications"
    assert row["details"]["part"] == "numerator"
    assert row["details"]["total"] == 1


async def test_members_drilldown_refuses_a_checkin_outcome(
    committed_db: AsyncSession, client: AsyncClient,
) -> None:
    seed = await _seed_company(committed_db)
    await committed_db.commit()
    headers = _auth(seed.hr, ["hr_manager"])

    resp = await client.get(
        "/hr/analytics/members", headers=headers,
        params={"metric": "performance_90d", "part": "numerator", "cohort": "hire"},
    )
    assert resp.status_code == 422

    resp = await client.get(
        "/hr/analytics/members", headers=headers,
        params={"metric": "retention_90d", "part": "numerator", "cohort": "hire"},
    )
    assert resp.status_code == 422

    # But the DENOMINATOR (who was covered) is allowed.
    resp = await client.get(
        "/hr/analytics/members", headers=headers,
        params={"metric": "retention_90d", "part": "denominator", "cohort": "hire"},
    )
    assert resp.status_code == 200


# ===========================================================================
# Cross-consumer consistency
# ===========================================================================
async def test_cross_consumer_consistency(
    committed_db: AsyncSession, client: AsyncClient,
) -> None:
    """The same company, the same all-time cohort, read through FIVE
    consumers: `/hr/analytics` (conversion), `/hr/analytics/funnel`, the
    requisition dashboard, the copilot tool, and the watcher's gathered
    input. All five must agree on applications, interviewed and hires, AND
    on the application -> interview RATE (not just the counts underneath it)
    — five applications, a non-trivial (neither 0% nor 100%) interview count,
    so the rate clears the suppression floor and is asserted non-null
    everywhere it appears. PH5 Wave 1 close-out (C2-5/C1-9): `HrFunnel`'s
    `total_applications`/`interview_completed`/`hired` are no longer a second,
    independently-computed number over `application_progress` — they ARE
    `conversion`'s `applied`/`ever_interviewed`/`ever_hired`, so they must
    equal them here too (see `test_funnel_interview_completed_agrees_with_
    governed_interviewed` for the disagreement this specifically fixed).
    """
    from shared.agents import ToolContext

    from app.agents import tools as tools_module
    from app.agents.watch_runner import gather_company_input
    from app.company_board import hiring_board

    seed = await _seed_company(committed_db)

    # Five applications: three interviewed (AI session), one of those also
    # hired; two never interviewed. applications=5, interviewed=3, hires=1,
    # application_to_interview = 3/5 = 60.0%.
    interviewed_enrolments: list[uuid.UUID] = []
    for _i in range(3):
        e = await _application(committed_db, seed)
        applicant_id = (
            await committed_db.execute(
                text("SELECT applicant_id FROM enrolments WHERE id = :e"), {"e": e},
            )
        ).scalar_one()
        await _ai_interview_completed(committed_db, seed, applicant_id, enrolment_id=e)
        interviewed_enrolments.append(e)
    await _hire(committed_db, seed, interviewed_enrolments[0])
    for _ in range(2):
        await _application(committed_db, seed)
    await committed_db.commit()

    headers = _auth(seed.hr, ["hr_manager"])

    # 1. /hr/analytics (conversion + the ungoverned HrFunnel snapshot).
    analytics = (await client.get("/hr/analytics", headers=headers)).json()
    assert analytics["conversion"]["applied"] == 5
    assert analytics["conversion"]["ever_interviewed"] == 3
    assert analytics["conversion"]["ever_hired"] == 1
    assert analytics["conversion"]["pct_interviewed"] == pytest.approx(60.0)
    assert analytics["funnel"]["total_applications"] == 5  # == conversion.applied
    assert analytics["funnel"]["interview_completed"] == 3  # == conversion.ever_interviewed
    assert analytics["funnel"]["hired"] == 1  # == conversion.ever_hired

    # 2. /hr/analytics/funnel.
    funnel = (await client.get("/hr/analytics/funnel", headers=headers)).json()
    fm = funnel["groups"][0]["metrics"]
    assert fm["applications"]["value"] == 5
    assert fm["interviewed"]["value"] == 3
    assert fm["hires"]["value"] == 1
    assert fm["application_to_interview"]["value"] == pytest.approx(60.0)
    assert fm["application_to_interview"]["suppressed"] is False

    # 3. The requisition dashboard.
    dash = (
        await client.get(
            f"/hr/requisitions/{seed.requisition}/dashboard", headers=headers,
        )
    ).json()
    assert dash["progress"]["applications"] == 5
    assert dash["progress"]["hired"] == 1

    # 4. The copilot tool.
    ctx = ToolContext(
        actor_id=str(seed.hr), role="hr_manager", company_id=str(seed.company),
        resources={"db": committed_db},
    )
    _spec, handler = next(
        (s, h) for s, h in tools_module.registry._tools.values() if s.name == "get_funnel_analytics"
    )
    tool_out = await handler({}, ctx)
    overall = tool_out.data["overall"]
    assert overall["applications"]["value"] == 5
    assert overall["interviewed"]["value"] == 3
    assert overall["hires"]["value"] == 1
    assert overall["application_to_interview"]["value"] == pytest.approx(60.0)

    # Every consumer that exposes the rate agrees, and none reports null.
    rate_values = {
        "/hr/analytics": analytics["conversion"]["pct_interviewed"],
        "/hr/analytics/funnel": fm["application_to_interview"]["value"],
        "copilot get_funnel_analytics": overall["application_to_interview"]["value"],
    }
    assert all(v is not None for v in rate_values.values()), rate_values
    assert len(set(rate_values.values())) == 1, rate_values

    # 5. The watcher's gathered input (per open opening — one opening here).
    watcher_input = await gather_company_input(committed_db, str(seed.company))
    assert len(watcher_input.funnels) == 1
    assert watcher_input.funnels[0].applicants == 5
    assert watcher_input.funnels[0].interviewed == 3

    # 6. The company board (PH5-C2) — summed `hired` across its openings
    # equals the governed hire count too.
    board = await hiring_board(committed_db, company_id=seed.company)
    assert sum(o["hired"] for o in board["openings"]) == 1
    assert sum(o["applied"] for o in board["openings"]) == 5


async def test_funnel_interview_completed_agrees_with_governed_interviewed(
    committed_db: AsyncSession, client: AsyncClient,
) -> None:
    """The exact disagreement PH5 Wave 1 close-out fixes (C2-5/C1-9).

    Before this change, ``HrFunnel.interview_completed`` was
    ``scorecard_id IS NOT NULL`` on ``application_progress`` — an AI session's
    scorecard only — so a candidate interviewed by a HUMAN panel with no AI
    session at all counted on the Analytics page's ``Interviewed``
    (``conversion.ever_interviewed``, already governed) but NOT on the HR
    console's ``Interviewed`` tile (``funnel.interview_completed``). One
    AI-completed application, one human-scorecard-only application, one
    application with neither: both fields must now agree, and both must be 2
    (not 1, which is what the old AI-only count would have shown).
    """
    seed = await _seed_company(committed_db)

    ai_only = await _application(committed_db, seed)
    applicant_id = (
        await committed_db.execute(
            text("SELECT applicant_id FROM enrolments WHERE id = :e"), {"e": ai_only},
        )
    ).scalar_one()
    await _ai_interview_completed(committed_db, seed, applicant_id, enrolment_id=ai_only)

    human_only = await _application(committed_db, seed)
    await _human_scorecard(committed_db, seed, human_only)

    await _application(committed_db, seed)  # neither — never interviewed
    await committed_db.commit()

    headers = _auth(seed.hr, ["hr_manager"])
    analytics = (await client.get("/hr/analytics", headers=headers)).json()

    assert analytics["funnel"]["total_applications"] == 3
    assert analytics["conversion"]["ever_interviewed"] == 2
    assert analytics["funnel"]["interview_completed"] == 2
    assert (
        analytics["funnel"]["interview_completed"] == analytics["conversion"]["ever_interviewed"]
    )
    assert analytics["definitions"]["funnel.interview_completed"] == "interviewed@1"
    assert analytics["definitions"]["funnel.total_applications"] == "applications@1"
    assert analytics["definitions"]["funnel.exam_taken"] == "assessed@1"
    assert analytics["definitions"]["funnel.hired"] == "hires@1"


async def test_get_company_overview_agrees_with_get_funnel_analytics(
    committed_db: AsyncSession,
) -> None:
    """C2-6: the super-admin copilot's ``get_company_overview`` used to count
    ``applicants.status`` and an AI-only ``scorecards`` join — a second
    definition of "how many at each stage" and "how many interviewed" from
    ``get_funnel_analytics``. Both now read the SAME governed
    ``app.metrics.compute`` figures, so they cannot disagree, and no
    check-in metric ever reaches either payload."""
    from shared.agents import ToolContext

    from app.agents import tools as tools_module

    seed = await _seed_company(committed_db)
    for _i in range(3):
        e = await _application(committed_db, seed)
        applicant_id = (
            await committed_db.execute(
                text("SELECT applicant_id FROM enrolments WHERE id = :e"), {"e": e},
            )
        ).scalar_one()
        await _ai_interview_completed(committed_db, seed, applicant_id, enrolment_id=e)
    hired = await _application(committed_db, seed)
    await _hire(committed_db, seed, hired)
    await committed_db.commit()

    funnel_ctx = ToolContext(
        actor_id=str(seed.hr), role="hr_manager", company_id=str(seed.company),
        resources={"db": committed_db},
    )
    _funnel_spec, funnel_handler = next(
        (s, h) for s, h in tools_module.registry._tools.values() if s.name == "get_funnel_analytics"
    )
    funnel_out = await funnel_handler({}, funnel_ctx)

    overview_ctx = ToolContext(
        actor_id=str(seed.admin), role="super_admin", company_id=str(seed.company),
        resources={"db": committed_db},
    )
    _overview_spec, overview_handler = next(
        (s, h) for s, h in tools_module.registry._tools.values() if s.name == "get_company_overview"
    )
    overview_out = await overview_handler({}, overview_ctx)

    funnel_overall = funnel_out.data["overall"]
    overview_stage = overview_out.data["applicants_by_stage"]
    for name in ("applications", "interviewed", "hires"):
        assert overview_stage[name]["value"] == funnel_overall[name]["value"], name
    assert overview_out.data["applicants_total"] == funnel_overall["applications"]["value"]
    assert overview_out.data["registry_hash"] == funnel_out.data["registry_hash"]

    # No check-in metric ever reaches this payload (PH5-C1).
    for name in ("checkin_coverage", "retention_90d", "performance_90d"):
        assert name not in overview_stage

    # The last-30-days interview figure is also the governed flag, not the
    # old AI-only `scorecards` join.
    assert overview_out.data["interviews_last_30d"]["interviewed"] == 3
