"""AR-2 — feedback_billing's verification path against an RS256 token minted
with a keypair this file generates for itself.

See ``services/data_gateway/tests/integration/test_ar2_asymmetric_jwt_http.py``
for the full rationale (this file's ``_PRIV_PEM``/``_PUB_PEM`` are generated per file) and
``services/interview_core/tests/integration/test_ar2_asymmetric_jwt_http.py``
for the sibling that proves the same thing for interview_core.

``GET /api/scorecards`` (``app.routers.scorecard_list``, through
``app.auth.require_jwt`` unmodified) is the target, on the
``test_scorecard_list_endpoint.py`` precedent exactly: ``TestClient`` + a
mocked ``get_db_session`` override, no live Postgres. feedback_billing has no
dedicated `-m integration` CI job the way data_gateway and admin_ops do (grep
confirms no other file in this service's suite carries that marker), so a
real-Postgres version of this file — deliberate in an earlier draft — would
never have run in CI at all; this file is therefore NOT marked
``pytest.mark.integration`` and runs in the normal unit step alongside its
model.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from shared.auth.jwt import encode_key_material, issue_access_token
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.main import app


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
    """feedback_billing never signs — only jwt_verify_algorithms/jwt_public_keys
    exist on this Settings class (no jwt_private_key field at all)."""
    monkeypatch.setattr(settings, "jwt_verify_algorithms", "HS256,RS256")
    monkeypatch.setattr(
        settings, "jwt_public_keys", json.dumps({_KID: encode_key_material(_PUB_PEM)})
    )


def _empty_scorecards_db() -> Any:
    """Dependency override for get_db_session: an empty scorecard list —
    exactly test_scorecard_list_endpoint.py's ``_patch_db(count=0, rows=[])``,
    duplicated rather than imported so this file has no import-order coupling
    to that one."""
    from unittest.mock import MagicMock

    def _empty_result() -> MagicMock:
        # Matches app/routers/scorecard_list.py's two queries: the count query
        # reads ``count_row["cnt"]``, the data query iterates ``.mappings().all()``.
        # One mock answers both — .first() and .all() are independent attributes
        # of the same MagicMock, so which query hits it first does not matter.
        mappings_mock = MagicMock()
        mappings_mock.first.return_value = {"cnt": 0}
        mappings_mock.all.return_value = []
        result = MagicMock()
        result.mappings.return_value = mappings_mock
        return result

    async def _session_gen() -> Any:
        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.execute = AsyncMock(return_value=_empty_result())
        yield mock_db

    return _session_gen


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_db_session] = _empty_scorecards_db()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_rs256_token_from_the_shared_test_keypair_is_accepted(client: TestClient) -> None:
    """The cross-service half of the proof: a token signed with the SAME key
    data_gateway's own AR-2 test uses to issue a real login token is accepted
    by feedback_billing's real, unmodified require_jwt dependency."""
    user_id = str(uuid.uuid4())
    token = issue_access_token(
        user_id, ["candidate"], _PRIV_PEM, algorithm="RS256",
        issuer=settings.jwt_issuer, audience=settings.jwt_audience, kid=_KID,
    )

    resp = client.get("/api/scorecards", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []


def test_hs256_still_accepted_concurrently(client: TestClient) -> None:
    user_id = str(uuid.uuid4())
    token = issue_access_token(
        user_id, ["candidate"], settings.jwt_secret, algorithm="HS256",
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )

    resp = client.get("/api/scorecards", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text


def test_unknown_kid_rejected(client: TestClient) -> None:
    token = issue_access_token(
        str(uuid.uuid4()), ["candidate"], _PRIV_PEM, algorithm="RS256",
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
        kid="ghost-kid-nobody-configured",
    )

    resp = client.get("/api/scorecards", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401


def test_hs256_rejected_once_dropped(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    token = issue_access_token(
        str(uuid.uuid4()), ["candidate"], settings.jwt_secret, algorithm="HS256",
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )
    monkeypatch.setattr(settings, "jwt_verify_algorithms", "RS256")

    resp = client.get("/api/scorecards", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
