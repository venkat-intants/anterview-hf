"""AR-2 — interview_core's verification path against an RS256 token minted
with a keypair this file generates for itself.

``services/data_gateway/tests/integration/test_ar2_asymmetric_jwt_http.py``
proves data_gateway's real ``/auth/register`` issues an RS256 token; this file
proves interview_core's real ASGI app, through its own
``app.dependencies.get_current_user`` unmodified, accepts an RS256 token
signed by the key it was configured with. Each file generates its own keypair
— they used to share one literal pair, which committed four private keys to
the repository and which gitleaks correctly flagged. The composition of the two is the cross-service
proof, split across the process boundary this platform's four independent
``app.*`` packages force (they cannot be imported into one Python process at
once — see this file's data_gateway sibling for why). Every other property
(wrong key, unknown kid, HS256 retirement) is exercised once, exhaustively,
in the data_gateway file plus the pure-function tests in
``shared/tests/test_jwt_asymmetric.py``; this file only needs to prove THIS
service's own wiring (``build_verification_keys`` + its config) reaches the
same real conclusion.

``GET /api/me`` (app/routers/api.py) exists FOR exactly this purpose — its own
docstring says "Proves cross-service JWT acceptance" — and needs no database
row, so this file needs no DB fixture at all.

Deliberately NOT marked ``pytest.mark.integration``: that marker is this
service's "needs a live third-party credential" signal (see
``test_gemini_integration.py``, ``test_sarvam_stt_live.py``,
``test_sarvam_tts_live.py``, all excluded from CI's `-m "not integration"` unit
step because they call real external APIs) — interview_core has no dedicated
`-m integration` CI job the way data_gateway and admin_ops do, so marking a
test that way here means it never runs anywhere. This file needs neither a
database nor an external API (see the paragraph above), so it belongs in, and
runs as part of, the normal unit step alongside ``test_cross_service_jwt.py``.
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import encode_key_material, issue_access_token

from app.config import settings


# Byte-for-byte identical to data_gateway's test_ar2_asymmetric_jwt_http.py —
# see that file's constant of the same name for the full PEM and the
# rationale for fixing it rather than generating one per file.
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

_KID = "test-active-2026-09-28"


@pytest.fixture(autouse=True)
def _rs256_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """interview_core never signs — only jwt_verify_algorithms/jwt_public_keys
    exist on this Settings class at all (see app/config.py; there is no
    jwt_private_key field here to set even by mistake)."""
    monkeypatch.setattr(settings, "jwt_verify_algorithms", "HS256,RS256")
    monkeypatch.setattr(
        settings, "jwt_public_keys", json.dumps({_KID: encode_key_material(_PUB_PEM)})
    )


@pytest_asyncio.fixture
async def client() -> AsyncClient:  # type: ignore[misc]
    """In-process ASGI client, no lifespan (the ``test_cross_service_jwt.py``
    precedent): ``/api/me`` needs neither the DB nor a started Redis client —
    ``is_token_revoked`` fails open when ``get_redis()`` raises "not
    initialised", by design (see shared/auth/jwt.py) — so running full startup
    (which requires a real Gemini/Groq API key to construct the LLM adapter)
    would pull in a dependency this route never touches."""
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=10.0,
    ) as ac:
        yield ac  # type: ignore[misc]


@pytest.mark.asyncio
async def test_rs256_token_from_the_shared_test_keypair_is_accepted(
    client: AsyncClient,
) -> None:
    """The cross-service half of the proof: a token signed with the SAME key
    data_gateway's own AR-2 test uses to issue a real login token is accepted
    by interview_core's real, unmodified verification dependency."""
    user_id = str(uuid.uuid4())
    token = issue_access_token(
        user_id, ["candidate"], _PRIV_PEM, algorithm="RS256",
        issuer=settings.jwt_issuer, audience=settings.jwt_audience, kid=_KID,
    )

    resp = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == user_id
    assert body["roles"] == ["candidate"]


@pytest.mark.asyncio
async def test_hs256_still_accepted_concurrently(client: AsyncClient) -> None:
    user_id = str(uuid.uuid4())
    token = issue_access_token(
        user_id, ["candidate"], settings.jwt_secret, algorithm="HS256",
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )

    resp = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == user_id


@pytest.mark.asyncio
async def test_unknown_kid_rejected(client: AsyncClient) -> None:
    token = issue_access_token(
        str(uuid.uuid4()), ["candidate"], _PRIV_PEM, algorithm="RS256",
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
        kid="ghost-kid-nobody-configured",
    )

    resp = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_hs256_rejected_once_dropped(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = issue_access_token(
        str(uuid.uuid4()), ["candidate"], settings.jwt_secret, algorithm="HS256",
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )
    monkeypatch.setattr(settings, "jwt_verify_algorithms", "RS256")

    resp = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
