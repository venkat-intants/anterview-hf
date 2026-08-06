"""JWT helpers — issue and verify access tokens; manage refresh token lifecycle.

Kept thin: one HS256 signing key, no rotation of signing keys in Sprint 1.

S3-005 additions:
  - issue_access_token now includes iss, aud, jti claims.
  - verify_access_token now requires iss and aud and validates them.
  - jti is auto-generated (uuid4.hex) per token for replay prevention.
  - iss/aud have safe defaults so existing callers without explicit args
    continue to work without signature changes.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import structlog
from jose import JWTError, jwt

log = structlog.get_logger(__name__)

# Access token TTL is 15 minutes regardless of JWT_EXPIRY_HOURS env setting.
# (The env setting is intentionally kept for legacy compat; Sprint 1 spec
# mandates short-lived access tokens.)
ACCESS_TOKEN_TTL_SECONDS: int = 900  # 15 minutes

# Default iss/aud values — must match JWT_ISSUER / JWT_AUDIENCE in both
# services' settings.  Callers that do not pass explicit values get these
# defaults so the function signature stays backward-compatible.
_DEFAULT_ISSUER: str = "intants-data-gateway"
_DEFAULT_AUDIENCE: str = "intants-services"

# TTL for internal service-to-service tokens. Far shorter than a user session
# because the threat model is different: a service token's `sub` is a service
# name, so it is NOT covered by the auth_epoch kill switch (logout_all only ever
# writes auth_epoch:<user-uuid>). Its lifetime IS its containment — a captured
# one is usable until it expires and there is no way to revoke it early.
SERVICE_TOKEN_TTL_SECONDS: int = 60


def issue_access_token(
    user_id: str,
    roles: list[str],
    secret: str,
    algorithm: str = "HS256",
    *,
    issuer: str = _DEFAULT_ISSUER,
    audience: str = _DEFAULT_AUDIENCE,
    extra_claims: dict[str, Any] | None = None,
    ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS,
) -> str:
    """Sign and return a JWT access token.

    Includes required S3-005 claims: iss, aud, jti.
    The jti (JWT ID) is a fresh uuid4.hex per call — used for replay prevention
    via a Redis blocklist in interview_core.

    extra_claims: optional additional claims (e.g. a ``session_id`` binding for a
        guest interview token). They are added via setdefault so they can NEVER
        override a standard claim (sub/roles/iss/aud/exp/jti) — defence against a
        caller accidentally forging identity through extra_claims.

    ttl_seconds: defaults to the 15-minute user-session TTL. Service-to-service
        callers should pass ``SERVICE_TOKEN_TTL_SECONDS``. This parameter exists
        because without it the only way to mint a short-lived token was to
        hand-roll the claims dict — which interview_core did, and which is how a
        second implementation of token minting came to exist.
    """
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": user_id,
        "roles": roles,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
        "iss": issuer,
        "aud": audience,
        "jti": uuid.uuid4().hex,
    }
    for key, value in (extra_claims or {}).items():
        claims.setdefault(key, value)
    result: str = jwt.encode(claims, secret, algorithm=algorithm)
    return result


def verify_access_token(
    token: str,
    secret: str,
    algorithm: str = "HS256",
    *,
    expected_issuer: str = _DEFAULT_ISSUER,
    expected_audience: str = _DEFAULT_AUDIENCE,
) -> dict[str, Any]:
    """Decode and verify a JWT access token.

    Returns the decoded payload dict.

    Raises:
        JWTError: if the token is invalid, expired, tampered, or is missing
                  required claims (iss, aud, jti, iat).

    S3-005: iss and aud are validated against expected_issuer / expected_audience.
    jti presence is required — absence raises JWTError.

    Security-audit follow-up (2026-08): require_iat is now enforced. The
    "log out all devices" kill switch (app/dependencies.py in every service)
    compares a token's ``iat`` against the user's revocation epoch in Redis —
    a token minted without ``iat`` skipped that comparison entirely (`iat is
    None` was treated as "can't compare, let it through" by some callers) and
    was therefore silently unrevocable. Rejecting it here, at decode time, is
    defence in depth on top of every verifier now also treating a missing
    ``iat`` as revoked.
    """
    # python-jose options dict: each "require_<claim>" key forces the claim to
    # be present; combining with audience/issuer args also validates values.
    # jose supports require_exp, require_iss, require_aud, require_jti etc.
    decode_options: dict[str, bool] = {
        "require_exp": True,
        "require_iss": True,
        "require_aud": True,
        "require_jti": True,
        "require_iat": True,
    }
    payload = dict(
        jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            audience=expected_audience,
            issuer=expected_issuer,
            options=decode_options,
        )
    )
    # Explicit defence-in-depth check: jose raises JWTError when require_jti=True
    # and jti is missing, but an empty string would pass the require check.
    # Guard against that edge case explicitly.
    if not payload.get("jti"):
        raise JWTError("jti claim is empty")
    return payload


# ---------------------------------------------------------------------------
# Revocation epoch — the "log out all devices" kill switch
# ---------------------------------------------------------------------------
#
# Redis key prefix for the per-user revocation epoch. Any access token whose
# ``iat`` predates this value is revoked, so logout_all / password reset / admin
# delete / DPDP erasure take effect immediately rather than waiting out the
# 15-minute access-token TTL.
#
# It lives HERE, not in shared/auth/local.py, for one reason: local.py imports
# bcrypt at module scope, and three of the four services do not ship bcrypt. So
# every verifier except data_gateway's re-declared the literal with a comment
# saying "kept in sync — do not change". Six copies held together by a comment
# is not synchronisation. jwt.py has no bcrypt in its import graph, so every
# service can import the real constant.
#
# local.py re-exports this name, so its existing importers are unaffected.
USER_TOKEN_EPOCH_PREFIX: str = "auth_epoch:"


class _RedisGet(Protocol):
    """The one Redis method the revocation check needs.

    A Protocol rather than ``redis.asyncio.Redis`` so this module imports no
    Redis client at all: each service passes its own, and a test passes a dict
    wrapper. Typing it structurally is what keeps this helper importable from any
    of the four service images regardless of which client they ship.
    """

    async def get(self, key: str) -> Any: ...


async def is_token_revoked(
    redis_factory: Callable[[], _RedisGet], user_id: str, iat: Any
) -> bool:
    """True if *iat* predates the user's revocation epoch.

    Takes a FACTORY, not a client. Each service's ``get_redis()`` raises
    ``RuntimeError("Redis not initialised")`` when the app has no lifespan — and
    every local copy of this check called ``get_redis()`` INSIDE its try block,
    so that error was part of what fail-open absorbed. Accepting an already-
    resolved client would move the call outside the protected region and turn a
    fail-open path into a 500. Pass the function itself: ``is_token_revoked(
    get_redis, ...)``, not ``is_token_revoked(get_redis(), ...)``.

    The single implementation of a check that existed in five places, one of
    which had drifted: ``feedback_billing``'s scorecard-list copy shipped without
    it, so "log out all devices", password reset, HR account deletion and DPDP
    erasure all failed to revoke access to scorecard history until 40df357.

    FAILS OPEN — any Redis error, or a missing/unparseable epoch, returns False.
    This is deliberate and unchanged from every copy it replaces. The real auth
    control is the signature and ``exp``, both verified locally with no Redis
    involved; the epoch only *accelerates* revocation. Failing closed would make
    cache availability equal platform availability, turning a bounded 15-minute
    revocation delay into a total outage. The trade-off worth knowing during an
    incident: this and the per-IP rate limiter share a Redis and therefore fail
    open together.

    Consolidating also makes that fail-open state ALERTABLE, which is the point:
    five copies logged five different event names, so no single alert could say
    "revocation is not currently being enforced". There is now one:
    ``auth.token_epoch.check_skipped``.

    A missing or non-integer ``iat`` counts as REVOKED. verify_access_token sets
    require_iat, so a token reaching here without one is already anomalous, and
    treating it as unrevocable was the exact hole the 2026-08 audit closed.
    """
    try:
        raw = await redis_factory().get(USER_TOKEN_EPOCH_PREFIX + user_id)
    except Exception as exc:  # noqa: BLE001 — fail open on any Redis/client error
        log.warning("auth.token_epoch.check_skipped", error_type=type(exc).__name__)
        return False

    if raw is None:
        return False
    try:
        epoch = int(raw)
    except (TypeError, ValueError):
        # A corrupt epoch value must not lock the user out; treat as unset.
        log.warning("auth.token_epoch.unparseable")
        return False

    if iat is None:
        return True
    try:
        # `<=`, not `<`. Both are whole-second Unix timestamps and logout_all
        # sets epoch = now(), so a token minted in the SAME second as the
        # revocation has iat == epoch. Under a strict `<` it survived its full
        # 15-minute TTL immediately after the user asked to be logged out
        # everywhere.
        return int(iat) <= epoch
    except (TypeError, ValueError):
        return True


def generate_refresh_token() -> str:
    """Return a cryptographically random opaque refresh token (URL-safe, 48 bytes)."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Return SHA-256 hex digest of the raw refresh token."""
    return hashlib.sha256(token.encode()).hexdigest()
