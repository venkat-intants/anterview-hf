"""AR-2 — asymmetric (RS256) JWT signing and verification.

``docs/ACCEPTED-RISKS.md`` AR-2: one shared HS256 secret both signs and
verifies for every service, so read access to any one of them mints a token
for any ``sub``/``roles``, ``service`` included. The fix is RS256: the private
key lives only where ``resolve_signing_key`` is ever called with a Settings
object that HAS one (data_gateway), everyone else verifies with
``VerificationKey``s built from ``JWT_PUBLIC_KEYS``.

These tests pin the properties that make that a real guarantee rather than a
naming convention:
  - a real RS256 token round-trips through issue -> verify
  - a token signed with the WRONG private key is rejected even when its `kid`
    correctly names the RIGHT one (kid selects a candidate to try, it does not
    replace the signature check)
  - a holder of the PUBLIC key alone cannot mint anything: issuance refuses it
    outright (the guard is ours, not the library's — see the test for why),
    let alone anything a verifier accepts
  - HS256 and RS256 verify concurrently from one candidate list, and HS256
    stops being accepted the moment it is dropped from that list
  - an unrecognised `kid` is rejected outright, never falling through to a
    kid-less legacy secret it was never signed with
  - the key-material parsers (`decode_private_key`, `parse_public_keys`,
    `parse_verify_algorithms`) fail closed on every malformed input they exist
    to catch, including the operator mistake of pasting the wrong half of a
    keypair into the wrong setting
"""

from __future__ import annotations

import base64
import json
import uuid
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from shared.auth.jwt import TokenError as JWTError
from shared.auth.jwt import (
    VerificationKey,
    build_verification_keys,
    decode_private_key,
    encode_key_material,
    issue_access_token,
    parse_public_keys,
    parse_verify_algorithms,
    resolve_signing_key,
    verify_access_token,
)

_ISSUER = "intants-data-gateway"
_AUDIENCE = "intants-services"


def _keypair() -> tuple[str, str]:
    """Fresh (private_pem, public_pem) RSA-2048 keypair."""
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


# A single keypair reused by most tests — generating RSA keys is slow enough
# (milliseconds, but pytest runs hundreds of these) that a per-test fresh
# keypair would visibly slow the suite for no test-isolation benefit; nothing
# here mutates a key.
_PRIV_PEM, _PUB_PEM = _keypair()
_PRIV_B64 = encode_key_material(_PRIV_PEM)
_PUB_B64 = encode_key_material(_PUB_PEM)
_KID = "2026-09-28"

_ROGUE_PRIV_PEM, _ROGUE_PUB_PEM = _keypair()


def _issue_rs256(*, kid: str = _KID, secret: str = _PRIV_PEM) -> str:
    return issue_access_token(
        str(uuid.uuid4()), ["candidate"], secret, algorithm="RS256",
        issuer=_ISSUER, audience=_AUDIENCE, kid=kid,
    )


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


def test_rs256_roundtrip() -> None:
    token = _issue_rs256()

    payload = verify_access_token(
        token,
        [VerificationKey(key=_PUB_PEM, algorithm="RS256", kid=_KID)],
        expected_issuer=_ISSUER,
        expected_audience=_AUDIENCE,
    )

    assert payload["roles"] == ["candidate"]
    assert payload["jti"]


def test_rs256_token_carries_the_kid_in_its_header() -> None:
    """resolve_signing_key's whole point: the header names the key, so a
    verifier holding several can select instead of guessing."""
    import jwt as pyjwt

    token = _issue_rs256()
    assert pyjwt.get_unverified_header(token)["kid"] == _KID
    assert pyjwt.get_unverified_header(token)["alg"] == "RS256"


# ---------------------------------------------------------------------------
# kid selects a candidate; it does not replace the signature check
# ---------------------------------------------------------------------------


def test_kid_selects_the_right_key_among_several() -> None:
    other_priv, other_pub = _keypair()
    other_kid = "2025-01-01"
    token = _issue_rs256()  # signed with _PRIV_PEM, kid=_KID

    payload = verify_access_token(
        token,
        [
            VerificationKey(key=other_pub, algorithm="RS256", kid=other_kid),
            VerificationKey(key=_PUB_PEM, algorithm="RS256", kid=_KID),
        ],
        expected_issuer=_ISSUER,
        expected_audience=_AUDIENCE,
    )
    assert payload["roles"] == ["candidate"]


def test_unknown_kid_is_rejected_outright() -> None:
    """No fallback to a kid-less secret, and no trying every candidate anyway
    — an unrecognised `kid` must fail before any signature is even checked."""
    token = _issue_rs256(kid="ghost-kid-nobody-configured")

    with pytest.raises(JWTError):
        verify_access_token(
            token,
            [VerificationKey(key=_PUB_PEM, algorithm="RS256", kid=_KID)],
            expected_issuer=_ISSUER,
            expected_audience=_AUDIENCE,
        )


def test_a_token_with_no_kid_never_matches_a_keyed_candidate() -> None:
    """The mirror case: a legacy HS256 token (no `kid`) must not be tried
    against an RS256 VerificationKey just because one happens to be configured."""
    legacy_secret = "test-only-legacy-secret-0123456789abcdef"
    token = issue_access_token(
        str(uuid.uuid4()), ["candidate"], legacy_secret, algorithm="HS256",
        issuer=_ISSUER, audience=_AUDIENCE,
    )

    with pytest.raises(JWTError):
        verify_access_token(
            token,
            [VerificationKey(key=_PUB_PEM, algorithm="RS256", kid=_KID)],
            expected_issuer=_ISSUER,
            expected_audience=_AUDIENCE,
        )


def test_wrong_private_key_rejected_even_with_the_correct_kid() -> None:
    """The security-critical case: an attacker (or a misconfigured deploy) who
    signs with the WRONG private key but correctly names the RIGHT `kid` must
    still be rejected. `kid` selects which key to TRY; it is not a substitute
    for the signature actually matching."""
    forged = issue_access_token(
        str(uuid.uuid4()), ["service"], _ROGUE_PRIV_PEM, algorithm="RS256",
        issuer=_ISSUER, audience=_AUDIENCE, kid=_KID,  # claims to be the real kid
    )

    with pytest.raises(JWTError):
        verify_access_token(
            forged,
            [VerificationKey(key=_PUB_PEM, algorithm="RS256", kid=_KID)],
            expected_issuer=_ISSUER,
            expected_audience=_AUDIENCE,
        )


# ---------------------------------------------------------------------------
# A holder of the public key alone cannot mint anything
# ---------------------------------------------------------------------------


def test_public_key_cannot_be_used_to_sign_at_all() -> None:
    """The structural guarantee AR-2 exists for: a service handed only
    JWT_PUBLIC_KEYS has no private exponent, so it cannot mint a token. This fails at
    ISSUANCE, before there is even a token to verify.

    THE GUARANTEE MOVED, AND THAT IS THE POINT. Under jose this was the library's
    refusal — `jwk.construct` raised a `JOSEError` — and this test asserted that.
    PyJWT does not refuse: `RSAAlgorithm.prepare_key` happily returns an
    `RSAPublicKey` and `sign` then raises `AttributeError: 'RSAPublicKey' object has
    no attribute 'sign'`, measured, and NOT a `PyJWTError`. Borrowed from the
    library, the guarantee would have degraded from a clean refusal to a 500 on the
    one path AR-2 is written about.

    So `issue_access_token` now checks for the `PRIVATE KEY` marker itself. The
    assertion is on the GUARANTEE — issuance refuses, with a message naming the
    cause — not on which library happens to be installed.
    """
    with pytest.raises(ValueError) as exc:
        issue_access_token(
            str(uuid.uuid4()), ["service"], _PUB_PEM, algorithm="RS256",
            issuer=_ISSUER, audience=_AUDIENCE, kid=_KID,
        )

    assert "PUBLIC key" in str(exc.value)
    assert "AR-2" in str(exc.value), (
        "the refusal must point at the decision it enforces, or the next person "
        "reads it as a key-format complaint and goes looking for a better PEM"
    )


def test_the_library_would_not_have_caught_that_on_its_own() -> None:
    """Why the guard is in our code and not left to PyJWT.

    Handed a public key for RS256, PyJWT raises `AttributeError` — not a
    `PyJWTError` — so without the guard above an operator misconfiguration would
    surface as a 500 rather than a refusal. This asserts the guard is what stops it:
    nothing uncatchable escapes `issue_access_token`.
    """
    with pytest.raises(Exception) as exc:  # noqa: PT011 — the type IS the assertion
        issue_access_token(
            str(uuid.uuid4()), ["service"], _PUB_PEM, algorithm="RS256",
            issuer=_ISSUER, audience=_AUDIENCE, kid=_KID,
        )

    assert not isinstance(exc.value, AttributeError), (
        "an AttributeError escaped issuance — the marker guard in "
        "issue_access_token has gone, and a public key in JWT_PRIVATE_KEY is a 500 "
        "again instead of a refusal"
    )


def test_the_public_key_cannot_be_used_as_an_hmac_secret_to_forge_a_token() -> None:
    """ALGORITHM CONFUSION — the most famous way a JWT implementation breaks.

    The attack: take the RSA PUBLIC key, which is not a secret and which every
    verifier already holds, and use its raw bytes as the HMAC secret for an
    HS256 token. A verifier that picks its algorithm from the TOKEN'S OWN
    HEADER will then verify it happily, and the public key has become a
    signing key.

    Forged with ``hmac.new`` rather than the library's encoder deliberately: it
    refuses this at encode time, so going through the library would be testing
    the library's politeness rather than our verifier. A real attacker does not
    call our encoder.

    It is rejected because every verification candidate carries its OWN
    algorithm, taken from CONFIGURATION, and the header's ``alg`` only ever
    selects a ``kid`` — it never decides what the signature is checked with.

    Added after a security review ran this attack by hand, found it correctly
    rejected, and pointed out the property was pinned by no named test.
    """
    import hashlib
    import hmac

    def _b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": _KID}).encode())
    payload = _b64(
        json.dumps(
            {
                "sub": str(uuid.uuid4()),
                "roles": ["platform_owner"],  # the forger asks for everything
                "iss": _ISSUER,
                "aud": _AUDIENCE,
                "exp": 4_102_444_800,  # year 2100, so expiry is never the reason
            }
        ).encode()
    )
    signing_input = header + b"." + payload
    forged = (
        signing_input
        + b"."
        + _b64(hmac.new(_PUB_PEM.encode(), signing_input, hashlib.sha256).digest())
    )

    with pytest.raises(JWTError):
        verify_access_token(
            forged.decode(),
            [VerificationKey(_PUB_PEM, "RS256", _KID)],
            expected_issuer=_ISSUER,
            expected_audience=_AUDIENCE,
        )


# ---------------------------------------------------------------------------
# HS256 + RS256 concurrently, and dropping HS256
# ---------------------------------------------------------------------------


def test_hs256_and_rs256_verify_concurrently_from_one_candidate_list() -> None:
    legacy_secret = "test-only-legacy-secret-0123456789abcdef"
    hs256_token = issue_access_token(
        str(uuid.uuid4()), ["candidate"], legacy_secret, algorithm="HS256",
        issuer=_ISSUER, audience=_AUDIENCE,
    )
    rs256_token = _issue_rs256()

    candidates: list[str | VerificationKey] = [
        legacy_secret,
        VerificationKey(key=_PUB_PEM, algorithm="RS256", kid=_KID),
    ]

    assert verify_access_token(
        hs256_token, candidates, expected_issuer=_ISSUER, expected_audience=_AUDIENCE,
    )["roles"] == ["candidate"]
    assert verify_access_token(
        rs256_token, candidates, expected_issuer=_ISSUER, expected_audience=_AUDIENCE,
    )["roles"] == ["candidate"]


def test_hs256_token_rejected_once_hs256_dropped_from_the_list() -> None:
    """Rollout step 4: removing the bare secret from the candidate list is what
    actually ends HS256 acceptance — the RS256 VerificationKey staying present
    must not accidentally keep verifying a kid-less HS256 token."""
    legacy_secret = "test-only-legacy-secret-0123456789abcdef"
    hs256_token = issue_access_token(
        str(uuid.uuid4()), ["candidate"], legacy_secret, algorithm="HS256",
        issuer=_ISSUER, audience=_AUDIENCE,
    )

    with pytest.raises(JWTError):
        verify_access_token(
            hs256_token,
            [VerificationKey(key=_PUB_PEM, algorithm="RS256", kid=_KID)],
            expected_issuer=_ISSUER,
            expected_audience=_AUDIENCE,
        )


# ---------------------------------------------------------------------------
# build_verification_keys / resolve_signing_key — the Settings-object wiring
# ---------------------------------------------------------------------------


def test_build_verification_keys_defaults_to_hs256_secret_only() -> None:
    """A Settings object with none of the new attributes (getattr defaults)
    must reproduce today's exact behaviour: one HS256 secret, no RS256."""
    settings = SimpleNamespace(jwt_secret="test-only-single-secret-0123456789abcdef")
    keys = build_verification_keys(settings)
    assert keys == ["test-only-single-secret-0123456789abcdef"]


def test_build_verification_keys_both_algorithms() -> None:
    settings = SimpleNamespace(
        jwt_secret="test-only-symmetric-secret-0123456789abcdef",
        jwt_verify_algorithms="HS256,RS256",
        jwt_public_keys=json.dumps({_KID: _PUB_B64}),
    )
    keys = build_verification_keys(settings)
    assert "test-only-symmetric-secret-0123456789abcdef" in keys
    assert VerificationKey(key=_PUB_PEM, algorithm="RS256", kid=_KID) in keys


def test_build_verification_keys_rs256_only_excludes_the_hs256_secret() -> None:
    settings = SimpleNamespace(
        jwt_secret="test-only-symmetric-secret-0123456789abcdef",
        jwt_verify_algorithms="RS256",
        jwt_public_keys=json.dumps({_KID: _PUB_B64}),
    )
    keys = build_verification_keys(settings)
    assert "test-only-symmetric-secret-0123456789abcdef" not in keys
    assert keys == [VerificationKey(key=_PUB_PEM, algorithm="RS256", kid=_KID)]


def test_resolve_signing_key_defaults_to_hs256() -> None:
    settings = SimpleNamespace(jwt_secret="test-only-default-secret-0123456789abcdef")
    algorithm, key, kid = resolve_signing_key(settings)
    assert (algorithm, key, kid) == ("HS256", "test-only-default-secret-0123456789abcdef", None)


def test_resolve_signing_key_rs256() -> None:
    settings = SimpleNamespace(
        jwt_signing_algorithm="RS256", jwt_private_key=_PRIV_B64, jwt_active_kid=_KID,
    )
    algorithm, key, kid = resolve_signing_key(settings)
    assert algorithm == "RS256"
    assert key == _PRIV_PEM
    assert kid == _KID


@pytest.mark.parametrize("missing", ["jwt_private_key", "jwt_active_kid"])
def test_resolve_signing_key_rs256_incomplete_raises(missing: str) -> None:
    values = {
        "jwt_signing_algorithm": "RS256",
        "jwt_private_key": _PRIV_B64,
        "jwt_active_kid": _KID,
    }
    values[missing] = ""
    with pytest.raises(RuntimeError):
        resolve_signing_key(SimpleNamespace(**values))


# ---------------------------------------------------------------------------
# Key-material parsing — fail closed on every malformed shape
# ---------------------------------------------------------------------------


def test_decode_private_key_roundtrip() -> None:
    assert decode_private_key(_PRIV_B64) == _PRIV_PEM


def test_decode_private_key_rejects_a_public_key() -> None:
    """The operator-error guard: pasting the PUBLIC half into JWT_PRIVATE_KEY
    must fail loudly, not silently produce a service that cannot sign."""
    with pytest.raises(ValueError, match="PRIVATE"):
        decode_private_key(_PUB_B64)


def test_decode_private_key_rejects_bad_base64() -> None:
    with pytest.raises(ValueError):
        decode_private_key("not-valid-base64!!!")


def test_decode_private_key_rejects_base64_of_garbage() -> None:
    garbage_b64 = base64.b64encode(b"this is not a PEM key at all").decode()
    with pytest.raises(ValueError):
        decode_private_key(garbage_b64)


def test_parse_public_keys_roundtrip() -> None:
    parsed = parse_public_keys(json.dumps({_KID: _PUB_B64}))
    assert parsed == {_KID: _PUB_PEM}


def test_parse_public_keys_empty_default() -> None:
    assert parse_public_keys("{}") == {}
    assert parse_public_keys("") == {}


def test_parse_public_keys_rejects_a_private_key() -> None:
    """The mirror of decode_private_key's guard: a private key pasted into
    JWT_PUBLIC_KEYS would otherwise verify fine (a private key IS a valid RSA
    key) and silently defeat the entire point of AR-2 without ever raising."""
    with pytest.raises(ValueError, match="PRIVATE"):
        parse_public_keys(json.dumps({_KID: _PRIV_B64}))


def test_parse_public_keys_rejects_invalid_json() -> None:
    with pytest.raises(ValueError):
        parse_public_keys("{not json")


def test_parse_public_keys_rejects_a_json_array() -> None:
    with pytest.raises(ValueError):
        parse_public_keys(json.dumps([_PUB_B64]))


def test_parse_public_keys_rejects_non_string_value() -> None:
    with pytest.raises(ValueError):
        parse_public_keys(json.dumps({_KID: 12345}))


def test_parse_public_keys_rejects_empty_kid() -> None:
    with pytest.raises(ValueError):
        parse_public_keys(json.dumps({"": _PUB_B64}))


def test_parse_verify_algorithms_valid() -> None:
    assert parse_verify_algorithms("HS256") == frozenset({"HS256"})
    assert parse_verify_algorithms("HS256,RS256") == frozenset({"HS256", "RS256"})
    assert parse_verify_algorithms(" rs256 , hs256 ") == frozenset({"HS256", "RS256"})


def test_parse_verify_algorithms_rejects_empty() -> None:
    with pytest.raises(ValueError):
        parse_verify_algorithms("")
    with pytest.raises(ValueError):
        parse_verify_algorithms("   ,  ")


def test_parse_verify_algorithms_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="ES256"):
        parse_verify_algorithms("HS256,ES256")


def test_encode_key_material_is_the_inverse_of_decode() -> None:
    assert encode_key_material(_PRIV_PEM) == _PRIV_B64
    assert decode_private_key(encode_key_material(_PRIV_PEM)) == _PRIV_PEM
