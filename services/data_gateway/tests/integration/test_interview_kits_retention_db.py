"""``app.interview_kits.purge_expired_notes`` — proven against a real, migrated
Postgres. Part of the DPDP §8(7) retention cron (PH4-A5), called from
``app.main._run_retention_job`` as
``await purge_expired_notes(session, retention_days=..., dry_run=...)``.

BEFORE THIS FILE the function had ONLY mocked-``AsyncSession`` unit tests
(``tests/unit/test_ph4_wave1_hardening.py``): the SELECT's join across
``interviewer_notes``, ``enrolments``, ``stage_transitions`` and
``interviewer_scorecards`` was pinned only by string-matching the SQL text
against a fake result, never executed against the real schema.

Two independent purge paths (``app/interview_kits.py::purge_expired_notes``):
  A. The application reached a final decision (hired/rejected) more than
     ``retention_days`` ago — dated by the LATEST ``stage_transitions`` row
     moving TO that status, not by the note's own age. A note untouched
     yesterday is purged if the decision itself is old enough.
  B. The note itself has not been touched for ``retention_days`` AND the
     interviewer holds no remaining LIVE scorecard (status <> 'withdrawn')
     for that (round, enrolment, interviewer) triple — every assignment they
     had was withdrawn, so nobody can read the note anymore. This path does
     NOT check the enrolment's decision status at all.

Neither path fires while the enrolment is still undecided AND the
interviewer holds a live scorecard — "notes on an application still in
progress are never touched" per the module docstring, regardless of how old
the note is. That is the survivor case this file cares about most.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import interview_kits as svc
from app.config import settings

pytestmark = pytest.mark.integration

_NOW = datetime.now(tz=UTC)


class F:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.hr = uuid.uuid4()
        self.interviewer = uuid.uuid4()
        self.req = uuid.uuid4()
        self.workflow = uuid.uuid4()
        self.round = uuid.uuid4()
        self.applicant = uuid.uuid4()
        self.enrolment = uuid.uuid4()


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "h": f.hr, "iv": f.interviewer, "r": f.req, "w": f.workflow,
        "rnd": f.round, "a": f.applicant, "e": f.enrolment,
        "slug": f"ik-{tag}", "m1": f"hr-{tag}@ik.test", "m2": f"iv-{tag}@ik.test",
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'IK co', :slug)",
        "INSERT INTO users (id, email, company_id) VALUES (:h, :m1, :c), (:iv, :m2, :c)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now())",
        "INSERT INTO workflows (id, company_id, requisition_id, version, status,"
        " created_at, updated_at) VALUES (:w, :c, :r, 1, 'draft', now(), now())",
        "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
        " created_at, updated_at)"
        " VALUES (:rnd, :c, :w, 0, 'Interview', 'human_review', now(), now())",
        "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
        " VALUES (:a, :c, 'Asha', 'Engineer')",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, target_job_title,"
        " status, created_at, updated_at)"
        " VALUES (:e, :c, :r, :a, 'Engineer', 'shortlisted', now(), now())",
    ):
        await db.execute(text(sql), p)
    return f


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(database_url=settings.database_url, database_ssl=settings.database_ssl,
                          pool_size=2)
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


async def _decide(db: AsyncSession, f: F, *, status: str, occurred_at: datetime) -> None:
    await db.execute(
        text("UPDATE enrolments SET status = :s, updated_at = :n WHERE id = :e"),
        {"s": status, "n": occurred_at, "e": f.enrolment},
    )
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
            " automated, reason, reason_code, reason_label, occurred_at)"
            " VALUES (:c, :e, 'shortlisted', :s, false, 'fixture', 'fixture', 'Fixture', :occ)"
        ),
        {"c": f.company, "e": f.enrolment, "s": status, "occ": occurred_at},
    )


async def _note(db: AsyncSession, f: F, *, updated_at: datetime) -> uuid.UUID:
    nid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO interviewer_notes (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, notes, updated_at)"
            " VALUES (:i, :c, :e, :r, :iv, 'private working notes', :u)"
        ),
        {"i": nid, "c": f.company, "e": f.enrolment, "r": f.round, "iv": f.interviewer, "u": updated_at},
    )
    return nid


async def _scorecard(db: AsyncSession, f: F, *, status: str = "assigned") -> uuid.UUID:
    sid = uuid.uuid4()
    submitted = _NOW if status == "submitted" else None
    withdrawn = _NOW if status == "withdrawn" else None
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, status, submitted_at, withdrawn_at)"
            " VALUES (:i, :c, :e, :r, :iv, :s, :sub, :wd)"
        ),
        {"i": sid, "c": f.company, "e": f.enrolment, "r": f.round, "iv": f.interviewer,
         "s": status, "sub": submitted, "wd": withdrawn},
    )
    return sid


async def _note_exists(db: AsyncSession, nid: uuid.UUID) -> bool:
    row = await db.execute(text("SELECT 1 FROM interviewer_notes WHERE id = :i"), {"i": nid})
    return row.first() is not None


# ===========================================================================
# Path A: decided long enough ago — purges regardless of the note's own age
# ===========================================================================
@pytest.mark.asyncio
async def test_a_note_on_a_decision_older_than_the_cutoff_is_purged(db: AsyncSession) -> None:
    f = await _build(db)
    await _decide(db, f, status="rejected", occurred_at=_NOW - timedelta(days=100))
    nid = await _note(db, f, updated_at=_NOW)  # touched YESTERDAY-equivalent — still purged
    purged = await svc.purge_expired_notes(db, retention_days=90, dry_run=False)
    assert purged == 1
    assert not await _note_exists(db, nid)


@pytest.mark.asyncio
async def test_a_note_on_a_recent_decision_survives(db: AsyncSession) -> None:
    f = await _build(db)
    await _decide(db, f, status="hired", occurred_at=_NOW - timedelta(days=10))
    await _scorecard(db, f, status="submitted")
    nid = await _note(db, f, updated_at=_NOW - timedelta(days=200))
    await svc.purge_expired_notes(db, retention_days=90, dry_run=False)
    assert await _note_exists(db, nid)


# ===========================================================================
# The survivor that matters most: still in progress, with a live assignment
# ===========================================================================
@pytest.mark.asyncio
async def test_a_note_on_an_application_still_in_progress_is_never_touched(
    db: AsyncSession,
) -> None:
    """Enrolment is undecided (still 'shortlisted') and the interviewer holds
    a LIVE scorecard — path A cannot fire (not decided) and path B cannot
    fire (a live assignment exists) no matter how stale the note is."""
    f = await _build(db)
    await _scorecard(db, f, status="in_progress")
    nid = await _note(db, f, updated_at=_NOW - timedelta(days=400))
    purged = await svc.purge_expired_notes(db, retention_days=90, dry_run=False)
    assert purged == 0
    assert await _note_exists(db, nid)


# ===========================================================================
# Path B: every assignment withdrawn, note itself stale
# ===========================================================================
@pytest.mark.asyncio
async def test_a_note_with_no_live_assignment_and_gone_stale_is_purged(
    db: AsyncSession,
) -> None:
    f = await _build(db)  # enrolment stays 'shortlisted' — undecided
    await _scorecard(db, f, status="withdrawn")
    nid = await _note(db, f, updated_at=_NOW - timedelta(days=100))
    purged = await svc.purge_expired_notes(db, retention_days=90, dry_run=False)
    assert purged == 1
    assert not await _note_exists(db, nid)


@pytest.mark.asyncio
async def test_a_note_with_no_live_assignment_but_not_yet_stale_survives(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    await _scorecard(db, f, status="withdrawn")
    nid = await _note(db, f, updated_at=_NOW - timedelta(days=5))
    purged = await svc.purge_expired_notes(db, retention_days=90, dry_run=False)
    assert purged == 0
    assert await _note_exists(db, nid)


# ===========================================================================
# Dry run predicts the live count exactly, on identical seed data
# ===========================================================================
@pytest.mark.asyncio
async def test_dry_run_count_matches_the_live_delete_count(db: AsyncSession) -> None:
    f = await _build(db)
    await _decide(db, f, status="rejected", occurred_at=_NOW - timedelta(days=200))
    await _note(db, f, updated_at=_NOW)

    dry_count = await svc.purge_expired_notes(db, retention_days=90, dry_run=True)
    live_count = await svc.purge_expired_notes(db, retention_days=90, dry_run=False)

    assert dry_count == live_count == 1


# ===========================================================================
# Idempotency
# ===========================================================================
@pytest.mark.asyncio
async def test_running_it_twice_purges_nothing_new_the_second_time(db: AsyncSession) -> None:
    f = await _build(db)
    await _decide(db, f, status="rejected", occurred_at=_NOW - timedelta(days=200))
    await _note(db, f, updated_at=_NOW)

    first = await svc.purge_expired_notes(db, retention_days=90, dry_run=False)
    assert first == 1
    second = await svc.purge_expired_notes(db, retention_days=90, dry_run=False)
    assert second == 0
