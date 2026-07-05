"""Integration tests for Naipunyam SSO endpoints — S5-003a.

The SSO router is mounted on a minimal test FastAPI app (not the full
``app.main`` app with lifespan) so tests run without any real DB / Redis.
Naipunyam network calls are fully patched; the ``get_db_session`` dependency
is overridden with an in-memory fake.

Test matrix (6 tests):
  1.  test_initiate_returns_302_when_naipunyam_provider
  2.  test_initiate_returns_404_when_local_provider
  3.  test_initiate_returns_503_when_base_url_missing
  4.  test_callback_with_stub_returns_jwt
  5.  test_callback_naipunyam_unavailable_returns_503
  6.  test_callback_returns_404_when_local_provider
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.database import get_db_session
from app.naipunyam.circuit_breaker import CircuitOpenError
from app.redis_client import get_redis
from app.routers.sso_naipunyam import router as sso_router

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_INITIATE_URL = "/auth/sso/naipunyam/initiate"
_CALLBACK_URL = "/auth/sso/naipunyam/callback"

_FAKE_NAIPUNYAM_BASE = "https://naipunyam.example.com"
_FAKE_CLIENT_ID = "test-client-id"
_FAKE_CLIENT_SECRET = "test-client-secret"
_FAKE_UID = "NAIP-UID-001"
_FAKE_USER_UUID = uuid.uuid4()

# ---------------------------------------------------------------------------
# Minimal test app — carries only the SSO router
# ---------------------------------------------------------------------------
_test_app = FastAPI()
_test_app.include_router(sso_router)


@pytest.fixture(autouse=True)
def _redis_override() -> Any:
    """The callback route depends on get_redis (mint_refresh_session writes the
    tracked refresh token). No real Redis in this suite — an AsyncMock accepts
    the setex/sadd/expire calls. Autouse (rather than module-level) because the
    callback tests clear() dependency_overrides in their finally blocks."""
    _test_app.dependency_overrides[get_redis] = lambda: AsyncMock()
    yield
    _test_app.dependency_overrides.pop(get_redis, None)

# ---------------------------------------------------------------------------
# Fake DB session for upsert
# ---------------------------------------------------------------------------


class _FakeResult:
    """Minimal fake for SQLAlchemy execute() result."""

    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class _FakeDbSession:
    """In-memory fake for AsyncSession — records execute/commit calls."""

    def __init__(self, return_user_id: uuid.UUID = _FAKE_USER_UUID) -> None:
        self._user_id = return_user_id

    async def execute(self, stmt: Any) -> _FakeResult:  # noqa: ARG002
        return _FakeResult((self._user_id,))

    async def commit(self) -> None:
        pass

    async def __aenter__(self) -> _FakeDbSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


def _override_db(user_id: uuid.UUID = _FAKE_USER_UUID) -> Any:
    """Return an async generator that yields a _FakeDbSession."""

    async def _dep() -> Any:
        yield _FakeDbSession(return_user_id=user_id)

    return _dep


# ---------------------------------------------------------------------------
# Fixture — lightweight ASGI client against the minimal test app
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncClient:  # type: ignore[misc]
    """ASGI test client for the minimal SSO-only test app."""
    async with AsyncClient(
        transport=ASGITransport(app=_test_app),
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        yield ac  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Shared settings patches
# ---------------------------------------------------------------------------

_NAIPUNYAM_SETTINGS = {
    "auth_provider": "naipunyam",
    "naipunyam_api_base_url": _FAKE_NAIPUNYAM_BASE,
    "naipunyam_client_id": _FAKE_CLIENT_ID,
    "naipunyam_client_secret": _FAKE_CLIENT_SECRET,
    "naipunyam_saml_acs_url": "",
    "jwt_secret": "test-secret-32-bytes-xxxxxxxxxxxx",
    "jwt_algorithm": "HS256",
    "jwt_issuer": "intants-data-gateway",
    "jwt_audience": "intants-services",
    # Tracked-refresh-session + cookie settings read by the callback handler
    # (mirrors the app.config defaults).
    "jwt_refresh_expiry_days": 7,
    "auth_refresh_cookie_name": "refresh_token",
    "auth_csrf_cookie_name": "csrf_token",
    "auth_cookie_secure": False,
    "auth_cookie_samesite": "lax",
    "auth_cookie_domain": None,
    "auth_cookie_path": "/",
}


def _patch_settings(**overrides: str) -> Any:
    """Return a ``patch`` context manager that overrides settings attributes."""
    merged = {**_NAIPUNYAM_SETTINGS, **overrides}

    class _PatchedSettings:
        pass

    for key, value in merged.items():
        setattr(_PatchedSettings, key, value)

    return patch("app.routers.sso_naipunyam.settings", _PatchedSettings)


# ---------------------------------------------------------------------------
# 1. test_initiate_returns_302_when_naipunyam_provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initiate_returns_302_when_naipunyam_provider(client: AsyncClient) -> None:
    """GET /initiate with AUTH_PROVIDER=naipunyam must return 302 to Naipunyam."""
    with _patch_settings():
        resp = await client.get(_INITIATE_URL)

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "/oauth/authorize" in location
    assert "client_id=test-client-id" in location
    assert "response_type=code" in location
    assert "state=" in location


# ---------------------------------------------------------------------------
# 2. test_initiate_returns_404_when_local_provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initiate_returns_404_when_local_provider(client: AsyncClient) -> None:
    """GET /initiate with AUTH_PROVIDER=local must return 404."""
    with _patch_settings(auth_provider="local"):
        resp = await client.get(_INITIATE_URL)

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. test_initiate_returns_503_when_base_url_missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initiate_returns_503_when_base_url_missing(client: AsyncClient) -> None:
    """GET /initiate when base_url is empty must return 503 NAIPUNYAM_NOT_CONFIGURED."""
    with _patch_settings(naipunyam_api_base_url=""):
        resp = await client.get(_INITIATE_URL)

    assert resp.status_code == 503
    assert resp.json()["detail"] == "NAIPUNYAM_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# 4. test_callback_with_stub_returns_jwt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_with_stub_returns_jwt(client: AsyncClient) -> None:
    """POST /callback with a stubbed NaipunyamClient returns 200 and a valid JWT."""
    from app.naipunyam.client import Profile

    # Build a fake profile (no PII in logs — acceptable in test context)
    fake_profile = Profile(
        uid=_FAKE_UID,
        name="Ravi Kumar",
        email="ravi@naipunyam.example.com",
        phone="9000000000",
        preferred_language="te",
        skills=["Python"],
    )

    # Fake token exchange response (httpx.Response-like MagicMock)
    fake_token_resp = MagicMock()
    fake_token_resp.status_code = 200
    fake_token_resp.json.return_value = {
        "access_token": "naip-access-tok",
        "expires_in": 3600,
        "sub": _FAKE_UID,
    }

    # Override the DB dependency on the test app
    _test_app.dependency_overrides[get_db_session] = _override_db(_FAKE_USER_UUID)
    try:
        with (
            _patch_settings(),
            patch(
                "app.routers.sso_naipunyam.NaipunyamClient",
                autospec=False,
            ) as mock_client_cls,
        ):
            mock_instance = AsyncMock()
            mock_instance._http = AsyncMock()
            mock_instance._http.post = AsyncMock(return_value=fake_token_resp)
            mock_instance.get_profile = AsyncMock(return_value=fake_profile)
            mock_instance.aclose = AsyncMock()
            mock_client_cls.return_value = mock_instance

            resp = await client.post(
                _CALLBACK_URL,
                json={"code": "auth-code-xyz", "state": "random-state"},
            )
    finally:
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["user_id"]
    assert body["access_token"]

    # Verify the JWT is structurally valid (decodable)
    payload = jwt.decode(
        body["access_token"],
        "test-secret-32-bytes-xxxxxxxxxxxx",
        algorithms=["HS256"],
        audience="intants-services",
        options={"verify_exp": False},
    )
    assert payload["sub"] == body["user_id"]
    assert "candidate" in payload["roles"]


# ---------------------------------------------------------------------------
# 5. test_callback_naipunyam_unavailable_returns_503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_naipunyam_unavailable_returns_503(client: AsyncClient) -> None:
    """POST /callback when get_profile raises CircuitOpenError → 503."""
    fake_token_resp = MagicMock()
    fake_token_resp.status_code = 200
    fake_token_resp.json.return_value = {
        "access_token": "tok",
        "expires_in": 3600,
        "sub": _FAKE_UID,
    }

    _test_app.dependency_overrides[get_db_session] = _override_db()
    try:
        with (
            _patch_settings(),
            patch(
                "app.routers.sso_naipunyam.NaipunyamClient",
                autospec=False,
            ) as mock_client_cls,
        ):
            mock_instance = AsyncMock()
            mock_instance._http = AsyncMock()
            mock_instance._http.post = AsyncMock(return_value=fake_token_resp)
            # Profile fetch raises CircuitOpenError
            mock_instance.get_profile = AsyncMock(
                side_effect=CircuitOpenError("Circuit open")
            )
            mock_instance.aclose = AsyncMock()
            mock_client_cls.return_value = mock_instance

            resp = await client.post(
                _CALLBACK_URL,
                json={"code": "any-code", "state": "any-state"},
            )
    finally:
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 503
    assert resp.json()["detail"] == "NAIPUNYAM_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 6. test_callback_returns_404_when_local_provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_returns_404_when_local_provider(client: AsyncClient) -> None:
    """POST /callback with AUTH_PROVIDER=local must return 404.

    The DB dependency override is still required because FastAPI resolves all
    declared dependencies before the endpoint body runs.  The DB is never
    actually queried — the provider guard short-circuits first — but without
    the override the engine-not-initialised error surfaces before the 404.
    """
    _test_app.dependency_overrides[get_db_session] = _override_db()
    try:
        with _patch_settings(auth_provider="local"):
            resp = await client.post(
                _CALLBACK_URL,
                json={"code": "code", "state": "state"},
            )
    finally:
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 404
