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
(``shared.auth.jwt.is_token_revoked``, called from
``dependencies.get_current_user``) use the same Redis and therefore fail open
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
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import Depends, HTTPException, Request, status
from prometheus_client import Counter
from shared.auth.base import User

from app.dependencies import get_current_user, get_hr_company
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


def rate_limit_context(
    bucket: str, per_minute: int, context_dep: Callable[..., Any],
) -> Callable[..., Awaitable[None]]:
    """Cap a route at *per_minute* requests per COMPANY, keyed off an
    arbitrary tenant-context dependency rather than ``get_hr_company``
    specifically (PH5-E2). A route shared by more than one role —
    ``app/routers/hr_corpus.py``'s ``_corpus_ctx`` returns
    ``(actor_id, company_id, role)`` for both ``hr_manager`` and
    ``super_admin`` — cannot use ``rate_limit_company`` below, which
    hardcodes the hr_manager-only ``get_hr_company``. *context_dep* must
    return a tuple whose SECOND element is the company_id, the shape every
    tenant-context dependency in this service already returns.

    Same fixed 60-second window and fail-open posture as ``rate_limit`` — see
    its docstring; a cache blip must not lock every HR seat out of the
    console. FastAPI caches a dependency's result per request by callable
    identity, so a route that also takes *context_dep* as its own parameter
    resolves it once and both call sites share the result — the
    ``rate_limit_company`` precedent this generalises already relied on that.
    """

    async def _dep(ctx: tuple[Any, ...] = Depends(context_dep)) -> None:  # noqa: B008
        company_id = ctx[1]
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


def rate_limit_company(bucket: str, per_minute: int) -> Callable[..., Awaitable[None]]:
    """Cap a route at *per_minute* requests per COMPANY rather than per client
    IP (MEDIUM-4, PH4-D3) — the ``get_hr_company``-scoped case of
    ``rate_limit_context`` above."""
    return rate_limit_context(bucket, per_minute, get_hr_company)


def rate_limit_actor(bucket: str, per_minute: int) -> Callable[..., Awaitable[None]]:
    """Cap a route at *per_minute* requests per SIGNED-IN ACCOUNT.

    For the candidate mint routes, where the abuse being bounded is targeted
    rather than volumetric: somebody holding a candidate's credential rotating
    their assessment or interview link repeatedly to keep them out of their own
    round. Keyed on IP that is barely a control — the attacker gets a fresh
    budget from every address, while a college computer lab, this product's
    primary market, shares one between every candidate in the room.

    Same fixed 60-second window and fail-open posture as ``rate_limit``; see
    its docstring, and note that this and the JWT revocation-epoch check fail
    open together.
    """

    async def _dep(user: User = Depends(get_current_user)) -> None:  # noqa: B008
        try:
            redis = get_redis()
            key = f"rl:{bucket}:{user.user_id}"
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
            log.warning("rate_limit.exceeded", bucket=bucket, keyed="actor")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please wait a minute and try again.",
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
