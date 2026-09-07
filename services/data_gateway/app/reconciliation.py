"""Reconciliation loop — A1.

What this replaces
------------------
Three enrichment steps in this service are written as "best-effort": ATS scoring
on applicant upload, resume embedding, and the same two inside a bulk upload.
Each catches its own failure, logs a warning, and moves on — which is right, an
outage must not lose the applicant. What was missing is the other half: nothing
ever came back for the row afterwards.

The observable consequences today are a manual **Reindex** button that processes
96 applicants per click, and applicants that sit unscored indefinitely with a
code comment that says "HR can POST /rescore later". Both are a human doing a
retry loop by hand.

How it finds work
-----------------
By the *absence* of data, not by a queue:

* ``ats_overall IS NULL`` and there is resume text -> needs scoring
* ``embedding IS NULL`` and there is resume text  -> needs embedding

This is deliberate. A queue can drift from reality — a lost message means a row
is never retried and nothing notices. The absence query cannot drift: if the
column is null, the work is outstanding, whatever happened before. It also means
this loop correctly picks up rows that failed *before* it was deployed.

``reconciliation_state`` exists only to stop a permanently broken row consuming
the batch on every cycle. It is an exception list, never the work list; deleting
every row in it simply causes the next pass to retry everything at once.

Why an asyncio loop rather than APScheduler
-------------------------------------------
This is interval work, not calendar work, so it follows the house pattern for
interval work already used by the email outbox worker and the erasure executor:
a task started in ``lifespan`` that sleeps between passes. That also sidesteps
A6 entirely — an interval loop resumes correctly after the Space wakes, whereas
a fixed-hour cron silently skips the window it slept through.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.applicant_enrichment import (
    apply_ats_score,
    apply_extracted_identity,
    store_embedding,
)
from app.config import settings
from app.embedding_client import embed_texts_remote
from app.models import Applicant
from app.notifications_util import create_notification
from app.scoring_client import score_resume_remote

log = structlog.get_logger(__name__)

# How many rows one pass will attempt per check. Small on purpose: a pass should
# finish well inside its interval even when every call is slow, so a backlog
# drains steadily instead of one pass running for an hour and overlapping.
SCORE_BATCH = 12
EMBED_BATCH = 32
# The embedder accepts up to 64 per call; 16 matches what the upload path uses.
EMBED_CHUNK = 16

# Backoff between attempts on one row: ~5 min, 20 min, 80 min, 5.3 h, capped.
_BACKOFF_BASE_SECONDS = 300
_BACKOFF_CAP_SECONDS = 6 * 3600
# After this many attempts a row is parked and reported rather than retried
# forever. A resume that cannot be scored after eight tries is not a transient
# outage; it is a bad row that wants a human.
MAX_ATTEMPTS = 8

KIND_ATS = "applicant_ats"
KIND_EMBED = "applicant_embedding"


@dataclass
class PassResult:
    """What one pass did — returned for logging, tests and the ops endpoint."""

    scored: int = 0
    embedded: int = 0
    # Rows whose real name (and sometimes email) were read out of the PDF and
    # written over the filename-derived placeholder a deferred ingest left.
    named: int = 0
    # Bulk uploads whose last outstanding row finished during this pass, and
    # which therefore produced one "upload complete" notification (A4).
    batches_finished: int = 0
    failed: int = 0
    gave_up: int = 0
    outstanding: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scored": self.scored,
            "embedded": self.embedded,
            "named": self.named,
            "batches_finished": self.batches_finished,
            "failed": self.failed,
            "gave_up": self.gave_up,
            "outstanding": self.outstanding,
        }


def _backoff(attempts: int) -> timedelta:
    return timedelta(
        seconds=min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)))
    )


async def _record_failure(
    db: AsyncSession, kind: str, ref_id: uuid.UUID, error: str
) -> bool:
    """Note a failed attempt and schedule the retry. Returns True if it gave up.

    The row is upserted so the first failure creates it; ``attempts`` drives both
    the backoff and the give-up decision.
    """
    now = datetime.now(tz=UTC)
    row = (
        await db.execute(
            text(
                """
                INSERT INTO reconciliation_state
                       (kind, ref_id, attempts, last_error, last_attempt_at, next_attempt_at)
                VALUES (:kind, :ref, 1, :err, :now, :now)
                ON CONFLICT (kind, ref_id) DO UPDATE
                   SET attempts        = reconciliation_state.attempts + 1,
                       last_error      = :err,
                       last_attempt_at = :now
                RETURNING attempts
                """
            ),
            {"kind": kind, "ref": ref_id, "err": error[:1000], "now": now},
        )
    ).first()
    attempts = int(row[0]) if row else 1

    if attempts >= MAX_ATTEMPTS:
        await db.execute(
            text(
                "UPDATE reconciliation_state SET gave_up_at = :now, next_attempt_at = NULL "
                "WHERE kind = :kind AND ref_id = :ref"
            ),
            {"kind": kind, "ref": ref_id, "now": now},
        )
        await db.commit()
        log.warning(
            "reconcile.gave_up", kind=kind, ref_id=str(ref_id), attempts=attempts, error=error[:200]
        )
        return True

    await db.execute(
        text(
            "UPDATE reconciliation_state SET next_attempt_at = :nxt "
            "WHERE kind = :kind AND ref_id = :ref"
        ),
        {"kind": kind, "ref": ref_id, "nxt": now + _backoff(attempts)},
    )
    await db.commit()
    return False


async def _clear_state(db: AsyncSession, kind: str, ref_id: uuid.UUID) -> None:
    """Forget a row that has now succeeded, so a later failure starts fresh."""
    await db.execute(
        text("DELETE FROM reconciliation_state WHERE kind = :kind AND ref_id = :ref"),
        {"kind": kind, "ref": ref_id},
    )


# The shared tail of both selects: outstanding work that is not currently
# backing off and has not been given up on. Written once because the two checks
# differ only in the column they test for absence.
_DUE_PREDICATE = """
      AND a.deleted_at IS NULL
      AND a.resume_text IS NOT NULL
      AND length(trim(a.resume_text)) > 0
      AND NOT EXISTS (
            SELECT 1 FROM reconciliation_state rs
             WHERE rs.kind = :kind AND rs.ref_id = a.id
               AND (rs.gave_up_at IS NOT NULL
                    OR (rs.next_attempt_at IS NOT NULL AND rs.next_attempt_at > :now))
          )
"""


async def _notify_batch_done(db: AsyncSession, applicant: Applicant) -> bool:
    """Tell the uploader when the LAST row of their bulk upload finishes (A4).

    Called after a row has been scored and committed, so the count below already
    excludes it. Zero remaining means this row was the last one and the batch is
    now fully read.

    One notification per batch, not per applicant: the whole point of the batch
    id is that twenty-five separate reconciler passes are one event to the
    person who started them. It is emitted at most once because the condition —
    "no pending rows left" — can only become true on the transition, and the
    rows that would re-trigger it have already been cleared.

    Returns True when a notification was staged, purely so the pass can count
    it. Failure here must never fail the enrichment that succeeded: the score is
    written and committed before this runs.
    """
    batch_id = applicant.upload_batch_id
    if batch_id is None or applicant.created_by_user_id is None:
        return False
    remaining = await db.scalar(
        text(
            "SELECT count(*) FROM applicants"
            " WHERE upload_batch_id = :b AND pending_enrichment AND deleted_at IS NULL"
        ),
        {"b": batch_id},
    )
    if remaining:
        return False
    total = await db.scalar(
        text("SELECT count(*) FROM applicants WHERE upload_batch_id = :b"),
        {"b": batch_id},
    )
    await create_notification(
        db,
        user_id=applicant.created_by_user_id,
        kind="bulk_upload",
        title="Bulk upload finished",
        body=(
            f"{total} resume{'' if total == 1 else 's'} have been read and scored."
        ),
        link="/hr/applicants",
    )
    await db.commit()
    return True


async def _score_pass(db: AsyncSession, result: PassResult) -> None:
    """Retry ATS scoring for applicants that have none."""
    now = datetime.now(tz=UTC)
    ids = [
        r[0]
        for r in (
            await db.execute(
                text(
                    "SELECT a.id FROM applicants a WHERE a.ats_overall IS NULL"
                    + _DUE_PREDICATE
                    + " ORDER BY a.created_at LIMIT :lim"
                ),
                {"kind": KIND_ATS, "now": now, "lim": SCORE_BATCH},
            )
        ).all()
    ]
    if not ids:
        return

    for aid in ids:
        applicant = await db.get(Applicant, aid)
        if applicant is None:  # deleted between the select and here
            continue
        try:
            score = await score_resume_remote(
                resume_text=applicant.resume_text or "",
                job_title=applicant.target_job_title,
                level=applicant.target_level,
                jd_text=applicant.target_jd_text,
                # Attributed to the account that created the applicant so the
                # internal-token audit trail names a real actor rather than a
                # synthetic "system" principal that has no company scope.
                acting_user_id=str(applicant.created_by_user_id or applicant.id),
            )
        except Exception as exc:  # noqa: BLE001 — one bad row must not stop the pass
            await db.rollback()
            if await _record_failure(db, KIND_ATS, aid, f"{type(exc).__name__}: {exc}"):
                result.gave_up += 1
            result.failed += 1
            continue

        apply_ats_score(applicant, score)
        # A deferred ingest stored this row without reading it, so its name is
        # derived from the filename and its email is missing. The scorer just
        # read both out of the PDF — this is the only moment they are available,
        # so it is the moment they get written.
        if apply_extracted_identity(applicant, score):
            result.named += 1
        await _clear_state(db, KIND_ATS, aid)
        await db.commit()
        result.scored += 1
        # After the commit: the batch is only finished once this row's own
        # pending flag is durably cleared, and a notification is never worth
        # rolling back a score for.
        try:
            if await _notify_batch_done(db, applicant):
                result.batches_finished += 1
        except Exception as exc:  # noqa: BLE001 — notification is not the work
            await db.rollback()
            log.warning(
                "reconcile.batch_notify_failed",
                applicant_id=str(aid),
                error_type=type(exc).__name__,
            )


async def _embed_pass(db: AsyncSession, result: PassResult) -> None:
    """Backfill resume embeddings — what the manual Reindex button did by hand."""
    now = datetime.now(tz=UTC)
    rows = (
        await db.execute(
            text(
                "SELECT a.id, a.company_id, a.resume_text FROM applicants a "
                "WHERE a.embedding IS NULL"
                + _DUE_PREDICATE
                + " ORDER BY a.created_at LIMIT :lim"
            ),
            {"kind": KIND_EMBED, "now": now, "lim": EMBED_BATCH},
        )
    ).all()
    if not rows:
        return

    for i in range(0, len(rows), EMBED_CHUNK):
        chunk = rows[i : i + EMBED_CHUNK]
        try:
            vecs = await embed_texts_remote(
                texts=[r[2] for r in chunk],
                task_type="document",
                acting_user_id=str(chunk[0][0]),
            )
        except Exception as exc:  # noqa: BLE001 — one bad chunk must not stop the pass
            await db.rollback()
            # A chunk failure is almost always the embedder being down rather
            # than these particular rows being bad, so every row in the chunk
            # backs off together and the next pass tries a different slice.
            for r in chunk:
                if await _record_failure(db, KIND_EMBED, r[0], f"{type(exc).__name__}: {exc}"):
                    result.gave_up += 1
                result.failed += 1
            continue

        for r, vec in zip(chunk, vecs, strict=False):
            if not vec:
                continue
            await store_embedding(db, r[1], r[0], vec)
            await _clear_state(db, KIND_EMBED, r[0])
            result.embedded += 1
        await db.commit()


async def _outstanding(db: AsyncSession) -> dict[str, int]:
    """How much work is still queued — the number an operator actually wants."""
    row = (
        await db.execute(
            text(
                """
                SELECT
                  count(*) FILTER (WHERE ats_overall IS NULL)  AS unscored,
                  count(*) FILTER (WHERE embedding   IS NULL)  AS unembedded
                FROM applicants
                WHERE deleted_at IS NULL
                  AND resume_text IS NOT NULL
                  AND length(trim(resume_text)) > 0
                """
            )
        )
    ).first()
    parked = await db.scalar(
        text("SELECT count(*) FROM reconciliation_state WHERE gave_up_at IS NOT NULL")
    )
    return {
        "unscored": int(row[0] or 0) if row else 0,
        "unembedded": int(row[1] or 0) if row else 0,
        "parked": int(parked or 0),
    }


async def run_once(factory: async_sessionmaker[AsyncSession]) -> PassResult:
    """One reconciliation pass. Safe to call concurrently and from tests."""
    result = PassResult()
    async with factory() as db:
        await _score_pass(db, result)
        await _embed_pass(db, result)
        result.outstanding = await _outstanding(db)
    if result.scored or result.embedded or result.failed:
        log.info("reconcile.pass", **result.as_dict())
    return result


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------
_task: asyncio.Task[None] | None = None


async def _loop(factory: async_sessionmaker[AsyncSession]) -> None:
    interval = max(60, settings.reconciliation_interval_seconds)
    # A short delay before the first pass so boot is not competing with
    # migrations and the first requests for the connection pool.
    await asyncio.sleep(min(30, interval))
    while True:
        try:
            await run_once(factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the loop must outlive any one pass
            log.error(
                "reconcile.pass_failed", exc_type=type(exc).__name__, exc_msg=str(exc)
            )
        await asyncio.sleep(interval)


def start(factory: async_sessionmaker[AsyncSession]) -> None:
    """Start the loop. No-op when disabled or already running."""
    global _task
    if not settings.reconciliation_enabled:
        log.info("reconcile.disabled")
        return
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop(factory), name="reconciliation-worker")
    log.info("reconcile.started", interval_s=settings.reconciliation_interval_seconds)


async def stop() -> None:
    """Cancel the loop and wait for it — called from lifespan shutdown."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001 — shutdown must not raise
        pass
    _task = None
    log.info("reconcile.stopped")
