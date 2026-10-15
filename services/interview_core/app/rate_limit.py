"""Per-user Redis rate limiting for the routes that cost money (interview_core).

Why this exists: ``POST /api/sessions`` and ``POST /api/rooms/{id}/token`` had no
limit at all. Each token request can DISPATCH A WORKER (LLM + Sarvam speech + an
avatar session), worker slots are scarce (``worker_max_concurrent_jobs``), and a
scripted loop from one account burned the platform's per-session budget and filled
the slots for everyone else.

Same mechanism as ``data_gateway/app/rate_limit.py`` -- a Redis fixed 60-second
window (``INCR`` + ``EXPIRE``) -- re-stated here rather than imported, because that
module imports data_gateway-only code (``app.dependencies.get_hr_company``) and a
service image does not carry another service's ``app`` package. Keyed by USER, not
client IP: these routes are authenticated, and an IP key would make every candidate
behind a college NAT share one bucket.

FAILS OPEN, consistently with data_gateway's limiter and with this service's own
worker-capacity check (``rooms.py`` step 4, ``worker_capacity.read_active_jobs``): a
Redis error skips the check rather than refusing a candidate mid-interview-setup. The
compensating control that does NOT depend on Redis is the single-active-session rule
in ``rooms.py`` (a database query), which bounds concurrent workers per user even
while the limiter is down.
"""

from __future__ import annotations

import structlog
from fastapi import HTTPException, status

from app.redis_client import get_redis

log = structlog.get_logger(__name__)


async def enforce_user_rate_limit(bucket: str, user_id: str, per_minute: int) -> None:
    """Raise 429 when *user_id* exceeded *per_minute* calls to *bucket* this minute.

    ``per_minute <= 0`` disables the limit. Any Redis failure allows the request.
    """
    if per_minute <= 0:
        return
    try:
        redis = get_redis()
        key = f"rl:{bucket}:{user_id}"
        count: int = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
    except Exception as exc:  # noqa: BLE001 -- Redis down / any error -> fail open
        log.warning("rate_limit.skipped", bucket=bucket, error_type=type(exc).__name__)
        return
    if count > per_minute:
        log.warning("rate_limit.exceeded", bucket=bucket, user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait a minute and try again.",
            headers={"Retry-After": "60"},
        )
