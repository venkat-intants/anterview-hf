"""Openings that go live at a chosen time — PH3-B4a.

An interval loop, deliberately, and not an APScheduler cron job. ``scheduling.py``
documents why at length: a ``CronTrigger`` cannot fire while the container is
suspended, and the demo Space sleeps after ~48h, so a job pinned to a clock time
is not delayed by a sleeping container — it is *skipped*. An interval loop wakes
and finds the work still waiting.

THE TOLERANCE, STATED
"Publishes at the scheduled time" means *within a second or two of it, on a
running instance*: the loop sleeps until the next schedule falls due rather than
polling on a fixed cadence, so the interval is a CEILING on lateness and not the
tolerance anybody normally experiences. On a cold Space it still means "shortly
after the Space next wakes" — no loop can fire inside a suspended container, and
that is written into the API docstring and the console copy rather than left to
be discovered during a demo.

THE GATE IS RE-CHECKED AT FIRE TIME
A requisition scheduled while approved and rejected an hour later must not
publish. So the publisher does not trust the state that existed when the
schedule was set: it re-reads approval at the moment it acts, and a requisition
that no longer qualifies is left alone with its schedule intact and a warning
logged, rather than being silently published or silently unscheduled. Somebody
asked for this opening to go live; the system declining to do so is information,
not a no-op.

WHAT IT WRITES
``public_apply_enabled = true``, ``published_at = now()``, ``publish_at = NULL``.
Clearing the request is what makes the operation idempotent: a second pass finds
nothing due. The publish gate itself (app/publishing.py) is untouched — this
flips one of the inputs it already reads.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models import AuditLog
from app.publishing import APPROVED
from app.scheduling import record_loop_pass

log = structlog.get_logger(__name__)

#: The scheduled-job bookkeeping id, so a missed or failing publisher shows up
#: in the same place every other background worker does.
LOOP_JOB_ID = "scheduled_publishing"

#: How often to look. Sixty seconds is the floor rather than the target: the
#: cost of a pass is one index probe against a partial index, and the tolerance
#: a recruiter experiences is exactly this number.
_MIN_INTERVAL_SECONDS = 60

#: How many to publish in one pass. A bound rather than a limit anybody will
#: hit: it exists so a bad data migration that schedules ten thousand openings
#: cannot turn one pass into a ten-thousand-row transaction.
_BATCH = 100

_task: asyncio.Task[None] | None = None


def interval_seconds() -> int:
    configured = getattr(settings, "scheduled_publish_interval_seconds", 60)
    return max(_MIN_INTERVAL_SECONDS, int(configured))


async def due_requisitions(db: AsyncSession, *, now: datetime) -> list[dict[str, Any]]:
    """Everything whose publish time has arrived and which is not already live.

    ``FOR UPDATE SKIP LOCKED`` so two instances sharing a database never publish
    the same opening twice — the same "claim, don't coordinate" approach the
    erasure executor and the scheduler take.
    """
    rows = (
        await db.execute(
            text(
                "SELECT id, company_id, title, approval_status, status,"
                "       publish_at, publish_at_set_by_user_id, public_apply_enabled"
                "  FROM job_requisitions"
                " WHERE publish_at IS NOT NULL"
                "   AND publish_at <= :now"
                "   AND deleted_at IS NULL"
                " ORDER BY publish_at"
                " LIMIT :lim"
                " FOR UPDATE SKIP LOCKED"
            ),
            {"now": now, "lim": _BATCH},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _blocked_reason(row: dict[str, Any]) -> str | None:
    """Why this opening must not be published right now, or None.

    Re-derived at fire time rather than trusted from when the schedule was set.
    """
    if str(row.get("approval_status")) != APPROVED:
        return f"approval_status is {row.get('approval_status')}"
    if str(row.get("status")) != "open":
        return f"status is {row.get('status')}"
    return None


async def publish_due(factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """One pass. Returns a small histogram for the loop's log line."""
    published = skipped = 0
    now = datetime.now(tz=UTC)
    async with factory() as db:
        for row in await due_requisitions(db, now=now):
            requisition_id = row["id"]
            reason = _blocked_reason(row)
            if reason is not None:
                # Left scheduled on purpose. Somebody asked for this to go live;
                # if the block is cleared (the requisition is approved, or
                # reopened) the next pass publishes it. Silently unscheduling
                # would lose the request, and silently publishing would defeat
                # the gate.
                skipped += 1
                log.warning(
                    "publish.scheduled.blocked",
                    requisition_id=str(requisition_id), reason=reason,
                )
                continue

            await db.execute(
                text(
                    "UPDATE job_requisitions"
                    "   SET public_apply_enabled = true, published_at = :n,"
                    "       publish_at = NULL, updated_at = :n"
                    " WHERE id = :i"
                ),
                {"i": requisition_id, "n": now},
            )
            db.add(
                AuditLog(
                    # The person who SET the schedule, not the loop: an audit
                    # trail that attributes every scheduled publication to
                    # nobody cannot answer who decided it. actor_type says the
                    # system carried it out on their behalf.
                    actor_id=row.get("publish_at_set_by_user_id"),
                    actor_type="system",
                    action="requisition.published.scheduled",
                    resource_type="job_requisition",
                    resource_id=requisition_id,
                    details={
                        "company_id": str(row["company_id"]),
                        "title": row["title"],
                        "scheduled_for": row["publish_at"].isoformat(),
                        # The gap between asked-for and actually-done. This is
                        # the tolerance, made visible rather than inferred.
                        "delay_seconds": round(
                            (now - row["publish_at"]).total_seconds()
                        ),
                    },
                    event_ts=now,
                )
            )
            published += 1
            log.info(
                "publish.scheduled.done",
                requisition_id=str(requisition_id), company_id=str(row["company_id"]),
            )
        await db.commit()
    return {"published": published, "skipped": skipped}


async def next_due_at(factory: async_sessionmaker[AsyncSession]) -> datetime | None:
    """When the earliest not-yet-due schedule falls due, if any.

    Deliberately NOT filtered by approval state. This decides when to WAKE, and
    the gate is re-derived at fire time anyway (see the module docstring); a row
    that turns out to be blocked costs one pass that logs why. Waking late
    because the approval arrived after this query would be the worse error.
    """
    async with factory() as db:
        return await db.scalar(
            text(
                "SELECT MIN(publish_at) FROM job_requisitions"
                " WHERE publish_at IS NOT NULL"
                "   AND publish_at > :now"
                "   AND deleted_at IS NULL"
            ),
            {"now": datetime.now(tz=UTC)},
        )


async def _sleep_seconds(
    factory: async_sessionmaker[AsyncSession], interval: int
) -> float:
    """How long to wait before the next pass.

    A FIXED interval makes the tolerance the whole interval every single time,
    for no better reason than that the loop was not looking: an opening set for
    09:00:00 published at up to 09:00:59, and "publishes at the scheduled time"
    then meant "within a minute of it". Sleeping until the next schedule
    actually falls due makes the common case exact to about a second.

    Still capped at ``interval``, which is what keeps the two properties the
    fixed sleep had: the loop-pass heartbeat that makes a stalled publisher
    visible keeps its cadence, and a schedule created DURING a sleep is picked
    up no later than it would have been before.

    NEVER WORSE ON LATENESS. NOT ON LOAD - be precise about which.
    The cap bounds how LATE a publish can be; it does not bound how OFTEN this
    loop runs. The old loop had a hard ceiling of one pass per ``interval``
    (about three database sessions a minute); this one's ceiling is one pass per
    SECOND, reached if upcoming schedules are ever clustered about a second
    apart. That is a ~60x rise in the worst-case load ceiling, and an earlier
    draft of this docstring said "never worse than the fixed interval" without
    qualifying it - true of the only dimension that draft was considering.

    It is not a practical denial of service: it needs an authenticated
    hr_manager to create and get approved many requisitions scheduled seconds
    apart, each costing more work than the pass it triggers, and
    uq_job_requisitions_company_title forces unique titles. The 1.0s floor below
    is what bounds it; raising that floor to ~5s would buy a tighter ceiling and
    still sit inside the "a few seconds" promise in tolerance_note().

    The steady-state cost is one extra indexed MIN() probe per pass, paid on
    every pass including quiet ones - a real cost, not a saving. It rides
    ix_job_requisitions_publish_due (partial on publish_at where it is not null
    and the row is live); EXPLAIN reports an Index Only Scan, checked rather
    than assumed.

    A failure here must not stop the publisher: the interval is the fallback,
    which is exactly the behaviour before this existed.
    """
    try:
        upcoming = await next_due_at(factory)
    except Exception as exc:  # noqa: BLE001 — the loop must outlive one probe
        log.warning(
            "publish.scheduled.next_due_probe_failed",
            exc_type=type(exc).__name__, exc_msg=str(exc),
        )
        return float(interval)
    if upcoming is None:
        return float(interval)
    if upcoming.tzinfo is None:  # a naive column value is UTC by convention
        upcoming = upcoming.replace(tzinfo=UTC)
    remaining = (upcoming - datetime.now(tz=UTC)).total_seconds()
    # Floored at one second so a row that moves under us — or a clock that
    # steps backwards — can never turn this into a hot loop.
    return max(1.0, min(float(interval), remaining))


async def _loop(factory: async_sessionmaker[AsyncSession]) -> None:
    interval = interval_seconds()
    # A short delay before the first pass so boot is not competing with
    # migrations and the first requests for the connection pool — the same
    # courtesy the reconciler extends.
    await asyncio.sleep(min(30, interval))
    while True:
        started = datetime.now(tz=UTC)
        error: str | None = None
        try:
            await publish_due(factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the loop must outlive one pass
            error = f"{type(exc).__name__}: {exc}"
            log.error(
                "publish.scheduled.pass_failed",
                exc_type=type(exc).__name__, exc_msg=str(exc),
            )
        # Every pass leaves a record, so a publisher that has silently stopped
        # is visible in the same place a missed retention purge is (A6).
        await record_loop_pass(factory, LOOP_JOB_ID, started_at=started, error=error)
        await asyncio.sleep(await _sleep_seconds(factory, interval))


def start(factory: async_sessionmaker[AsyncSession]) -> None:
    """Start the publisher. No-op if already running."""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop(factory), name="scheduled-publisher")
    log.info("publish.scheduled.started", interval_s=interval_seconds())


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _task
    _task = None


def tolerance_note() -> str:
    """The honest promise, in one sentence, for API docs and console copy.

    Kept in step with what the loop actually does. It used to say "within about
    one minute" because the loop slept a fixed interval; it now wakes at the due
    time, so the promise is seconds — but the cold-Space caveat is unchanged and
    still has to be said, because that is the case a console would otherwise
    quietly misrepresent.
    """
    return (
        "Scheduled openings go live within a few seconds of the chosen time "
        "while the service is running; if the service is asleep, shortly after "
        f"it next wakes, and never more than about {interval_seconds() // 60 or 1} "
        "minute(s) late."
    )


__all__ = [
    "LOOP_JOB_ID",
    "due_requisitions",
    "interval_seconds",
    "next_due_at",
    "publish_due",
    "start",
    "stop",
    "tolerance_note",
]
