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


def rate_limit_window(
    bucket: str, limit: int, window_seconds: int
) -> Callable[..., Awaitable[None]]:
    """Cap a route at *limit* requests per client IP over a LONGER window.

    ``rate_limit`` bounds a burst; this bounds a sustained rate, and a route
    that writes to object storage before it knows whether it will keep the
    object needs both. Six per minute is a reasonable burst for a person
    filling in a form and is also 8,640 uploads a day from one address.

    Applied ALONGSIDE ``rate_limit`` rather than instead of it: separate
    buckets, separate keys, and the tighter of the two answers first.

    Same fail-open posture and the same 429 as ``rate_limit`` — see the module
    docstring for why failing open is the right call here.
    """

    async def _dep(request: Request) -> None:
        try:
            ip = extract_client_ip(request)
            redis = get_redis()
            key = f"rl:{bucket}:{ip}"
            count: int = await redis.incr(key)
            if count == 1:
                await redis.expire(key, window_seconds)
        except Exception as exc:  # noqa: BLE001 — Redis down / any error → fail open
            _rate_limit_skipped.labels(
                bucket=bucket, error_type=type(exc).__name__
            ).inc()
            log.warning("rate_limit.skipped", bucket=bucket, error_type=type(exc).__name__)
            return
        if count > limit:
            _rate_limit_exceeded.labels(bucket=bucket).inc()
            log.warning("rate_limit.exceeded", bucket=bucket, window_seconds=window_seconds)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                # The SAME sentence the per-minute limiter gives. Which of the
                # two caps was hit is not something an anonymous caller needs
                # to be able to tell apart.
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


def rate_limit_link(
    bucket: str, header: str, per_token: int, per_ip: int
) -> Callable[..., Awaitable[None]]:
    """Cap a public magic-link route primarily by the candidate's OWN link —
    the opaque token in *header*, hashed — rather than by client IP (M3,
    security review PH4-D4).

    ``rate_limit`` keys everything from one route on one shared IP bucket,
    which is the wrong boundary for a magic-link route for two independent
    reasons this fixes together: (a) sibling routes sharing ONE bucket name
    with different caps let a cheap, frequent call exhaust the budget an
    important, rare one needed; separate ``bucket`` values per call site fix
    that on their own; (b) a college computer lab — CLAUDE.md's #1 target
    market — puts many candidates, each holding their OWN link, behind one NAT
    address, so an IP-keyed cap punishes every candidate in the room for one
    candidate's entirely normal use. Keying on the token instead gives each
    candidate their own budget regardless of how many others share their
    address.

    A request with no token (or an unrecognised one, since the header is
    opaque here) still hits a LOOSER per-IP ceiling — a backstop against
    volumetric abuse, not the normal-use limit. Size *per_ip* from the busiest
    REAL room you expect to serve, not as a tidy multiple of *per_token*: the
    token cap is what actually protects the resource, because getting past it
    needs as many valid, unexpired links as the flood has requests.

    Same fixed 60-second window and fail-open posture as ``rate_limit``; see
    its docstring. Starlette matches header names case-insensitively, so the
    casing passed for *header* is cosmetic.
    """

    async def _dep(request: Request) -> None:
        await enforce_link_limit(
            request, bucket=bucket, header=header, per_token=per_token, per_ip=per_ip
        )

    return Depends(_dep)


async def enforce_link_limit(
    request: Request, *, bucket: str, header: str, per_token: int, per_ip: int
) -> None:
    """``rate_limit_link``'s body, callable directly.

    Exposed because one caller — the exam integrity-event ingest — has to pick
    its bucket from the PARSED BODY (a violation signal and a clipboard event
    must not share a budget), which a route-level dependency cannot see.

    The IP budget is charged and enforced BEFORE the token budget, and the
    token key is not touched at all once the IP budget is spent (security
    review MEDIUM-1). The obvious ordering — token first — bounds how many
    requests are SERVED but not how many distinct Redis keys are CREATED,
    since every rejected request still minted a fresh
    ``rl:{bucket}:tok:{sha256}``. That matters more than it sounds: this Redis
    also carries the JWT revocation epoch, and rate limiting and the "log out
    all devices" kill switch fail open together (see the module docstring), so
    memory pressure here disables a platform-wide auth control.
    """
    token = request.headers.get(header)
    ip = extract_client_ip(request)
    try:
        redis = get_redis()
        ip_key = f"rl:{bucket}:ip:{ip}"
        ip_count: int = await redis.incr(ip_key)
        if ip_count == 1:
            await redis.expire(ip_key, 60)
        over_ip = ip_count > per_ip
        tok_count: int | None = None
        if token and not over_ip:
            tok_key = f"rl:{bucket}:tok:{_token_fingerprint(token)}"
            tok_count = await redis.incr(tok_key)
            if tok_count == 1:
                await redis.expire(tok_key, 60)
    except Exception as exc:  # noqa: BLE001 — Redis down / any error → fail open
        _rate_limit_skipped.labels(
            bucket=bucket, error_type=type(exc).__name__
        ).inc()
        log.warning("rate_limit.skipped", bucket=bucket, error_type=type(exc).__name__)
        return
    if over_ip:
        _rate_limit_exceeded.labels(bucket=bucket).inc()
        log.warning("rate_limit.exceeded", bucket=bucket, keyed="ip")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests from your network. Please wait a minute and try again.",
        )
    if tok_count is not None and tok_count > per_token:
        _rate_limit_exceeded.labels(bucket=bucket).inc()
        log.warning("rate_limit.exceeded", bucket=bucket, keyed="token")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait a minute and try again.",
        )


async def enforce_token_budget(
    request: Request, *, bucket: str, header: str, per_minute: int
) -> None:
    """Charge one request to a PER-TOKEN budget only — no IP dimension.

    For a caller that already has a separate per-IP guard in front of it and
    needs the token check somewhere it can react to the refusal. The exam
    integrity ingest uses this twice, because a refusal there has to be
    recorded on the attempt before the 429 goes out (security re-audit
    MEDIUM-1): a drop the HR timeline cannot see is the whole defect that
    review blocked on, and a route-level dependency raises before the handler
    can write anything.

    A request with no token is not charged and not refused — it has no budget
    to exceed. Every such request 404s at the magic-link context anyway, and
    the per-IP guard is what bounds it. Fails OPEN on any Redis error, like
    every other limiter in this module; see the module docstring.
    """
    token = request.headers.get(header)
    if not token:
        return
    try:
        redis = get_redis()
        key = f"rl:{bucket}:tok:{_token_fingerprint(token)}"
        count: int = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
    except Exception as exc:  # noqa: BLE001 — Redis down / any error → fail open
        _rate_limit_skipped.labels(bucket=bucket, error_type=type(exc).__name__).inc()
        log.warning("rate_limit.skipped", bucket=bucket, error_type=type(exc).__name__)
        return
    if count > per_minute:
        _rate_limit_exceeded.labels(bucket=bucket).inc()
        log.warning("rate_limit.exceeded", bucket=bucket, keyed="token")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait a minute and try again.",
        )


def rate_limit_task(bucket: str, per_token: int, per_ip: int) -> Callable[..., Awaitable[None]]:
    """The job-simulation/portfolio task flavour of ``rate_limit_link`` — its
    magic link travels in ``X-Task-Token``.

    Kept as a named wrapper rather than inlined at its eight call sites in
    ``routers/job_tasks.py`` so the header name is stated once; the behaviour
    is entirely ``rate_limit_link``'s, including the reasoning in its
    docstring for why a token beats an IP here.
    """
    return rate_limit_link(bucket, "X-Task-Token", per_token, per_ip)
