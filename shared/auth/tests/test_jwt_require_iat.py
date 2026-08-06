"""Security-audit follow-up (2026-08, finding 4): verify_access_token must
require the ``iat`` claim.

The revocation kill switch ("log out all devices") depends on comparing a
token's ``iat`` against the user's revocation epoch stored in Redis (see
``app/dependencies.py`` in every service). A token minted without ``iat`` was
silently unrevocable via that path. ``decode_options`` now sets
``require_iat: True`` so python-jose rejects such a token at decode time —
defence in depth on top of the per-service "missing iat == revoked" checks.

These tests hand-craft tokens with python-jose directly (bypassing
``issue_access_token``, which always sets ``iat``) to exercise the decode-time
gate in isolation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from jose import JWTError
from jose import jwt as jose_jwt

from shared.auth.jwt import issue_access_token, verify_access_token

_SECRET = "test-only-secret-not-real-0123456789"
_ISSUER = "intants-data-gateway"
_AUDIENCE = "intants-services"


def _claims_without_iat(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": str(uuid.uuid4()),
        "roles": ["candidate"],
        "exp": now + timedelta(minutes=15),
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "jti": uuid.uuid4().hex,
    }
    claims.update(overrides)
    return claims


def test_token_missing_iat_is_rejected() -> None:
    """A hand-crafted token with no ``iat`` claim must fail verification."""
    claims = _claims_without_iat()
    assert "iat" not in claims
    token = jose_jwt.encode(claims, _SECRET, algorithm="HS256")

    with pytest.raises(JWTError):
        verify_access_token(
            token,
            secret=_SECRET,
            expected_issuer=_ISSUER,
            expected_audience=_AUDIENCE,
        )


def test_token_with_iat_still_verifies() -> None:
    """A well-formed token (iat present) must continue to verify normally —
    this would fail if require_iat were reverted to also reject good tokens."""
    now = datetime.now(tz=UTC)
    claims = _claims_without_iat(iat=now)
    token = jose_jwt.encode(claims, _SECRET, algorithm="HS256")

    payload = verify_access_token(
        token,
        secret=_SECRET,
        expected_issuer=_ISSUER,
        expected_audience=_AUDIENCE,
    )
    assert payload["sub"] == claims["sub"]
    assert "iat" in payload


def test_issue_access_token_always_sets_iat() -> None:
    """The normal minting path always includes iat, so this fix does not
    break any legitimate caller — only hand-crafted / malformed tokens."""
    token = issue_access_token(
        user_id=str(uuid.uuid4()),
        roles=["candidate"],
        secret=_SECRET,
    )
    payload = verify_access_token(token, secret=_SECRET)
    assert "iat" in payload
