"""AR-2 — admin_ops's verification path against an RS256 token minted with
a keypair this file generates for itself.

See ``services/data_gateway/tests/integration/test_ar2_asymmetric_jwt_http.py``
for the full rationale, and its sibling files for the same proof against
their own real ASGI apps.

Each file generates its OWN keypair rather than sharing one literal PEM pair.
They originally shared one, which read well but committed four private keys to
the repository and was correctly flagged by gitleaks — a secret scanner cannot
tell a test key from a real one, and teaching it this shape is safe would
blunt it for the real case. Nothing is lost: what each file proves is that
THIS service's real verifier accepts an RS256 token signed by the key it was
configured with, which a per-file pair demonstrates exactly as well.

``GET /admin/overview`` (``app.routers.analytics.overview``, through
``app.admin_auth.verify_admin_role`` -> ``_authenticated_subject`` unmodified)
is the target: real Postgres, real lifespan. It is a single aggregate query
over an empty database, so this file needs no seeded rows.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import encode_key_material, issue_access_token

from app.config import settings

pytestmark = pytest.mark.integration

# Byte-for-byte identical to data_gateway's test_ar2_asymmetric_jwt_http.py.
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
    """admin_ops never signs — only jwt_verify_algorithms/jwt_public_keys
    exist on this Settings class (no jwt_private_key field at all)."""
    monkeypatch.setattr(settings, "jwt_verify_algorithms", "HS256,RS256")
    monkeypatch.setattr(
        settings, "jwt_public_keys", json.dumps({_KID: encode_key_material(_PUB_PEM)})
    )


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=30.0) as ac,
        app.router.lifespan_context(app),
    ):
        yield ac


@pytest.mark.asyncio
async def test_rs256_token_from_the_shared_test_keypair_is_accepted(
    client: AsyncClient,
) -> None:
    """The cross-service half of the proof: a token signed with the SAME key
    data_gateway's own AR-2 test uses to issue a real login token is accepted
    by admin_ops's real, unmodified verify_admin_role dependency."""
    token = issue_access_token(
        str(uuid.uuid4()), ["admin"], _PRIV_PEM, algorithm="RS256",
        issuer=settings.jwt_issuer, audience=settings.jwt_audience, kid=_KID,
    )

    resp = await client.get("/admin/overview", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_non_admin_role_still_forbidden_with_an_rs256_token(
    client: AsyncClient,
) -> None:
    """RS256 changes how the signature is checked, not what the role gate
    decides — a candidate role must still 403."""
    token = issue_access_token(
        str(uuid.uuid4()), ["candidate"], _PRIV_PEM, algorithm="RS256",
        issuer=settings.jwt_issuer, audience=settings.jwt_audience, kid=_KID,
    )

    resp = await client.get("/admin/overview", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_hs256_still_accepted_concurrently(client: AsyncClient) -> None:
    token = issue_access_token(
        str(uuid.uuid4()), ["admin"], settings.jwt_secret, algorithm="HS256",
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )

    resp = await client.get("/admin/overview", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_unknown_kid_rejected(client: AsyncClient) -> None:
    token = issue_access_token(
        str(uuid.uuid4()), ["admin"], _PRIV_PEM, algorithm="RS256",
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
        kid="ghost-kid-nobody-configured",
    )

    resp = await client.get("/admin/overview", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_hs256_rejected_once_dropped(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = issue_access_token(
        str(uuid.uuid4()), ["admin"], settings.jwt_secret, algorithm="HS256",
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )
    monkeypatch.setattr(settings, "jwt_verify_algorithms", "RS256")

    resp = await client.get("/admin/overview", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
