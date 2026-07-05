"""Integration tests for S1-006 — cross-service JWT validation.

Strategy:
- interview_core ASGI runs in-process (no network hop).
- data_gateway is called live on port 8002 for the real cross-service scenario.
  If data_gateway is not running the live tests are skipped automatically.
- Expired-token and bad-token cases are generated locally via shared helpers
  so they never need a live dependency.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt

from app.config import settings
from app.main import app

DATA_GATEWAY_URL = "http://localhost:8002"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_email() -> str:
    return f"crosstest-{uuid.uuid4().hex[:8]}@intants-test.com"


def _make_expired_token(user_id: str, roles: list[str]) -> str:
    """Craft a token whose exp is 60 seconds in the past.

    Includes S3-005 required claims (iss, aud, jti) so verification fails
    on the expiry check, not on missing-claim validation.
    """
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": user_id,
        "roles": roles,
        "iat": now - timedelta(seconds=960),
        "exp": now - timedelta(seconds=60),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": uuid.uuid4().hex,
    }
    token: str = jose_jwt.encode(
        claims,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return token


async def _data_gateway_available() -> bool:
    """Return True if data_gateway is accepting connections on port 8002."""
    try:
        async with AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{DATA_GATEWAY_URL}/health/live")
            return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# interview_core ASGI client fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ic_client() -> AsyncClient:  # type: ignore[misc]
    """In-process ASGI client for interview_core."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=10.0,
    ) as ac:
        yield ac  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Cross-service test: register via data_gateway → verify via interview_core
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_service_jwt_live(ic_client: AsyncClient) -> None:
    """Register on data_gateway, use the token on interview_core /api/me.

    Skipped automatically when data_gateway is not running on port 8002.
    """
    if not await _data_gateway_available():
        pytest.skip("data_gateway not running on port 8002 — skipping live test")

    email = _unique_email()
    async with AsyncClient(base_url=DATA_GATEWAY_URL, timeout=10.0) as gw:
        reg = await gw.post(
            "/auth/register",
            json={"email": email, "password": "Secur3Pass!", "full_name": "Cross Test"},
        )
    assert reg.status_code == 201, f"register failed: {reg.text}"
    token_data = reg.json()
    access_token: str = token_data["access_token"]
    expected_user_id: str = token_data["user_id"]

    resp = await ic_client.get(
        "/api/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200, f"GET /api/me failed: {resp.text}"
    body = resp.json()
    assert body["user_id"] == expected_user_id
    assert "candidate" in body["roles"]


# ---------------------------------------------------------------------------
# Local token tests — no live data_gateway needed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_no_token_401(ic_client: AsyncClient) -> None:
    """Missing Authorization header must return 401."""
    resp = await ic_client.get("/api/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_bad_token_401(ic_client: AsyncClient) -> None:
    """Malformed / tampered token must return 401."""
    resp = await ic_client.get(
        "/api/me",
        headers={"Authorization": "Bearer thisisnotavalidjwt"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_expired_token_401(ic_client: AsyncClient) -> None:
    """Expired token (exp in the past) must return 401."""
    expired = _make_expired_token(user_id=str(uuid.uuid4()), roles=["candidate"])
    resp = await ic_client.get(
        "/api/me",
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_valid_local_token_200(ic_client: AsyncClient) -> None:
    """A locally-generated valid JWT must be accepted by interview_core.

    This proves that any service sharing the same JWT_SECRET can issue tokens
    that interview_core will accept — the core cross-service guarantee.
    """
    from shared.auth.jwt import issue_access_token

    user_id = str(uuid.uuid4())
    token = issue_access_token(
        user_id=user_id,
        roles=["candidate"],
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    resp = await ic_client.get(
        "/api/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == user_id
    assert body["roles"] == ["candidate"]


@pytest.mark.asyncio
async def test_me_wrong_secret_token_401(ic_client: AsyncClient) -> None:
    """Token signed with a different secret must be rejected."""
    from shared.auth.jwt import issue_access_token

    token = issue_access_token(
        user_id=str(uuid.uuid4()),
        roles=["candidate"],
        secret="totally-wrong-secret-that-doesnt-match",
        algorithm=settings.jwt_algorithm,
    )
    resp = await ic_client.get(
        "/api/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
