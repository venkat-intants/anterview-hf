"""Redis-backed fixed-window rate limiting (data_gateway).

Distributed (works across replicas) using the existing Redis client — no extra
dependency. Keyed by (bucket, real client IP via the trusted-proxy-aware
extractor).

FAIL-OPEN, deliberately and re-affirmed 2026-08-06
--------------------------------------------------
Any Redis error skips the check rather than rejecting the request. This is a
chosen trade-off, not an inherited one: a cache blip must not lock every user
out of login. Two things make it acceptable, and both should be re-checked if
either changes:

  1. It is bounded by the outage, and the compensating controls do not depend on
     Redis — bcrypt cost 12 on password verification, 256-bit opaque reset
     tokens, uniform 404s on enumeration probes.
  2. It is now OBSERVABLE. The failure mode that mattered was not "brute-force
     protection is off" but "brute-force protection is off and nothing says so".

Worth knowing when reading an incident: this and the JWT revocation-epoch check
(``dependencies._token_epoch``) use the same Redis and therefore fail open
TOGETHER. During an Upstash outage, login throttling and the "log out all
devices" kill switch are both inactive at once. Alert on
``rate_limit_check_skipped_total``.

Usage:
    @router.post("/login", dependencies=[Depends(rate_limit("login", settings.rate_limit_login_per_minute))])
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from fastapi import Depends, HTTPException, Request, status
from prometheus_client import Counter

from app.redis_client import get_redis
from app.routers.consent import _extract_client_ip

log = structlog.get_logger(__name__)

# The alert hook for the fail-open branch. A WARNING log line is not enough on
# its own: nobody greps for the absence of throttling.
_rate_limit_skipped = Counter(
    "rate_limit_check_skipped_total",
    "Rate-limit checks skipped because Redis was unreachable (fail-open). "
    "Sustained non-zero means login/register/password-reset are unthrottled.",
    ["bucket", "error_type"],
)

_rate_limit_exceeded = Counter(
    "rate_limit_exceeded_total",
    "Requests rejected with 429 by the rate limiter.",
    ["bucket"],
)


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
            # Behaviour unchanged (see the module docstring for why fail-open is
            # the right call); what is new is that the skipped state is now
            # countable rather than only greppable.
            _rate_limit_skipped.labels(
                bucket=bucket, error_type=type(exc).__name__
            ).inc()
            log.warning("rate_limit.skipped", bucket=bucket, error_type=type(exc).__name__)
            return
        if count > per_minute:
            _rate_limit_exceeded.labels(bucket=bucket).inc()
            log.warning("rate_limit.exceeded", bucket=bucket)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please wait a minute and try again.",
            )

    return Depends(_dep)
