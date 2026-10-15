"""Openings close themselves after their closing date — real Postgres.

Needs an EMPTY database, as CI's is: close_expired is platform-wide, so other
past-due rows would sort first and change the batch tests. Never point it at a
shared one (tests/integration/conftest.py refuses a remote host anyway).

The rule (app/auto_close.py): an opening past its closing date is closed
automatically only when nobody is still in it; with candidates in progress it
stays open and its owner is told once. Candidates are never touched.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import auto_close
from app.config import settings

pytestmark = pytest.mark.integration

NOW = datetime.now(tz=UTC)
PAST = NOW - timedelta(days=1)
FUTURE = NOW + timedelta(days=3)


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


async def _company(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    c, owner = uuid.uuid4(), uuid.uuid4()
    await db.execute(text("INSERT INTO companies (id, name, slug) VALUES (:c, 'AC co', :s)"),
                     {"c": c, "s": f"ac-{c.hex[:10]}"})
    await db.execute(text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
                     {"u": owner, "e": f"own-{c.hex[:10]}@ac.test", "c": c})
    return c, owner


async def _opening(db: AsyncSession, company: uuid.UUID, owner: uuid.UUID | None, *,
                   closes_at: datetime | None, status: str = "open") -> uuid.UUID:
    r = uuid.uuid4()
    await db.execute(
        text("INSERT INTO job_requisitions (id, company_id, title, status, closes_at,"
             " owner_user_id, created_at, updated_at)"
             " VALUES (:r, :c, :t, :s, :ca, :o, now(), now())"),
        {"r": r, "c": company, "t": f"Role {r.hex[:6]}", "s": status, "ca": closes_at,
         "o": owner},
    )
    return r


async def _candidate(db: AsyncSession, company: uuid.UUID, req: uuid.UUID, status: str) -> uuid.UUID:
    a, e = uuid.uuid4(), uuid.uuid4()
    await db.execute(text("INSERT INTO applicants (id, company_id, full_name, target_job_title)"
                          " VALUES (:a, :c, 'P', 'Role')"), {"a": a, "c": company})
    await db.execute(
        text("INSERT INTO enrolments (id, company_id, requisition_id, applicant_id,"
             " target_job_title, status, created_at, updated_at)"
             " VALUES (:e, :c, :r, :a, 'Role', 'new', now(), now())"),
        {"e": e, "c": company, "r": req, "a": a},
    )
    if status != "new":
        await db.execute(text("UPDATE enrolments SET status = :s WHERE id = :e"),
                         {"s": status, "e": e})
    return e


async def _status(db: AsyncSession, req: uuid.UUID) -> str:
    return str(await db.scalar(text("SELECT status FROM job_requisitions WHERE id = :r"),
                               {"r": req}))


@pytest.mark.asyncio
@pytest.mark.parametrize("start", ["open", "paused"])
async def test_an_empty_opening_past_its_date_closes_and_is_audited(
    db: AsyncSession, start: str
) -> None:
    c, owner = await _company(db)
    r = await _opening(db, c, owner, closes_at=PAST, status=start)

    assert await auto_close.close_expired(db, now=NOW) >= 1
    assert await _status(db, r) == "closed"
    audit = (await db.execute(
        text("SELECT actor_id, actor_type, details FROM audit_log"
             " WHERE resource_id = :r AND action = 'requisition.status.closed'"),
        {"r": r},
    )).one()
    assert audit.actor_id is None and audit.actor_type == "system"
    assert audit.details["automatic"] is True and audit.details["previous_status"] == start
    assert await db.scalar(
        text("SELECT count(*) FROM notifications WHERE user_id = :u"
             " AND kind = 'requisition_auto_closed'"), {"u": owner}) == 1


@pytest.mark.asyncio
async def test_only_finished_candidates_do_not_hold_it_open(db: AsyncSession) -> None:
    c, owner = await _company(db)
    r = await _opening(db, c, owner, closes_at=PAST)
    await _candidate(db, c, r, "hired")
    await _candidate(db, c, r, "rejected")
    await auto_close.close_expired(db, now=NOW)
    assert await _status(db, r) == "closed"


@pytest.mark.asyncio
async def test_an_opening_with_a_candidate_in_progress_stays_open_and_the_owner_is_told_once(
    db: AsyncSession,
) -> None:
    """AC-12 / D-05: no automated close over people, and nobody is rejected."""
    c, owner = await _company(db)
    r = await _opening(db, c, owner, closes_at=PAST)
    e = await _candidate(db, c, r, "shortlisted")

    await auto_close.close_expired(db, now=NOW)
    assert await _status(db, r) == "open"
    assert await db.scalar(text("SELECT status FROM enrolments WHERE id = :e"),
                           {"e": e}) == "shortlisted"

    assert await auto_close.nudge_overdue(db, now=NOW) >= 1
    assert await auto_close.nudge_overdue(db, now=NOW) == 0  # once, not every pass
    body = await db.scalar(
        text("SELECT body FROM notifications WHERE user_id = :u"
             " AND kind = 'requisition_closing_overdue'"), {"u": owner})
    assert "1 candidate is still in progress" in body


@pytest.mark.asyncio
async def test_future_and_undated_openings_are_left_alone(db: AsyncSession) -> None:
    c, owner = await _company(db)
    future = await _opening(db, c, owner, closes_at=FUTURE)
    undated = await _opening(db, c, owner, closes_at=None)
    await auto_close.close_expired(db, now=NOW)
    assert await _status(db, future) == "open"
    assert await _status(db, undated) == "open"
    # ... and the loop is told to wake for the future one.
    nxt = await auto_close.next_closing_at(db, now=NOW)
    # Rounded UP to the minute (security review M2): never early, at most 60s late.
    assert nxt is not None and FUTURE <= nxt < FUTURE + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_stuck_openings_cannot_starve_the_batch(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Openings that cannot close never match the close query, so a backlog of
    them — older, sorting first — cannot fill the batch."""
    monkeypatch.setattr(auto_close, "BATCH", 2)
    c, owner = await _company(db)
    for _ in range(3):
        stuck = await _opening(db, c, owner, closes_at=PAST - timedelta(days=30))
        await _candidate(db, c, stuck, "new")
    closable = await _opening(db, c, owner, closes_at=PAST)
    await auto_close.close_expired(db, now=NOW)
    assert await _status(db, closable) == "closed"


@pytest.mark.asyncio
async def test_unnotifiable_openings_cannot_starve_the_nudge(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An owner-less opening (nobody to tell) and an already-told one would
    match every pass; they are filtered out, so an older backlog of them cannot
    keep the owner of a newer one from ever being told."""
    monkeypatch.setattr(auto_close, "BATCH", 1)
    c, owner = await _company(db)
    orphan = await _opening(db, c, None, closes_at=PAST - timedelta(days=30))
    await _candidate(db, c, orphan, "new")
    told_before = await _opening(db, c, owner, closes_at=PAST - timedelta(days=20))
    await _candidate(db, c, told_before, "new")
    assert await auto_close.nudge_overdue(db, now=NOW) == 1  # the older owned one

    waiting = await _opening(db, c, owner, closes_at=PAST)
    await _candidate(db, c, waiting, "new")
    assert await auto_close.nudge_overdue(db, now=NOW) == 1
    links = set((await db.execute(
        text("SELECT link FROM notifications WHERE user_id = :u"
             " AND kind = 'requisition_closing_overdue'"), {"u": owner},
    )).scalars().all())
    assert f"/hr/requisitions/{waiting}?tab=pipeline" in links


@pytest.mark.asyncio
async def test_a_soft_deleted_candidate_does_not_hold_an_opening_open(db: AsyncSession) -> None:
    c, owner = await _company(db)
    r = await _opening(db, c, owner, closes_at=PAST)
    e = await _candidate(db, c, r, "new")
    await db.execute(text("UPDATE enrolments SET deleted_at = now() WHERE id = :e"), {"e": e})
    await auto_close.close_expired(db, now=NOW)
    assert await _status(db, r) == "closed"


@pytest.mark.asyncio
async def test_auto_close_clears_a_pending_scheduled_publish(db: AsyncSession) -> None:
    c, owner = await _company(db)
    r = await _opening(db, c, owner, closes_at=PAST)
    await db.execute(text("UPDATE job_requisitions SET publish_at = :p WHERE id = :r"),
                     {"p": FUTURE, "r": r})
    await auto_close.close_expired(db, now=NOW)
    assert await db.scalar(text("SELECT publish_at FROM job_requisitions WHERE id = :r"),
                           {"r": r}) is None


# ---------------------------------------------------------------------------
# Security review of auto-close
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_closed_opening_takes_no_new_candidates(db: AsyncSession) -> None:
    """M1: a bulk upload accepted while the opening was open and ingested after
    auto-close must not land people in a closed opening nobody was asked about."""
    from app.workflow_runner import enrol_applicant

    c, owner = await _company(db)
    r = await _opening(db, c, owner, closes_at=PAST)
    assert await auto_close.close_expired(db, now=NOW) >= 1
    a = uuid.uuid4()
    await db.execute(text("INSERT INTO applicants (id, company_id, full_name, target_job_title)"
                          " VALUES (:a, :c, 'Late', 'Role')"), {"a": a, "c": c})
    out = await enrol_applicant(db, company_id=c, applicant_id=a, requisition_id=r,
                                target_job_title="Role", actor_user_id=owner,
                                reason="added by HR bulk upload")
    assert out.action == "noop" and out.enrolment_id is None
    assert await db.scalar(text("SELECT count(*) FROM enrolments WHERE requisition_id = :r"),
                           {"r": r}) == 0


@pytest.mark.asyncio
async def test_an_open_opening_still_takes_candidates(db: AsyncSession) -> None:
    """Guards the guard: the refusal is for closed openings only."""
    from app.workflow_runner import enrol_applicant

    c, owner = await _company(db)
    r = await _opening(db, c, owner, closes_at=FUTURE)
    a = uuid.uuid4()
    await db.execute(text("INSERT INTO applicants (id, company_id, full_name, target_job_title)"
                          " VALUES (:a, :c, 'On time', 'Role')"), {"a": a, "c": c})
    out = await enrol_applicant(db, company_id=c, applicant_id=a, requisition_id=r,
                                target_job_title="Role", actor_user_id=owner)
    assert out.action == "enrolled"


@pytest.mark.asyncio
async def test_an_inactive_owner_is_not_notified(db: AsyncSession) -> None:
    """L2: an off-boarded owner's feed is read by nobody."""
    c, owner = await _company(db)
    await db.execute(text("UPDATE users SET is_active = false WHERE id = :u"), {"u": owner})
    r = await _opening(db, c, owner, closes_at=PAST)
    await _candidate(db, c, r, "new")
    assert await auto_close.nudge_overdue(db, now=NOW) == 0
    closable = await _opening(db, c, owner, closes_at=PAST)
    await auto_close.close_expired(db, now=NOW)
    assert await _status(db, closable) == "closed"  # still closed, just nobody told
    assert await db.scalar(text("SELECT count(*) FROM notifications WHERE user_id = :u"),
                           {"u": owner}) == 0


@pytest.mark.asyncio
async def test_the_audit_records_a_cancelled_scheduled_publish(db: AsyncSession) -> None:
    """L3: publish_at was a person's decision; the close says it cancelled it."""
    c, owner = await _company(db)
    r = await _opening(db, c, owner, closes_at=PAST)
    await db.execute(text("UPDATE job_requisitions SET publish_at = :p WHERE id = :r"),
                     {"p": FUTURE, "r": r})
    await auto_close.close_expired(db, now=NOW)
    details = await db.scalar(
        text("SELECT details FROM audit_log WHERE resource_id = :r"
             " AND action = 'requisition.status.closed'"), {"r": r})
    assert "cancelled_publish_at" in details


def test_closing_wake_ups_are_rounded_up_to_a_minute() -> None:
    """M2: dates a second apart cost one pass, not one a second."""
    base = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    for s in (1, 2, 30, 59):
        assert auto_close._round_up(base + timedelta(seconds=s), 60) == base + timedelta(minutes=1)
    assert auto_close._round_up(base, 60) == base


@pytest.mark.asyncio
async def test_hr_cannot_add_a_cv_to_a_closed_opening(db: AsyncSession) -> None:
    """Said up front (409), not discovered later as an applicant with no application."""
    from fastapi import HTTPException

    from app.routers.hr_applicants import _resolve_opening

    c, owner = await _company(db)
    r = await _opening(db, c, owner, closes_at=None, status="closed")
    with pytest.raises(HTTPException) as exc:
        await _resolve_opening(db, company_id=c, hr_uid=owner, requisition_id=r,
                               title="Role", level="mid", jd_text=None)
    assert exc.value.status_code == 409

    live = await _opening(db, c, owner, closes_at=None)
    row = await _resolve_opening(db, company_id=c, hr_uid=owner, requisition_id=live,
                                 title="Role", level="mid", jd_text=None)
    assert row["id"] == live and "status" not in row
