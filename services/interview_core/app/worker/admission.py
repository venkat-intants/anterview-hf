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
import os
from collections.abc import Callable
from typing import Any

from app.worker_capacity import (
    SlotResult,
    claim_job_marker,
    clear_job_marker,
    extend_slot,
    release_slot,
    reserve_slot,
)

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


async def extend_lease(redis_factory: RedisFactory, room_name: str, ttl_seconds: int) -> bool:
    """Promote the short token-time reservation to the interview-long lease.

    False when the slot was already gone (the reservation lapsed before the job
    started) or Redis failed: the interview then runs with no ledger entry and
    the ledger under-counts until it ends, so say so.
    """
    if not room_name:
        return False
    try:
        extended = await extend_slot(redis_factory(), room_name, ttl_seconds=ttl_seconds)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker.slot_extend_failed room=%s err=%s",
            room_name, type(exc).__name__,
        )
        return False
    if not extended:
        logger.warning(
            "interview-worker.slot_missing_on_accept room=%s - the reservation lapsed; "
            "this interview is running without a ledger entry",
            room_name,
        )
    return extended


async def release(redis_factory: RedisFactory, room_name: str) -> None:
    """Free this room's slot and job marker (best-effort; expiry is the backstop)."""
    if not room_name:
        return
    try:
        redis = redis_factory()
        await release_slot(redis, room_name)
        await clear_job_marker(redis, room_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker.slot_release_failed room=%s err=%s",
            room_name, type(exc).__name__,
        )


def room_has_job(server: Any, room_name: str) -> bool:
    """Is this worker already running a job for *room_name*?

    A second job for the same room means two interviewers talking over each
    other, double billing and two writers on one transcript. ``server`` is the
    livekit-agents server object; None / an unexpected shape answers False.
    """
    if server is None or not room_name:
        return False
    try:
        return any(job_room_name(j.job) == room_name for j in server.active_jobs)
    except (AttributeError, TypeError):
        return False


async def decide(
    redis_factory: RedisFactory,
    room_name: str,
    *,
    active_local: int,
    cap: int,
    ttl_seconds: int,
    mem_per_job_mb: int,
    mem_limit_mb: int,
    marker_ttl_seconds: int = 120,
) -> tuple[str | None, str, int]:
    """Decide whether to accept a job. Returns ``(reject_reason, verdict, active)``.

    ``reject_reason`` is None to accept. Guards, in order:

    0. A per-room job marker (``SET NX``): a second job for a room that already
       has one is rejected before anything else is touched.
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

    # One interviewer JOB per room, across every worker. First guard so a
    # duplicate never touches the ledger. Fails open on a Redis error.
    marker_set = False
    if room_name:
        try:
            marker_set = await claim_job_marker(redis_factory(), room_name, marker_ttl_seconds)
            if not marker_set:
                return "this room already has an interviewer job", SLOT_UNKNOWN, 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "interview-worker.job_marker_failed room=%s err=%s - failing open",
                room_name, type(exc).__name__,
            )

    async def _reject(reason: str, v: str, a: int) -> tuple[str, str, int]:
        # We set the marker, so a rejection must not leave it blocking a retry.
        if marker_set:
            try:
                await clear_job_marker(redis_factory(), room_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "interview-worker.job_marker_clear_failed room=%s err=%s",
                    room_name, type(exc).__name__,
                )
        return reason, v, a

    if cap > 0:
        verdict, others = await claim_slot(
            redis_factory, room_name, cap=cap, ttl_seconds=ttl_seconds
        )
        if verdict == SLOT_FULL:
            return await _reject(
                f"concurrency ceiling reached (slots={others} cap={cap})", verdict, others
            )

    # Jobs in OTHER processes show up only in the ledger; jobs sharing this
    # process are only exact in the local counter. Trust the larger.
    active = max(active_local, others)

    if cap > 0 and active_local >= cap:
        return await _reject(
            f"concurrency ceiling reached (active={active_local} cap={cap})", verdict, active
        )

    if mem_per_job_mb > 0 and mem_limit_mb > 0:
        # Incremental cost only: the prewarmed VAD model (~100-200 MB) is already
        # in the worker and shared across jobs.
        estimated = (active + 1) * mem_per_job_mb
        if estimated > mem_limit_mb:
            return await _reject(
                f"estimated RSS {estimated} MB would exceed container limit {mem_limit_mb} MB",
                verdict,
                active,
            )
    return None, verdict, active


# Slots of headroom between what LiveKit thinks is "full" and our real cap.
# livekit-agents computes   effective_load = load + reserved_offers * (load / jobs)
# where ``reserved_offers`` counts job offers accepted but not yet launched. An
# offer is counted in BOTH terms for a moment (launched into ``active_jobs`` while
# its request task has not finished), so with 11 of 12 jobs running and one more
# in flight LiveKit saw 12/12 and told the server "no worker is available" for the
# 12th candidate (replica test, 2026-10). Reporting running/(cap+2) makes LiveKit's
# gate looser by two slots; the EXACT cap is enforced by the Redis ledger and the
# job marker in ``decide``, so being loose here costs only a cheap rejection.
LOAD_HEADROOM_SLOTS = 2


def worker_load(server: Any, cap: int) -> float:
    """Load reported to LiveKit: running jobs / (cap + headroom), NOT container CPU.

    livekit-agents' default is a 2.5 s moving average of whole-container CPU
    against ``load_threshold``. In the single-container HF Space every API burst
    (bcrypt, PDF, exam grading) counts towards it, so the worker flapped
    FULL/AVAILABLE every few seconds with only three interviews running - and a
    job offered while FULL is never re-offered by LiveKit.

    With ``worker_load_threshold = 1.0`` the worker is AVAILABLE until
    ``running + in-flight offers`` reaches ``cap + LOAD_HEADROOM_SLOTS``; the
    precise cap is the ledger's job (see ``LOAD_HEADROOM_SLOTS``). livekit-agents
    also divides load by active jobs to shrink its idle-process target as the
    worker fills.
    """
    if cap <= 0:
        return 0.0
    try:
        running = len(server.active_jobs)
    except AttributeError:
        # Never let a livekit-agents API change turn into "always FULL".
        return 0.0
    return min(1.0, running / (cap + LOAD_HEADROOM_SLOTS))


def idle_process_target(
    configured: int | None, cap: int, cpu_count: int | None = None
) -> int:
    """Warm job processes to keep: configured, else min(cap, 2 x CPUs, 8).

    livekit-agents' default is ``min(cpu_count, 4)`` = 2 on the Space, so a THIRD
    interview inside a few seconds logged "no warmed process available for job,
    waiting for one to be created", missed its assignment timeout ("no worker
    available") and was never re-offered. A warm process per allowed slot removes
    that wait - but every warm process holds the prewarmed VAD model (~100-200 MB)
    and, after each job starts, the pool respawns one more, which on a small host
    competes for CPU with the interviews it exists to serve (replica test: p95
    turn latency 6.7 s with 11 processes starting at once on 2 vCPU). So the
    default is capped at two per CPU: 2 on a 2-vCPU Space (= its cap), 4 on the
    2-vCPU/12-slot replica. This is a reasoned bound, NOT a measured optimum -
    set WORKER_NUM_IDLE_PROCESSES to override once a host has been measured.
    """
    if configured is not None:
        return configured
    if cap <= 0:
        return 2
    cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    return max(1, min(cap, MAX_IDLE_PROCESSES, 2 * cpus))
