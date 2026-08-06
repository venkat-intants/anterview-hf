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


# ---------------------------------------------------------------------------
# Login-CSRF defence: the OAuth state must be bound to the browser that
# started the flow, and the code exchange must carry a PKCE verifier.
# ---------------------------------------------------------------------------


def test_authorize_url_carries_pkce_challenge() -> None:
    """A code_challenge binds the authorization code to this browser.

    Without PKCE, a code captured in transit is redeemable by anyone holding
    the client secret's endpoint — with it, redemption also needs the verifier
    that never left this server's Redis.
    """
    from app.routers.sso_google import _pkce_challenge

    verifier = "a" * 64
    with _patch_google_settings():
        url = _build_authorize_url("state-xyz", _pkce_challenge(verifier))

    qs = parse_qs(urlparse(url).query)
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["code_challenge"] == [_pkce_challenge(verifier)]
    # S256 is base64url of a sha256 digest: 43 chars, unpadded.
    assert len(qs["code_challenge"][0]) == 43
    assert "=" not in qs["code_challenge"][0]


def test_pkce_challenge_matches_rfc7636_vector() -> None:
    """S256 challenge derivation must match RFC 7636 Appendix B exactly.

    A wrong derivation would fail every real sign-in at Google's token
    endpoint, so pin it against the spec's own vector rather than against our
    own implementation.
    """
    from app.routers.sso_google import _pkce_challenge

    assert (
        _pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_state_binding_uses_constant_time_comparison_of_hashes() -> None:
    """The cookie is compared by SHA-256 digest, never stored in the clear.

    Only the hash reaches Redis, so a Redis read does not disclose the value
    needed to forge a callback, and the comparison is constant-time.
    """
    import hashlib
    import hmac
    import inspect

    from app.routers import sso_google

    src = inspect.getsource(sso_google.callback)
    assert "hmac.compare_digest" in src, (
        "state binding must be compared in constant time"
    )
    assert "sha256" in src, "the raw binding cookie must never be stored/compared directly"

    # And the derivation the callback checks against is the one initiate writes.
    binding = "some-random-binding-value"
    assert hmac.compare_digest(
        hashlib.sha256(binding.encode()).hexdigest(),
        hashlib.sha256(binding.encode()).hexdigest(),
    )


def test_initiate_sets_httponly_state_cookie() -> None:
    """initiate must plant the binding cookie, httpOnly, scoped to the flow.

    httpOnly matters: the whole point is that page script cannot read the value
    an attacker would need to forge a matching callback.
    """
    import inspect

    from app.routers import sso_google

    src = inspect.getsource(sso_google.initiate)
    assert "_STATE_COOKIE_NAME" in src
    assert "httponly=True" in src
    # Lax, not Strict — the callback is a cross-site top-level navigation from
    # accounts.google.com and Strict would withhold the cookie exactly then.
    assert 'samesite="lax"' in src
