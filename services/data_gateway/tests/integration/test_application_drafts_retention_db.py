"""``app.application_drafts.purge_expired`` — proven against a real, migrated
Postgres. Part of the DPDP §8(7) retention cron, called from
``app.main._run_retention_job`` as ``keys = await purge_expired_drafts(session)``.

BEFORE THIS FILE the function had ONLY mocked-``AsyncSession`` unit tests
(``tests/unit/test_ph3_drafts_and_confirmation.py``) — the raw, multi-table SQL
here (a ``DELETE ... RETURNING`` against ``application_drafts``, then a
``NOT EXISTS``-gated ``DELETE`` against ``users`` that must correctly see the
FIRST delete's effect) had never run against a real schema.

THE SECOND FINDING THIS FILE PINS: like ``app.mailer.purge_old_email_events``,
this function takes no ``dry_run`` argument and consults no retention setting.
``app.main._run_retention_job`` calls it unconditionally. So this rule too has
been deleting real drafts, real guest identities and real consent-ledger rows
every night, regardless of ``RETENTION_DRY_RUN`` — see
``test_the_dry_run_setting_has_no_effect_on_this_purge``.

Windows (from ``app/application_drafts.py``):
  - abandoned drafts: ``status='draft' AND expires_at <= now()`` —
    ``expires_at`` is written at creation as ``created_at + DRAFT_TTL_DAYS``
    (30 days), not computed at read time.
  - submitted drafts: ``status='submitted' AND submitted_at <= now() -
    SUBMITTED_RETENTION_DAYS`` (90 days) — the row is a duplicate of details
    that now live on the applicant; the OBJECT survives (it is the
    application's own CV), only the ROW goes.
  - orphan guest identities: a ``guest+...@applicants.invalid`` user with no
    remaining draft, no applicant and no erasure_requests row is deleted with
    the draft that created it — cascading into ``user_roles`` and
    ``dpdp_consent_ledger`` (both ON DELETE CASCADE on ``user_id``).
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

from app import application_drafts as svc
from app.config import settings

pytestmark = pytest.mark.integration

_NOW = datetime.now(tz=UTC)


class F:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.req = uuid.uuid4()
        self.req2 = uuid.uuid4()


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    p: dict[str, Any] = {"c": f.company, "r": f.req, "r2": f.req2, "slug": f"draft-{tag}"}
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'Draft co', :slug)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now()), (:r2, :c, 'Engineer 2', now(), now())",
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


async def _guest(db: AsyncSession) -> uuid.UUID:
    """A guest identity exactly as ``public_apply``'s draft path mints one:
    a ``guest+<uuid>@applicants.invalid`` user plus its consent-ledger row."""
    uid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email) VALUES (:u, :e)"),
        {"u": uid, "e": f"guest+{uid.hex}@applicants.invalid"},
    )
    await db.execute(
        text(
            "INSERT INTO dpdp_consent_ledger (id, user_id, consent_type, granted, purpose,"
            " evidence) VALUES (:i, :u, 'application_data', true, 'recruitment',"
            " CAST(:ev AS jsonb))"
        ),
        {"i": uuid.uuid4(), "u": uid, "ev": '{"version": 1}'},
    )
    return uid


async def _draft(
    db: AsyncSession, f: F, *, user_id: uuid.UUID, status: str = "draft",
    expires_at: datetime, submitted_at: datetime | None = None,
    resume_s3_key: str | None = None, requisition_id: uuid.UUID | None = None,
) -> uuid.UUID:
    did = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO application_drafts (id, company_id, requisition_id, user_id,"
            " token_hash, email, status, expires_at, submitted_at, resume_s3_key,"
            " created_at, updated_at)"
            " VALUES (:i, :c, :r, :u, :h, :e, :st, :exp, :sub, :key, now(), now())"
        ),
        {
            "i": did, "c": f.company, "r": requisition_id or f.req, "u": user_id, "h": did.hex,
            "e": "candidate@example.com", "st": status, "exp": expires_at, "sub": submitted_at,
            "key": resume_s3_key,
        },
    )
    return did


async def _draft_exists(db: AsyncSession, did: uuid.UUID) -> bool:
    row = await db.execute(text("SELECT 1 FROM application_drafts WHERE id = :i"), {"i": did})
    return row.first() is not None


async def _user_exists(db: AsyncSession, uid: uuid.UUID) -> bool:
    row = await db.execute(text("SELECT 1 FROM users WHERE id = :i"), {"i": uid})
    return row.first() is not None


async def _consent_exists(db: AsyncSession, uid: uuid.UUID) -> bool:
    row = await db.execute(
        text("SELECT 1 FROM dpdp_consent_ledger WHERE user_id = :i"), {"i": uid}
    )
    return row.first() is not None


# ===========================================================================
# The boundary: expired drafts
# ===========================================================================
@pytest.mark.asyncio
async def test_an_expired_draft_is_purged(db: AsyncSession) -> None:
    f = await _build(db)
    guest = await _guest(db)
    did = await _draft(db, f, user_id=guest, expires_at=_NOW - timedelta(hours=1))
    await svc.purge_expired(db, now=_NOW)
    assert not await _draft_exists(db, did)


@pytest.mark.asyncio
async def test_a_live_draft_survives(db: AsyncSession) -> None:
    """The half that matters: a candidate still filling this in must never
    lose it to the retention cron."""
    f = await _build(db)
    guest = await _guest(db)
    did = await _draft(db, f, user_id=guest, expires_at=_NOW + timedelta(days=1))
    await svc.purge_expired(db, now=_NOW)
    assert await _draft_exists(db, did)
    assert await _user_exists(db, guest), "a live draft's guest identity must not be orphaned"


# ===========================================================================
# The boundary: submitted drafts
# ===========================================================================
@pytest.mark.asyncio
async def test_a_submitted_draft_past_its_retention_is_purged(db: AsyncSession) -> None:
    f = await _build(db)
    guest = await _guest(db)
    did = await _draft(
        db, f, user_id=guest, status="submitted",
        expires_at=_NOW - timedelta(days=100),
        submitted_at=_NOW - timedelta(days=svc.SUBMITTED_RETENTION_DAYS + 1),
        resume_s3_key="drafts/live/cv.pdf",
    )
    keys = await svc.purge_expired(db, now=_NOW)
    assert not await _draft_exists(db, did)
    # THE ROW GOES; THE OBJECT STAYS — submit_draft points the applicant's own
    # resume_s3_key at this same object, so queuing it for deletion here would
    # take a live applicant's CV down.
    assert "drafts/live/cv.pdf" not in keys


@pytest.mark.asyncio
async def test_a_submitted_draft_inside_its_retention_survives(db: AsyncSession) -> None:
    f = await _build(db)
    guest = await _guest(db)
    did = await _draft(
        db, f, user_id=guest, status="submitted",
        expires_at=_NOW - timedelta(days=100),
        submitted_at=_NOW - timedelta(days=10),
    )
    await svc.purge_expired(db, now=_NOW)
    assert await _draft_exists(db, did)


@pytest.mark.asyncio
async def test_an_abandoned_drafts_resume_key_is_queued_for_deletion(db: AsyncSession) -> None:
    """The opposite of the submitted case: an ABANDONED draft's CV was never
    adopted by an application, so it is the only pointer to that object and
    must be queued."""
    f = await _build(db)
    guest = await _guest(db)
    await _draft(
        db, f, user_id=guest, status="draft", expires_at=_NOW - timedelta(hours=1),
        resume_s3_key="drafts/abandoned/cv.pdf",
    )
    keys = await svc.purge_expired(db, now=_NOW)
    assert "drafts/abandoned/cv.pdf" in keys


# ===========================================================================
# Orphan guest identities — the over-deletion risk
# ===========================================================================
@pytest.mark.asyncio
async def test_an_orphaned_guest_is_deleted_with_its_only_draft(db: AsyncSession) -> None:
    f = await _build(db)
    guest = await _guest(db)
    await _draft(db, f, user_id=guest, expires_at=_NOW - timedelta(hours=1))
    await svc.purge_expired(db, now=_NOW)
    assert not await _user_exists(db, guest)
    assert not await _consent_exists(db, guest), (
        "user_roles/dpdp_consent_ledger must cascade with the orphaned guest, "
        "not survive pointing at nobody"
    )


@pytest.mark.asyncio
async def test_a_guest_with_a_second_live_draft_is_not_orphaned(db: AsyncSession) -> None:
    """THE test that matters most in this file: one expired draft must not
    take down a guest identity that still has a LIVE draft under it — the
    guest is a person mid-application, not a leftover."""
    f = await _build(db)
    guest = await _guest(db)
    # Two different openings: one live draft per person per opening is
    # enforced at the database (uq_application_drafts_live), so a SECOND live
    # draft for the same guest must sit under a different requisition.
    expired = await _draft(db, f, user_id=guest, expires_at=_NOW - timedelta(hours=1))
    live = await _draft(
        db, f, user_id=guest, expires_at=_NOW + timedelta(days=10), requisition_id=f.req2,
    )
    await svc.purge_expired(db, now=_NOW)
    assert not await _draft_exists(db, expired)
    assert await _draft_exists(db, live)
    assert await _user_exists(db, guest), (
        "the guest still has a live draft — deleting the user would CASCADE "
        "and take that live draft down with it"
    )
    assert await _consent_exists(db, guest)


@pytest.mark.asyncio
async def test_a_guest_named_by_an_erasure_request_is_not_orphaned(db: AsyncSession) -> None:
    """A guest under an open §12 erasure request is left for that request to
    finish, not raced by the nightly draft sweep."""
    f = await _build(db)
    guest = await _guest(db)
    await _draft(db, f, user_id=guest, expires_at=_NOW - timedelta(hours=1))
    requester = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email) VALUES (:u, :e)"),
        {"u": requester, "e": f"staff-{requester.hex[:8]}@example.com"},
    )
    await db.execute(
        text(
            "INSERT INTO erasure_requests (user_id, requested_by, scheduled_for)"
            " VALUES (:u, :rb, now())"
        ),
        {"u": guest, "rb": requester},
    )
    await svc.purge_expired(db, now=_NOW)
    assert await _user_exists(db, guest)


# ===========================================================================
# THE FINDING: this purge has no dry-run mode at all
# ===========================================================================
@pytest.mark.asyncio
async def test_the_dry_run_setting_has_no_effect_on_this_purge(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RETENTION_DRY_RUN defaults true and has never been set false — but
    ``purge_expired`` takes no ``dry_run`` argument at all and reads no
    retention setting. This test sets the untouched production default
    (``True``) and shows the row is deleted anyway: like the mailer purge,
    this rule has never had a dry-run mode to switch off."""
    monkeypatch.setattr(settings, "retention_dry_run", True)
    f = await _build(db)
    guest = await _guest(db)
    did = await _draft(db, f, user_id=guest, expires_at=_NOW - timedelta(hours=1))

    await svc.purge_expired(db, now=_NOW)

    assert not await _draft_exists(db, did), (
        "purge_expired ignored retention_dry_run=True and deleted the row anyway — "
        "this is CURRENT, RUNNING behaviour, not a hypothetical"
    )
    assert not await _user_exists(db, guest)


# ===========================================================================
# Draining and idempotency
# ===========================================================================
@pytest.mark.asyncio
async def test_a_small_limit_drains_across_multiple_passes(db: AsyncSession) -> None:
    """``purge_expired``'s return value is the list of S3 KEYS to clean up, not
    a count of drafts purged — so each seeded draft carries its own key here,
    making ``len(keys)`` a reliable proxy for "how many drafts did this drain."
    """
    f = await _build(db)
    drafts = []
    for i in range(4):
        guest = await _guest(db)
        drafts.append(
            await _draft(
                db, f, user_id=guest, expires_at=_NOW - timedelta(hours=1),
                resume_s3_key=f"drafts/drain/{i}.pdf",
            )
        )

    keys = await svc.purge_expired(db, now=_NOW, limit=2)

    assert len(keys) == 4
    for did in drafts:
        assert not await _draft_exists(db, did)


@pytest.mark.asyncio
async def test_running_it_twice_purges_nothing_new_the_second_time(db: AsyncSession) -> None:
    f = await _build(db)
    guest = await _guest(db)
    await _draft(
        db, f, user_id=guest, expires_at=_NOW - timedelta(hours=1),
        resume_s3_key="drafts/twice/a.pdf",
    )
    survivor_guest = await _guest(db)
    survivor = await _draft(db, f, user_id=survivor_guest, expires_at=_NOW + timedelta(days=1))

    first = await svc.purge_expired(db, now=_NOW)
    assert len(first) == 1
    second = await svc.purge_expired(db, now=_NOW)
    assert second == []
    assert await _draft_exists(db, survivor)


# ===========================================================================
# Interruption: a failure inside one pass must not half-apply that pass
# ===========================================================================
@pytest.mark.asyncio
async def test_a_failure_inside_a_pass_leaves_that_passs_draft_undeleted(
    db: AsyncSession,
) -> None:
    """Each pass runs the draft DELETE, then the orphan-user DELETE, then
    commits. If the SECOND statement blows up before that commit, the first
    must not have persisted either -- otherwise a transient failure mid-run
    would delete a draft while leaving its guest identity behind forever
    (an orphan ``users``/``dpdp_consent_ledger`` row this sweep can never
    find again, since the draft that would have named it is already gone)."""
    f = await _build(db)
    guest = await _guest(db)
    did = await _draft(
        db, f, user_id=guest, expires_at=_NOW - timedelta(hours=1),
        resume_s3_key="drafts/interrupt/a.pdf",
    )
    await db.commit()  # seed must be durable before we induce a mid-pass failure

    real_execute = db.execute
    calls = 0

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:  # the orphan-user DELETE, inside the same pass
            raise RuntimeError("simulated failure mid-pass")
        return await real_execute(*args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db, "execute", _boom)
    try:
        with pytest.raises(RuntimeError, match="simulated failure"):
            await svc.purge_expired(db, now=_NOW)
    finally:
        monkeypatch.undo()

    await db.rollback()
    assert await _draft_exists(db, did), (
        "the draft DELETE must not survive a failure in the same pass's "
        "orphan-user DELETE that follows it"
    )
    assert await _user_exists(db, guest), "the guest must not be split from its draft either"

    # A retry (the next nightly run) must then complete cleanly.
    keys = await svc.purge_expired(db, now=_NOW)
    assert len(keys) == 1
    assert not await _draft_exists(db, did)
    assert not await _user_exists(db, guest)
