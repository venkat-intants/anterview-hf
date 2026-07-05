"""Single-use auth tokens for password reset + email verification.

Same hash-only discipline as exam/interview magic links (see app.exam_link): the
RAW token lives only in the emailed URL; we persist HMAC-SHA256(raw, secret) in
``auth_tokens.token_hash``. A DB read can never recover a working token.

Each purpose ('password_reset', 'email_verify') uses its own secret. To avoid
adding a new REQUIRED env var (which would crash already-provisioned services on
import), the secret falls back to one DERIVED from ``jwt_secret`` with a
per-purpose label — so it works out of the box and is never ``jwt_secret``
verbatim, while still being overridable for independent rotation in production.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.config import settings

_TOKEN_BYTES = 32  # 256-bit opaque token


def _derive(label: str) -> str:
    """Namespaced secret derived from jwt_secret when no dedicated one is set."""
    return hmac.new(
        settings.jwt_secret.encode(), f"auth_token:{label}".encode(), hashlib.sha256
    ).hexdigest()


def secret_for(kind: str) -> str:
    """Return the effective signing secret for a token ``kind``."""
    if kind == "password_reset":
        return settings.password_reset_secret or _derive("password_reset")
    if kind == "email_verify":
        return settings.email_verify_secret or _derive("email_verify")
    raise ValueError(f"unknown auth-token kind: {kind!r}")


def ttl_hours_for(kind: str) -> int:
    if kind == "password_reset":
        return settings.password_reset_ttl_hours
    if kind == "email_verify":
        return settings.email_verify_ttl_hours
    raise ValueError(f"unknown auth-token kind: {kind!r}")


def mint_token() -> str:
    """Fresh URL-safe opaque token (raw — store only its hash, email this)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(raw_token: str, kind: str) -> str:
    """Keyed HMAC-SHA256 hex of the raw token for the given purpose."""
    return hmac.new(
        secret_for(kind).encode(), raw_token.encode(), hashlib.sha256
    ).hexdigest()
