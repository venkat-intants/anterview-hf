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
#
# Every value is passed explicitly rather than relying on the process
# environment. These tests originally passed locally and failed in CI, because a
# developer checkout has services/data_gateway/.env sitting next to it and
# pydantic-settings reads it — so the production-mode requirements below were
# being satisfied invisibly by a file CI does not have. Anything a test depends
# on has to be in the test.
_BASE_ENV: dict[str, object] = {
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
    "redis_url": "redis://localhost:6379/0",
    "jwt_secret": "a" * 48,
    "exam_link_secret": "b" * 48,
    "interview_link_secret": "c" * 48,
    "consent_ip_salt": "d" * 48,
}

# What production/staging additionally demand. Kept separate from _BASE_ENV so
# the development-mode tests still prove the defaults are permissive:
#   AUTH_COOKIE_SECURE=true  — no plain-HTTP session cookies
#   DATABASE_SSL             — no cleartext PII to Neon/Postgres
_PROD_ENV: dict[str, object] = {
    "auth_cookie_secure": True,
    "database_ssl": "require",
}


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE_ENV, **overrides})  # type: ignore[arg-type]


def _prod_settings(**overrides: object) -> Settings:
    """Settings in production mode with its prerequisites satisfied.

    Lets a test target one production gate without tripping the others first.
    """
    return Settings(**{**_BASE_ENV, **_PROD_ENV, **overrides})  # type: ignore[arg-type]


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
    assert _prod_settings(app_env=raw).app_env == "production"


def test_app_env_normalisation_reaches_the_production_gates() -> None:
    """Pins the consequence, not just the string.

    APP_ENV=PRODUCTION must actually ARM the production checks. Asserting only
    that the string is lowercased would still pass if the gates read a different
    attribute.

    Everything except the gate under test is satisfied, so the error can only be
    the one this asserts on.
    """
    with pytest.raises(ValidationError) as exc:
        _prod_settings(app_env="PRODUCTION", auth_cookie_secure=False)
    assert "AUTH_COOKIE_SECURE" in str(exc.value)


def test_database_ssl_is_required_in_production() -> None:
    """The other production gate, pinned for the same reason.

    Without SSL, PII travels in cleartext to Neon/Postgres. This test exists
    because a missing DATABASE_SSL is what actually broke the suite in CI while
    passing locally — it deserves to be asserted rather than stumbled into.
    """
    with pytest.raises(ValidationError) as exc:
        _prod_settings(app_env="production", database_ssl="")
    assert "DATABASE_SSL" in str(exc.value)


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


def test_cors_checks_every_origin_not_just_the_first() -> None:
    """The bad entry is in the middle, where a first-entry-only check misses it.

    data_gateway was the last service still carrying its own copy of this
    validator; it now delegates to shared/security.py. The delegation must not
    quietly narrow what gets inspected.
    """
    with pytest.raises(ValidationError):
        _settings(cors_allowed_origins="http://localhost:5173, *, https://app.intants.com")


# ---------------------------------------------------------------------------
# Candidate links must open an origin the API will talk to
# ---------------------------------------------------------------------------
#
# These three settings build the links HR sends a candidate — account
# activation, the exam paper, the interview invite. They are deliberately
# separate from CORS_ALLOWED_ORIGINS (see app_base_url's note: the magic-link
# bases may point at a different host one day), and that separation is exactly
# how they drifted: CORS was moved to :5174 when the dev server moved, these
# were left on the :5173 code default. CORS being right made the console work
# in a browser while every emailed link opened a dead port.
#
# Nothing anywhere reports that. The mint returns 201, the email sends, the
# audit row is written — the failure lands entirely on the candidate, who sees
# a browser error and has no way to tell anyone. So it is asserted here.

_LINK_BASES = ("app_base_url", "exam_link_base_url", "interview_link_base_url")


def _origin(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


@pytest.mark.parametrize("field", _LINK_BASES)
def test_a_candidate_link_opens_an_origin_cors_allows(field: str) -> None:
    """The candidate's browser loads {base}/exam and that page calls this API.
    A base whose origin is not in CORS is broken by construction — the page
    renders and every request from it is refused."""
    s = _settings(
        cors_allowed_origins="http://localhost:5174,https://app.intants.com",
        **{field: "http://localhost:5174/"},
    )
    allowed = {_origin(o.strip()) for o in s.cors_allowed_origins.split(",")}
    assert _origin(getattr(s, field)) in allowed


@pytest.mark.parametrize("field", _LINK_BASES)
def test_the_drift_this_closes_is_detectable(field: str) -> None:
    """The real 2026-09 configuration, asserted as broken.

    Without this the suite is happy with the exact combination that was
    shipping: CORS on 5174, links on 5173.
    """
    s = _settings(
        cors_allowed_origins="http://localhost:5174,http://127.0.0.1:5174",
        **{field: "http://localhost:5173"},
    )
    allowed = {_origin(o.strip()) for o in s.cors_allowed_origins.split(",")}
    assert _origin(getattr(s, field)) not in allowed
