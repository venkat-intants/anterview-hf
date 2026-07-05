"""Integration tests for auth REST endpoints — S1-004.

Runs against the real local Postgres (port 5433) and Redis (port 6379).
Each test uses a unique email to avoid cross-test conflicts.

Cookie tests:
  - test_register_sets_httponly_cookie
  - test_login_sets_httponly_cookie
  - test_refresh_via_cookie (requires X-CSRF-Token)
  - test_logout_clears_cookie
  - test_logout_clears_csrf_cookie

Security tests (new — post-hardening):
  - test_register_response_has_no_refresh_token_in_body
  - test_login_response_has_no_refresh_token_in_body
  - test_refresh_response_has_no_refresh_token_in_body
  - test_refresh_cookie_requires_csrf_header (403 without it)
  - test_refresh_cookie_wrong_csrf_header_is_403
  - test_refresh_cookie_correct_csrf_header_succeeds
  - test_refresh_body_token_skips_csrf_check
  - test_logout_no_token_returns_200_and_clears_cookies
  - test_logout_cross_user_rejected
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex[:8]}@example.com"


@pytest_asyncio.fixture
async def client() -> AsyncClient:  # type: ignore[misc]
    # Use lifespan=True so the FastAPI lifespan context manager fires (init DB + Redis)
    async with AsyncClient(  # noqa: SIM117
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=30.0,
    ) as ac:
        # lifespan_context must be separate: yield inside nested `async with` is intentional
        async with app.router.lifespan_context(app):
            yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_set_cookie_header(resp: object, cookie_name: str) -> str | None:
    """Return the raw Set-Cookie header value for *cookie_name*, or None.

    httpx exposes all headers (including multiple Set-Cookie headers) via
    ``response.headers.multi_items()``.  We match case-insensitively on the
    header name and then check whether the value starts with the cookie name.
    """
    from httpx import Response as HttpxResponse

    if not isinstance(resp, HttpxResponse):  # pragma: no cover
        return None

    for header_name, header_value in resp.headers.multi_items():
        # Set-Cookie value starts with "<name>=<value>; ..."
        if header_name.lower() == "set-cookie" and header_value.lower().startswith(
            f"{cookie_name.lower()}="
        ):
            return header_value
    return None


def _extract_csrf_from_response(resp: object) -> str | None:
    """Return the csrf_token cookie value from the Set-Cookie headers, or None."""
    raw = _get_set_cookie_header(resp, settings.auth_csrf_cookie_name)
    if raw is None:
        return None
    # Format: "csrf_token=<value>; Path=/; ..."
    value_part = raw.split(";")[0]
    _, _, value = value_part.partition("=")
    return value if value else None


async def _register(client: AsyncClient, email: str | None = None) -> dict:  # type: ignore[type-arg]
    """Register a fresh user and return the parsed JSON response body."""
    if email is None:
        email = _unique_email()
    resp = await client.post(
        "/auth/register",
        json={"email": email, "password": "Secur3Pass!", "full_name": "Test User"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_201(client: AsyncClient) -> None:
    email = _unique_email()
    resp = await client.post(
        "/auth/register",
        json={"email": email, "password": "Secur3Pass!", "full_name": "Test User"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["expires_in"] == 900
    assert body["user_id"]
    assert "candidate" in body["roles"]


@pytest.mark.asyncio
async def test_register_response_has_no_refresh_token_in_body(client: AsyncClient) -> None:
    """Security: refresh_token MUST NOT appear in the JSON response body.

    It must be delivered exclusively via the httpOnly Set-Cookie header.
    Returning it in JSON lets JS read it and negates the XSS protection.
    """
    resp = await client.post(
        "/auth/register",
        json={"email": _unique_email(), "password": "Secur3Pass!", "full_name": "NoRT"},
    )
    assert resp.status_code == 201, resp.text
    assert "refresh_token" not in resp.json(), (
        "refresh_token MUST NOT be present in the JSON response body — "
        "it must be set as an httpOnly cookie only."
    )


@pytest.mark.asyncio
async def test_register_409_duplicate_email(client: AsyncClient) -> None:
    email = _unique_email()
    payload = {"email": email, "password": "Secur3Pass!", "full_name": "Dup User"}
    r1 = await client.post("/auth/register", json=payload)
    assert r1.status_code == 201
    r2 = await client.post("/auth/register", json=payload)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_register_400_short_password(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/register",
        json={"email": _unique_email(), "password": "short", "full_name": "X"},
    )
    assert resp.status_code == 422  # Pydantic validation → 422


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_200(client: AsyncClient) -> None:
    email = _unique_email()
    pw = "MyP@ssw0rd"
    await client.post(
        "/auth/register",
        json={"email": email, "password": pw, "full_name": "Login User"},
    )
    resp = await client.post("/auth/login", json={"email": email, "password": pw})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert "candidate" in body["roles"]


@pytest.mark.asyncio
async def test_login_response_has_no_refresh_token_in_body(client: AsyncClient) -> None:
    """Security: refresh_token MUST NOT appear in the login JSON response body."""
    email = _unique_email()
    pw = "MyP@ssw0rd"
    await client.post(
        "/auth/register",
        json={"email": email, "password": pw, "full_name": "Login NoRT"},
    )
    resp = await client.post("/auth/login", json={"email": email, "password": pw})
    assert resp.status_code == 200, resp.text
    assert "refresh_token" not in resp.json(), (
        "refresh_token MUST NOT be present in the login JSON response body."
    )


@pytest.mark.asyncio
async def test_login_401_wrong_password(client: AsyncClient) -> None:
    email = _unique_email()
    await client.post(
        "/auth/register",
        json={"email": email, "password": "correctpass", "full_name": "U"},
    )
    resp = await client.post(
        "/auth/login", json={"email": email, "password": "wrongpass"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_401_unknown_user(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/login",
        json={"email": "ghost-nobody@example.com", "password": "doesnotmatter"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Refresh — body-based (non-browser / curl flow, CSRF skipped)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_body_token_skips_csrf_check(client: AsyncClient) -> None:
    """Body-supplied refresh tokens bypass CSRF check — no X-CSRF-Token header needed.

    This keeps non-browser clients (curl, test suites, backend services) working
    without cookies.  The refresh token is extracted from the login Set-Cookie
    header, not from the JSON body (which no longer contains it).
    """
    email = _unique_email()
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "Passw0rd!!", "full_name": "Body Refresh"},
    )
    assert reg.status_code == 201, reg.text

    # Extract refresh token from Set-Cookie header (no longer in JSON body).
    old_rt = reg.cookies.get(settings.auth_refresh_cookie_name)
    assert old_rt, "refresh_token cookie not set on register"

    # Refresh via body — no X-CSRF-Token header — using a fresh cookie-less client.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0
    ) as fresh:
        resp = await fresh.post("/auth/refresh", json={"refresh_token": old_rt})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    # refresh_token must NOT appear in the JSON body even on body-based refresh.
    assert "refresh_token" not in body, (
        "refresh_token MUST NOT be present in the refresh JSON response body."
    )


@pytest.mark.asyncio
async def test_refresh_200_rotates_token(client: AsyncClient) -> None:
    """Body-based refresh rotates the token; old token is then dead."""
    email = _unique_email()
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "Passw0rd!!", "full_name": "Refresh User"},
    )
    # Get old refresh token from the cookie (not JSON body).
    old_rt = reg.cookies.get(settings.auth_refresh_cookie_name)
    assert old_rt, "refresh_token cookie not set on register"

    # Use a fresh client (no cookies) to do a body-based refresh.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0
    ) as fresh:
        resp = await fresh.post("/auth/refresh", json={"refresh_token": old_rt})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]

    # After rotation, the old token must fail.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0
    ) as fresh2:
        resp2 = await fresh2.post("/auth/refresh", json={"refresh_token": old_rt})
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_refresh_401_invalid_token(client: AsyncClient) -> None:
    resp = await client.post("/auth/refresh", json={"refresh_token": "garbage_token"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_401_no_token(client: AsyncClient) -> None:
    """Neither cookie nor body provided → 401."""
    # Use a fresh client with no cookies so the cookie path is not taken.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0
    ) as fresh:
        resp = await fresh.post("/auth/refresh", json={})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Refresh — CSRF double-submit protection (cookie path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_cookie_requires_csrf_header(client: AsyncClient) -> None:
    """Cookie-based refresh MUST be rejected (403) if X-CSRF-Token header is absent."""
    email = _unique_email()
    await client.post(
        "/auth/register",
        json={"email": email, "password": "CsrfPass1!", "full_name": "CSRF Test"},
    )
    # client now carries the refresh_token + csrf_token cookies.
    # Send the refresh request WITHOUT the X-CSRF-Token header.
    resp = await client.post("/auth/refresh", json={})
    assert resp.status_code == 403, (
        f"Expected 403 when X-CSRF-Token header is absent; got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_refresh_cookie_wrong_csrf_header_is_403(client: AsyncClient) -> None:
    """Cookie-based refresh MUST be rejected (403) if X-CSRF-Token value is wrong."""
    email = _unique_email()
    await client.post(
        "/auth/register",
        json={"email": email, "password": "CsrfPass1!", "full_name": "CSRF Wrong"},
    )
    resp = await client.post(
        "/auth/refresh",
        json={},
        headers={"X-CSRF-Token": "this-is-the-wrong-value"},
    )
    assert resp.status_code == 403, (
        f"Expected 403 with wrong CSRF token; got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_refresh_cookie_correct_csrf_header_succeeds(client: AsyncClient) -> None:
    """Cookie-based refresh MUST succeed when X-CSRF-Token matches the csrf_token cookie."""
    email = _unique_email()
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "CsrfPass1!", "full_name": "CSRF Correct"},
    )
    assert reg.status_code == 201, reg.text

    # Read the csrf_token cookie value from the registration response Set-Cookie header.
    csrf_value = _extract_csrf_from_response(reg)
    assert csrf_value, (
        f"Expected csrf_token cookie in registration response; "
        f"headers: {list(reg.headers.multi_items())}"
    )

    # Perform cookie-based refresh with the matching X-CSRF-Token header.
    resp = await client.post(
        "/auth/refresh",
        json={},
        headers={"X-CSRF-Token": csrf_value},
    )
    assert resp.status_code == 200, (
        f"Expected 200 with correct CSRF token; got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["access_token"]
    assert "refresh_token" not in body

    # New csrf_token cookie must be set (rotated).
    new_csrf = _extract_csrf_from_response(resp)
    assert new_csrf, "Expected csrf_token cookie in refresh response"
    # The csrf_token should be rotated (different from the registration one).
    assert new_csrf != csrf_value, "csrf_token was not rotated on refresh"


@pytest.mark.asyncio
async def test_refresh_response_has_no_refresh_token_in_body(client: AsyncClient) -> None:
    """Security: refresh_token MUST NOT appear in /auth/refresh JSON response body."""
    email = _unique_email()
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "Passw0rd!!", "full_name": "Refresh NoRT"},
    )
    csrf_value = _extract_csrf_from_response(reg)
    assert csrf_value

    resp = await client.post(
        "/auth/refresh",
        json={},
        headers={"X-CSRF-Token": csrf_value},
    )
    assert resp.status_code == 200, resp.text
    assert "refresh_token" not in resp.json(), (
        "refresh_token MUST NOT be present in the /auth/refresh JSON response body."
    )


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_200(client: AsyncClient) -> None:
    email = _unique_email()
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "Passw0rd!!", "full_name": "Me User"},
    )
    access = reg.json()["access_token"]

    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == email
    assert body["full_name"] == "Me User"
    assert "candidate" in body["roles"]


@pytest.mark.asyncio
async def test_me_401_no_token(client: AsyncClient) -> None:
    resp = await client.get("/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_401_bad_token(client: AsyncClient) -> None:
    resp = await client.get(
        "/auth/me", headers={"Authorization": "Bearer notavalidjwt"}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_200(client: AsyncClient) -> None:
    email = _unique_email()
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "Passw0rd!!", "full_name": "Logout User"},
    )
    tokens = reg.json()
    access = tokens["access_token"]
    # Get refresh token from cookie (no longer in body).
    rt = reg.cookies.get(settings.auth_refresh_cookie_name)
    assert rt, "refresh_token cookie not set"

    resp = await client.post(
        "/auth/logout",
        json={"refresh_token": rt},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Refresh token is now dead (use fresh cookie-less client to avoid cookie interference).
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0
    ) as fresh:
        resp2 = await fresh.post("/auth/refresh", json={"refresh_token": rt})
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_logout_no_token_returns_200_and_clears_cookies(client: AsyncClient) -> None:
    """Idempotent logout: no token provided → 200 + cookies cleared.

    A logged-out or token-free client calling /auth/logout should not 500 or 401.
    The server should clear the cookies and return {ok: true}.
    """
    email = _unique_email()
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "Passw0rd!!", "full_name": "Idempotent"},
    )
    access = reg.json()["access_token"]

    # Logout with NO token in body and NO cookie (use a fresh client to strip cookies).
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0
    ) as fresh:
        resp = await fresh.post(
            "/auth/logout",
            json={},
            headers={"Authorization": f"Bearer {access}"},
        )
    assert resp.status_code == 200, (
        f"Expected 200 for no-token logout; got {resp.status_code}: {resp.text}"
    )
    assert resp.json()["ok"] is True

    # Both cookies should be cleared.
    refresh_cookie_header = _get_set_cookie_header(resp, settings.auth_refresh_cookie_name)
    csrf_cookie_header = _get_set_cookie_header(resp, settings.auth_csrf_cookie_name)
    # delete_cookie sets max-age=0.
    if refresh_cookie_header:
        assert "max-age=0" in refresh_cookie_header.lower(), (
            f"refresh_token cookie not cleared: {refresh_cookie_header}"
        )
    if csrf_cookie_header:
        assert "max-age=0" in csrf_cookie_header.lower(), (
            f"csrf_token cookie not cleared: {csrf_cookie_header}"
        )


@pytest.mark.asyncio
async def test_logout_cross_user_rejected(client: AsyncClient) -> None:
    """A user must not be able to invalidate another user's refresh token.

    Strategy:
      1. Register user A and user B.
      2. Extract user A's refresh token.
      3. Log in as user B and attempt to pass user A's refresh token in the body.
      4. Expect 200 (the call succeeds for user B's own session) BUT user A's
         token must remain valid (the cross-user delete was silently rejected).
    """
    # Register user A.
    email_a = _unique_email()
    reg_a = await client.post(
        "/auth/register",
        json={"email": email_a, "password": "Passw0rd!!", "full_name": "User A"},
    )
    assert reg_a.status_code == 201
    rt_a = reg_a.cookies.get(settings.auth_refresh_cookie_name)
    assert rt_a, "User A: refresh_token cookie not set"

    # Register user B (separate client to avoid cookie contamination).
    email_b = _unique_email()
    # noqa: SIM117 — async context managers must be nested (AsyncClient + lifespan)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0
    ) as client_b, app.router.lifespan_context(app):
        reg_b = await client_b.post(
            "/auth/register",
            json={"email": email_b, "password": "Passw0rd!!", "full_name": "User B"},
        )
        assert reg_b.status_code == 201
        access_b = reg_b.json()["access_token"]

        # User B attempts logout supplying user A's refresh token in the body.
        resp = await client_b.post(
            "/auth/logout",
            json={"refresh_token": rt_a},
            headers={"Authorization": f"Bearer {access_b}"},
        )
        # Logout is idempotent — still 200.
        assert resp.status_code == 200, resp.text

    # User A's token must still be valid (cross-user delete was rejected).
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0
    ) as verifier, app.router.lifespan_context(app):
        verify = await verifier.post(
            "/auth/refresh", json={"refresh_token": rt_a}
        )
    assert verify.status_code == 200, (
        f"User A's token should still be valid after cross-user logout attempt; "
        f"got {verify.status_code}: {verify.text}"
    )


# ---------------------------------------------------------------------------
# Cookie-specific tests (httpOnly refresh-token + csrf_token cookie behaviour)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_sets_httponly_cookie(client: AsyncClient) -> None:
    """POST /auth/register must set an httpOnly Set-Cookie for the refresh token."""
    resp = await client.post(
        "/auth/register",
        json={
            "email": _unique_email(),
            "password": "CookiePass1!",
            "full_name": "Cookie Tester",
        },
    )
    assert resp.status_code == 201, resp.text

    cookie_name = settings.auth_refresh_cookie_name
    assert cookie_name in resp.cookies, (
        f"Expected Set-Cookie '{cookie_name}' in response; "
        f"got cookies: {dict(resp.cookies)}"
    )

    set_cookie_header = _get_set_cookie_header(resp, cookie_name)
    assert set_cookie_header is not None, "Set-Cookie header not found in raw headers"
    header_lower = set_cookie_header.lower()
    assert "httponly" in header_lower, f"httpOnly flag missing: {set_cookie_header}"
    assert f"path={settings.auth_cookie_path}" in header_lower, (
        f"path attribute missing or wrong: {set_cookie_header}"
    )
    assert "max-age=" in header_lower, f"max-age missing: {set_cookie_header}"

    # refresh_token must NOT be in the JSON body.
    assert "refresh_token" not in resp.json()


@pytest.mark.asyncio
async def test_register_sets_csrf_cookie(client: AsyncClient) -> None:
    """POST /auth/register must set a non-httpOnly csrf_token cookie for JS to read."""
    resp = await client.post(
        "/auth/register",
        json={
            "email": _unique_email(),
            "password": "CookiePass1!",
            "full_name": "CSRF Cookie Tester",
        },
    )
    assert resp.status_code == 201, resp.text

    csrf_name = settings.auth_csrf_cookie_name
    set_cookie_header = _get_set_cookie_header(resp, csrf_name)
    assert set_cookie_header is not None, (
        f"Expected Set-Cookie '{csrf_name}' in response; "
        f"raw headers: {list(resp.headers.multi_items())}"
    )
    # Must NOT have httponly (JS must be able to read it).
    assert "httponly" not in set_cookie_header.lower(), (
        f"csrf_token cookie MUST NOT be httpOnly: {set_cookie_header}"
    )
    assert "max-age=" in set_cookie_header.lower(), (
        f"max-age missing from csrf_token cookie: {set_cookie_header}"
    )


@pytest.mark.asyncio
async def test_login_sets_httponly_cookie(client: AsyncClient) -> None:
    """POST /auth/login must set an httpOnly Set-Cookie for the refresh token."""
    email = _unique_email()
    pw = "CookieLogin99!"
    await client.post(
        "/auth/register",
        json={"email": email, "password": pw, "full_name": "Cookie Login"},
    )
    resp = await client.post("/auth/login", json={"email": email, "password": pw})
    assert resp.status_code == 200, resp.text

    cookie_name = settings.auth_refresh_cookie_name
    assert cookie_name in resp.cookies, (
        f"Expected Set-Cookie '{cookie_name}'; got: {dict(resp.cookies)}"
    )

    set_cookie_header = _get_set_cookie_header(resp, cookie_name)
    assert set_cookie_header is not None
    assert "httponly" in set_cookie_header.lower()

    # refresh_token must NOT be in the JSON body.
    assert "refresh_token" not in resp.json()


@pytest.mark.asyncio
async def test_refresh_via_cookie(client: AsyncClient) -> None:
    """POST /auth/refresh works when the refresh token comes from the cookie.

    Steps:
      1. Register — receive both cookies (refresh_token + csrf_token).
      2. Extract the csrf_token value from the Set-Cookie header.
      3. Call /auth/refresh with NO body, forwarding the cookie + X-CSRF-Token header.
      4. Assert 200, new access_token, new refresh_token cookie set.
      5. Verify the old refresh_token is now dead (rotation happened).
    """
    email = _unique_email()
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "CookieRefresh7!", "full_name": "Cookie Refresher"},
    )
    assert reg.status_code == 201, reg.text

    cookie_name = settings.auth_refresh_cookie_name
    assert cookie_name in reg.cookies
    old_rt = reg.cookies[cookie_name]

    # Extract the csrf_token from the Set-Cookie header (httponly=False, so the
    # cookie jar also carries it, but reading from header is explicit).
    csrf_value = _extract_csrf_from_response(reg)
    assert csrf_value, "Expected csrf_token cookie on register"

    # No body token — rely on cookie + X-CSRF-Token header.
    resp = await client.post(
        "/auth/refresh",
        json={},
        headers={"X-CSRF-Token": csrf_value},
    )
    assert resp.status_code == 200, (
        f"Expected 200 from cookie-based refresh; got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["access_token"], "access_token missing from refresh response"
    assert "refresh_token" not in body, "refresh_token must not be in response body"

    # New cookie must be set with the rotated token.
    assert cookie_name in resp.cookies
    new_rt = resp.cookies[cookie_name]
    assert new_rt != old_rt, "refresh_token was not rotated"

    # Old token is dead (rotation confirmed).
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0
    ) as fresh:
        resp_old = await fresh.post(
            "/auth/refresh", json={"refresh_token": old_rt}
        )
    assert resp_old.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_cookie(client: AsyncClient) -> None:
    """POST /auth/logout must emit a Set-Cookie that clears the refresh cookie."""
    email = _unique_email()
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "LogoutCookie8!", "full_name": "Cookie Logout"},
    )
    assert reg.status_code == 201, reg.text
    tokens = reg.json()
    access = tokens["access_token"]

    resp = await client.post(
        "/auth/logout",
        json={},  # token comes from cookie
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True

    cookie_name = settings.auth_refresh_cookie_name
    set_cookie_header = _get_set_cookie_header(resp, cookie_name)
    assert set_cookie_header is not None, (
        "Expected a Set-Cookie header on logout to clear the cookie; "
        f"raw headers: {list(resp.headers.multi_items())}"
    )
    header_lower = set_cookie_header.lower()
    assert "max-age=0" in header_lower or 'expires="' in header_lower, (
        f"Cookie clear signal (max-age=0 or past expires) missing: {set_cookie_header}"
    )


@pytest.mark.asyncio
async def test_logout_clears_csrf_cookie(client: AsyncClient) -> None:
    """POST /auth/logout must also emit a Set-Cookie that clears the csrf_token cookie."""
    email = _unique_email()
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "LogoutCsrf8!", "full_name": "CSRF Logout"},
    )
    assert reg.status_code == 201, reg.text
    access = reg.json()["access_token"]

    resp = await client.post(
        "/auth/logout",
        json={},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 200, resp.text

    csrf_name = settings.auth_csrf_cookie_name
    csrf_header = _get_set_cookie_header(resp, csrf_name)
    assert csrf_header is not None, (
        f"Expected Set-Cookie for '{csrf_name}' on logout; "
        f"raw headers: {list(resp.headers.multi_items())}"
    )
    assert "max-age=0" in csrf_header.lower() or 'expires="' in csrf_header.lower(), (
        f"csrf_token cookie clear signal missing: {csrf_header}"
    )
