"""XS-04: production/staging must refuse to start on a plaintext DB link.

``validate_database_ssl`` lived in data_gateway alone, so this service — the one
that reads and writes scorecards, i.e. the assessment PII — could boot in
production against an unencrypted Postgres link and log nothing (DPDP §8,
CWE-319). The guard now comes from shared/security.py; the policy is tested
there, and what these tests pin is that feedback_billing's Settings actually
applies it, including the normalisation half.

Every value is passed explicitly. ``services/feedback_billing/.env`` sits next
to these tests in a developer checkout and pydantic-settings reads it, so a test
that relied on DATABASE_SSL being absent would pass locally on a file CI does
not have — the exact failure 128f763 fixed in data_gateway.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

_BASE_ENV: dict[str, object] = {
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
    "redis_url": "redis://localhost:6379/0",
    "jwt_secret": "a" * 48,
}


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE_ENV, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize("env", ["production", "staging"])
def test_hardened_env_without_database_ssl_refuses_to_start(env: str) -> None:
    """The message has to name DATABASE_SSL — a bare 'validation error' at boot
    on a rolling deploy is a paging incident, not a fix."""
    with pytest.raises(ValidationError) as exc:
        _settings(app_env=env, database_ssl="")

    assert "DATABASE_SSL" in str(exc.value)


@pytest.mark.parametrize("env", ["production", "staging"])
def test_hardened_env_with_database_ssl_starts(env: str) -> None:
    """Control: the guard must not be refusing everything."""
    assert _settings(app_env=env, database_ssl="require").database_ssl == "require"


def test_capitalised_app_env_cannot_buy_a_plaintext_link() -> None:
    """APP_ENV=Production is normalised before the gate reads it.

    Without this the guard is bypassed by a casing difference in a deploy
    config, which is the failure mode normalise_app_env exists for — and the one
    that makes every other production gate in the service optional too.
    """
    with pytest.raises(ValidationError) as exc:
        _settings(app_env="Production", database_ssl="")

    assert "DATABASE_SSL" in str(exc.value)


def test_whitespace_only_database_ssl_is_not_a_value() -> None:
    """DATABASE_SSL="   " is truthy in Python. Accepting it would pass the
    production gate and then hand whitespace to asyncpg as an SSL mode — a
    plaintext link AND a connection error from one config typo."""
    with pytest.raises(ValidationError):
        _settings(app_env="production", database_ssl="   ")


def test_loopback_exempt_is_accepted_and_stripped() -> None:
    """The sentinel is an operator's written acknowledgement that TLS is
    terminated upstream. It satisfies the gate and must NOT survive into
    settings — asyncpg has no such SSL mode and would reject the connection."""
    assert _settings(app_env="production", database_ssl="loopback-exempt").database_ssl == ""


@pytest.mark.parametrize("env", ["development", "test"])
def test_development_and_test_are_unaffected(env: str) -> None:
    """dev/compose/CI run against local Postgres with no TLS. A guard that broke
    them would be switched off within the week."""
    assert _settings(app_env=env, database_ssl="").database_ssl == ""
