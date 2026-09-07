"""Scheduled-job bookkeeping — A6.

The problem this solves
-----------------------
APScheduler's ``CronTrigger`` fires only while the process is alive. The demo
tier runs on a Hugging Face Space that suspends after ~48h without a visit, so a
job pinned to 02:00 UTC is not delayed by a sleeping container — it is *skipped*,
silently, for as many nights as the Space stays cold. Two jobs are affected: the
DPDP §8(7) retention purge and the nightly watcher sweep. Neither is the kind of
thing anyone notices missing until it matters.

The fix is not to abandon cron. A nightly job should still run nightly on a warm
instance; what it must additionally do is notice, on waking, that its window
passed while it was asleep. So every scheduled job is wrapped by
:func:`run_scheduled_job`, which records each run in ``scheduled_job_runs``, and
:func:`run_overdue_jobs_on_startup` replays anything whose interval has elapsed.

Why a table and not Redis
-------------------------
This is the record of whether a statutory deletion job ran. Redis on the demo
tier is Upstash, shared, and may be flushed; losing the record would cause a
duplicate purge run (harmless) or mask a missed one (not harmless). It is one
row per job, written once a day.

Concurrency
-----------
Several instances may share a database. ``_claim`` uses an atomic conditional
UPDATE, so exactly one instance wins a given window and the rest no-op. This is
the same "claim, don't coordinate" approach the erasure executor takes with
``SELECT … FOR UPDATE SKIP LOCKED``.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger(__name__)

# A job whose last successful run is older than its interval plus this grace is
# considered overdue. The grace absorbs ordinary jitter (a slow tick, a restart
# a few minutes before the trigger) so a warm instance does not double-run.
_OVERDUE_GRACE = timedelta(minutes=30)

# A run that has been "running" longer than this is treated as abandoned — the
# process was killed mid-job, which on a Space is routine rather than
# exceptional. Without this a single hard restart would wedge the job forever.
_STALE_RUNNING_AFTER = timedelta(hours=6)


async def _claim(db: AsyncSession, job_id: str, *, interval: timedelta) -> bool:
    """Atomically claim the current window for ``job_id``.

    Returns True if this caller should run the job. The whole decision is one
    statement so two instances waking together cannot both win: the row is
    inserted if absent, and updated only while it is genuinely due.
    """
    now = datetime.now(tz=UTC)
    due_before = now - interval + _OVERDUE_GRACE
    stale_before = now - _STALE_RUNNING_AFTER

    claimed = await db.scalar(
        text(
            """
            INSERT INTO scheduled_job_runs (job_id, last_started_at, last_status, run_count)
            VALUES (:jid, :now, 'running', 0)
            ON CONFLICT (job_id) DO UPDATE
               SET last_started_at = :now,
                   last_status     = 'running'
             -- Two ways to win the claim, parenthesised explicitly because the
             -- precedence here is load-bearing: either the job is idle and its
             -- window has elapsed, or a previous run died mid-flight and the
             -- 'running' marker is stale.
             WHERE (scheduled_job_runs.last_status IS DISTINCT FROM 'running'
                    AND (scheduled_job_runs.last_finished_at IS NULL
                         OR scheduled_job_runs.last_finished_at <= :due_before))
                OR (scheduled_job_runs.last_status = 'running'
                    AND scheduled_job_runs.last_started_at <= :stale_before)
            RETURNING 1
            """
        ),
        {"jid": job_id, "now": now, "due_before": due_before, "stale_before": stale_before},
    )
    await db.commit()
    return claimed is not None


async def _release(
    db: AsyncSession, job_id: str, *, status: str, error: str | None = None
) -> None:
    """Record the outcome. Never raises — bookkeeping must not mask the job."""
    try:
        await db.execute(
            text(
                """
                UPDATE scheduled_job_runs
                   SET last_finished_at = :now,
                       last_status      = :status,
                       last_error       = :err,
                       run_count        = run_count + 1
                 WHERE job_id = :jid
                """
            ),
            {
                "jid": job_id,
                "now": datetime.now(tz=UTC),
                "status": status,
                # Truncated: this column is read by an operator in a dashboard,
                # not parsed, and an unbounded traceback in a row nobody reads
                # is just storage.
                "err": (error or "")[:2000] or None,
            },
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — bookkeeping is never fatal
        log.warning("scheduler.release_failed", job_id=job_id, error=str(exc))


async def run_scheduled_job(
    factory: async_sessionmaker[AsyncSession],
    *,
    job_id: str,
    interval: timedelta,
    fn: Callable[[], Awaitable[None]],
    force: bool = False,
) -> bool:
    """Run ``fn`` once for this window, recording the outcome.

    Returns True if the job actually ran. ``force=True`` skips the due check but
    still takes the claim, so a manual trigger cannot collide with a scheduled
    one.

    The job's own exceptions are caught and recorded rather than propagated:
    APScheduler drops a job after a raised exception, which would turn one
    transient database hiccup into a permanently silent nightly purge.
    """
    async with factory() as db:
        if force:
            # A forced run still needs the claim, but should not be refused for
            # being early — clear the finished marker so the window looks due.
            await db.execute(
                text(
                    "UPDATE scheduled_job_runs SET last_finished_at = NULL "
                    "WHERE job_id = :jid AND last_status IS DISTINCT FROM 'running'"
                ),
                {"jid": job_id},
            )
            await db.commit()
        if not await _claim(db, job_id, interval=interval):
            log.debug("scheduler.skipped_not_due", job_id=job_id)
            return False

    started = time.monotonic()
    log.info("scheduler.job.start", job_id=job_id)
    try:
        await fn()
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.error(
            "scheduler.job.error",
            job_id=job_id,
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
            elapsed_s=round(time.monotonic() - started, 2),
        )
        async with factory() as db:
            await _release(db, job_id, status="error", error=f"{type(exc).__name__}: {exc}")
        return True

    log.info(
        "scheduler.job.done", job_id=job_id, elapsed_s=round(time.monotonic() - started, 2)
    )
    async with factory() as db:
        await _release(db, job_id, status="ok")
    return True


async def run_overdue_jobs_on_startup(
    factory: async_sessionmaker[AsyncSession],
    jobs: list[tuple[str, timedelta, Callable[[], Awaitable[None]]]],
) -> None:
    """Replay any job whose interval elapsed while this process was not running.

    Called once during startup, after the scheduler is up. Each job's own due
    check inside :func:`run_scheduled_job` decides whether it actually runs, so
    this is safe on an ordinary restart: a job that ran an hour ago stays put.
    """
    for job_id, interval, fn in jobs:
        try:
            ran = await run_scheduled_job(factory, job_id=job_id, interval=interval, fn=fn)
            if ran:
                log.info("scheduler.catchup.ran", job_id=job_id)
        except Exception as exc:  # noqa: BLE001 — one bad job must not block boot
            log.error(
                "scheduler.catchup.failed",
                job_id=job_id,
                exc_type=type(exc).__name__,
                exc_msg=str(exc),
            )


async def job_status(factory: async_sessionmaker[AsyncSession]) -> list[dict[str, object]]:
    """Every job's last run — for the ops surface and for tests."""
    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT job_id, last_started_at, last_finished_at, last_status, "
                    "       last_error, run_count "
                    "  FROM scheduled_job_runs ORDER BY job_id"
                )
            )
        ).all()
    return [
        {
            "job_id": r[0],
            "last_started_at": r[1].isoformat() if r[1] else None,
            "last_finished_at": r[2].isoformat() if r[2] else None,
            "last_status": r[3],
            "last_error": r[4],
            "run_count": r[5],
        }
        for r in rows
    ]
