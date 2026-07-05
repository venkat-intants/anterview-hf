"""Redis-backed fixed-window rate limiting (data_gateway).

Distributed (works across replicas) using the existing Redis client — no extra
dependency. Keyed by (bucket, real client IP via the trusted-proxy-aware
extractor). FAILS OPEN on any Redis error so a cache hiccup never locks users
out of login.

Usage:
    @router.post("/login", dependencies=[Depends(rate_limit("login", settings.rate_limit_login_per_minute))])
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from fastapi import Depends, HTTPException, Request, status

from app.redis_client import get_redis
from app.routers.consent import _extract_client_ip

log = structlog.get_logger(__name__)


def rate_limit(bucket: str, per_minute: int) -> Callable[..., Awaitable[None]]:
    """Dependency factory: cap a route at *per_minute* requests per client IP.

    Fixed 60-second window. Raises 429 once the cap is exceeded. Fails open
    (allows the request) when Redis is unavailable.
    """

    async def _dep(request: Request) -> None:
        try:
            ip = _extract_client_ip(request)
            redis = get_redis()
            key = f"rl:{bucket}:{ip}"
            count: int = await redis.incr(key)
            if count == 1:
                await redis.expire(key, 60)
        except Exception as exc:  # noqa: BLE001 — Redis down / any error → fail open
            log.warning("rate_limit.skipped", bucket=bucket, error_type=type(exc).__name__)
            return
        if count > per_minute:
            log.warning("rate_limit.exceeded", bucket=bucket)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please wait a minute and try again.",
            )

    return Depends(_dep)
