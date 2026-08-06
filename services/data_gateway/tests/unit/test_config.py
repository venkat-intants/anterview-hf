"""Config guardrails — the ones that silently disable other guards when wrong.

There was no test_config.py at all, which is how ``_normalise_app_env`` came to
exist in one service out of four and ``trusted_proxy_count`` came to be an
unbounded int feeding rate-limit bucket arithmetic. Both failure modes are quiet:
nothing errors, the control just stops controlling.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

# Minimum viable env for a Settings() that only exercises validators.
_BASE_ENV: dict[str, str] = {
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
    "redis_url": "redis://localhost:6379/0",
    "jwt_secret": "a" * 48,
    "exam_link_secret": "b" * 48,
    "interview_link_secret": "c" * 48,
    "consent_ip_salt": "d" * 48,
}


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE_ENV, **overrides})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# app_env normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw", ["production", "Production", "PRODUCTION", "  Production  ", "pRoDuCtIoN"]
)
def test_app_env_normalises_to_lowercase(raw: str) -> None:
    """Every production gate in this codebase compares ``== "production"``.

    Without normalisation ``APP_ENV=Production`` bypasses all of them — including
    assert_strong_secrets, so a placeholder JWT secret would boot unchallenged.
    A one-character casing difference should not be a security boundary.
    """
    assert _settings(app_env=raw, auth_cookie_secure=True).app_env == "production"


def test_app_env_normalisation_reaches_the_production_gates() -> None:
    """Pins the consequence, not just the string.

    APP_ENV=PRODUCTION must actually ARM the production checks. Asserting only
    that the string is lowercased would still pass if the gates read a different
    attribute.
    """
    with pytest.raises(ValidationError) as exc:
        _settings(app_env="PRODUCTION", auth_cookie_secure=False)
    assert "AUTH_COOKIE_SECURE" in str(exc.value)


def test_development_env_is_untouched() -> None:
    """Local runs and the test suite must not trip production gates."""
    assert _settings(app_env="development").app_env == "development"


# ---------------------------------------------------------------------------
# trusted_proxy_count bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, 2, 3, 4])
def test_trusted_proxy_count_accepts_real_topologies(value: int) -> None:
    """0 (no proxy) through 4 (CDN -> WAF -> LB -> Caddy) are all deployable."""
    assert _settings(trusted_proxy_count=value).trusted_proxy_count == value


@pytest.mark.parametrize("value", [-1, 5, 99])
def test_trusted_proxy_count_rejects_out_of_range(value: int) -> None:
    """Out of range degrades SILENTLY at runtime, which is why it must fail here.

    Too high and _extract_client_ip takes the real_index < 0 branch for every
    request, so every client resolves to the socket peer and per-IP rate limiting
    on login/register/password-reset collapses into one global bucket. Negative
    would index from the wrong end of the hop list entirely.
    """
    with pytest.raises(ValidationError):
        _settings(trusted_proxy_count=value)


def test_trusted_proxy_count_defaults_to_zero() -> None:
    """Default must be the safe one: ignore X-Forwarded-For entirely.

    Any other default would trust a client-supplied header on a deployment that
    has no proxy in front of it.
    """
    assert _settings().trusted_proxy_count == 0


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("origin", ["*", "null", "ftp://evil.example", "evil.example"])
def test_cors_rejects_wildcard_and_non_http_origins(origin: str) -> None:
    with pytest.raises(ValidationError):
        _settings(cors_allowed_origins=origin)


def test_cors_accepts_a_normal_origin_list() -> None:
    value = "http://localhost:5173,https://app.intants.com"
    assert _settings(cors_allowed_origins=value).cors_allowed_origins == value
