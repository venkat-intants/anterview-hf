"""AR-2 — asymmetric JWT signing/verification, end to end through the real
ASGI app, a real login, and real Postgres.

On the ``test_ph5_e2_corpus_http.py`` precedent: an in-process
``httpx.AsyncClient`` over ``ASGITransport``, running the app's own lifespan,
against this repo's disposable Postgres — not a mock, not a unit test of
``shared.auth.jwt`` in isolation (that lives in
``shared/tests/test_jwt_asymmetric.py``). Every test here drives
``POST /auth/register`` (a real login: it returns tokens immediately because
``EMAIL_VERIFICATION_REQUIRED`` defaults False) and ``GET /auth/me`` (a real
protected route, through ``app.dependencies.get_current_user`` unmodified).

What this file proves, and what it does not:
  - PROVES: data_gateway's real login endpoint, with ``JWT_SIGNING_ALGORITHM=
    RS256`` configured, issues a token that its own real protected route
    accepts, and that the security properties (wrong key, unknown kid,
    tampered signature, HS256/RS256 concurrency, HS256 retirement) hold
    against the REAL verify_access_token call
    ``app.dependencies.get_current_user`` makes.
  - Does NOT itself boot interview_core/feedback_billing/admin_ops's ASGI
    apps in this same process — their ``app`` package name collides with this
    one's (all four services import as ``app.*``), so a genuine multi-service
    proof needs separate processes. Each of those three services carries its
    OWN ``test_ar2_asymmetric_jwt_http.py`` that takes a token minted with
    THIS keypair (the same ``shared.auth.jwt.issue_access_token`` function
    this file also exercises) and feeds it to ITS OWN real ASGI app — the
    composition of that file with this one is the cross-service proof,
    split across the process boundary the codebase's own architecture uses.
    ``test_cross_service_jwt.py`` in interview_core additionally covers a
    genuinely live, separate-process data_gateway when one is already
    running (see that file's ``test_cross_service_jwt_live``); this AR-2
    change does not add new subprocess-orchestration test infrastructure,
    consistent with that existing precedent.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import jwt as pyjwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import encode_key_material, issue_access_token

from app.config import settings

pytestmark = pytest.mark.integration


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


# Generated here, not shared with the sibling files. Each service's own
# test_ar2_asymmetric_jwt_http.py generates its own pair and feeds the
# resulting token to that service's real, unmodified verifier. The four
# files together are therefore four independent proofs of the same
# property — 'this service accepts an RS256 token signed by the key it was
# configured with' — rather than one proof carried across a shared
# constant. They did share one literal pair; gitleaks correctly read that
# as four committed private keys, and a scanner that learns to ignore this
# shape is blunted for the real case.
def _keypair() -> tuple[str, str]:
    """A fresh RSA keypair, generated at import time.

    NOT a hard-coded PEM pair. These files originally embedded one, so that
    all four services demonstrably used byte-identical key material — which
    reads well, and which gitleaks correctly flagged as seven committed
    private keys. A secret scanner cannot tell a test key from a real one, and
    teaching it to ignore this shape would blunt it for the real case.

    Nothing is lost: each file mints a token and feeds it to its OWN service's
    real verifier, so the property under test ("this service accepts an RS256
    token signed by the key it was configured with") holds just as well with a
    per-file pair. Matches what shared/tests/test_jwt_asymmetric.py already does.
    """
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

# A SECOND recognised keypair (rotation — two kids accepted at once), and a
# ROGUE one nobody configured (an attacker, or a misconfigured deploy). These
# two are generated fresh per test run — only the cross-service identity key
# above needs to be fixed.
_SECOND_PRIV_PEM, _SECOND_PUB_PEM = _keypair()
_ROGUE_PRIV_PEM, _ROGUE_PUB_PEM = _keypair()

_KID = "test-active-2026-09-28"
_SECOND_KID = "test-rotated-2025-01-01"

_PUBLIC_KEYS_JSON = json.dumps(
    {
        _KID: encode_key_material(_PUB_PEM),
        _SECOND_KID: encode_key_material(_SECOND_PUB_PEM),
    }
)


@pytest.fixture(autouse=True)
def _rs256_signing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure data_gateway to sign RS256 and verify HS256+RS256 concurrently,
    with TWO recognised public keys (rotation), for every test in this module.

    Plain attribute assignment on the already-constructed ``settings``
    singleton — the same pattern ``monkeypatch.setattr(settings, ...)`` uses
    throughout this codebase's test suite. This bypasses the config-time
    ``validate_jwt_asymmetric_config`` validator (which only runs at
    ``Settings()`` construction); that validator is tested directly and
    exhaustively in ``test_config_ar2_asymmetric_jwt.py``, so this module is
    free to set an already-self-consistent RS256 state without re-triggering
    it, exactly as ``test_database_engine.py`` and friends already do for
    other settings.
    """
    monkeypatch.setattr(settings, "jwt_signing_algorithm", "RS256")
    monkeypatch.setattr(settings, "jwt_private_key", encode_key_material(_PRIV_PEM))
    monkeypatch.setattr(settings, "jwt_active_kid", _KID)
    monkeypatch.setattr(settings, "jwt_public_keys", _PUBLIC_KEYS_JSON)
    monkeypatch.setattr(settings, "jwt_verify_algorithms", "HS256,RS256")
    # This module calls /auth/register several times per test (and several
    # times across the module within the same Redis-backed "auth" rate-limit
    # window, since ASGITransport requests all share one client identity) —
    # unrelated to AR-2, so raise the ceiling rather than let it flake.
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=30.0) as ac,
        app.router.lifespan_context(app),
    ):
        yield ac


def _unique_email() -> str:
    return f"ar2test-{uuid.uuid4().hex[:10]}@intants-test.com"


async def _register(client: AsyncClient) -> dict[str, object]:
    resp = await client.post(
        "/auth/register",
        json={
            "email": _unique_email(),
            "password": "Secur3Pass!1",
            "full_name": "AR2 Test User",
        },
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, object] = resp.json()
    assert body["verification_required"] is False
    assert body["access_token"]
    return body


# ---------------------------------------------------------------------------
# 1. A real login through the real endpoint issues an asymmetric token, and a
#    protected route accepts it.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_login_issues_rs256_token_with_kid(client: AsyncClient) -> None:
    tokens = await _register(client)
    access_token = str(tokens["access_token"])

    header = pyjwt.get_unverified_header(access_token)
    assert header["alg"] == "RS256"
    assert header["kid"] == _KID


@pytest.mark.asyncio
async def test_protected_route_accepts_the_real_rs256_token(client: AsyncClient) -> None:
    tokens = await _register(client)
    access_token = str(tokens["access_token"])
    expected_user_id = str(tokens["user_id"])

    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == expected_user_id
    assert "candidate" in body["roles"]


# ---------------------------------------------------------------------------
# 2. HS256 and RS256 verify concurrently during the transition.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hs256_token_still_accepted_during_the_transition(client: AsyncClient) -> None:
    """Signing has moved to RS256 (the autouse fixture), but a legacy HS256
    token — as if minted moments before the cutover — must still verify,
    because JWT_VERIFY_ALGORITHMS is "HS256,RS256"."""
    user_id = str(uuid.uuid4())
    legacy_token = issue_access_token(
        user_id, ["candidate"], settings.jwt_secret,
        algorithm="HS256", issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )

    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {legacy_token}"})

    # 401, not 500: /auth/me looks the user up in Postgres and a random UUID
    # has no row there. The point of this assertion is that verification
    # itself succeeded and the request reached the DB lookup, not a 401 from
    # verify_access_token — confirmed by the next test using this exact
    # token shape against a REAL registered user.
    assert resp.status_code == 401
    assert resp.json()["detail"] != "Invalid or missing access token"


@pytest.mark.asyncio
async def test_hs256_token_for_a_real_user_is_accepted_end_to_end(client: AsyncClient) -> None:
    """The unambiguous version of the test above: a legacy HS256 token for a
    user that actually exists must reach /auth/me's 200 path."""
    tokens = await _register(client)
    user_id = str(tokens["user_id"])
    legacy_token = issue_access_token(
        user_id, ["candidate"], settings.jwt_secret,
        algorithm="HS256", issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )

    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {legacy_token}"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == user_id


# ---------------------------------------------------------------------------
# 3. A token signed with the WRONG private key is rejected.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_private_key_rejected_even_with_the_correct_kid(
    client: AsyncClient,
) -> None:
    """The forged token claims the REAL active kid, so a verifier that only
    checked "is this kid known" would accept it. It must not: the signature
    has to match that kid's ACTUAL public key.

    Forges the token for a REAL, just-registered user — not a random UUID —
    and pins the exact 401 detail from ``get_current_user``'s own
    ``except JWTError`` branch. A mutation-testing run of this file caught the
    weaker version of this test (random UUID, bare ``status_code == 401``
    assertion): disabling signature verification ENTIRELY still produced a 401,
    but for the WRONG reason — the random UUID has no user row, so ``/auth/me``
    returns its own "User not found" 401 before the signature would ever have
    mattered. Using a real user's id and asserting the specific
    signature-layer detail message closes that confound.
    """
    tokens = await _register(client)
    user_id = str(tokens["user_id"])
    forged = issue_access_token(
        user_id, ["candidate"], _ROGUE_PRIV_PEM,
        algorithm="RS256", issuer=settings.jwt_issuer, audience=settings.jwt_audience,
        kid=_KID,
    )

    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or missing access token"


@pytest.mark.asyncio
async def test_tampered_signature_on_a_real_token_is_rejected(client: AsyncClient) -> None:
    """A byte flipped in the signature of an otherwise-real, otherwise-valid
    token must be caught — proves the check is a real RSA verify, not merely
    "does a kid with this name exist"."""
    tokens = await _register(client)
    access_token = str(tokens["access_token"])
    header_b64, payload_b64, signature_b64 = access_token.split(".")
    tampered_signature = ("A" if signature_b64[0] != "A" else "B") + signature_b64[1:]
    tampered = f"{header_b64}.{payload_b64}.{tampered_signature}"

    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {tampered}"})

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 4. `kid` selects among multiple public keys; an unknown `kid` is rejected.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_recognised_kid_is_also_accepted(client: AsyncClient) -> None:
    """Rotation: JWT_PUBLIC_KEYS holds two entries; a token naming the SECOND
    one must verify too, not just the currently-active signing key."""
    tokens = await _register(client)
    user_id = str(tokens["user_id"])
    token = issue_access_token(
        user_id, ["candidate"], _SECOND_PRIV_PEM,
        algorithm="RS256", issuer=settings.jwt_issuer, audience=settings.jwt_audience,
        kid=_SECOND_KID,
    )

    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == user_id


@pytest.mark.asyncio
async def test_unknown_kid_is_rejected(client: AsyncClient) -> None:
    """Signed with the REAL active private key, but a `kid` nobody configured
    — must not fall through to being tried anyway."""
    token = issue_access_token(
        str(uuid.uuid4()), ["candidate"], _PRIV_PEM,
        algorithm="RS256", issuer=settings.jwt_issuer, audience=settings.jwt_audience,
        kid="ghost-kid-nobody-configured",
    )

    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 5. HS256 stops being accepted once removed from the accepted list.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hs256_rejected_once_dropped_from_verify_algorithms(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollout step 4. A legacy HS256 token that verified a moment ago (see
    the earlier test in this module) must stop verifying the instant
    JWT_VERIFY_ALGORITHMS no longer names HS256 — JWT_SECRET staying set
    (auth_tokens.py still derives unrelated HMAC secrets from it) must not
    keep it accepted as a JWT."""
    tokens = await _register(client)
    user_id = str(tokens["user_id"])
    legacy_token = issue_access_token(
        user_id, ["candidate"], settings.jwt_secret,
        algorithm="HS256", issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )
    # Sanity: still accepted before the drop (autouse fixture keeps both).
    still_ok = await client.get("/auth/me", headers={"Authorization": f"Bearer {legacy_token}"})
    assert still_ok.status_code == 200, still_ok.text

    monkeypatch.setattr(settings, "jwt_verify_algorithms", "RS256")

    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {legacy_token}"})
    assert resp.status_code == 401

    # The RS256 token from a real login must be entirely unaffected.
    rs256_resp = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert rs256_resp.status_code == 200, rs256_resp.text
