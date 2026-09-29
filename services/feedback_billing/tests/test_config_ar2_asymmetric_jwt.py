"""AR-2 — feedback_billing's asymmetric-verification config wiring.

feedback_billing only ever VERIFIES (app/auth.py's user tokens and
routers/score.py's "service" tokens); its Settings class has no
jwt_private_key field at all. See shared/tests/test_jwt_asymmetric.py and
shared/tests/test_forbid_private_signing_key.py for the underlying function
tests; these pin that feedback_billing's config validator actually calls both.
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


def test_defaults_boot_clean() -> None:
    settings = _settings()
    assert settings.jwt_verify_algorithms == "HS256"
    assert settings.jwt_public_keys == "{}"


def test_jwt_private_key_in_the_environment_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_PRIVATE_KEY", "should-never-be-here")
    with pytest.raises(ValidationError, match="JWT_PRIVATE_KEY"):
        _settings()


def test_no_jwt_private_key_field_exists_on_this_settings_class() -> None:
    assert not hasattr(_settings(), "jwt_private_key")


def test_bad_public_keys_json_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(jwt_public_keys="{not json")


def test_unsupported_verify_algorithm_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(jwt_verify_algorithms="HS256,ES256")


def test_empty_verify_algorithms_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(jwt_verify_algorithms="")
