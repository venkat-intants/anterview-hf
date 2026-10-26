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
import math
import os
from collections.abc import Callable
from typing import Any

from app.worker_capacity import (
    SlotResult,
    claim_job_marker,
    clear_job_marker,
    extend_slot,
    refresh_job_marker,
    release_slot,
    reserve_slot,
    slot_held,
    user_pointer_key,
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


async def extend_lease(
    redis_factory: RedisFactory,
    room_name: str,
    ttl_seconds: int,
    *,
    refresh_marker: bool = True,
) -> bool:
    """Promote the short token-time reservation to the interview-long lease.

    ``refresh_marker`` also re-sets the room's job marker to the same lease: it is
    claimed with a SHORT starting-time TTL (so an accepted job that never reaches
    the job process cannot block a re-dispatch), and once the job is genuinely
    running it must protect the room for the whole interview, across replicas too.

    False when the slot was already gone (the reservation lapsed before the job
    started) or Redis failed: the interview then runs with no ledger entry and
    the ledger under-counts until it ends, so say so.
    """
    if not room_name:
        return False
    try:
        redis = redis_factory()
        extended = await extend_slot(redis, room_name, ttl_seconds=ttl_seconds)
        if refresh_marker:
            await refresh_job_marker(redis, room_name, ttl_seconds)
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


def at_capacity(server: Any, cap: int) -> bool:
    """Has this worker already LAUNCHED ``cap`` jobs? Needs no Redis.

    The parent process is where ``request_fnc`` runs, and ``server.active_jobs``
    is the one place it can see running jobs (they live in child processes, so a
    module counter incremented inside the job is invisible here). This is the
    exact backstop for when Redis is down and the ledger fails open - without it
    the worker takes ``cap + LOAD_HEADROOM_SLOTS`` jobs during an outage.
    """
    if cap <= 0 or server is None:
        return False
    try:
        return len(server.active_jobs) >= cap
    except (AttributeError, TypeError):
        return False


async def claim_permitted(redis_factory: RedisFactory, room_name: str) -> bool:
    """May this worker CLAIM a fresh slot for the room's user? (security N1)

    Called only when there was no live reservation (the join token outlived it, or
    the dispatch bypassed the token endpoint). The user must not already hold
    another slot or another in-progress session - the one-interview-per-user rule
    that ``/token`` enforces and that a lapsed-then-claimed slot would otherwise
    bypass. Fails OPEN on any error: refusing on a lookup failure strands a
    candidate.
    """
    try:
        import uuid

        from sqlalchemy import select, text

        from app.config import settings
        from app.database import get_session_factory, init_engine
        from app.models import Session as InterviewSession

        init_engine()
        async with get_session_factory()() as db:
            user_id = (
                await db.execute(
                    select(InterviewSession.user_id).where(
                        InterviewSession.id == uuid.UUID(room_name)
                    )
                )
            ).scalar_one_or_none()
            if user_id is None:
                return True
            if settings.max_active_sessions_per_user > 0:
                others = (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM sessions WHERE user_id = :uid AND id <> :sid"
                            " AND status = 'in_progress' AND deleted_at IS NULL"
                            " AND updated_at > now() - make_interval(mins => :mins)"
                        ),
                        {
                            "uid": user_id,
                            "sid": uuid.UUID(room_name),
                            "mins": settings.active_session_window_minutes,
                        },
                    )
                ).scalar_one()
                if int(others) >= settings.max_active_sessions_per_user:
                    return False
        redis = redis_factory()
        pointer = await redis.get(user_pointer_key(str(user_id)))
        return not (pointer and pointer != room_name and await slot_held(redis, pointer))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker.claim_check_failed room=%s err=%s - failing open",
            room_name, type(exc).__name__,
        )
        return True


async def decide(
    redis_factory: RedisFactory,
    room_name: str,
    *,
    server: Any = None,
    active_local: int,
    cap: int,
    ttl_seconds: int,
    mem_per_job_mb: int,
    mem_limit_mb: int,
    marker_ttl_seconds: int = 45,
    lease_seconds: int | None = None,
) -> tuple[str | None, str, int]:
    """Decide whether to accept a job. Returns ``(reject_reason, verdict, active)``.

    ``reject_reason`` is None to accept. Guards, in order:

    A. Redis-free, on this worker: refuse a second job for a room it already
       runs, and refuse when it has already launched ``cap`` jobs (``at_capacity``).
       These hold during a Redis outage, when everything below fails open.
    0. A per-room job marker (``SET NX``): a second job for a room that already
       has one - on ANY worker - is rejected before anything else is touched.
    1. The Redis slot ledger (``cap > 0``) - authoritative and cross-process.
       A room whose slot the token endpoint already reserved (HELD) is accepted
       and its lease is lengthened AT ONCE (only the worker may lengthen one), so
       it cannot lapse while the job process starts; one with no reservation
       (CLAIMED) must pass ``claim_permitted``; a full ledger rejects; a Redis
       outage accepts.
    2. The local counter - exact where jobs share this process, harmless where
       they do not.
    3. A memory estimate: one more job's RSS above the container limit would
       OOM-kill EVERY live interview, which is far worse than one rejection.
    """
    verdict, others = SLOT_UNKNOWN, 0

    if room_has_job(server, room_name):
        return "this worker already runs a job for this room", SLOT_UNKNOWN, 0
    if at_capacity(server, cap):
        return f"this worker already runs {cap} jobs (cap)", SLOT_UNKNOWN, cap

    # One interviewer JOB per room, across every worker. Fails open on a Redis error.
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
        if verdict == SLOT_HELD and lease_seconds:
            await extend_lease(redis_factory, room_name, lease_seconds, refresh_marker=False)
        if verdict == SLOT_CLAIMED and not await claim_permitted(redis_factory, room_name):
            return await _reject(
                "this user already holds another slot or has another interview in progress",
                verdict,
                others,
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


def container_cpus() -> int:
    """CPUs available to THIS container, honouring a cgroup CPU quota.

    ``os.cpu_count()`` reports the HOST's cores, so a 2-vCPU container on a 16-core
    host would size its warm pool for 16 (review F8). livekit-agents' own CPU
    monitor reads the cgroup quota (``cpu.max``); fall back to ``os.cpu_count()``
    only if it cannot be imported or answers nonsense.
    """
    try:
        from livekit.agents.utils.hw import get_cpu_monitor

        value = float(get_cpu_monitor().cpu_count())
        if value > 0:
            return max(1, math.ceil(value))
    except Exception as exc:  # noqa: BLE001 - never block worker start-up on this
        logger.warning("interview-worker.cpu_count_fallback err=%s", type(exc).__name__)
    return os.cpu_count() or 1


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
    turn latency 6.7 s with 11 processes starting at once on 2 vCPU; 3.0 s with
    this default on the 12-slot replica). So the default is capped at two per
    CPU - the CONTAINER's CPUs (``container_cpus``) - giving 2 on a 2-vCPU Space
    (= its cap) and 4 on the 2-vCPU/12-slot replica. A reasoned bound, not a
    measured optimum: set WORKER_NUM_IDLE_PROCESSES to override.
    """
    if configured is not None:
        return configured
    if cap <= 0:
        return 2
    cpus = cpu_count if cpu_count is not None else container_cpus()
    return max(1, min(cap, MAX_IDLE_PROCESSES, 2 * cpus))
