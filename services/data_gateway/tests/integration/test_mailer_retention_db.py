"""``app.mailer.purge_old_email_events`` — proven against a real, migrated
Postgres. Part of the DPDP §8(7) retention cron (called unconditionally, every
tick, from ``app.main._run_retention_job``).

BEFORE THIS FILE this function had NO test at all — unit or integration. It
also, uniquely among the retention job's ten other purge calls, takes no
``dry_run`` argument and consults no setting: ``app.main._run_retention_job``
calls it as ``await purge_old_email_events(db=session)`` with nothing
downstream of ``settings.retention_dry_run`` at all. So while every other rule
in this cron has spent months counting what it would delete, this one has been
deleting real ``email_events`` and ``auth_tokens`` rows for real, every night,
regardless of ``RETENTION_DRY_RUN`` — see
``test_the_dry_run_setting_has_no_effect_on_this_purge`` below, which is the
load-bearing test in this file.

Windows (from ``app/mailer.py::purge_old_email_events``):
  - ``email_events``: status IN ('sent','failed','cancelled') AND
    created_at < now() - EMAIL_RETENTION_DAYS (default 30). 'queued' and
    'sending' survive regardless of age — an in-flight email must never be
    dropped mid-delivery.
  - ``auth_tokens``: consumed_at IS NOT NULL OR expires_at < now() — no
    window at all; a used or expired token is swept the next tick it is seen.
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

from app import mailer as svc
from app.config import settings

pytestmark = pytest.mark.integration

_NOW = datetime.now(tz=UTC)
_OLD = _NOW - timedelta(days=31)  # past EMAIL_RETENTION_DAYS (30)
_RECENT = _NOW - timedelta(days=5)  # inside the window


class F:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.user = uuid.uuid4()


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "u": f.user,
        "slug": f"mail-{tag}", "e": f"u-{tag}@mail.test",
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'Mail co', :slug)",
        "INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)",
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


async def _event(
    db: AsyncSession, f: F, *, status: str, created_at: datetime,
) -> uuid.UUID:
    eid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO email_events (id, template, to_email, to_user_id, company_id,"
            " subject, status, created_at, updated_at)"
            " VALUES (:i, 'welcome', 'candidate@example.com', :u, :c, 'Subject', :st, :ca, :ca)"
        ),
        {"i": eid, "u": f.user, "c": f.company, "st": status, "ca": created_at},
    )
    return eid


async def _token(
    db: AsyncSession, f: F, *, kind: str = "password_reset",
    consumed_at: datetime | None, expires_at: datetime,
) -> uuid.UUID:
    tid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO auth_tokens (id, user_id, kind, token_hash, expires_at, consumed_at,"
            " created_at) VALUES (:i, :u, :k, :h, :exp, :cons, now())"
        ),
        {"i": tid, "u": f.user, "k": kind, "h": tid.hex, "exp": expires_at, "cons": consumed_at},
    )
    return tid


async def _event_exists(db: AsyncSession, eid: uuid.UUID) -> bool:
    row = await db.execute(text("SELECT 1 FROM email_events WHERE id = :i"), {"i": eid})
    return row.first() is not None


async def _token_exists(db: AsyncSession, tid: uuid.UUID) -> bool:
    row = await db.execute(text("SELECT 1 FROM auth_tokens WHERE id = :i"), {"i": tid})
    return row.first() is not None


# ===========================================================================
# email_events: the boundary
# ===========================================================================
@pytest.mark.asyncio
async def test_an_old_sent_event_is_purged(db: AsyncSession) -> None:
    f = await _build(db)
    eid = await _event(db, f, status="sent", created_at=_OLD)
    await svc.purge_old_email_events(db)
    assert not await _event_exists(db, eid)


@pytest.mark.asyncio
async def test_a_recent_sent_event_survives(db: AsyncSession) -> None:
    """The half that matters: over-deletion is unrecoverable."""
    f = await _build(db)
    eid = await _event(db, f, status="sent", created_at=_RECENT)
    await svc.purge_old_email_events(db)
    assert await _event_exists(db, eid)


@pytest.mark.asyncio
async def test_an_old_failed_and_cancelled_event_are_purged(db: AsyncSession) -> None:
    f = await _build(db)
    failed = await _event(db, f, status="failed", created_at=_OLD)
    cancelled = await _event(db, f, status="cancelled", created_at=_OLD)
    await svc.purge_old_email_events(db)
    assert not await _event_exists(db, failed)
    assert not await _event_exists(db, cancelled)


@pytest.mark.asyncio
async def test_a_queued_event_survives_no_matter_how_old(db: AsyncSession) -> None:
    """An in-flight email must never be dropped mid-delivery — the worker may
    still be about to claim it."""
    f = await _build(db)
    eid = await _event(db, f, status="queued", created_at=_NOW - timedelta(days=365))
    await svc.purge_old_email_events(db)
    assert await _event_exists(db, eid)


@pytest.mark.asyncio
async def test_a_sending_event_survives_no_matter_how_old(db: AsyncSession) -> None:
    f = await _build(db)
    eid = await _event(db, f, status="sending", created_at=_NOW - timedelta(days=365))
    await svc.purge_old_email_events(db)
    assert await _event_exists(db, eid)


# ===========================================================================
# auth_tokens: no window at all — consumed or expired goes, live survives
# ===========================================================================
@pytest.mark.asyncio
async def test_a_consumed_token_is_swept_immediately(db: AsyncSession) -> None:
    f = await _build(db)
    tid = await _token(db, f, consumed_at=_NOW, expires_at=_NOW + timedelta(days=1))
    await svc.purge_old_email_events(db)
    assert not await _token_exists(db, tid)


@pytest.mark.asyncio
async def test_an_expired_unconsumed_token_is_swept(db: AsyncSession) -> None:
    f = await _build(db)
    tid = await _token(db, f, consumed_at=None, expires_at=_NOW - timedelta(minutes=1))
    await svc.purge_old_email_events(db)
    assert not await _token_exists(db, tid)


@pytest.mark.asyncio
async def test_a_live_unconsumed_token_survives(db: AsyncSession) -> None:
    f = await _build(db)
    tid = await _token(db, f, consumed_at=None, expires_at=_NOW + timedelta(hours=1))
    await svc.purge_old_email_events(db)
    assert await _token_exists(db, tid)


# ===========================================================================
# THE FINDING: this purge has no dry-run mode at all
# ===========================================================================
@pytest.mark.asyncio
async def test_the_dry_run_setting_has_no_effect_on_this_purge(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RETENTION_DRY_RUN defaults true precisely so an operator can inspect
    counts before anything is deleted. ``purge_old_email_events`` takes no
    ``dry_run`` parameter and reads no retention setting, so — unlike every
    other rule in ``_run_retention_job`` — it has always deleted for real,
    on every tick, whatever RETENTION_DRY_RUN says. This test sets
    ``retention_dry_run = True`` (the untouched production default) and
    proves the row is gone anyway: the premise that nothing has been deleted
    while the flag is unset does not hold for this rule."""
    monkeypatch.setattr(settings, "retention_dry_run", True)
    f = await _build(db)
    eid = await _event(db, f, status="sent", created_at=_OLD)
    tid = await _token(db, f, consumed_at=_NOW, expires_at=_NOW + timedelta(days=1))

    await svc.purge_old_email_events(db)

    assert not await _event_exists(db, eid), (
        "purge_old_email_events ignored retention_dry_run=True and deleted the row anyway — "
        "this is CURRENT, RUNNING behaviour, not a hypothetical"
    )
    assert not await _token_exists(db, tid)


# ===========================================================================
# Atomicity: the two DELETEs commit together or not at all
# ===========================================================================
@pytest.mark.asyncio
async def test_a_failure_between_the_two_deletes_leaves_neither_committed(
    db: AsyncSession,
) -> None:
    """``purge_old_email_events`` issues DELETE email_events, then DELETE
    auth_tokens, then one commit. If the second delete blows up before that
    commit, the first delete must not have persisted either — otherwise a
    transient failure would silently half-apply a compliance purge.

    The seed row is committed BEFORE the monkeypatch goes on: this fixture's
    ``db`` session runs the whole test in one transaction, so inserting and
    then rolling back a NEVER-committed row would prove nothing (the row
    would be absent afterward whether the delete really ran or the whole
    thing was undone). Committing first makes ``_event_exists`` after
    rollback a real check of what survived."""
    f = await _build(db)
    eid = await _event(db, f, status="sent", created_at=_OLD)
    await db.commit()

    real_execute = db.execute
    calls = 0

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:  # the auth_tokens DELETE
            raise RuntimeError("simulated failure between the two deletes")
        return await real_execute(*args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db, "execute", _boom)
    try:
        with pytest.raises(RuntimeError, match="simulated failure"):
            await svc.purge_old_email_events(db)
    finally:
        monkeypatch.undo()

    await db.rollback()
    assert await _event_exists(db, eid), (
        "the email_events DELETE must not survive a failure in the auth_tokens "
        "DELETE that follows it in the same, uncommitted transaction"
    )


# ===========================================================================
# Idempotency
# ===========================================================================
@pytest.mark.asyncio
async def test_running_it_twice_deletes_nothing_new_the_second_time(db: AsyncSession) -> None:
    f = await _build(db)
    await _event(db, f, status="sent", created_at=_OLD)
    survivor = await _event(db, f, status="sent", created_at=_RECENT)

    first = await svc.purge_old_email_events(db)
    assert first >= 1
    second = await svc.purge_old_email_events(db)
    assert second == 0
    assert await _event_exists(db, survivor)
