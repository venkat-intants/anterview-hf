"""Unit tests for the logout_all TOCTOU race fix in LocalAuthProvider.

These tests use mocked Redis (no live server required) and prove that:

1. logout_all writes the epoch BEFORE purging sessions.
2. refresh() after logout_all yields no usable token (epoch check rejects).
3. refresh() DURING logout_all (between epoch-write and session-purge) also
   yields no usable token — the epoch is already present when refresh() checks.
4. A refresh token issued AFTER logout_all (new login) is NOT blocked.
5. Legacy tokens (no created_at in Redis value) skip the epoch check (fail-open).
6. Redis read errors during epoch check in refresh() are fail-open.
7. _parse_rt_value handles both legacy and current Redis value formats.
8. logout() ownership check correctly parses the new value format.
9. mint_refresh_session writes the tracked format and session index (AUTH-01).
10. An SSO-minted session (via mint_refresh_session) is rejected after logout_all.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.auth.local import (
    USER_TOKEN_EPOCH_PREFIX,
    LocalAuthProvider,
    _RT_PREFIX,
    _SESSIONS_PREFIX,
    mint_refresh_session,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_settings(
    *,
    jwt_refresh_expiry_days: int = 30,
    password_hash_rounds: int = 4,
    jwt_secret: str = "test-secret-12345",
    jwt_algorithm: str = "HS256",
) -> MagicMock:
    s = MagicMock()
    s.jwt_refresh_expiry_days = jwt_refresh_expiry_days
    s.password_hash_rounds = password_hash_rounds
    s.jwt_secret = jwt_secret
    s.jwt_algorithm = jwt_algorithm
    s.jwt_issuer = "intants-data-gateway"
    s.jwt_audience = "intants-services"
    return s


def _make_redis(
    *,
    get_return: str | None = None,
    smembers_return: set[bytes] | None = None,
) -> AsyncMock:
    """Return a minimal AsyncMock Redis client."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=get_return)
    redis.setex = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.sadd = AsyncMock(return_value=1)
    redis.srem = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.smembers = AsyncMock(return_value=smembers_return or set())
    return redis


async def _empty_db_factory() -> AsyncGenerator[Any, None]:
    """Async generator that yields nothing — stands in for a DB session factory
    in tests that do not exercise DB queries."""
    return
    yield  # makes this an async generator


def _make_provider(redis: AsyncMock) -> LocalAuthProvider:
    return LocalAuthProvider(
        db_session_factory=lambda: _empty_db_factory(),
        redis_client=redis,
        settings=_make_settings(),
    )


# ---------------------------------------------------------------------------
# _parse_rt_value — pure static method, no I/O
# ---------------------------------------------------------------------------


class TestParseRtValue:
    def test_current_format_parses_user_id_and_timestamp(self) -> None:
        uid, ts = LocalAuthProvider._parse_rt_value("user-uuid-123:1719820000")
        assert uid == "user-uuid-123"
        assert ts == 1719820000

    def test_legacy_format_no_colon_returns_none_timestamp(self) -> None:
        """A plain UUID (no colon) is treated as a legacy token."""
        uid, ts = LocalAuthProvider._parse_rt_value("550e8400-e29b-41d4-a716-446655440000")
        # Dashes in UUID are NOT colons — the value has no colon → legacy path.
        assert uid == "550e8400-e29b-41d4-a716-446655440000"
        assert ts is None

    def test_malformed_timestamp_returns_none(self) -> None:
        uid, ts = LocalAuthProvider._parse_rt_value("user-uuid:not-a-number")
        assert uid == "user-uuid"
        assert ts is None

    def test_empty_timestamp_part_returns_none(self) -> None:
        uid, ts = LocalAuthProvider._parse_rt_value("user-uuid:")
        assert uid == "user-uuid"
        assert ts is None

    def test_zero_timestamp_is_valid(self) -> None:
        uid, ts = LocalAuthProvider._parse_rt_value("user-uuid:0")
        assert uid == "user-uuid"
        assert ts == 0


# ---------------------------------------------------------------------------
# logout_all — epoch written BEFORE session purge
# ---------------------------------------------------------------------------


class TestLogoutAllOrdering:
    @pytest.mark.asyncio
    async def test_epoch_written_before_smembers(self) -> None:
        """logout_all must call setex (epoch) before smembers (session read)."""
        call_order: list[str] = []
        redis = AsyncMock()
        redis.smembers = AsyncMock(
            side_effect=lambda k: call_order.append("smembers") or set()
        )
        redis.setex = AsyncMock(
            side_effect=lambda *a, **kw: call_order.append("setex")
        )
        redis.delete = AsyncMock(return_value=0)

        provider = _make_provider(redis)
        await provider.logout_all("user-1")

        assert "setex" in call_order, "setex (epoch write) was never called"
        assert "smembers" in call_order, "smembers was never called"
        assert call_order.index("setex") < call_order.index("smembers"), (
            "Epoch must be written BEFORE the session set is read. "
            f"Actual call order: {call_order}"
        )

    @pytest.mark.asyncio
    async def test_epoch_key_is_correct(self) -> None:
        """logout_all must write the epoch under auth_epoch:<user_id>."""
        user_id = "user-abc-123"
        redis = _make_redis()
        provider = _make_provider(redis)
        await provider.logout_all(user_id)

        redis.setex.assert_called_once()
        args = redis.setex.call_args[0]
        assert args[0] == USER_TOKEN_EPOCH_PREFIX + user_id

    @pytest.mark.asyncio
    async def test_logout_all_purges_all_session_keys(self) -> None:
        """logout_all must delete every refresh-token key in the session index."""
        user_id = "user-xyz"
        sess_key = _SESSIONS_PREFIX + user_id
        rt_key_1 = "refresh:aaa"
        rt_key_2 = "refresh:bbb"
        redis = _make_redis(smembers_return={rt_key_1.encode(), rt_key_2.encode()})
        provider = _make_provider(redis)

        revoked = await provider.logout_all(user_id)
        assert revoked == 2

        # delete must have been called with both keys
        all_delete_calls = [c[0] for c in redis.delete.call_args_list]
        deleted_keys = {k for args in all_delete_calls for k in args}
        assert rt_key_1 in deleted_keys
        assert rt_key_2 in deleted_keys
        assert sess_key in deleted_keys

    @pytest.mark.asyncio
    async def test_logout_all_proceeds_even_if_epoch_write_fails(self) -> None:
        """If the epoch write fails, logout_all should still purge refresh tokens."""
        user_id = "user-failepoch"
        rt_key = b"refresh:deadbeef"
        redis = _make_redis(smembers_return={rt_key})
        redis.setex = AsyncMock(side_effect=ConnectionError("Redis down"))
        provider = _make_provider(redis)

        # Must not raise
        revoked = await provider.logout_all(user_id)
        assert revoked == 1

        # The refresh key and session index should still be deleted
        deleted = {
            k
            for call in redis.delete.call_args_list
            for k in call[0]
        }
        assert rt_key.decode() in deleted

    @pytest.mark.asyncio
    async def test_logout_all_returns_0_when_no_sessions(self) -> None:
        redis = _make_redis(smembers_return=set())
        provider = _make_provider(redis)
        revoked = await provider.logout_all("user-nosess")
        assert revoked == 0


# ---------------------------------------------------------------------------
# _is_session_revoked_by_epoch
# ---------------------------------------------------------------------------


class TestIsSessionRevokedByEpoch:
    @pytest.mark.asyncio
    async def test_no_epoch_in_redis_returns_false(self) -> None:
        redis = _make_redis(get_return=None)
        provider = _make_provider(redis)
        result = await provider._is_session_revoked_by_epoch("uid", 1000)
        assert result is False

    @pytest.mark.asyncio
    async def test_created_at_before_epoch_returns_true(self) -> None:
        redis = _make_redis(get_return="2000")  # epoch = 2000
        provider = _make_provider(redis)
        result = await provider._is_session_revoked_by_epoch("uid", 1999)
        assert result is True

    @pytest.mark.asyncio
    async def test_created_at_equal_to_epoch_returns_true(self) -> None:
        """Equal means the session was issued exactly at logout_all time — treat as revoked."""
        redis = _make_redis(get_return="2000")
        provider = _make_provider(redis)
        result = await provider._is_session_revoked_by_epoch("uid", 2000)
        assert result is True

    @pytest.mark.asyncio
    async def test_created_at_after_epoch_returns_false(self) -> None:
        """A session created AFTER logout_all is a valid new session."""
        redis = _make_redis(get_return="2000")
        provider = _make_provider(redis)
        result = await provider._is_session_revoked_by_epoch("uid", 2001)
        assert result is False

    @pytest.mark.asyncio
    async def test_none_created_at_skips_check_returns_false(self) -> None:
        """Legacy tokens (no timestamp) are fail-open — epoch check skipped."""
        redis = _make_redis(get_return="2000")
        provider = _make_provider(redis)
        result = await provider._is_session_revoked_by_epoch("uid", None)
        assert result is False
        # Redis.get should NOT have been called (early return before any I/O)
        redis.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_read_error_fails_open(self) -> None:
        """A Redis error during epoch read must not block a valid refresh — fail-open."""
        redis = _make_redis()
        redis.get = AsyncMock(side_effect=ConnectionError("Redis unreachable"))
        provider = _make_provider(redis)
        result = await provider._is_session_revoked_by_epoch("uid", 1000)
        assert result is False

    @pytest.mark.asyncio
    async def test_malformed_epoch_in_redis_returns_false(self) -> None:
        """If the epoch value in Redis is not a valid integer, fail-open."""
        redis = _make_redis(get_return="not-a-number")
        provider = _make_provider(redis)
        result = await provider._is_session_revoked_by_epoch("uid", 1000)
        assert result is False


# ---------------------------------------------------------------------------
# refresh() — epoch check integration
# ---------------------------------------------------------------------------


class TestRefreshEpochCheck:
    @pytest.mark.asyncio
    async def test_refresh_rejected_when_session_predates_epoch(self) -> None:
        """refresh() must raise ValueError when the stored created_at <= epoch."""
        user_id = "user-test-1"
        created_at = 1000
        epoch = 1500  # logout_all was called at t=1500

        redis = AsyncMock()
        # First get() call returns the refresh token value
        # Second get() call (inside _is_session_revoked_by_epoch) returns the epoch
        redis.get = AsyncMock(
            side_effect=[
                f"{user_id}:{created_at}",  # refresh token value
                str(epoch),                  # epoch
            ]
        )
        redis.delete = AsyncMock(return_value=1)
        redis.srem = AsyncMock(return_value=1)

        provider = _make_provider(redis)
        with pytest.raises(ValueError, match="invalid or expired"):
            await provider.refresh("some-refresh-token")

    @pytest.mark.asyncio
    async def test_refresh_rejected_when_created_at_equals_epoch(self) -> None:
        """Edge case: session created exactly at the epoch boundary — revoked."""
        user_id = "user-edge"
        ts = 2000
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=[f"{user_id}:{ts}", str(ts)])
        redis.delete = AsyncMock(return_value=1)
        redis.srem = AsyncMock(return_value=1)

        provider = _make_provider(redis)
        with pytest.raises(ValueError, match="invalid or expired"):
            await provider.refresh("token-x")

    @pytest.mark.asyncio
    async def test_refresh_allowed_when_session_postdates_epoch(self) -> None:
        """A session created AFTER logout_all must proceed through refresh normally."""
        user_id = "user-new-login"
        created_at = 3000  # new login happened at t=3000
        epoch = 2000       # logout_all was at t=2000
        raw_rt_value = f"{user_id}:{created_at}"

        redis = AsyncMock()
        redis.get = AsyncMock(
            side_effect=[
                raw_rt_value,  # refresh token lookup
                str(epoch),    # epoch lookup
            ]
        )
        redis.setex = AsyncMock(return_value=True)
        redis.delete = AsyncMock(return_value=1)
        redis.sadd = AsyncMock(return_value=1)
        redis.srem = AsyncMock(return_value=1)
        redis.expire = AsyncMock(return_value=True)

        # Mock DB to return roles
        async def _db_factory_with_roles() -> AsyncGenerator[Any, None]:
            session = AsyncMock()
            result = MagicMock()
            result.fetchall.return_value = [("candidate",)]
            session.execute = AsyncMock(return_value=result)
            yield session

        provider = LocalAuthProvider(
            db_session_factory=lambda: _db_factory_with_roles(),
            redis_client=redis,
            settings=_make_settings(),
        )

        tokens = await provider.refresh("new-refresh-token")
        assert tokens.user_id == user_id
        assert "candidate" in tokens.roles

    @pytest.mark.asyncio
    async def test_refresh_fails_open_on_epoch_redis_error(self) -> None:
        """If Redis raises when reading the epoch, refresh() must NOT block the caller."""
        user_id = "user-redis-err"
        created_at = 1000
        raw_rt_value = f"{user_id}:{created_at}"

        get_call_count = 0

        async def _get_side_effect(key: str) -> str | None:
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return raw_rt_value   # refresh token value
            raise ConnectionError("Redis blip")   # epoch read fails

        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=_get_side_effect)
        redis.setex = AsyncMock(return_value=True)
        redis.delete = AsyncMock(return_value=1)
        redis.sadd = AsyncMock(return_value=1)
        redis.srem = AsyncMock(return_value=1)
        redis.expire = AsyncMock(return_value=True)

        async def _db_factory_with_roles() -> AsyncGenerator[Any, None]:
            session = AsyncMock()
            result = MagicMock()
            result.fetchall.return_value = [("candidate",)]
            session.execute = AsyncMock(return_value=result)
            yield session

        provider = LocalAuthProvider(
            db_session_factory=lambda: _db_factory_with_roles(),
            redis_client=redis,
            settings=_make_settings(),
        )

        # Must NOT raise — fail-open means the refresh goes through
        tokens = await provider.refresh("some-token")
        assert tokens.user_id == user_id

    @pytest.mark.asyncio
    async def test_refresh_invalid_token_still_rejected(self) -> None:
        """refresh() must still reject tokens not found in Redis (unchanged behaviour)."""
        redis = _make_redis(get_return=None)
        provider = _make_provider(redis)
        with pytest.raises(ValueError, match="invalid or expired"):
            await provider.refresh("garbage-token")


# ---------------------------------------------------------------------------
# Race scenario: refresh() races logout_all()
# ---------------------------------------------------------------------------


class TestLogoutAllRefreshRace:
    @pytest.mark.asyncio
    async def test_refresh_racing_logout_all_produces_no_usable_token(self) -> None:
        """Simulate the TOCTOU race scenario end-to-end with ordered mock calls.

        Scenario:
          - A refresh token was issued at t=1000.
          - logout_all is called at t=1500 and writes epoch=1500 first.
          - Concurrently, refresh() reads the token value (still in Redis because
            logout_all hasn't deleted it yet) then checks the epoch.
          - Because epoch(1500) >= created_at(1000), refresh() must raise.
        """
        user_id = "user-race"
        created_at = 1000
        epoch = 1500
        raw_rt_value = f"{user_id}:{created_at}"

        # Simulate: refresh token key still exists (logout_all hasn't deleted it yet)
        # but epoch is already written.
        redis = AsyncMock()
        redis.get = AsyncMock(
            side_effect=[
                raw_rt_value,  # call 1: refresh token lookup — key still exists
                str(epoch),    # call 2: epoch lookup — epoch already written
            ]
        )
        redis.delete = AsyncMock(return_value=1)
        redis.srem = AsyncMock(return_value=1)

        provider = _make_provider(redis)
        with pytest.raises(ValueError, match="invalid or expired"):
            await provider.refresh("racing-token")

    @pytest.mark.asyncio
    async def test_new_token_after_logout_all_is_not_blocked(self) -> None:
        """A token issued AFTER logout_all (new login/re-auth) must work.

        The epoch is set at t=epoch.  A new token issued at t=epoch+1 has
        created_at > epoch so refresh() must allow it through.
        """
        user_id = "user-relogin"
        epoch = 1500
        created_at_after = 1501  # issued after the logout_all epoch
        raw_rt_value = f"{user_id}:{created_at_after}"

        redis = AsyncMock()
        redis.get = AsyncMock(
            side_effect=[
                raw_rt_value,  # refresh token value
                str(epoch),    # epoch
            ]
        )
        redis.setex = AsyncMock(return_value=True)
        redis.delete = AsyncMock(return_value=1)
        redis.sadd = AsyncMock(return_value=1)
        redis.srem = AsyncMock(return_value=1)
        redis.expire = AsyncMock(return_value=True)

        async def _db_factory() -> AsyncGenerator[Any, None]:
            session = AsyncMock()
            result = MagicMock()
            result.fetchall.return_value = [("candidate",)]
            session.execute = AsyncMock(return_value=result)
            yield session

        provider = LocalAuthProvider(
            db_session_factory=lambda: _db_factory(),
            redis_client=redis,
            settings=_make_settings(),
        )

        tokens = await provider.refresh("valid-post-logout-token")
        assert tokens.user_id == user_id


# ---------------------------------------------------------------------------
# logout() — ownership check still works with new value format
# ---------------------------------------------------------------------------


class TestLogoutOwnershipCheckNewFormat:
    @pytest.mark.asyncio
    async def test_logout_rejects_cross_user_with_new_format(self) -> None:
        """logout() must extract user_id correctly from new '<uid>:<ts>' format."""
        owner_uid = "user-owner"
        caller_uid = "user-attacker"
        stored_value = f"{owner_uid}:1000"  # new format

        redis = _make_redis(get_return=stored_value)
        provider = _make_provider(redis)

        # Attacker tries to pass the owner's refresh token during their own logout
        await provider.logout(
            refresh_token="owner-token",
            current_user_id=caller_uid,
        )

        # delete must NOT have been called — ownership check rejected the attempt
        redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_logout_accepts_own_token_with_new_format(self) -> None:
        """logout() must allow deletion when caller matches stored user_id."""
        owner_uid = "user-owner"
        stored_value = f"{owner_uid}:1000"

        redis = _make_redis(get_return=stored_value)
        provider = _make_provider(redis)

        await provider.logout(
            refresh_token="owner-token",
            current_user_id=owner_uid,
        )

        redis.delete.assert_called_once()


# ---------------------------------------------------------------------------
# AUTH-01: mint_refresh_session — shared SSO token mint path
# ---------------------------------------------------------------------------


class TestMintRefreshSession:
    """Verify that mint_refresh_session always writes the tracked format.

    These tests use an in-memory dict-backed fake Redis so we can inspect the
    exact bytes stored, without importing any network code.
    """

    @pytest.mark.asyncio
    async def test_writes_tracked_format_to_redis(self) -> None:
        """mint_refresh_session must store '<user_id>:<created_at_unix>' in Redis."""
        store: dict[str, str] = {}

        class _FakeRedis:
            async def setex(self, key: str, ttl: int, value: str) -> None:  # noqa: ARG002
                store[key] = value

            async def sadd(self, key: str, member: str) -> int:  # noqa: ARG002
                return 1

            async def expire(self, key: str, ttl: int) -> bool:  # noqa: ARG002
                return True

        user_id = "sso-user-uuid-abc"
        raw = await mint_refresh_session(_FakeRedis(), user_id, ttl_seconds=604800)  # type: ignore[arg-type]

        assert raw, "raw refresh token must be non-empty"
        assert len(store) == 1, "exactly one Redis key must be written"
        stored_value = next(iter(store.values()))
        assert stored_value.startswith(f"{user_id}:"), (
            f"Redis value must start with '<user_id>:' — got {stored_value!r}"
        )
        ts_part = stored_value[len(user_id) + 1:]
        assert ts_part.isdigit(), f"created_at part must be a digit string — got {ts_part!r}"

    @pytest.mark.asyncio
    async def test_adds_key_to_session_index(self) -> None:
        """mint_refresh_session must SADD the refresh key to user_sessions:<uid>."""
        sadd_calls: list[tuple[str, str]] = []
        stored: dict[str, str] = {}

        class _FakeRedis:
            async def setex(self, key: str, ttl: int, value: str) -> None:  # noqa: ARG002
                stored[key] = value

            async def sadd(self, key: str, member: str) -> int:
                sadd_calls.append((key, member))
                return 1

            async def expire(self, key: str, ttl: int) -> bool:  # noqa: ARG002
                return True

        user_id = "sso-user-index-test"
        await mint_refresh_session(_FakeRedis(), user_id, ttl_seconds=86400)  # type: ignore[arg-type]

        assert len(sadd_calls) == 1
        index_key, member = sadd_calls[0]
        assert index_key == _SESSIONS_PREFIX + user_id
        # The member added to the index must be the same refresh: key written to Redis.
        assert member in stored
        assert member.startswith(_RT_PREFIX)

    @pytest.mark.asyncio
    async def test_sso_session_rejected_by_refresh_after_logout_all(self) -> None:
        """An SSO session minted via mint_refresh_session must be revoked by logout_all.

        This is the primary regression test for AUTH-01.

        Scenario
        --------
        1. mint_refresh_session() simulates Google/Naipunyam SSO sign-in.
        2. logout_all() is called for the user (e.g. admin deletes the account).
        3. refresh() with the SSO-minted token must raise ValueError — the session
           is rejected by the epoch check because the token's created_at <= epoch.

        Before the fix, the SSO router wrote plain ``<user_id>`` (no timestamp),
        which caused _parse_rt_value to return (user_id, None), skipping the epoch
        check entirely (fail-open) and allowing the session to survive revocation.
        """
        user_id = "sso-candidate-auth01"
        # In-memory fake Redis with real dict storage so logout_all can read back
        # what mint_refresh_session wrote.
        store: dict[str, str] = {}
        sets: dict[str, set[str]] = {}
        expiries: dict[str, int] = {}

        class _FullFakeRedis:
            async def setex(self, key: str, ttl: int, value: str) -> None:
                store[key] = value
                expiries[key] = ttl

            async def get(self, key: str) -> str | None:
                return store.get(key)

            async def delete(self, *keys: str) -> int:
                deleted = 0
                for k in keys:
                    if store.pop(k, None) is not None:
                        deleted += 1
                return deleted

            async def sadd(self, key: str, member: str) -> int:
                sets.setdefault(key, set()).add(member)
                return 1

            async def srem(self, key: str, member: str) -> int:
                sets.get(key, set()).discard(member)
                return 1

            async def expire(self, key: str, ttl: int) -> bool:
                expiries[key] = ttl
                return True

            async def smembers(self, key: str) -> set[bytes]:
                return {m.encode() for m in sets.get(key, set())}

        fake_redis = _FullFakeRedis()

        # Step 1: SSO sign-in — mint a tracked refresh token (AUTH-01 fix path).
        raw_token = await mint_refresh_session(fake_redis, user_id, ttl_seconds=604800)  # type: ignore[arg-type]

        # Confirm the token is tracked in the session index.
        assert _SESSIONS_PREFIX + user_id in sets
        session_index = sets[_SESSIONS_PREFIX + user_id]
        assert len(session_index) == 1

        # Confirm the stored value is in the new tracked format.
        refresh_key = next(iter(session_index))
        stored_value = store[refresh_key]
        assert ":" in stored_value, (
            "AUTH-01 REGRESSION: SSO token stored in legacy plain format — "
            f"got {stored_value!r}, expected '<user_id>:<created_at>'"
        )

        # Step 2: logout_all() called (e.g. HR deletes the candidate account).
        async def _empty_db() -> AsyncGenerator[Any, None]:
            return
            yield

        provider = LocalAuthProvider(
            db_session_factory=lambda: _empty_db(),
            redis_client=fake_redis,  # type: ignore[arg-type]
            settings=_make_settings(),
        )
        revoked = await provider.logout_all(user_id)
        assert revoked == 1, "logout_all must report 1 session revoked"

        # Step 3: attempt to use the SSO refresh token after logout_all.
        # Must raise — the epoch check sees created_at <= epoch.
        with pytest.raises(ValueError, match="invalid or expired"):
            await provider.refresh(raw_token)

    @pytest.mark.asyncio
    async def test_session_index_failure_does_not_block_token_mint(self) -> None:
        """mint_refresh_session must succeed even if the session-index SADD fails."""
        store: dict[str, str] = {}

        class _FailingIndexRedis:
            async def setex(self, key: str, ttl: int, value: str) -> None:  # noqa: ARG002
                store[key] = value

            async def sadd(self, key: str, member: str) -> int:  # noqa: ARG002
                raise ConnectionError("Redis index unavailable")

            async def expire(self, key: str, ttl: int) -> bool:  # noqa: ARG002
                return True

        # Must not raise — sadd failure is best-effort.
        raw = await mint_refresh_session(_FailingIndexRedis(), "user-fail-index", ttl_seconds=3600)  # type: ignore[arg-type]
        assert raw, "raw token must still be returned even when session index fails"
        assert len(store) == 1, "the refresh:hash key must still be written"
