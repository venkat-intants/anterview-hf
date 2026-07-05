"""Integration tests for Google OAuth 2.0 SSO endpoints — S5-003b.

The SSO router is mounted on a minimal test FastAPI app (not the full
``app.main`` app with lifespan) so tests run without any real DB / Redis.
Google network calls are fully patched; get_db_session and get_redis
dependencies are overridden with in-memory fakes.

Test matrix (6 integration + 1 unit-level URL test):
  1.  test_initiate_returns_302_when_google_provider
  2.  test_initiate_returns_302_when_local_provider_but_google_configured
  3.  test_initiate_returns_503_when_not_configured
  4.  test_callback_valid_code_returns_jwt
  5.  test_callback_invalid_state_returns_400
  6.  test_callback_google_token_failure_returns_502
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.database import get_db_session
from app.redis_client import get_redis
from app.routers.sso_google import router as google_router

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INITIATE_URL = "/auth/sso/google/initiate"
_CALLBACK_URL = "/auth/sso/google/callback"

_FAKE_CLIENT_ID = "test-google-client-id"
_FAKE_CLIENT_SECRET = "test-google-client-secret"
_FAKE_REDIRECT_URI = "https://app.intants.com/auth/sso/google/callback"
_FAKE_USER_UUID = uuid.uuid4()
_FAKE_GOOGLE_SUB = "118200000000000000000"
_FAKE_STATE = "valid_state_token_abc123"

# ---------------------------------------------------------------------------
# Minimal test app — carries only the Google SSO router
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(google_router)

# ---------------------------------------------------------------------------
# Shared settings dict — Google provider fully configured
# ---------------------------------------------------------------------------

_GOOGLE_SETTINGS: dict[str, Any] = {
    "auth_provider": "google",
    "google_oauth_client_id": _FAKE_CLIENT_ID,
    "google_oauth_client_secret": _FAKE_CLIENT_SECRET,
    "google_oauth_redirect_uri": _FAKE_REDIRECT_URI,
    "jwt_secret": "test-secret-32-bytes-xxxxxxxxxxxx",
    "jwt_algorithm": "HS256",
    "jwt_issuer": "intants-data-gateway",
    "jwt_audience": "intants-services",
    # Session-cookie + refresh-token fields the callback now reads to persist the
    # Google session like local login.
    "jwt_refresh_expiry_days": 7,
    "auth_refresh_cookie_name": "refresh_token",
    "auth_csrf_cookie_name": "csrf_token",
    "auth_cookie_secure": False,
    "auth_cookie_samesite": "lax",
    "auth_cookie_domain": None,
    "auth_cookie_path": "/",
}


def _patch_settings(**overrides: str) -> Any:
    """Return a patch context manager that overrides sso_google.settings."""
    merged = {**_GOOGLE_SETTINGS, **overrides}

    class _PatchedSettings:
        pass

    for key, value in merged.items():
        setattr(_PatchedSettings, key, value)

    return patch("app.routers.sso_google.settings", _PatchedSettings)


# ---------------------------------------------------------------------------
# Fake DB session
# ---------------------------------------------------------------------------


class _FakeResult:
    """Minimal fake for SQLAlchemy execute() result."""

    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class _FakeDbSession:
    """In-memory fake for AsyncSession — records execute/commit calls."""

    def __init__(
        self, return_user_id: uuid.UUID = _FAKE_USER_UUID, privileged: bool = False
    ) -> None:
        self._user_id = return_user_id
        self._privileged = privileged  # simulate an existing HR/admin account
        self.executed: list[str] = []  # str() of every statement run (for assertions)

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:  # noqa: ARG002
        sql = str(stmt)
        self.executed.append(sql)
        # The only SELECT the SSO callback runs is the candidate-only privileged-role
        # probe. Return a row only when simulating a privileged account; all other
        # statements (upsert RETURNING, role/consent INSERTs) yield the user_id row.
        if sql.lstrip().upper().startswith("SELECT"):
            return _FakeResult((1,) if self._privileged else None)
        return _FakeResult((self._user_id,))

    async def commit(self) -> None:
        pass

    async def __aenter__(self) -> _FakeDbSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


def _override_db(
    user_id: uuid.UUID = _FAKE_USER_UUID, session: _FakeDbSession | None = None
) -> Any:
    """Return an async generator that yields a _FakeDbSession.

    Pass ``session`` to inspect the statements the handler ran after the request.
    """
    sess = session or _FakeDbSession(return_user_id=user_id)

    async def _dep() -> Any:
        yield sess

    return _dep


# ---------------------------------------------------------------------------
# Fake Redis client
# ---------------------------------------------------------------------------


class _FakeRedis:
    """In-memory fake for the Redis async client.

    Supports both ``set`` (used by state-key storage in initiate) and ``setex``
    (used by mint_refresh_session for refresh token storage), plus the minimal
    session-index ops (sadd, expire) so AUTH-01 tracked-format writes succeed.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self._store[key] = value

    async def setex(self, key: str, ttl: int, value: str) -> None:  # noqa: ARG002
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def delete(self, key: str) -> int:
        return self._store.pop(key, None) and 1 or 0  # type: ignore[return-value]

    async def sadd(self, key: str, member: str) -> int:
        self._sets.setdefault(key, set()).add(member)
        return 1

    async def expire(self, key: str, ttl: int) -> bool:  # noqa: ARG002
        return True


def _make_redis_override(fake: _FakeRedis) -> Any:
    """Return a FastAPI dependency override that yields the fake Redis."""

    def _dep() -> _FakeRedis:
        return fake

    return _dep


# ---------------------------------------------------------------------------
# Fixture — lightweight ASGI client against the minimal test app
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncClient:  # type: ignore[misc]
    """ASGI test client for the minimal Google SSO-only test app."""
    async with AsyncClient(
        transport=ASGITransport(app=_test_app),
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        yield ac  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 1. test_initiate_returns_302_when_google_provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initiate_returns_302_when_google_provider(client: AsyncClient) -> None:
    """GET /initiate with AUTH_PROVIDER=google must return 302 to accounts.google.com."""
    fake_redis = _FakeRedis()
    _test_app.dependency_overrides[get_redis] = _make_redis_override(fake_redis)
    try:
        with _patch_settings():
            resp = await client.get(_INITIATE_URL)
    finally:
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "accounts.google.com" in location
    assert "client_id=test-google-client-id" in location
    assert "response_type=code" in location
    assert "state=" in location
    assert "scope=" in location


# ---------------------------------------------------------------------------
# 2. test_initiate_returns_302_when_local_provider_but_google_configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initiate_returns_302_when_local_provider_but_google_configured(
    client: AsyncClient,
) -> None:
    """B-035: SSO is decoupled from AUTH_PROVIDER.

    With AUTH_PROVIDER=local but Google credentials configured, /initiate must
    still return 302 — Google SSO works alongside local password login. (The
    auth factory has no bootable "google" provider, so the platform always runs
    AUTH_PROVIDER=local; gating SSO on the provider would make it unreachable.)
    """
    fake_redis = _FakeRedis()
    _test_app.dependency_overrides[get_redis] = _make_redis_override(fake_redis)
    try:
        with _patch_settings(auth_provider="local"):
            resp = await client.get(_INITIATE_URL)
    finally:
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 302
    assert "accounts.google.com" in resp.headers["location"]


# ---------------------------------------------------------------------------
# 3. test_initiate_returns_503_when_not_configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initiate_returns_503_when_not_configured(client: AsyncClient) -> None:
    """GET /initiate when google_oauth_client_id is empty → 503 GOOGLE_OAUTH_NOT_CONFIGURED.

    Redis dependency override is required even though the configured-guard fires
    before Redis is used — FastAPI resolves all deps before calling the handler.
    """
    fake_redis = _FakeRedis()
    _test_app.dependency_overrides[get_redis] = _make_redis_override(fake_redis)
    try:
        with _patch_settings(google_oauth_client_id=""):
            resp = await client.get(_INITIATE_URL)
    finally:
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 503
    assert resp.json()["detail"] == "GOOGLE_OAUTH_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# 4. test_callback_valid_code_returns_jwt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_valid_code_returns_jwt(client: AsyncClient) -> None:
    """Callback with a valid code + state → 200 with a valid Intants JWT."""
    # Seed Redis with the expected state token
    fake_redis = _FakeRedis()
    await fake_redis.set(f"oauth:google:state:{_FAKE_STATE}", "https://app.intants.com/dashboard")

    # Build fake httpx responses for token exchange and userinfo
    fake_token_resp = MagicMock()
    fake_token_resp.status_code = 200
    fake_token_resp.json.return_value = {
        "access_token": "google-access-token-xyz",
        "token_type": "bearer",
        "expires_in": 3600,
        "id_token": "fake-id-token",
    }

    fake_userinfo_resp = MagicMock()
    fake_userinfo_resp.status_code = 200
    fake_userinfo_resp.json.return_value = {
        "sub": _FAKE_GOOGLE_SUB,
        "email": "test.user@gmail.com",
        "name": "Test User",
        "email_verified": True,
    }

    # Mock httpx.AsyncClient as a context manager
    mock_http_instance = AsyncMock()
    mock_http_instance.post = AsyncMock(return_value=fake_token_resp)
    mock_http_instance.get = AsyncMock(return_value=fake_userinfo_resp)
    mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
    mock_http_instance.__aexit__ = AsyncMock(return_value=None)

    _test_app.dependency_overrides[get_db_session] = _override_db(_FAKE_USER_UUID)
    _test_app.dependency_overrides[get_redis] = _make_redis_override(fake_redis)
    try:
        with (
            _patch_settings(),
            patch("app.routers.sso_google.httpx.AsyncClient", return_value=mock_http_instance),
        ):
            resp = await client.get(
                _CALLBACK_URL,
                params={"code": "auth-code-from-google", "state": _FAKE_STATE},
            )
    finally:
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["user_id"]
    assert body["access_token"]

    # Verify the JWT is structurally valid and contains expected claims
    payload = jwt.decode(
        body["access_token"],
        "test-secret-32-bytes-xxxxxxxxxxxx",
        algorithms=["HS256"],
        audience="intants-services",
        options={"verify_exp": False},
    )
    assert payload["sub"] == body["user_id"]
    assert "candidate" in payload["roles"]

    # State key must have been consumed (deleted from Redis)
    assert await fake_redis.get(f"oauth:google:state:{_FAKE_STATE}") is None

    # Session cookies must be set so the Google session persists past the access
    # token (httpOnly refresh_token + JS-readable csrf_token), like local login.
    assert "refresh_token" in resp.cookies
    assert "csrf_token" in resp.cookies
    # A refresh token must have been stored in Redis under the refresh: prefix.
    assert any(k.startswith("refresh:") for k in fake_redis._store)

    # AUTH-01: the stored value must be in the tracked "<user_id>:<created_at>"
    # format so that logout_all / epoch check can revoke SSO sessions.
    refresh_keys = [k for k in fake_redis._store if k.startswith("refresh:")]
    assert len(refresh_keys) == 1, "exactly one refresh token must be stored"
    stored_rt_value = fake_redis._store[refresh_keys[0]]
    user_id_from_body = body["user_id"]
    assert stored_rt_value.startswith(f"{user_id_from_body}:"), (
        f"AUTH-01 REGRESSION: stored value must be '<user_id>:<ts>' — "
        f"got {stored_rt_value!r}"
    )
    ts_part = stored_rt_value[len(user_id_from_body) + 1:]
    assert ts_part.isdigit(), f"created_at must be a digit string — got {ts_part!r}"

    # Cookie ATTRIBUTES must match local login (httpx hides these on resp.cookies,
    # so parse the raw Set-Cookie headers — same approach as the auth-endpoint tests).
    set_cookie_headers = [v for k, v in resp.headers.multi_items() if k.lower() == "set-cookie"]
    refresh_header = next(h for h in set_cookie_headers if h.startswith("refresh_token="))
    csrf_header = next(h for h in set_cookie_headers if h.startswith("csrf_token="))
    assert "httponly" in refresh_header.lower()      # refresh cookie: XSS-safe
    assert "httponly" not in csrf_header.lower()      # csrf cookie: JS must read it
    assert "samesite=lax" in refresh_header.lower()
    assert "secure" not in refresh_header.lower()     # auth_cookie_secure=False in test settings


# ---------------------------------------------------------------------------
# 4b. test_callback_with_consent_records_dpdp_ledger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_with_consent_records_dpdp_ledger(client: AsyncClient) -> None:
    """When the state carries consent=true, the callback inserts a DPDP consent row
    (interview_voice_recording / interview) atomically with the user upsert."""
    fake_redis = _FakeRedis()
    # State payload as written by initiate when the candidate ticked the box.
    await fake_redis.set(
        f"oauth:google:state:{_FAKE_STATE}",
        json.dumps({"return_url": "/dashboard", "consent": True, "consent_version": 1}),
    )

    fake_token_resp = MagicMock()
    fake_token_resp.status_code = 200
    fake_token_resp.json.return_value = {"access_token": "google-access-token-xyz"}

    fake_userinfo_resp = MagicMock()
    fake_userinfo_resp.status_code = 200
    fake_userinfo_resp.json.return_value = {
        "sub": _FAKE_GOOGLE_SUB,
        "email": "consenting.user@gmail.com",
        "name": "Consenting User",
    }

    mock_http_instance = AsyncMock()
    mock_http_instance.post = AsyncMock(return_value=fake_token_resp)
    mock_http_instance.get = AsyncMock(return_value=fake_userinfo_resp)
    mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
    mock_http_instance.__aexit__ = AsyncMock(return_value=None)

    sess = _FakeDbSession()
    _test_app.dependency_overrides[get_db_session] = _override_db(session=sess)
    _test_app.dependency_overrides[get_redis] = _make_redis_override(fake_redis)
    try:
        with (
            _patch_settings(),
            patch("app.routers.sso_google.httpx.AsyncClient", return_value=mock_http_instance),
        ):
            resp = await client.get(
                _CALLBACK_URL,
                params={"code": "auth-code-from-google", "state": _FAKE_STATE},
            )
    finally:
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    # The consent ledger INSERT must have been issued in the same handler.
    assert any("dpdp_consent_ledger" in s for s in sess.executed), sess.executed
    # And the candidate role grant (every Google sign-in is candidate-only).
    assert any("user_roles" in s for s in sess.executed)


# ---------------------------------------------------------------------------
# 4c. test_callback_rejects_privileged_account (candidate-only guarantee)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_rejects_privileged_account(client: AsyncClient) -> None:
    """Google sign-in is candidate-only: an email mapping to an account that holds
    a privileged role (hr_manager/super_admin/platform_owner/admin) is rejected with
    403 and NO session (no access token, no refresh token, no cookies) is issued —
    closing the refresh-escalation path."""
    fake_redis = _FakeRedis()
    await fake_redis.set(
        f"oauth:google:state:{_FAKE_STATE}",
        json.dumps({"return_url": "/dashboard", "consent": True, "consent_version": 1}),
    )

    fake_token_resp = MagicMock()
    fake_token_resp.status_code = 200
    fake_token_resp.json.return_value = {"access_token": "google-access-token-xyz"}

    fake_userinfo_resp = MagicMock()
    fake_userinfo_resp.status_code = 200
    fake_userinfo_resp.json.return_value = {
        "sub": _FAKE_GOOGLE_SUB,
        "email": "hr.manager@company.com",
        "name": "HR Manager",
    }

    mock_http_instance = AsyncMock()
    mock_http_instance.post = AsyncMock(return_value=fake_token_resp)
    mock_http_instance.get = AsyncMock(return_value=fake_userinfo_resp)
    mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
    mock_http_instance.__aexit__ = AsyncMock(return_value=None)

    # privileged=True → the candidate-only probe finds an existing privileged role.
    sess = _FakeDbSession(privileged=True)
    _test_app.dependency_overrides[get_db_session] = _override_db(session=sess)
    _test_app.dependency_overrides[get_redis] = _make_redis_override(fake_redis)
    try:
        with (
            _patch_settings(),
            patch("app.routers.sso_google.httpx.AsyncClient", return_value=mock_http_instance),
        ):
            resp = await client.get(
                _CALLBACK_URL,
                params={"code": "auth-code-from-google", "state": _FAKE_STATE},
            )
    finally:
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "GOOGLE_SIGNIN_CANDIDATES_ONLY"
    # No session artifacts for a rejected privileged account.
    assert "refresh_token" not in resp.cookies
    assert not any(k.startswith("refresh:") for k in fake_redis._store)
    # Rejected before any write (no upsert / role / consent INSERT ran).
    assert not any("INSERT" in s.upper() for s in sess.executed)


# ---------------------------------------------------------------------------
# 5. test_callback_invalid_state_returns_400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_invalid_state_returns_400(client: AsyncClient) -> None:
    """Callback with an unknown/expired state token → 400 INVALID_OR_EXPIRED_STATE."""
    # Empty Redis — state key not present
    fake_redis = _FakeRedis()

    _test_app.dependency_overrides[get_db_session] = _override_db()
    _test_app.dependency_overrides[get_redis] = _make_redis_override(fake_redis)
    try:
        with _patch_settings():
            resp = await client.get(
                _CALLBACK_URL,
                params={"code": "any-code", "state": "no-such-state"},
            )
    finally:
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_OR_EXPIRED_STATE"


# ---------------------------------------------------------------------------
# 6. test_callback_google_token_failure_returns_502
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_google_token_failure_returns_502(client: AsyncClient) -> None:
    """Callback where Google token endpoint returns 400 → 502 GOOGLE_TOKEN_EXCHANGE_FAILED."""
    fake_redis = _FakeRedis()
    await fake_redis.set(f"oauth:google:state:{_FAKE_STATE}", "")

    # Token endpoint returns an error status
    fake_token_resp = MagicMock()
    fake_token_resp.status_code = 400
    fake_token_resp.json.return_value = {
        "error": "invalid_grant",
        "error_description": "Bad Request",
    }

    mock_http_instance = AsyncMock()
    mock_http_instance.post = AsyncMock(return_value=fake_token_resp)
    mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
    mock_http_instance.__aexit__ = AsyncMock(return_value=None)

    _test_app.dependency_overrides[get_db_session] = _override_db()
    _test_app.dependency_overrides[get_redis] = _make_redis_override(fake_redis)
    try:
        with (
            _patch_settings(),
            patch("app.routers.sso_google.httpx.AsyncClient", return_value=mock_http_instance),
        ):
            resp = await client.get(
                _CALLBACK_URL,
                params={"code": "expired-code", "state": _FAKE_STATE},
            )
    finally:
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 502
    assert resp.json()["detail"] == "GOOGLE_TOKEN_EXCHANGE_FAILED"
