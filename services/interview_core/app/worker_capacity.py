"""Worker capacity — shared-state helpers for admission control.

WHY this module exists:
    The LiveKit interview worker (``app/worker/interview_worker.py``) runs as a
    SEPARATE process from the FastAPI HTTP server.  When the worker is at or
    near its concurrency ceiling, the HTTP ``POST /api/rooms/{id}/token``
    endpoint has no way to know — it issues the join token, the candidate
    enters the LiveKit room, and then… the worker's ``request_fnc`` rejects the
    job.  Under AUTOMATIC dispatch this leaves the candidate in a dead room
    with NO interviewer and no feedback.  That silent failure is WORSE than a
    clear HTTP 503 before the token is issued.

    This module bridges the two processes via a shared Redis key.  The worker
    writes its current active-job count on every admission change; the token
    endpoint reads it BEFORE issuing the token.

Redis key:
    ``worker:active_jobs``  — integer string, no TTL (the worker refreshes it
    continuously; the token endpoint only reads it).

Stale-value safety:
    If the worker crashes without decrementing, the key may be too high,
    causing spurious rejections.  This is the SAFE failure mode — a candidate
    gets a 503 rather than a dead room.  The key is updated on every
    increment/decrement so it goes stale only when the worker process is dead
    (which means there is genuinely no capacity).

FAIL OPEN on Redis errors:
    The token endpoint's capacity check fails OPEN — if Redis is unavailable,
    the endpoint issues the token and lets the worker's in-process gate handle
    overload.  Redis unavailability does NOT block token issuance entirely.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("interview-worker")

# Redis key — stable across the lifetime of the worker process.
_ACTIVE_JOBS_KEY: str = "worker:active_jobs"
# Short TTL so a worker crash eventually clears stale state (next worker
# startup will re-publish the correct count immediately anyway).
_ACTIVE_JOBS_TTL_SECONDS: int = 60


async def publish_active_jobs(redis: Any, count: int) -> None:
    """Write the current active-job count to Redis.

    Called by the worker on every increment/decrement so the HTTP server can
    read it.  Best-effort — a Redis error must never propagate into the
    interview entrypoint or the admission gate.

    Args:
        redis: a ``redis.asyncio.Redis`` client (or any object with
               an async ``setex(key, seconds, value)`` method).
        count: the current value of the module-level ``_active_jobs`` counter.
    """
    try:
        await redis.setex(_ACTIVE_JOBS_KEY, _ACTIVE_JOBS_TTL_SECONDS, str(count))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker.capacity_publish_failed count=%d err=%s",
            count, type(exc).__name__,
        )


async def read_active_jobs(redis: Any) -> int | None:
    """Read the current active-job count published by the worker.

    Returns:
        The current active-job count (int), or ``None`` if the key is absent
        or Redis is unavailable (caller should fail open).

    Args:
        redis: a ``redis.asyncio.Redis`` client (or any object with
               an async ``get(key)`` method).
    """
    try:
        raw: str | None = await redis.get(_ACTIVE_JOBS_KEY)
        if raw is None:
            return None
        return int(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "rooms.capacity_read_failed err=%s — failing open",
            type(exc).__name__,
        )
        return None
