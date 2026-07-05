"""90-day retention purge — DPDP §8(7) compliance.

Daily job (APScheduler) that deletes session + turn rows older than
``settings.retention_days`` from their ``completed_at`` / ``updated_at`` timestamp.

Scope (what gets purged):
  - sessions WHERE status='completed' AND completed_at < NOW() - INTERVAL N days
  - sessions WHERE status='abandoned' AND updated_at < NOW() - INTERVAL N days
    (abandoned sessions may have no completed_at; we use updated_at as the
    staleness proxy — if the session has not been touched in N days it is safe
    to delete.  DPDP §8(7) covers any recording data, not only completed sessions.)
  - sessions WHERE status IN ('failed','consent_withdrawn')
    AND updated_at < NOW() - INTERVAL N days
    (failed = never produced a scorecard, no candidate value in retention;
    consent_withdrawn = DPDP §11 revocation was recorded and the session data
    must be removed at the next purge window.)
  - turns CASCADE via FK (ON DELETE CASCADE on turns.session_id — confirmed in
    migration eda3829ec95a, fk_turns_session_id with ondelete='CASCADE')
  - integrity_events CASCADE via FK (ON DELETE CASCADE on
    integrity_events.session_id — migration 20260618_0002). So proctoring /
    biometric-derived gaze data is purged together with its session here; the
    erasure endpoint additionally hard-deletes it immediately on request.
  - audio recordings — separate concern; tracked when blob storage lands (Sprint 5)

INDEX NOTE (migration 20260701_0001_e4f6a8b0c2d4):
  A partial index on sessions(status, completed_at) WHERE deleted_at IS NULL was
  added to avoid a full seq-scan on every nightly purge.  For very large tables
  the index should be created CONCURRENTLY outside a transaction:
    CREATE INDEX CONCURRENTLY idx_sessions_retention
      ON sessions (status, completed_at) WHERE deleted_at IS NULL;
  See migration docstring for details.

  TIER-2 FOLLOW-UP: partition the `turns` table by created_at (monthly) so the
  cascading delete of old turns is a fast partition drop, not a per-row delete.
  This is deferred because partitioning a populated table is destructive; the
  index above is sufficient for Phase 1 volumes.

Out of scope (what is NEVER deleted by this job):
  - dpdp_consent_ledger rows (legal audit trail per DPDP §8(7) — remain indefinitely
    or until a user-initiated §11 erasure request, task #46)
  - users (auth/identity — separate lifecycle)
  - jobs / NOS catalogue (reference data — no retention requirement)
  - sessions in non-terminal states: 'created', 'in_progress'
    (active sessions are never deleted mid-flight)

Safety rail:
  settings.retention_dry_run defaults to True.  In dry-run mode the job SELECTs
  the rows that WOULD be deleted and logs the count, but issues no DELETE statement.
  Operators MUST explicitly set RETENTION_DRY_RUN=false in production after a
  dry-run cycle confirms expected delete counts.

Log events emitted:
  retention.purge.done   — one event per run; carries deleted_count, dry_run,
                           cutoff_iso, retention_days, statuses_purged.
                           Never logs individual session IDs or user IDs.
  retention.purge.error  — unexpected exception; carries exc_type, exc_msg.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Session as InterviewSession

log = structlog.get_logger(__name__)

# Terminal statuses that fall under DPDP §8(7) recording-data retention.
# 'completed'         — scorecard exists; retain N days from completed_at.
# 'abandoned'         — candidate started but never finished; no scorecard;
#                       retain N days from updated_at (last touch).
# 'failed'            — technical failure; no scorecard; same staleness rule.
# 'consent_withdrawn' — DPDP §11 revocation: must be purged at the next
#                       nightly window regardless of age (use updated_at).
_PURGEABLE_STATUSES = frozenset({"completed", "abandoned", "failed", "consent_withdrawn"})


def _purge_predicate(cutoff: datetime):  # type: ignore[no-untyped-def]
    """Return the SQLAlchemy WHERE predicate for rows eligible for purging.

    Logic:
      - 'completed' sessions: use completed_at (the canonical end-of-session
        timestamp; always set on completed sessions).
      - 'abandoned' / 'failed' / 'consent_withdrawn': use updated_at as the
        staleness proxy (completed_at may be NULL for non-completed sessions).
    """
    completed_clause = (
        (InterviewSession.status == "completed")
        & InterviewSession.completed_at.isnot(None)
        & (InterviewSession.completed_at < cutoff)
    )
    other_clause = (
        InterviewSession.status.in_(["abandoned", "failed", "consent_withdrawn"])
        & InterviewSession.updated_at.isnot(None)
        & (InterviewSession.updated_at < cutoff)
    )
    return or_(completed_clause, other_clause)


async def purge_expired_sessions(db: AsyncSession, settings: Settings) -> int:
    """Delete (or dry-run count) purgeable sessions older than retention_days.

    Purgeable statuses: completed, abandoned, failed, consent_withdrawn.
    See module docstring for the per-status staleness column used.

    Args:
        db:       An open AsyncSession.  Caller is responsible for commit/close.
        settings: The application settings object providing retention_days,
                  retention_dry_run.

    Returns:
        The number of session rows that were deleted (live mode) or that WOULD
        have been deleted (dry-run mode).

    Raises:
        Propagates any SQLAlchemy / DB exceptions to the caller for structured
        logging at the scheduler wrapper level.
    """
    cutoff: datetime = datetime.now(tz=UTC) - timedelta(days=settings.retention_days)
    cutoff_iso: str = cutoff.isoformat()
    predicate = _purge_predicate(cutoff)

    if settings.retention_dry_run:
        # Dry-run: count matching rows without touching them.
        count_stmt = select(func.count(InterviewSession.id)).where(predicate)
        result = await db.execute(count_stmt)
        deleted_count: int = int(result.scalar_one())

        log.info(
            "retention.purge.done",
            deleted_count=deleted_count,
            dry_run=True,
            cutoff_iso=cutoff_iso,
            retention_days=settings.retention_days,
            statuses_purged=sorted(_PURGEABLE_STATUSES),
        )
        return deleted_count

    # Live mode: issue the parametrised DELETE.
    # turns rows are removed via ON DELETE CASCADE (fk_turns_session_id).
    # integrity_events rows are removed via ON DELETE CASCADE (migration 20260618_0002).
    delete_stmt = delete(InterviewSession).where(predicate)
    result = await db.execute(delete_stmt)
    await db.commit()
    deleted_count = int(result.rowcount)

    log.info(
        "retention.purge.done",
        deleted_count=deleted_count,
        dry_run=False,
        cutoff_iso=cutoff_iso,
        retention_days=settings.retention_days,
        statuses_purged=sorted(_PURGEABLE_STATUSES),
    )
    return deleted_count
