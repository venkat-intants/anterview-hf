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

Usage — note there is no ``Depends()`` around it; ``rate_limit`` returns one
already, and wrapping it a second time fails at import with "a parameter-less
dependency must have a callable dependency":

    @router.post("/login", dependencies=[rate_limit("login", settings.rate_limit_login_per_minute)])
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Depends, HTTPException, Request, status
from prometheus_client import Counter

from app.dependencies import get_hr_company
from app.redis_client import get_redis
from app.utils.request_ip import extract_client_ip

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
            ip = extract_client_ip(request)
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


def rate_limit_company(bucket: str, per_minute: int) -> Callable[..., Awaitable[None]]:
    """Cap a route at *per_minute* requests per COMPANY rather than per client
    IP (MEDIUM-4, PH4-D3). ``rate_limit`` above keys on IP, which is the right
    boundary for an anonymous or per-account route — but several HR seats at
    one company hitting an on-demand analysis route each get their own IP and
    so, keyed that way, effectively their own budget. ``get_hr_company`` is
    already a dependency of the route this guards, so FastAPI resolves it
    once per request and both call sites share the cached result.

    Same fixed 60-second window and fail-open posture as ``rate_limit`` — see
    its docstring; a cache blip must not lock every HR seat out of the
    console.
    """

    async def _dep(ctx: tuple[uuid.UUID, uuid.UUID] = Depends(get_hr_company)) -> None:  # noqa: B008
        _uid, company_id = ctx
        try:
            redis = get_redis()
            key = f"rl:{bucket}:{company_id}"
            count: int = await redis.incr(key)
            if count == 1:
                await redis.expire(key, 60)
        except Exception as exc:  # noqa: BLE001 — Redis down / any error → fail open
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
                detail="Too many requests from your company. Please wait a minute and try again.",
            )

    return Depends(_dep)


def _token_fingerprint(raw: str) -> str:
    """A stable, non-reversible bucket key for a magic-link credential — never
    the raw token itself, so a Redis key never carries a live credential."""
    return hashlib.sha256(raw.encode()).hexdigest()


def rate_limit_task(bucket: str, per_token: int, per_ip: int) -> Callable[..., Awaitable[None]]:
    """Cap a public job-simulation/portfolio task route primarily by the
    candidate's OWN link — the ``X-Task-Token`` header, hashed — rather than
    by client IP (M3, security review PH4-D4).

    ``rate_limit`` keys everything from one route on one shared IP bucket,
    which is the wrong boundary here for two independent reasons this fixes
    together: (a) four routes (start/save/delete/submit) shared ONE bucket
    name with four different caps, so autosaves could exhaust the budget a
    submit needed; separate ``bucket`` values per call site fix that on their
    own; (b) a college computer lab — the primary market — puts many
    candidates, each working their OWN task, behind one NAT address, so an
    IP-keyed cap punishes every candidate in the room for one candidate's
    normal use. Keying on the token instead gives each candidate their own
    budget regardless of how many others share their address.

    A request with no token (or an unrecognised one, since the header is
    opaque here) still hits a LOOSER per-IP ceiling — a backstop against
    volumetric abuse, not the normal-use limit. Same fixed 60-second window
    and fail-open posture as ``rate_limit``; see its docstring.
    """

    async def _dep(request: Request) -> None:
        token = request.headers.get("X-Task-Token")
        ip = extract_client_ip(request)
        try:
            redis = get_redis()
            tok_count: int | None = None
            if token:
                tok_key = f"rl:{bucket}:tok:{_token_fingerprint(token)}"
                tok_count = await redis.incr(tok_key)
                if tok_count == 1:
                    await redis.expire(tok_key, 60)
            ip_key = f"rl:{bucket}:ip:{ip}"
            ip_count: int = await redis.incr(ip_key)
            if ip_count == 1:
                await redis.expire(ip_key, 60)
        except Exception as exc:  # noqa: BLE001 — Redis down / any error → fail open
            _rate_limit_skipped.labels(
                bucket=bucket, error_type=type(exc).__name__
            ).inc()
            log.warning("rate_limit.skipped", bucket=bucket, error_type=type(exc).__name__)
            return
        if tok_count is not None and tok_count > per_token:
            _rate_limit_exceeded.labels(bucket=bucket).inc()
            log.warning("rate_limit.exceeded", bucket=bucket, keyed="token")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please wait a minute and try again.",
            )
        if ip_count > per_ip:
            _rate_limit_exceeded.labels(bucket=bucket).inc()
            log.warning("rate_limit.exceeded", bucket=bucket, keyed="ip")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests from your network. Please wait a minute and try again.",
            )

    return Depends(_dep)
