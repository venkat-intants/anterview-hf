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

Catching up without restarting
------------------------------
The overdue check used to run once, at startup, and startup waited for it — so
a woken Space answered nothing until the whole retention purge had finished.
And a process that was *paused* rather than restarted (a suspended VM, a laptop
lid) never ran it at all: APScheduler resumes, finds the trigger time already
past, and drops the run. Now :func:`start_catchup` runs the same check every
fifteen minutes in the background. Startup does not wait on it.

It uses a later threshold than the scheduler does (``_CATCHUP_SLACK``), so it
only ever picks up a window that was genuinely missed. With the same threshold
it would claim each night's run a little before the scheduler's own hour, and
the job would creep earlier every day.

Failures and history
--------------------
A run that fails is retried after ``_ERROR_RETRY_AFTER`` rather than waiting a
whole interval for the next night. Every run is written to
``scheduled_job_run_log``; the interval loops (reconciler, reminder sweep)
report each pass through :func:`record_loop_pass`, which updates their summary
row every time and writes history only when a pass fails.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger(__name__)

# A job whose last successful run is older than its interval minus this grace
# is due. The grace absorbs ordinary jitter (a slow tick, a restart a few
# minutes before the trigger) so the scheduler's own run is never refused for
# being a few minutes early.
_OVERDUE_GRACE = timedelta(minutes=30)

# The catch-up check only claims a window that is this far PAST due — a window
# the scheduler has actually missed, never one it is about to run.
_CATCHUP_SLACK = timedelta(hours=2)

# How often the background catch-up looks for missed windows.
CATCHUP_INTERVAL = timedelta(minutes=15)

# A failed run is tried again after this, instead of a whole interval later.
_ERROR_RETRY_AFTER = timedelta(hours=1)

# A run that has been "running" longer than this is treated as abandoned — the
# process was killed mid-job, which on a Space is routine rather than
# exceptional. Without this a single hard restart would wedge the job forever.
_STALE_RUNNING_AFTER = timedelta(hours=6)

_HISTORY_RETENTION = timedelta(days=90)

# Error text is stored for an operator to read. An exception message can quote
# the row it choked on — a unique-violation names the duplicate email — and
# these tables are declared as holding no personal data, so contact details
# are masked before anything is written.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s-]{8,}\d")


def _scrub(error: str | None) -> str | None:
    if not error:
        return None
    return _PHONE.sub("<number>", _EMAIL.sub("<email>", error))[:2000]


async def _claim(
    db: AsyncSession, job_id: str, *, interval: timedelta, catchup: bool = False
) -> bool:
    """Atomically claim the current window for ``job_id``.

    Returns True if this caller should run the job. The whole decision is one
    statement so two instances waking together cannot both win: the row is
    inserted if absent, and updated only while it is genuinely due.
    """
    now = datetime.now(tz=UTC)
    due_before = now - interval - _CATCHUP_SLACK if catchup else now - interval + _OVERDUE_GRACE
    retry_before = now - _ERROR_RETRY_AFTER
    stale_before = now - _STALE_RUNNING_AFTER

    claimed = await db.scalar(
        text(
            """
            INSERT INTO scheduled_job_runs (job_id, last_started_at, last_status, run_count)
            VALUES (:jid, :now, 'running', 0)
            ON CONFLICT (job_id) DO UPDATE
               SET last_started_at = :now,
                   last_status     = 'running'
             -- Three ways to win the claim, parenthesised explicitly because the
             -- precedence here is load-bearing: the job is idle and its window
             -- has elapsed; or its last run failed and the retry delay has
             -- passed; or a previous run died mid-flight and the 'running'
             -- marker is stale.
             WHERE (scheduled_job_runs.last_status IS DISTINCT FROM 'running'
                    AND (scheduled_job_runs.last_finished_at IS NULL
                         OR scheduled_job_runs.last_finished_at <= :due_before))
                OR (scheduled_job_runs.last_status = 'error'
                    AND scheduled_job_runs.last_finished_at <= :retry_before)
                OR (scheduled_job_runs.last_status = 'running'
                    AND scheduled_job_runs.last_started_at <= :stale_before)
            RETURNING 1
            """
        ),
        {"jid": job_id, "now": now, "due_before": due_before,
         "retry_before": retry_before, "stale_before": stale_before},
    )
    await db.commit()
    return claimed is not None


_LOG_SQL = """
INSERT INTO scheduled_job_run_log
       (job_id, trigger, status, started_at, finished_at, duration_ms, error)
VALUES (:jid, :trigger, :status, :started, :now, :ms, :err)
"""


async def _release(
    db: AsyncSession,
    job_id: str,
    *,
    status: str,
    error: str | None = None,
    trigger: str = "cron",
    started_at: datetime | None = None,
) -> None:
    """Record the outcome, in the summary row and the history. Never raises —
    bookkeeping must not mask the job."""
    now = datetime.now(tz=UTC)
    started = started_at or now
    try:
        params = {
            "jid": job_id, "now": now, "status": status, "trigger": trigger,
            "started": started,
            "ms": int((now - started).total_seconds() * 1000),
            # Truncated and scrubbed: read by an operator, never parsed.
            "err": _scrub(error),
        }
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
            params,
        )
        await db.execute(text(_LOG_SQL), params)
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
    trigger: str = "cron",
) -> bool:
    """Run ``fn`` once for this window, recording the outcome.

    Returns True if the job actually ran. ``force=True`` skips the due check but
    still takes the claim, so a manual trigger cannot collide with a scheduled
    one. ``trigger="catchup"`` claims only a window that was genuinely missed
    (see _CATCHUP_SLACK).

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
        if not await _claim(db, job_id, interval=interval, catchup=trigger == "catchup"):
            log.debug("scheduler.skipped_not_due", job_id=job_id, trigger=trigger)
            return False

    started = time.monotonic()
    started_at = datetime.now(tz=UTC)
    log.info("scheduler.job.start", job_id=job_id, trigger=trigger)
    try:
        await fn()
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.error(
            "scheduler.job.error",
            job_id=job_id,
            trigger=trigger,
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
            elapsed_s=round(time.monotonic() - started, 2),
        )
        async with factory() as db:
            await _release(db, job_id, status="error", error=f"{type(exc).__name__}: {exc}",
                           trigger=trigger, started_at=started_at)
        return True

    log.info(
        "scheduler.job.done", job_id=job_id, trigger=trigger,
        elapsed_s=round(time.monotonic() - started, 2),
    )
    async with factory() as db:
        await _release(db, job_id, status="ok", trigger=trigger, started_at=started_at)
    return True


Job = tuple[str, timedelta, Callable[[], Awaitable[None]]]


async def run_overdue_jobs_on_startup(
    factory: async_sessionmaker[AsyncSession],
    jobs: list[Job],
    *,
    trigger: str = "catchup",
) -> None:
    """Run every job whose window was missed. One tick of the catch-up loop.

    Each job's own claim inside :func:`run_scheduled_job` decides whether it
    actually runs, so this is safe to call as often as you like: a job that ran
    an hour ago stays put. (The name predates the loop; it is still the right
    thing to call once at startup, and the loop calls it every tick.)
    """
    for job_id, interval, fn in jobs:
        try:
            ran = await run_scheduled_job(
                factory, job_id=job_id, interval=interval, fn=fn, trigger=trigger
            )
            if ran:
                log.info("scheduler.catchup.ran", job_id=job_id)
        except Exception as exc:  # noqa: BLE001 — one bad job must not block the rest
            log.error(
                "scheduler.catchup.failed",
                job_id=job_id,
                exc_type=type(exc).__name__,
                exc_msg=str(exc),
            )


async def _prune_history(factory: async_sessionmaker[AsyncSession]) -> None:
    try:
        async with factory() as db:
            await db.execute(
                text("DELETE FROM scheduled_job_run_log WHERE started_at < :floor"),
                {"floor": datetime.now(tz=UTC) - _HISTORY_RETENTION},
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("scheduler.prune_failed", error=str(exc))


_catchup_task: asyncio.Task[None] | None = None


async def _catchup_loop(
    factory: async_sessionmaker[AsyncSession], jobs: list[Job], interval: timedelta
) -> None:
    # Short first delay: soon enough that a Space woken after two days runs its
    # purge within a minute, late enough not to compete with boot.
    await asyncio.sleep(60)
    while True:
        try:
            await run_overdue_jobs_on_startup(factory, jobs)
            await _prune_history(factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("scheduler.catchup_loop_failed", error=str(exc))
        await asyncio.sleep(interval.total_seconds())


def start_catchup(
    factory: async_sessionmaker[AsyncSession],
    jobs: list[Job],
    *,
    interval: timedelta = CATCHUP_INTERVAL,
) -> None:
    """Start looking for missed windows in the background. Startup does not wait."""
    global _catchup_task
    if _catchup_task is not None and not _catchup_task.done():
        return
    _catchup_task = asyncio.create_task(
        _catchup_loop(factory, jobs, interval), name="scheduler-catchup"
    )
    log.info("scheduler.catchup.started", jobs=[j[0] for j in jobs],
             interval_s=int(interval.total_seconds()))


async def stop_catchup() -> None:
    global _catchup_task
    if _catchup_task is None:
        return
    _catchup_task.cancel()
    try:
        await _catchup_task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001 — shutdown must not raise
        pass
    _catchup_task = None


async def record_loop_pass(
    factory: async_sessionmaker[AsyncSession],
    job_id: str,
    *,
    started_at: datetime,
    error: str | None = None,
) -> None:
    """Record one pass of an interval loop (reconciler, reminder sweep).

    The summary row is updated every pass, so the status endpoint shows when
    each loop last ran and whether it worked — a loop that has quietly died
    shows up as a stale timestamp. History gets a row only for a FAILED pass.
    Never raises.
    """
    now = datetime.now(tz=UTC)
    status = "error" if error else "ok"
    params = {"jid": job_id, "now": now, "started": started_at, "status": status,
              "trigger": "loop", "err": _scrub(error),
              "ms": int((now - started_at).total_seconds() * 1000)}
    try:
        async with factory() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO scheduled_job_runs
                           (job_id, last_started_at, last_finished_at, last_status,
                            last_error, run_count)
                    VALUES (:jid, :started, :now, :status, :err, 1)
                    ON CONFLICT (job_id) DO UPDATE
                       SET last_started_at  = :started,
                           last_finished_at = :now,
                           last_status      = :status,
                           last_error       = :err,
                           run_count        = scheduled_job_runs.run_count + 1
                    """
                ),
                params,
            )
            if error:
                await db.execute(text(_LOG_SQL), params)
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — bookkeeping is never fatal
        log.warning("scheduler.loop_record_failed", job_id=job_id, error=str(exc))


async def job_status(
    factory: async_sessionmaker[AsyncSession], *, history: int = 20
) -> list[dict[str, object]]:
    """Every job's last run, plus its most recent history — for the ops surface."""
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
        recent = (
            await db.execute(
                text(
                    "SELECT job_id, trigger, status, started_at, duration_ms, error FROM ("
                    "  SELECT *, row_number() OVER (PARTITION BY job_id ORDER BY started_at DESC)"
                    "         AS rn FROM scheduled_job_run_log) h"
                    " WHERE rn <= :n ORDER BY job_id, started_at DESC"
                ),
                {"n": history},
            )
        ).all()
    by_job: dict[str, list[dict[str, object]]] = {}
    for h in recent:
        by_job.setdefault(h[0], []).append({
            "trigger": h[1], "status": h[2],
            "started_at": h[3].isoformat() if h[3] else None,
            "duration_ms": h[4], "error": h[5],
        })
    return [
        {
            "job_id": r[0],
            "last_started_at": r[1].isoformat() if r[1] else None,
            "last_finished_at": r[2].isoformat() if r[2] else None,
            "last_status": r[3],
            "last_error": r[4],
            "run_count": r[5],
            "recent": by_job.get(r[0], []),
        }
        for r in rows
    ]
