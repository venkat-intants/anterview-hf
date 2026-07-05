"""Unit tests for Google OAuth URL builder logic — S5-003b.

Tests the pure ``_build_authorize_url`` helper in isolation; no HTTP calls,
no Redis, no DB.

Test matrix (1 test):
  1.  test_initiate_url_contains_correct_params
"""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app.routers.sso_google import _build_authorize_url

# ---------------------------------------------------------------------------
# Shared patched settings
# ---------------------------------------------------------------------------

_GOOGLE_SETTINGS = {
    "google_oauth_client_id": "test-google-client-id",
    "google_oauth_client_secret": "test-google-client-secret",
    "google_oauth_redirect_uri": "https://app.intants.com/auth/sso/google/callback",
    "auth_provider": "google",
    "jwt_secret": "test-secret-32-bytes-xxxxxxxxxxxx",
    "jwt_algorithm": "HS256",
    "jwt_issuer": "intants-data-gateway",
    "jwt_audience": "intants-services",
}


def _patch_google_settings(**overrides: str) -> object:
    """Return a patch context manager overriding sso_google.settings."""
    merged = {**_GOOGLE_SETTINGS, **overrides}

    class _PatchedSettings:
        pass

    for key, value in merged.items():
        setattr(_PatchedSettings, key, value)

    return patch("app.routers.sso_google.settings", _PatchedSettings)


# ---------------------------------------------------------------------------
# 1. test_initiate_url_contains_correct_params
# ---------------------------------------------------------------------------


def test_initiate_url_contains_correct_params() -> None:
    """_build_authorize_url must include all required Google OAuth2 params."""
    fake_state = "abc123_state_token"

    with _patch_google_settings():
        url = _build_authorize_url(fake_state)

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    assert parsed.path == "/o/oauth2/v2/auth"

    qs = parse_qs(parsed.query)

    assert qs["client_id"] == ["test-google-client-id"]
    assert qs["redirect_uri"] == [
        "https://app.intants.com/auth/sso/google/callback"
    ]
    assert qs["response_type"] == ["code"]
    # scope is a space-separated string — verify the required values are present
    scope_values = set(qs["scope"][0].split())
    assert {"openid", "email", "profile"}.issubset(scope_values)
    assert qs["state"] == [fake_state]
    assert qs["access_type"] == ["offline"]
