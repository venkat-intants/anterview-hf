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
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt

# Access token TTL is 15 minutes regardless of JWT_EXPIRY_HOURS env setting.
# (The env setting is intentionally kept for legacy compat; Sprint 1 spec
# mandates short-lived access tokens.)
ACCESS_TOKEN_TTL_SECONDS: int = 900  # 15 minutes

# Default iss/aud values — must match JWT_ISSUER / JWT_AUDIENCE in both
# services' settings.  Callers that do not pass explicit values get these
# defaults so the function signature stays backward-compatible.
_DEFAULT_ISSUER: str = "intants-data-gateway"
_DEFAULT_AUDIENCE: str = "intants-services"


def issue_access_token(
    user_id: str,
    roles: list[str],
    secret: str,
    algorithm: str = "HS256",
    *,
    issuer: str = _DEFAULT_ISSUER,
    audience: str = _DEFAULT_AUDIENCE,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Sign and return a JWT access token.

    Includes required S3-005 claims: iss, aud, jti.
    The jti (JWT ID) is a fresh uuid4.hex per call — used for replay prevention
    via a Redis blocklist in interview_core.

    extra_claims: optional additional claims (e.g. a ``session_id`` binding for a
        guest interview token). They are added via setdefault so they can NEVER
        override a standard claim (sub/roles/iss/aud/exp/jti) — defence against a
        caller accidentally forging identity through extra_claims.
    """
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": user_id,
        "roles": roles,
        "iat": now,
        "exp": now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS),
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
                  required claims (iss, aud, jti).

    S3-005: iss and aud are validated against expected_issuer / expected_audience.
    jti presence is required — absence raises JWTError.
    """
    # python-jose options dict: each "require_<claim>" key forces the claim to
    # be present; combining with audience/issuer args also validates values.
    # jose supports require_exp, require_iss, require_aud, require_jti etc.
    decode_options: dict[str, bool] = {
        "require_exp": True,
        "require_iss": True,
        "require_aud": True,
        "require_jti": True,
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


def generate_refresh_token() -> str:
    """Return a cryptographically random opaque refresh token (URL-safe, 48 bytes)."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Return SHA-256 hex digest of the raw refresh token."""
    return hashlib.sha256(token.encode()).hexdigest()
