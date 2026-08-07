"""Config-time guards for interview_core.

XS-04: ``validate_database_ssl`` existed in ``data_gateway`` alone, so this
service — which reads sessions, resumes and transcripts — could boot in
production against a plaintext Postgres link and say nothing (CWE-319, DPDP §8).
The rule now lives in ``shared/security.py`` and is called from here; these
tests pin that the call is real rather than decorative.

Every value is passed explicitly rather than relying on the process
environment: a developer checkout has ``services/interview_core/.env`` sitting
next to it and pydantic-settings reads it, so a production-mode requirement can
be satisfied invisibly by a file CI does not have. That exact failure has
already happened once in this repo (commit 128f763).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

# Minimum viable env for a Settings() that only exercises validators.
_BASE_ENV: dict[str, object] = {
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
    "redis_url": "redis://localhost:6379/0",
    "jwt_secret": "a" * 48,
    "s3_endpoint": "http://localhost:9000",
    "s3_access_key_id": "b" * 20,
    "s3_secret_access_key": "c" * 40,
}


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE_ENV, **overrides})  # type: ignore[arg-type]


def test_database_ssl_is_required_in_production() -> None:
    """No TLS on the DB link means candidate PII in cleartext (DPDP §8)."""
    with pytest.raises(ValidationError) as exc:
        _settings(app_env="production", database_ssl="")
    assert "DATABASE_SSL" in str(exc.value)


def test_database_ssl_is_required_in_staging_too() -> None:
    """``ENFORCED_ENVS`` is the package's one answer to "which envs are hardened".

    Pinned separately because a guard that reads ``== "production"`` looks
    correct and silently leaves staging — which holds real candidate data on
    this platform — unprotected.
    """
    with pytest.raises(ValidationError) as exc:
        _settings(app_env="staging", database_ssl="")
    assert "DATABASE_SSL" in str(exc.value)


def test_capitalised_app_env_cannot_buy_a_plaintext_link() -> None:
    """``APP_ENV=Production`` must arm the gate, not walk past it."""
    with pytest.raises(ValidationError):
        _settings(app_env="Production", database_ssl="")


def test_loopback_exempt_is_accepted_and_stripped() -> None:
    """The acknowledgement token is for the validator, never for asyncpg.

    ``loopback-exempt`` is not an SSL mode; handing it to the driver would turn
    an operator's written exemption into a connection error at first checkout.
    """
    settings = _settings(app_env="production", database_ssl="loopback-exempt")
    assert settings.database_ssl == ""


def test_production_with_ssl_starts_and_keeps_the_value() -> None:
    """The value has to survive intact — ``database.py`` passes it to asyncpg."""
    assert _settings(app_env="production", database_ssl="require").database_ssl == "require"


def test_development_is_untouched() -> None:
    """dev-up.ps1 and the suite run against local Postgres with no TLS."""
    assert _settings(app_env="development", database_ssl="").database_ssl == ""
