"""Admission control for the interview worker: slots, load, warm processes.

Split out of ``interview_worker.py`` (IC-4: that file must not grow; new code
that is not about running a LiveKit job lives here). Everything in this module
takes its inputs as arguments - settings values and a Redis *factory* - so it
imports nothing from the worker and is testable without a LiveKit job.

Why the cross-process ledger exists (2026-10 load test)
-------------------------------------------------------
livekit-agents runs each interview in its OWN child process, while
``request_fnc`` runs in the parent. A module-level ``_active_jobs`` counter
incremented inside the job is therefore invisible to the parent (it reads 0
forever), and the single ``worker:active_jobs`` Redis key the job processes
published was whichever child wrote last. The ledger in
``app/worker_capacity.py`` is shared by all processes and by the HTTP server, so
it is the one place a true count exists.

Fail-open: every Redis failure here answers ``SLOT_UNKNOWN`` / does nothing.
Rejecting a job because a ledger cannot be read is the one outcome that strands
a candidate with certainty; the load function and the memory estimate still bound
the real load.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.worker_capacity import SlotResult, extend_slot, release_slot, reserve_slot

logger = logging.getLogger("interview-worker")

SLOT_HELD = "held"  # the token endpoint already reserved this room's slot
SLOT_CLAIMED = "claimed"  # no reservation existed; we took a free slot
SLOT_FULL = "full"  # ledger is at the cap
SLOT_UNKNOWN = "unknown"  # Redis unreachable / no room name -> fail OPEN

# Upper bound on warm job processes whatever the cap: each holds the prewarmed
# VAD model, and an unbounded WORKER_MAX_CONCURRENT_JOBS must not become an
# unbounded number of idle processes.
MAX_IDLE_PROCESSES = 8

RedisFactory = Callable[[], Any]


def job_room_name(job_or_request: Any) -> str:
    """The LiveKit room name (== session_id) of a ``Job`` or ``JobRequest``.

    Empty string when it cannot be read as a string; every caller treats that as
    "no ledger operation" rather than an error.
    """
    name = getattr(getattr(job_or_request, "room", None), "name", "")
    return name if isinstance(name, str) else ""


async def claim_slot(
    redis_factory: RedisFactory, room_name: str, *, cap: int, ttl_seconds: int
) -> tuple[str, int]:
    """Confirm or take this room's slot. Returns ``(verdict, other_slots)``.

    ``other_slots`` is how many OTHER interviews hold a slot (0 when unknown).
    Never raises.
    """
    if not room_name:
        return SLOT_UNKNOWN, 0
    try:
        result, occupied = await reserve_slot(
            redis_factory(), room_name, cap=cap, ttl_seconds=ttl_seconds
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker.slot_claim_failed room=%s err=%s - failing open",
            room_name, type(exc).__name__,
        )
        return SLOT_UNKNOWN, 0
    if result is SlotResult.FULL:
        return SLOT_FULL, occupied
    verdict = SLOT_HELD if result is SlotResult.HELD else SLOT_CLAIMED
    return verdict, max(0, occupied - 1)


async def extend_lease(redis_factory: RedisFactory, room_name: str, ttl_seconds: int) -> None:
    """Promote the short token-time reservation to the interview-long lease."""
    if not room_name:
        return
    try:
        await extend_slot(redis_factory(), room_name, ttl_seconds=ttl_seconds)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker.slot_extend_failed room=%s err=%s",
            room_name, type(exc).__name__,
        )


async def release(redis_factory: RedisFactory, room_name: str) -> None:
    """Free this room's slot (best-effort; lease expiry is the backstop)."""
    if not room_name:
        return
    try:
        await release_slot(redis_factory(), room_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker.slot_release_failed room=%s err=%s",
            room_name, type(exc).__name__,
        )


async def decide(
    redis_factory: RedisFactory,
    room_name: str,
    *,
    active_local: int,
    cap: int,
    ttl_seconds: int,
    mem_per_job_mb: int,
    mem_limit_mb: int,
) -> tuple[str | None, str, int]:
    """Decide whether to accept a job. Returns ``(reject_reason, verdict, active)``.

    ``reject_reason`` is None to accept. Guards, in order:

    1. The Redis slot ledger (``cap > 0``) - authoritative and cross-process.
       A room whose slot the token endpoint already reserved is accepted; one
       with no reservation tries to take a slot and is rejected only if the
       ledger is genuinely full; a Redis outage accepts.
    2. The local counter - exact where jobs share this process, harmless where
       they do not.
    3. A memory estimate: one more job's RSS above the container limit would
       OOM-kill EVERY live interview, which is far worse than one rejection.
    """
    verdict, others = SLOT_UNKNOWN, 0
    if cap > 0:
        verdict, others = await claim_slot(
            redis_factory, room_name, cap=cap, ttl_seconds=ttl_seconds
        )
        if verdict == SLOT_FULL:
            return f"concurrency ceiling reached (slots={others} cap={cap})", verdict, others

    # Jobs in OTHER processes show up only in the ledger; jobs sharing this
    # process are only exact in the local counter. Trust the larger.
    active = max(active_local, others)

    if cap > 0 and active_local >= cap:
        return f"concurrency ceiling reached (active={active_local} cap={cap})", verdict, active

    if mem_per_job_mb > 0 and mem_limit_mb > 0:
        # Incremental cost only: the prewarmed VAD model (~100-200 MB) is already
        # in the worker and shared across jobs.
        estimated = (active + 1) * mem_per_job_mb
        if estimated > mem_limit_mb:
            return (
                f"estimated RSS {estimated} MB would exceed container limit {mem_limit_mb} MB",
                verdict,
                active,
            )
    return None, verdict, active


def worker_load(server: Any, cap: int) -> float:
    """Load reported to LiveKit: running jobs / cap, NOT container CPU.

    livekit-agents' default is a 2.5 s moving average of whole-container CPU
    against ``load_threshold``. In the single-container HF Space every API burst
    (bcrypt, PDF, exam grading) counts towards it, so the worker flapped
    FULL/AVAILABLE every few seconds with only three interviews running - and a
    job offered while FULL is never re-offered (under automatic dispatch).

    With ``worker_load_threshold = 1.0`` the worker is AVAILABLE while
    ``active < cap`` and FULL at ``active == cap``; livekit-agents also divides
    load by active jobs to shrink its idle-process target as the worker fills, so
    the warm pool never pushes total processes past the cap.
    """
    if cap <= 0:
        return 0.0
    try:
        running = len(server.active_jobs)
    except AttributeError:
        # Never let a livekit-agents API change turn into "always FULL".
        return 0.0
    return min(1.0, running / cap)


def idle_process_target(configured: int | None, cap: int) -> int:
    """Warm job processes to keep: configured, else the job cap (bounded).

    livekit-agents' default is ``min(cpu_count, 4)`` = 2 on the Space, so a THIRD
    interview inside a few seconds logged "no warmed process available for job,
    waiting for one to be created", missed its assignment timeout ("no worker
    available") and was never re-offered. One warm process per allowed slot
    removes that wait; each costs the prewarmed VAD model (~100-200 MB).
    """
    if configured is not None:
        return configured
    return min(cap, MAX_IDLE_PROCESSES) if cap > 0 else 2
