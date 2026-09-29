"""AR-2 — interview_core's asymmetric-verification config wiring.

interview_core only ever VERIFIES; its Settings class has no jwt_private_key
field at all. These tests pin the two things that would otherwise fail
silently: a malformed JWT_PUBLIC_KEYS/JWT_VERIFY_ALGORITHMS is caught at boot,
and a JWT_PRIVATE_KEY set on this service's environment by mistake is refused
loudly rather than quietly ignored by pydantic-settings' extra="ignore".

See shared/tests/test_jwt_asymmetric.py for the underlying parser tests and
shared/tests/test_forbid_private_signing_key.py for the guard tests; these pin
that interview_core's config validator actually calls both.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.config import Settings

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


def test_defaults_boot_clean() -> None:
    settings = _settings()
    assert settings.jwt_verify_algorithms == "HS256"
    assert settings.jwt_public_keys == "{}"


def test_jwt_private_key_in_the_environment_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The structural guarantee: this service has no field to hold a private
    key, so the only way to catch a stray JWT_PRIVATE_KEY is the explicit
    forbid_private_signing_key check, wired into this validator."""
    monkeypatch.setenv("JWT_PRIVATE_KEY", "should-never-be-here")
    with pytest.raises(ValidationError, match="JWT_PRIVATE_KEY"):
        _settings()


def test_no_jwt_private_key_field_exists_on_this_settings_class() -> None:
    """Not "blank" — ABSENT. A service that cannot even represent a private
    key cannot accidentally sign with one."""
    settings = _settings()
    assert not hasattr(settings, "jwt_private_key")


def test_bad_public_keys_json_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(jwt_public_keys="{not json")


def test_unsupported_verify_algorithm_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(jwt_verify_algorithms="HS256,ES256")


def test_empty_verify_algorithms_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(jwt_verify_algorithms="")


def test_valid_rs256_config_boots_clean() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from shared.auth.jwt import encode_key_material

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    settings = _settings(
        jwt_verify_algorithms="HS256,RS256",
        jwt_public_keys=json.dumps({"2026-09-28": encode_key_material(public_pem)}),
    )
    assert "RS256" in settings.jwt_verify_algorithms
