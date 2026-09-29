"""AR-2 — data_gateway's asymmetric-signing config validator.

data_gateway is the only issuer, so it is the only Settings class that can be
told to sign RS256 at all, and the only one whose config validator has to
prove SELF-consistency: a JWT_ACTIVE_KID this service cannot find in its own
JWT_PUBLIC_KEYS would let it mint tokens nobody — including itself — can
verify. See shared/tests/test_jwt_asymmetric.py for the underlying parser
tests this validator is built on; these pin that the validator actually calls
them, at boot, in every environment.
"""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError
from shared.auth.jwt import encode_key_material

from app.config import Settings

_BASE_ENV: dict[str, object] = {
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
    "redis_url": "redis://localhost:6379/0",
    "jwt_secret": "a" * 48,
    "exam_link_secret": "b" * 48,
    "interview_link_secret": "c" * 48,
    "consent_ip_salt": "d" * 48,
}


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE_ENV, **overrides})  # type: ignore[arg-type]


def _keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


_PRIV_PEM, _PUB_PEM = _keypair()
_PRIV_B64 = encode_key_material(_PRIV_PEM)
_PUB_B64 = encode_key_material(_PUB_PEM)
_KID = "2026-09-28"


def test_defaults_boot_clean() -> None:
    """No overrides at all: HS256 signing, empty public keys — today's exact
    behaviour, unaffected by any of this existing."""
    settings = _settings()
    assert settings.jwt_signing_algorithm == "HS256"
    assert settings.jwt_public_keys == "{}"


def test_rs256_signing_with_matching_public_key_boots_clean() -> None:
    settings = _settings(
        jwt_signing_algorithm="RS256",
        jwt_private_key=_PRIV_B64,
        jwt_active_kid=_KID,
        jwt_public_keys=json.dumps({_KID: _PUB_B64}),
        jwt_verify_algorithms="HS256,RS256",
    )
    assert settings.jwt_signing_algorithm == "RS256"


def test_rs256_signing_without_private_key_fails() -> None:
    with pytest.raises(ValidationError, match="JWT_PRIVATE_KEY"):
        _settings(
            jwt_signing_algorithm="RS256",
            jwt_active_kid=_KID,
            jwt_public_keys=json.dumps({_KID: _PUB_B64}),
        )


def test_rs256_signing_without_active_kid_fails() -> None:
    with pytest.raises(ValidationError, match="JWT_ACTIVE_KID"):
        _settings(
            jwt_signing_algorithm="RS256",
            jwt_private_key=_PRIV_B64,
            jwt_public_keys=json.dumps({_KID: _PUB_B64}),
        )


def test_rs256_signing_with_a_public_key_pasted_as_the_private_key_fails() -> None:
    """The operator-error guard, at the config layer this time: catches it at
    boot rather than at the first login attempt."""
    with pytest.raises(ValidationError, match="PRIVATE"):
        _settings(
            jwt_signing_algorithm="RS256",
            jwt_private_key=_PUB_B64,  # wrong half
            jwt_active_kid=_KID,
            jwt_public_keys=json.dumps({_KID: _PUB_B64}),
        )


def test_active_kid_must_be_present_in_public_keys() -> None:
    """Self-consistency: signing with a kid this service's OWN verifier does
    not recognise would make every token it mints unverifiable, including by
    itself."""
    with pytest.raises(ValidationError, match="JWT_ACTIVE_KID"):
        _settings(
            jwt_signing_algorithm="RS256",
            jwt_private_key=_PRIV_B64,
            jwt_active_kid="a-kid-not-in-public-keys",
            jwt_public_keys=json.dumps({_KID: _PUB_B64}),
        )


def test_unsupported_signing_algorithm_rejected() -> None:
    with pytest.raises(ValidationError, match="JWT_SIGNING_ALGORITHM"):
        _settings(jwt_signing_algorithm="ES256")


def test_bad_public_keys_json_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(jwt_public_keys="{not json")


def test_a_private_key_pasted_into_public_keys_rejected() -> None:
    with pytest.raises(ValidationError, match="PRIVATE"):
        _settings(jwt_public_keys=json.dumps({_KID: _PRIV_B64}))


def test_unsupported_verify_algorithm_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(jwt_verify_algorithms="HS256,ES256")


def test_empty_verify_algorithms_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(jwt_verify_algorithms="")
