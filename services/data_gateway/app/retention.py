"""90-day retention purge — DPDP §8(7) compliance.

Daily job (APScheduler) that deletes session + turn + scorecard rows older than
``settings.retention_days`` from their ``completed_at`` / ``updated_at`` timestamp,
together with the object-storage artefacts those scorecards point at.

Scope (what gets purged):
  - sessions WHERE status='completed' AND completed_at < NOW() - INTERVAL N days
  - sessions WHERE status='abandoned' AND updated_at < NOW() - INTERVAL N days
    (abandoned sessions may have no completed_at; we use updated_at as the
    staleness proxy — if the session has not been touched in N days it is safe
    to delete.  DPDP §8(7) covers any recording data, not only completed sessions.)
  - sessions WHERE status='failed' AND updated_at < NOW() - INTERVAL N days
    (failed = never produced a scorecard, no candidate value in retention)
  - sessions WHERE status='consent_withdrawn' — at the NEXT nightly window,
    with no age condition.  See the status table below for why this one status
    does not wait out the retention window.
  - turns CASCADE via FK (ON DELETE CASCADE on turns.session_id — confirmed in
    migration eda3829ec95a, fk_turns_session_id with ondelete='CASCADE')
  - integrity_events CASCADE via FK (ON DELETE CASCADE on
    integrity_events.session_id — migration 20260618_0002). So proctoring /
    biometric-derived gaze data is purged together with its session here; the
    erasure endpoint additionally hard-deletes it immediately on request.
  - scorecards — DELETED EXPLICITLY, because they do NOT cascade.  ``models.py``
    (Scorecard.session_id) says why: the column is deliberately not a foreign
    key, since scorecards are written by feedback_billing while sessions live in
    interview_core / data_gateway, and cross-service FK enforcement is done at
    the application layer.  This job IS that application layer for the retention
    half.  Until 2026-08-07 it was not: the purge issued one
    ``DELETE FROM sessions`` and the scorecard row survived indefinitely holding
    ``summary`` (a verdict on the candidate), ``strengths``, ``improvements``,
    ``scores`` and ``composite_score`` — orphaned, so no session-scoped erasure
    or audit query could even find it (DPDP-4, CWE-459).
  - scorecard OBJECTS in R2 — ``report_pdf_key`` (the PDF) and
    ``transcript_key`` (the transcript JSON) are collected BEFORE the row that
    holds them is deleted and removed from ``settings.s3_scorecard_bucket``.
    See the ordering note below.
  - audio recordings — turns.audio_s3_key is a placeholder that is NULL today.
    When the voice pipeline starts populating it, it belongs in the same
    collect-before-delete phase as the scorecard keys.

ORDERING (load-bearing — mirrors admin_ops/app/erasure_executor.py):
  1. SELECT the ids of the sessions about to be purged.
  2. SELECT report_pdf_key / transcript_key for those sessions' scorecards.
  3. DELETE those objects from the scorecard bucket.
  4. DELETE the scorecards rows.
  5. DELETE the sessions rows (turns + integrity_events cascade), then commit.

  Collection must precede deletion or the keys are gone before we can use them —
  that is exactly the bug M-1a fixed in the erasure path, where every superseded
  resume PDF was orphaned in R2 while the executor stamped 'completed'.  And
  object deletion must precede ROW deletion: if storage fails we abort with
  nothing deleted and the next nightly run retries the whole set, which is
  recoverable.  The other order leaves objects in the bucket with no row left
  pointing at them, which is not.

PER-RUN CAP:
  Step 1 takes at most ``MAX_SESSIONS_PER_RUN`` ids, and every later step
  addresses that materialised batch — so the ordering guarantee above is
  unchanged, it just applies to a bounded set. The rest waits for tomorrow's run.
  The cap exists because the all-or-nothing guarantee that makes this job safe
  also makes an unbounded run unable to finish: see the constant for the
  reasoning and for why the default is what it is.

INDEX NOTE (migration 20260807_0001_b4d6f8a0c2e5):
  ``idx_sessions_retention`` used to carry ``WHERE deleted_at IS NULL`` while
  ``_purge_predicate`` emitted no such clause, so Postgres could never use it and
  the nightly purge sequential-scanned a table sized for 20 lakh users (DPDP-9).
  The migration drops the partial predicate and adds
  ``idx_sessions_retention_updated`` on (status, updated_at), because the
  abandoned / failed branch filters on ``updated_at`` — a column the original
  index did not contain at all.  The predicate is unchanged in MEANING: a
  soft-deleted session is still purgeable, and deliberately so; it is pending
  erasure, which is the last thing that should be excluded from a deletion job.

  For very large tables create both CONCURRENTLY outside a transaction:
    CREATE INDEX CONCURRENTLY idx_sessions_retention ON sessions (status, completed_at);
  See the migration docstring for details.

  TIER-2 FOLLOW-UP: partition the `turns` table by created_at (monthly) so the
  cascading delete of old turns is a fast partition drop, not a per-row delete.
  This is deferred because partitioning a populated table is destructive; the
  indexes above are sufficient for Phase 1 volumes.

Out of scope (what is NEVER deleted by this job):
  - dpdp_consent_ledger rows (legal audit trail per DPDP §8(7) — remain indefinitely
    or until a user-initiated §11 erasure request, task #46)
  - users (auth/identity — separate lifecycle)
  - jobs / NOS catalogue (reference data — no retention requirement)
  - sessions in non-terminal states: 'created', 'in_progress'
    (active sessions are never deleted mid-flight)
  - resumes / applicants and their upload-bucket objects — candidate-lifecycle
    data, not session-recording data.  Erased on request by
    admin_ops/app/erasure_executor.py, which owns that inventory.

Safety rail:
  settings.retention_dry_run defaults to True.  In dry-run mode the job SELECTs
  the rows that WOULD be deleted and logs the count, but issues no DELETE
  statement and touches no object storage.
  Operators MUST explicitly set RETENTION_DRY_RUN=false in production after a
  dry-run cycle confirms expected delete counts.

Log events emitted:
  retention.purge.done   — one event per run; carries deleted_count, dry_run,
                           cutoff_iso, retention_days, statuses_purged,
                           max_sessions_per_run and, in live mode,
                           scorecards_deleted + objects_deleted + capped (the run
                           filled its cap, so more is waiting). Dry-run carries
                           would_exceed_cap instead, because its count is the
                           whole backlog and not one batch.
                           Never logs individual session IDs or user IDs.
  retention.purge.error  — unexpected exception; carries exc_type, exc_msg.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from sqlalchemy import delete, func, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Scorecard
from app.models import Session as InterviewSession

log = structlog.get_logger(__name__)

# Terminal statuses that fall under DPDP §8(7) recording-data retention.
#
# status              staleness column   retention rule
# ------------------  -----------------  ----------------------------------------
# 'completed'         completed_at       N days from completion. A scorecard
#                                        exists, so this is the status whose
#                                        purge MUST also delete the scorecard row
#                                        and its R2 objects (DPDP-4).
# 'abandoned'         updated_at         N days from last touch. Candidate started
#                                        but never finished; usually no scorecard.
# 'failed'            updated_at         N days from last touch. Technical
#                                        failure; no scorecard.
# 'consent_withdrawn' (none)             Purged at the NEXT nightly window,
#                                        regardless of age. DPDP §6(4) requires a
#                                        fiduciary to cease processing "within a
#                                        reasonable time" of withdrawal, and
#                                        STORING a recording is processing — so
#                                        holding it for the remainder of a 90-day
#                                        window would be the one status where
#                                        waiting is the non-compliant choice.
#                                        This module's docstring has claimed this
#                                        rule since the status was introduced;
#                                        until 2026-08-07 the predicate did not
#                                        implement it and applied the ordinary
#                                        N-day window instead.
_PURGEABLE_STATUSES = frozenset({"completed", "abandoned", "failed", "consent_withdrawn"})

# Statuses whose staleness is measured from updated_at rather than completed_at.
_STALENESS_STATUSES = ("abandoned", "failed")

# The status written when a candidate exercises DPDP §6(4)/§11 mid-flight.
# Imported by app/routers/consent.py, which is the code path that writes it —
# one definition so the vocabulary here and the writer cannot drift apart.
CONSENT_WITHDRAWN_STATUS = "consent_withdrawn"

# asyncpg's wire protocol caps a statement at 32767 bound parameters, so an
# ``IN (:id, :id, ...)`` list has a hard ceiling that is NOT a tuning knob. The
# purge set is materialised once (see the ORDERING note) and then fed to the
# DELETEs in chunks below that ceiling, so one enormous backlog — the first live
# run after RETENTION_DRY_RUN is flipped off — cannot fail on a protocol limit.
_ID_CHUNK_SIZE = 1000

# Ceiling on how many sessions ONE run may purge.
#
# _ID_CHUNK_SIZE bounds the bind parameters per STATEMENT; it does not bound the
# run. Step 1 materialises every eligible id and step 2 every object key, then
# steps 3-5 delete objects one HTTP round-trip at a time (s3_upload.delete_objects
# loops per key) inside a single uncommitted transaction. At the 20-lakh-user
# figure the first live run after RETENTION_DRY_RUN is flipped off is a backlog
# of everything ever completed, and that shape has a worse failure than memory:
# the purge is deliberately all-or-nothing, so one transient storage error aborts
# before any row is deleted and the next night retries the WHOLE set. Uncapped, a
# run long enough that at least one round-trip is likely to fail can never
# commit, and the backlog never drains — the compliance job stops being merely
# slow and stops working. A cap makes progress monotonic: each run commits its
# own bounded batch.
#
# 10_000 because the binding cost is those sequential object deletes — worst case
# two objects per session at ~50 ms per round-trip keeps a full batch inside the
# off-peak window the 03:00 UTC cron exists for, while still being several times
# a day's terminal sessions at the 20-lakh figure, so steady state is never
# capped and a missed night still catches up. Whatever does not fit is picked up
# by the next run; ``capped`` in retention.purge.done says when that is
# happening. A deployment that must drain a large one-off backlog faster should
# raise this deliberately (it belongs in Settings the day that is needed) rather
# than have the ceiling removed.
MAX_SESSIONS_PER_RUN = 10_000


def _chunked(items: list[uuid.UUID]) -> list[list[uuid.UUID]]:
    """Split *items* into ``_ID_CHUNK_SIZE``-sized lists."""
    return [items[i : i + _ID_CHUNK_SIZE] for i in range(0, len(items), _ID_CHUNK_SIZE)]


def _purge_predicate(cutoff: datetime):  # type: ignore[no-untyped-def]
    """Return the SQLAlchemy WHERE predicate for rows eligible for purging.

    Three clauses, matching the status table above:
      - 'completed': completed_at (the canonical end-of-session timestamp;
        always set on completed sessions) older than *cutoff*.
      - 'abandoned' / 'failed': updated_at as the staleness proxy, because
        completed_at may be NULL for non-completed sessions.
      - 'consent_withdrawn': no time condition at all.

    Deliberately emits NO ``deleted_at IS NULL`` clause. A soft-deleted session
    is one awaiting erasure, which is the last row a deletion job should skip;
    the alignment with ``idx_sessions_retention`` (DPDP-9) was therefore made by
    dropping the partial predicate from the index, not by adding it here.
    """
    completed_clause = (
        (InterviewSession.status == "completed")
        & InterviewSession.completed_at.isnot(None)
        & (InterviewSession.completed_at < cutoff)
    )
    stale_clause = (
        InterviewSession.status.in_(_STALENESS_STATUSES)
        & InterviewSession.updated_at.isnot(None)
        & (InterviewSession.updated_at < cutoff)
    )
    withdrawn_clause = InterviewSession.status == CONSENT_WITHDRAWN_STATUS
    return or_(completed_clause, stale_clause, withdrawn_clause)


async def _collect_scorecard_object_keys(
    db: AsyncSession, session_ids: list[uuid.UUID]
) -> list[str]:
    """Return every non-NULL R2 key held by the scorecards of *session_ids*.

    Read BEFORE any DELETE runs: once the scorecard row is gone the key is gone
    with it and the object is orphaned in the bucket forever, which is the
    failure this whole ordering exists to prevent.
    """
    keys: list[str] = []
    for chunk in _chunked(session_ids):
        result = await db.execute(
            select(Scorecard.report_pdf_key, Scorecard.transcript_key).where(
                Scorecard.session_id.in_(chunk)
            )
        )
        for pdf_key, transcript_key in result.all():
            if pdf_key:
                keys.append(str(pdf_key))
            if transcript_key:
                keys.append(str(transcript_key))
    # Deduplicate while preserving order: two rows pointing at one object would
    # otherwise inflate objects_deleted, which is the number an auditor reads.
    return list(dict.fromkeys(keys))


async def _delete_rows_by_session_id(
    db: AsyncSession, model: type[Any], session_ids: list[uuid.UUID], column: Any
) -> int:
    """DELETE *model* rows whose *column* is in *session_ids*; return the rowcount."""
    total = 0
    for chunk in _chunked(session_ids):
        result = await db.execute(delete(model).where(column.in_(chunk)))
        # AsyncSession.execute is typed as returning Result, which declares no
        # rowcount; a DML statement actually returns a CursorResult, which does.
        # Narrow it rather than ignoring, so a future change that stops issuing
        # DML here is caught.
        total += int(cast("CursorResult[Any]", result).rowcount)
    return total


async def purge_expired_sessions(db: AsyncSession, settings: Settings) -> int:
    """Delete (or dry-run count) purgeable sessions older than retention_days.

    Purgeable statuses: completed, abandoned, failed, consent_withdrawn.
    See the module docstring for the per-status staleness column used and for
    the collect-before-delete ordering, which is load-bearing.

    Args:
        db:       An open AsyncSession.  Caller is responsible for close; this
                  function commits once, at the end of the live path.
        settings: The application settings object providing retention_days,
                  retention_dry_run and the S3 scorecard-bucket credentials.

    Returns:
        The number of session rows that were deleted (live mode, at most
        ``MAX_SESSIONS_PER_RUN`` — the remainder is purged by the next run) or
        that WOULD have been deleted (dry-run mode, uncapped: the whole backlog).

    Raises:
        StorageNotConfiguredError: object keys were collected but S3 is not
            configured.  Nothing has been deleted at that point and nothing is
            committed, so the next nightly run retries the same set.
        ClientError: an object delete failed.  Same guarantee.
        Propagates any SQLAlchemy / DB exceptions to the caller for structured
        logging at the scheduler wrapper level.
    """
    cutoff: datetime = datetime.now(tz=UTC) - timedelta(days=settings.retention_days)
    cutoff_iso: str = cutoff.isoformat()
    predicate = _purge_predicate(cutoff)

    if settings.retention_dry_run:
        # Dry-run: count matching rows without touching them or object storage.
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
            max_sessions_per_run=MAX_SESSIONS_PER_RUN,
            # Deliberately NOT capped: dry-run's job is to report the true size of
            # the backlog before an operator flips the flag. Reporting the cap
            # instead would hide exactly the fact this field exists to surface —
            # that the first live run will only take a batch off the front.
            would_exceed_cap=deleted_count > MAX_SESSIONS_PER_RUN,
        )
        return deleted_count

    # ------------------------------------------------------------------
    # Live mode. Step 1 — materialise the purge set ONCE.
    #
    # The three following statements all address this exact id list rather than
    # re-evaluating the predicate. Re-evaluating would let the set drift between
    # statements (a session completing mid-run enters the predicate on the next
    # statement's snapshot), and the scorecard whose objects we deleted would
    # then not be the scorecard whose row we delete.
    #
    # LIMIT MAX_SESSIONS_PER_RUN — see the constant for why a run must be bounded.
    # The cap is applied HERE and nowhere else on purpose: every later step
    # addresses this one materialised list, so collect-before-delete and the
    # fail-closed shortfall check below cover the capped batch exactly as they
    # covered the whole set. No ORDER BY: the rows are all equally due, and the
    # only property that matters is that each run removes the batch it took, so
    # successive runs make progress regardless of which rows Postgres returns.
    # ------------------------------------------------------------------
    id_result = await db.execute(
        select(InterviewSession.id).where(predicate).limit(MAX_SESSIONS_PER_RUN)
    )
    session_ids: list[uuid.UUID] = list(id_result.scalars().all())
    # A full batch means there is probably more waiting. "Probably" because a
    # backlog of exactly the cap reports capped=True on its final run — the
    # cheaper wrong answer, since the alternative is a second COUNT over the same
    # predicate on every single run to be sure.
    capped: bool = len(session_ids) >= MAX_SESSIONS_PER_RUN

    if not session_ids:
        log.info(
            "retention.purge.done",
            deleted_count=0,
            scorecards_deleted=0,
            objects_deleted=0,
            dry_run=False,
            cutoff_iso=cutoff_iso,
            retention_days=settings.retention_days,
            statuses_purged=sorted(_PURGEABLE_STATUSES),
            max_sessions_per_run=MAX_SESSIONS_PER_RUN,
            capped=False,
        )
        return 0

    # Step 2 — collect the R2 keys while the rows that hold them still exist.
    object_keys = await _collect_scorecard_object_keys(db, session_ids)

    # Step 3 — delete the objects. Raises on any failure (including "storage is
    # not configured", which IS a failure and not a no-op), and because no row
    # has been deleted and nothing committed, aborting here costs one night.
    #
    # Imported per call, not at module scope, for two reasons: the tests patch
    # ``app.s3_upload.delete_objects`` and a module-level ``from … import`` would
    # bind the real function before any patch could reach it; and app/routers/
    # consent.py imports this module for CONSENT_WITHDRAWN_STATUS, so a
    # module-scope import would drag aioboto3 + botocore into the import graph of
    # a candidate-facing route that never touches object storage.
    from app.s3_upload import delete_objects  # noqa: PLC0415

    objects_deleted = await delete_objects(
        {settings.s3_scorecard_bucket: object_keys}, settings=settings
    )
    if objects_deleted < len(object_keys):
        # delete_objects raises rather than under-deleting, so reaching here
        # means its contract changed. Refuse to delete the rows that are the
        # only remaining record of these objects.
        raise RuntimeError(
            f"Object storage reported {objects_deleted} of {len(object_keys)} "
            "scorecard objects deleted; refusing to delete the rows that "
            "reference them."
        )

    # Step 4 — scorecards. Explicit, because session_id is not a FK (see the
    # module docstring). Must run before the sessions delete only for clarity;
    # both address the same materialised id list.
    scorecards_deleted = await _delete_rows_by_session_id(
        db, Scorecard, session_ids, Scorecard.session_id
    )

    # Step 5 — sessions. turns rows are removed via ON DELETE CASCADE
    # (fk_turns_session_id); integrity_events likewise (migration 20260618_0002).
    deleted_count = await _delete_rows_by_session_id(
        db, InterviewSession, session_ids, InterviewSession.id
    )
    await db.commit()

    log.info(
        "retention.purge.done",
        deleted_count=deleted_count,
        scorecards_deleted=scorecards_deleted,
        objects_deleted=objects_deleted,
        dry_run=False,
        cutoff_iso=cutoff_iso,
        retention_days=settings.retention_days,
        statuses_purged=sorted(_PURGEABLE_STATUSES),
        max_sessions_per_run=MAX_SESSIONS_PER_RUN,
        capped=capped,
    )
    return deleted_count
