"""Account TAKEOVER must fail closed when the previous owner's sessions survive.

Two paths take over an unverified, pre-registered password account for the
person who proved control of the address: ``POST /apply/activate`` and the Google
SSO callback. In both, the previous owner may be an attacker who registered the
victim's address first, so their sessions are the thing being evicted.

``AuthProvider.logout_all`` is best-effort BY DESIGN: it logs Redis errors and
returns normally. Wrapping it in ``try/except`` therefore cannot detect failure,
and the old code (which did exactly that, after the commit) left a pre-registered
attacker signed in beside the victim's new password. These tests pin the
replacement: ``revoke_all_sessions_strict`` raises, the revocation runs BEFORE the
commit, and a failure rolls the takeover back and answers 503.

The non-takeover path is deliberately unchanged and has its own test.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import USER_TOKEN_EPOCH_PREFIX

from app.session_revocation import SessionRevocationError, revoke_all_sessions_strict

UID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# The strict helper
# ---------------------------------------------------------------------------
class _Redis:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.setex_calls: list[tuple[str, int, int]] = []

    async def setex(self, key: str, ttl: int, value: int) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.setex_calls.append((key, ttl, value))


@pytest.mark.asyncio
async def test_strict_revocation_writes_the_epoch_and_purges() -> None:
    redis = _Redis()
    provider = MagicMock()
    provider.logout_all = AsyncMock(return_value=2)
    with (
        patch("app.session_revocation.get_redis", return_value=redis),
        patch("app.dependencies.get_auth_provider_dep", AsyncMock(return_value=provider)),
    ):
        await revoke_all_sessions_strict(UID)

    ((key, ttl, _value),) = redis.setex_calls
    assert key == USER_TOKEN_EPOCH_PREFIX + UID
    assert ttl > 7 * 86400  # outlives the refresh token, like logout_all's own epoch
    provider.logout_all.assert_awaited_once_with(UID)


@pytest.mark.asyncio
async def test_settle_lets_a_session_minted_straight_afterwards_survive() -> None:
    """The epoch has one-second resolution and the check is ``iat <= epoch``.

    Google sign-in revokes the previous owner and mints the new owner's session
    in the same request. Without ``settle`` the two land in the same second, the
    new token reads as revoked, and the person who just proved the address is
    theirs gets a 401 on their first request. Asserted through the REAL check,
    not a mock of it.
    """
    import time

    from shared.auth.jwt import is_token_revoked

    redis = _Redis()
    provider = MagicMock()
    provider.logout_all = AsyncMock(return_value=0)
    with (
        patch("app.session_revocation.get_redis", return_value=redis),
        patch("app.dependencies.get_auth_provider_dep", AsyncMock(return_value=provider)),
    ):
        await revoke_all_sessions_strict(UID, settle=True)
    minted_iat = int(time.time())

    ((_key, _ttl, epoch),) = redis.setex_calls

    class _Stored:
        async def get(self, _key: str) -> str:
            return str(epoch)

    assert minted_iat > epoch
    assert await is_token_revoked(lambda: _Stored(), UID, minted_iat) is False
    # ...while a token from the epoch second itself is still dead.
    assert await is_token_revoked(lambda: _Stored(), UID, epoch) is True


@pytest.mark.asyncio
async def test_strict_revocation_raises_when_the_epoch_cannot_be_written() -> None:
    """The case logout_all cannot report: it swallows this exact error."""
    with (
        patch("app.session_revocation.get_redis", return_value=_Redis(fail=True)),
        pytest.raises(SessionRevocationError),
    ):
        await revoke_all_sessions_strict(UID)


@pytest.mark.asyncio
async def test_strict_revocation_raises_when_redis_is_not_initialised() -> None:
    with (
        patch("app.session_revocation.get_redis", side_effect=RuntimeError("Redis not initialised")),
        pytest.raises(SessionRevocationError),
    ):
        await revoke_all_sessions_strict(UID)


@pytest.mark.asyncio
async def test_a_failed_refresh_purge_does_not_undo_a_written_epoch() -> None:
    """The epoch is the control (access AND refresh tokens check it); the purge is tidy-up."""
    redis = _Redis()
    provider = MagicMock()
    provider.logout_all = AsyncMock(side_effect=RuntimeError("purge failed"))
    with (
        patch("app.session_revocation.get_redis", return_value=redis),
        patch("app.dependencies.get_auth_provider_dep", AsyncMock(return_value=provider)),
    ):
        await revoke_all_sessions_strict(UID)  # does not raise
    assert len(redis.setex_calls) == 1


# ---------------------------------------------------------------------------
# POST /apply/activate
# ---------------------------------------------------------------------------
def _activate_body() -> Any:
    from app.routers.public_apply import ActivateIn

    return ActivateIn(token="t" * 32, new_password="Str0ng-Passw0rd!")


def _db_with_order(order: list[str]) -> MagicMock:
    db = MagicMock()

    async def _commit() -> None:
        order.append("commit")

    async def _rollback() -> None:
        order.append("rollback")

    db.commit = AsyncMock(side_effect=_commit)
    db.rollback = AsyncMock(side_effect=_rollback)
    return db


_TAKEOVER = {"email": "v@example.com", "linked": "existing",
             "revoke_sessions_for": UID, "password_replaced": "yes"}
_PLAIN = {"email": "v@example.com", "linked": "new"}


@pytest.mark.asyncio
async def test_activation_takeover_revokes_before_it_commits() -> None:
    from app.routers import public_apply

    order: list[str] = []

    async def _revoke(uid: str) -> None:
        order.append(f"revoke:{uid}")

    with (
        patch.object(public_apply, "activate", AsyncMock(return_value=dict(_TAKEOVER))),
        patch.object(public_apply, "revoke_all_sessions_strict", _revoke),
    ):
        out = await public_apply.activate_account(_activate_body(), _db_with_order(order))

    assert order == [f"revoke:{UID}", "commit"]
    assert out.linked == "existing"


@pytest.mark.asyncio
async def test_activation_takeover_fails_closed_when_revocation_fails() -> None:
    """5xx, nothing committed, and the rollback is explicit."""
    from app.routers import public_apply

    order: list[str] = []
    db = _db_with_order(order)
    with (
        patch.object(public_apply, "activate", AsyncMock(return_value=dict(_TAKEOVER))),
        patch.object(
            public_apply, "revoke_all_sessions_strict",
            AsyncMock(side_effect=SessionRevocationError("redis down")),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await public_apply.activate_account(_activate_body(), db)

    assert exc.value.status_code == 503
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited()
    assert "commit" not in order


@pytest.mark.asyncio
async def test_activation_without_takeover_does_not_touch_revocation() -> None:
    """Unchanged path: no revocation call, so a Redis outage cannot block it."""
    from app.routers import public_apply

    order: list[str] = []
    revoke = AsyncMock(side_effect=SessionRevocationError("must not be called"))
    with (
        patch.object(public_apply, "activate", AsyncMock(return_value=dict(_PLAIN))),
        patch.object(public_apply, "revoke_all_sessions_strict", revoke),
    ):
        out = await public_apply.activate_account(_activate_body(), _db_with_order(order))

    revoke.assert_not_awaited()
    assert order == ["commit"]
    assert out.linked == "new"


# ---------------------------------------------------------------------------
# Google SSO callback
# ---------------------------------------------------------------------------
from app.database import get_db_session  # noqa: E402
from app.redis_client import get_redis  # noqa: E402
from tests.integration.test_sso_google import (  # noqa: E402
    _CALLBACK_URL,
    _FAKE_GOOGLE_SUB,
    _FAKE_STATE,
    _STATE_COOKIE,
    _FakeDbSession,
    _FakeRedis,
    _make_redis_override,
    _override_db,
    _patch_settings,
    _seed_state,
    _test_app,
)


class _TrackedDb(_FakeDbSession):
    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self.order = order

    async def commit(self) -> None:
        self.order.append("commit")

    async def rollback(self) -> None:
        self.order.append("rollback")


@pytest_asyncio.fixture
async def client():  # type: ignore[no-untyped-def]
    async with AsyncClient(
        transport=ASGITransport(app=_test_app), base_url="http://test", follow_redirects=False
    ) as ac:
        yield ac


def _google_http() -> AsyncMock:
    token = MagicMock(status_code=200)
    token.json.return_value = {"access_token": "g-token"}
    info = MagicMock(status_code=200)
    info.json.return_value = {
        "sub": _FAKE_GOOGLE_SUB, "email": "victim@gmail.com", "name": "Victim",
        "email_verified": True,
    }
    http = AsyncMock()
    http.post = AsyncMock(return_value=token)
    http.get = AsyncMock(return_value=info)
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    return http


async def _run_callback(
    client: AsyncClient, order: list[str], *, taken_over: bool, revoke: Any
) -> tuple[Any, _FakeRedis]:
    fake_redis = _FakeRedis()
    client.cookies.set(_STATE_COOKIE, _seed_state(fake_redis))
    sess = _TrackedDb(order)
    _test_app.dependency_overrides[get_db_session] = _override_db(session=sess)
    _test_app.dependency_overrides[get_redis] = _make_redis_override(fake_redis)
    try:
        with (
            _patch_settings(),
            patch("app.routers.sso_google.httpx.AsyncClient", return_value=_google_http()),
            patch(
                "app.routers.sso_google._upsert_google_user",
                AsyncMock(return_value=(uuid.UUID(UID), taken_over)),
            ),
            patch("app.routers.sso_google.revoke_all_sessions_strict", revoke),
        ):
            resp = await client.get(_CALLBACK_URL, params={"code": "c", "state": _FAKE_STATE})
    finally:
        _test_app.dependency_overrides.clear()
    return resp, fake_redis


@pytest.mark.asyncio
async def test_google_takeover_revokes_before_commit_and_before_minting_a_session(
    client: AsyncClient,
) -> None:
    order: list[str] = []

    settled: list[bool] = []

    async def _revoke(uid: str, *, settle: bool = False) -> None:
        order.append(f"revoke:{uid}")
        settled.append(settle)

    resp, redis = await _run_callback(client, order, taken_over=True, revoke=_revoke)

    assert resp.status_code == 200, resp.text
    assert order == [f"revoke:{UID}", "commit"]
    # A session is minted right after, so it must not share the epoch's second.
    assert settled == [True]
    assert any(k.startswith("refresh:") for k in redis._store)  # the NEW session exists


@pytest.mark.asyncio
async def test_google_takeover_fails_closed_when_revocation_fails(client: AsyncClient) -> None:
    order: list[str] = []
    resp, redis = await _run_callback(
        client, order, taken_over=True,
        revoke=AsyncMock(side_effect=SessionRevocationError("redis down")),
    )

    assert resp.status_code == 503, resp.text
    assert "commit" not in order and "rollback" in order
    assert "access_token" not in resp.text
    assert "refresh_token" not in resp.cookies
    assert not any(k.startswith("refresh:") for k in redis._store)  # no session minted


@pytest.mark.asyncio
async def test_google_sign_in_without_takeover_is_unchanged(client: AsyncClient) -> None:
    order: list[str] = []
    revoke = AsyncMock(side_effect=SessionRevocationError("must not be called"))
    resp, _ = await _run_callback(client, order, taken_over=False, revoke=revoke)

    assert resp.status_code == 200, resp.text
    revoke.assert_not_awaited()
    assert order == ["commit"]
