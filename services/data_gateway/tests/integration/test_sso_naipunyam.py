"""Integration tests for Naipunyam SSO endpoints — S5-003a.

The SSO router is mounted on a minimal test FastAPI app (not the full
``app.main`` app with lifespan) so tests run without any real DB / Redis.
Naipunyam network calls are fully patched; the ``get_db_session`` dependency
is overridden with an in-memory fake.

Test matrix:
  1.  test_initiate_returns_302_when_naipunyam_provider
  2.  test_initiate_returns_404_when_local_provider
  3.  test_initiate_returns_503_when_base_url_missing
  4.  test_callback_with_stub_returns_jwt
  5.  test_callback_naipunyam_unavailable_returns_503
  6.  test_callback_returns_404_when_local_provider

Login-CSRF regression set (2026-08-06). Each of these fails if the corresponding
guard in ``sso_naipunyam.py`` is removed — that is the bar, and each was checked
by reverting the guard and confirming the test goes red:
  7.  test_initiate_sets_state_binding_cookie_and_stores_hash_only
  8.  test_initiate_sends_pkce_s256_challenge
  9.  test_initiate_does_not_log_raw_state
  10. test_callback_rejects_unknown_state
  11. test_callback_rejects_state_without_binding_cookie
  12. test_callback_rejects_mismatched_binding_cookie
  13. test_callback_state_is_single_use
  14. test_callback_rejects_privileged_account
  15. test_callback_sends_pkce_verifier_in_token_exchange
"""

from __future__ import annotations

import hashlib
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


class _FakeRedis:
    """In-memory Redis good enough for the state store + refresh-session index.

    A bare ``AsyncMock`` is NOT good enough here: ``redis.get`` would return a
    truthy mock for any key, so an unknown state would look valid and the
    login-CSRF tests below could not distinguish a working guard from a broken
    one. This stores real values so `get`/`delete` mean what they say.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                removed += 1
        return removed

    # mint_refresh_session writes the tracked refresh token + session index.
    async def setex(self, key: str, ttl: int, value: str) -> None:  # noqa: ARG002
        self.store[key] = value

    async def sadd(self, *_args: Any, **_kwargs: Any) -> int:
        return 1

    async def expire(self, *_args: Any, **_kwargs: Any) -> bool:
        return True


@pytest.fixture(autouse=True)
def fake_redis() -> Any:
    """Install a shared _FakeRedis for the duration of one test.

    Autouse (rather than module-level) because the callback tests clear()
    dependency_overrides in their finally blocks.
    """
    redis = _FakeRedis()
    _test_app.dependency_overrides[get_redis] = lambda: redis
    yield redis
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
    """In-memory fake for AsyncSession — records execute/commit calls.

    Distinguishes the privileged-role probe from the user upsert by inspecting
    the statement text. Returning a row for BOTH (the previous behaviour) would
    make every callback look like a privileged account and mask real failures.
    """

    def __init__(
        self,
        return_user_id: uuid.UUID = _FAKE_USER_UUID,
        *,
        privileged: bool = False,
    ) -> None:
        self._user_id = return_user_id
        self._privileged = privileged
        self.statements: list[str] = []

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:  # noqa: ARG002
        rendered = str(stmt)
        self.statements.append(rendered)
        if "JOIN user_roles" in rendered:
            return _FakeResult((1,) if self._privileged else None)
        return _FakeResult((self._user_id,))

    async def commit(self) -> None:
        pass

    async def __aenter__(self) -> _FakeDbSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


def _override_db(
    user_id: uuid.UUID = _FAKE_USER_UUID, *, privileged: bool = False
) -> Any:
    """Return an async generator that yields a _FakeDbSession."""

    async def _dep() -> Any:
        yield _FakeDbSession(return_user_id=user_id, privileged=privileged)

    return _dep


# ---------------------------------------------------------------------------
# State helpers — put the router into the post-initiate state the callback
# expects, without going through the redirect.
# ---------------------------------------------------------------------------

_STATE_COOKIE = "naipunyam_oauth_state"


def _seed_state(
    redis: _FakeRedis,
    state: str = "state-token",
    binding: str = "binding-secret",
    verifier: str = "pkce-verifier",
) -> str:
    """Write a valid state payload and return the binding secret to send back."""
    redis.store[f"oauth:naipunyam:state:{state}"] = json.dumps(
        {
            "return_url": "",
            "binding": hashlib.sha256(binding.encode()).hexdigest(),
            "code_verifier": verifier,
        }
    )
    return binding


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
async def test_callback_with_stub_returns_jwt(
    client: AsyncClient, fake_redis: _FakeRedis
) -> None:
    """POST /callback with a stubbed NaipunyamClient returns 200 and a valid JWT."""
    from app.naipunyam.client import Profile

    binding = _seed_state(fake_redis)
    client.cookies.set(_STATE_COOKIE, binding)

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
                json={"code": "auth-code-xyz", "state": "state-token"},
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
async def test_callback_naipunyam_unavailable_returns_503(
    client: AsyncClient, fake_redis: _FakeRedis
) -> None:
    """POST /callback when get_profile raises CircuitOpenError → 503."""
    binding = _seed_state(fake_redis)
    client.cookies.set(_STATE_COOKIE, binding)

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
                json={"code": "any-code", "state": "state-token"},
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


# ---------------------------------------------------------------------------
# Login-CSRF regression set (2026-08-06)
#
# Before this, `state` was generated in initiate(), declared on SsoCallbackBody,
# and never read again — so any attacker-chosen value was accepted. These tests
# pin each half of the fix. Every one of them was checked by reverting the guard
# and confirming it goes red; a test that passes with the control deleted is
# worse than no test, because it reads as coverage.
# ---------------------------------------------------------------------------


def _stub_naipunyam_client(
    profile: Any, token_payload: dict[str, Any] | None = None
) -> Any:
    """Start a patch of NaipunyamClient; returns (patcher, mock_instance)."""
    fake_token_resp = MagicMock()
    fake_token_resp.status_code = 200
    fake_token_resp.json.return_value = token_payload or {
        "access_token": "naip-access-tok",
        "expires_in": 3600,
        "sub": _FAKE_UID,
    }

    patcher = patch("app.routers.sso_naipunyam.NaipunyamClient", autospec=False)
    mock_client_cls = patcher.start()
    mock_instance = AsyncMock()
    mock_instance._http = AsyncMock()
    mock_instance._http.post = AsyncMock(return_value=fake_token_resp)
    mock_instance.get_profile = AsyncMock(return_value=profile)
    mock_instance.aclose = AsyncMock()
    mock_client_cls.return_value = mock_instance
    return patcher, mock_instance


def _candidate_profile() -> Any:
    from app.naipunyam.client import Profile

    return Profile(
        uid=_FAKE_UID,
        name="Ravi Kumar",
        email="ravi@naipunyam.example.com",
        phone="9000000000",
        preferred_language="te",
        skills=["Python"],
    )


@pytest.mark.asyncio
async def test_initiate_sets_state_binding_cookie_and_stores_hash_only(
    client: AsyncClient, fake_redis: _FakeRedis
) -> None:
    """initiate must set an httpOnly binding cookie and store only its SHA-256.

    Storing the raw binding would defeat the purpose: anyone who can read Redis
    could then forge a callback.
    """
    with _patch_settings():
        resp = await client.get(_INITIATE_URL)

    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    assert _STATE_COOKIE in set_cookie
    assert "HttpOnly" in set_cookie
    # Lax, not Strict: the callback follows a cross-site top-level navigation
    # back from the portal, and Strict would withhold the cookie exactly then.
    assert "samesite=lax" in set_cookie.lower()

    state_keys = [k for k in fake_redis.store if k.startswith("oauth:naipunyam:state:")]
    assert len(state_keys) == 1, "initiate must persist exactly one state"
    payload = json.loads(fake_redis.store[state_keys[0]])

    raw_binding = resp.cookies[_STATE_COOKIE]
    assert payload["binding"] == hashlib.sha256(raw_binding.encode()).hexdigest()
    assert raw_binding not in fake_redis.store[state_keys[0]], (
        "the raw binding secret must never reach Redis"
    )


@pytest.mark.asyncio
async def test_initiate_sends_pkce_s256_challenge(
    client: AsyncClient, fake_redis: _FakeRedis
) -> None:
    """The authorize URL must carry an S256 challenge derived from the verifier."""
    import base64

    with _patch_settings():
        resp = await client.get(_INITIATE_URL)

    location = resp.headers["location"]
    assert "code_challenge_method=S256" in location
    assert "code_challenge=" in location

    state_key = next(
        k for k in fake_redis.store if k.startswith("oauth:naipunyam:state:")
    )
    verifier = json.loads(fake_redis.store[state_key])["code_verifier"]
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    assert f"code_challenge={expected}" in location.replace("%3D", "=")


@pytest.mark.asyncio
async def test_initiate_does_not_log_raw_state(
    client: AsyncClient, fake_redis: _FakeRedis
) -> None:
    """The nonce must never be logged in full — a log reader could forge with it."""
    with _patch_settings(), patch("app.routers.sso_naipunyam.log") as mock_log:
        await client.get(_INITIATE_URL)

    state_key = next(
        k for k in fake_redis.store if k.startswith("oauth:naipunyam:state:")
    )
    full_state = state_key.removeprefix("oauth:naipunyam:state:")

    mock_log.info.assert_called_once()
    logged = mock_log.info.call_args.kwargs
    assert "state" not in logged, "the raw nonce must not be a log field"
    assert logged.get("state_prefix") == full_state[:8]


@pytest.mark.asyncio
async def test_callback_rejects_unknown_state(client: AsyncClient) -> None:
    """A state we never issued must be rejected before the token exchange.

    Mutation note: deleting EITHER the ``stored_value is None`` check or the
    binding comparison leaves this green, because an absent Redis value also
    yields an empty binding and the second guard catches it. Removing BOTH turns
    it red (verified). That is defence in depth working, not a weak test — but it
    does mean this test alone does not pin the first guard; the two
    ``rejects_*_binding_cookie`` tests below pin the second.
    """
    patcher, mock_instance = _stub_naipunyam_client(_candidate_profile())
    _test_app.dependency_overrides[get_db_session] = _override_db()
    try:
        with _patch_settings():
            resp = await client.post(
                _CALLBACK_URL,
                json={"code": "attacker-code", "state": "never-issued"},
            )
    finally:
        patcher.stop()
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_OR_EXPIRED_STATE"
    # The IdP must not be contacted on a forged state.
    mock_instance._http.post.assert_not_called()


@pytest.mark.asyncio
async def test_callback_rejects_state_without_binding_cookie(
    client: AsyncClient, fake_redis: _FakeRedis
) -> None:
    """The actual login-CSRF: attacker holds a real state, the victim's browser
    has no binding cookie. Must be refused."""
    _seed_state(fake_redis)
    client.cookies.clear()

    patcher, mock_instance = _stub_naipunyam_client(_candidate_profile())
    _test_app.dependency_overrides[get_db_session] = _override_db()
    try:
        with _patch_settings():
            resp = await client.post(
                _CALLBACK_URL,
                json={"code": "attacker-code", "state": "state-token"},
            )
    finally:
        patcher.stop()
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_OR_EXPIRED_STATE"
    mock_instance._http.post.assert_not_called()


@pytest.mark.asyncio
async def test_callback_rejects_mismatched_binding_cookie(
    client: AsyncClient, fake_redis: _FakeRedis
) -> None:
    """A binding cookie from a different flow must not authorise this state."""
    _seed_state(fake_redis, binding="the-real-binding")
    client.cookies.set(_STATE_COOKIE, "some-other-binding")

    patcher, _ = _stub_naipunyam_client(_candidate_profile())
    _test_app.dependency_overrides[get_db_session] = _override_db()
    try:
        with _patch_settings():
            resp = await client.post(
                _CALLBACK_URL,
                json={"code": "code", "state": "state-token"},
            )
    finally:
        patcher.stop()
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_OR_EXPIRED_STATE"


@pytest.mark.asyncio
async def test_callback_state_is_single_use(
    client: AsyncClient, fake_redis: _FakeRedis
) -> None:
    """A state consumed once must not authorise a second callback (replay)."""
    binding = _seed_state(fake_redis)
    client.cookies.set(_STATE_COOKIE, binding)

    patcher, _ = _stub_naipunyam_client(_candidate_profile())
    _test_app.dependency_overrides[get_db_session] = _override_db()
    try:
        with _patch_settings():
            first = await client.post(
                _CALLBACK_URL, json={"code": "code", "state": "state-token"}
            )
            # The response cleared the cookie; re-set it so the ONLY thing that
            # can fail the replay is the consumed state.
            client.cookies.set(_STATE_COOKIE, binding)
            second = await client.post(
                _CALLBACK_URL, json={"code": "code", "state": "state-token"}
            )
    finally:
        patcher.stop()
        _test_app.dependency_overrides.clear()

    assert first.status_code == 200, first.text
    assert second.status_code == 400
    assert second.json()["detail"] == "INVALID_OR_EXPIRED_STATE"


@pytest.mark.asyncio
async def test_callback_rejects_privileged_account(
    client: AsyncClient, fake_redis: _FakeRedis
) -> None:
    """An IdP email mapping to a staff role must get 403, not a candidate session.

    /auth/refresh re-derives roles from the DB, so a 'candidate' access token
    issued against a privileged row silently escalates 15 minutes later.
    """
    binding = _seed_state(fake_redis)
    client.cookies.set(_STATE_COOKIE, binding)

    patcher, _ = _stub_naipunyam_client(_candidate_profile())
    _test_app.dependency_overrides[get_db_session] = _override_db(privileged=True)
    try:
        with _patch_settings():
            resp = await client.post(
                _CALLBACK_URL, json={"code": "code", "state": "state-token"}
            )
    finally:
        patcher.stop()
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 403
    assert resp.json()["detail"] == "NAIPUNYAM_SIGNIN_CANDIDATES_ONLY"


@pytest.mark.asyncio
async def test_callback_sends_pkce_verifier_in_token_exchange(
    client: AsyncClient, fake_redis: _FakeRedis
) -> None:
    """The stored verifier must be presented at the token endpoint."""
    binding = _seed_state(fake_redis, verifier="verifier-abc123")
    client.cookies.set(_STATE_COOKIE, binding)

    patcher, mock_instance = _stub_naipunyam_client(_candidate_profile())
    _test_app.dependency_overrides[get_db_session] = _override_db()
    try:
        with _patch_settings():
            resp = await client.post(
                _CALLBACK_URL, json={"code": "code", "state": "state-token"}
            )
    finally:
        patcher.stop()
        _test_app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    sent = mock_instance._http.post.call_args.kwargs["data"]
    assert sent["code_verifier"] == "verifier-abc123"
