"""The revocation epoch: one implementation, and proof every verifier uses it.

Background. The "log out all devices" kill switch works by writing
``auth_epoch:<uid>`` = now, so any access token with an older ``iat`` is refused
before its 15-minute TTL runs out. Password reset, admin account deletion and
DPDP erasure all depend on it.

That check existed in FIVE places, each with its own copy of the Redis key
literal held in sync by a comment saying "do not change". One of them drifted:
``feedback_billing``'s scorecard list shipped without the check at all, so
logging out everywhere did not revoke access to scorecard history until 40df357.

The consolidation removes the copies. This file covers the two things the
consolidation alone does not:

  1. ``is_token_revoked`` behaves correctly at every boundary (below).
  2. Every verifier actually calls it — see
     ``services/*/tests/**/test_revocation_enforced.py``. That is the test that
     would have caught the original drift, and it is the one that catches the
     sixth copy someone adds next quarter.
"""

from __future__ import annotations

from typing import Any

import pytest

from shared.auth.jwt import USER_TOKEN_EPOCH_PREFIX, is_token_revoked


class _FakeRedis:
    """Minimal async Redis stub. Structural typing means no client import."""

    def __init__(self, store: dict[str, Any] | None = None) -> None:
        self.store = store or {}
        self.gets: list[str] = []

    async def get(self, key: str) -> Any:
        self.gets.append(key)
        return self.store.get(key)


class _BrokenRedis:
    async def get(self, key: str) -> Any:
        raise ConnectionError("upstash unreachable")


# ---------------------------------------------------------------------------
# Key shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reads_the_canonical_key() -> None:
    """The prefix is the contract between the writer (logout_all, erasure) and
    every reader. A typo in either half silently disables revocation."""
    redis = _FakeRedis()
    await is_token_revoked(lambda: redis, "user-1", 1000)
    assert redis.gets == ["auth_epoch:user-1"]
    assert USER_TOKEN_EPOCH_PREFIX == "auth_epoch:"


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_epoch_means_not_revoked() -> None:
    redis = _FakeRedis()
    assert await is_token_revoked(lambda: redis, "u", 1000) is False
    # Prove it actually consulted Redis. Passing a non-callable here would also
    # return False — via TypeError into the fail-open branch — so without this
    # assertion the test passes for entirely the wrong reason.
    assert redis.gets == ["auth_epoch:u"]


@pytest.mark.asyncio
async def test_token_older_than_epoch_is_revoked() -> None:
    redis = _FakeRedis({"auth_epoch:u": "2000"})
    assert await is_token_revoked(lambda: redis, "u", 1999) is True


@pytest.mark.asyncio
async def test_token_newer_than_epoch_survives() -> None:
    redis = _FakeRedis({"auth_epoch:u": "2000"})
    assert await is_token_revoked(lambda: redis, "u", 2001) is False


@pytest.mark.asyncio
async def test_same_second_as_the_revocation_is_revoked() -> None:
    """`<=`, not `<`.

    Both are whole-second Unix timestamps and logout_all sets epoch = now(), so
    a token minted in the SAME second as the revocation has iat == epoch. Under
    a strict `<` it survived its full 15-minute TTL immediately after the user
    asked to be logged out everywhere.
    """
    redis = _FakeRedis({"auth_epoch:u": "2000"})
    assert await is_token_revoked(lambda: redis, "u", 2000) is True


@pytest.mark.asyncio
async def test_missing_iat_is_treated_as_revoked() -> None:
    """A token with no `iat` cannot be compared against the epoch.

    Treating that as "can't tell, allow it" made such a token permanently
    unrevocable. verify_access_token now sets require_iat, so this is defence in
    depth — but it is the half that decides what happens if the other half ever
    regresses.
    """
    redis = _FakeRedis({"auth_epoch:u": "2000"})
    assert await is_token_revoked(lambda: redis, "u", None) is True


@pytest.mark.asyncio
async def test_non_numeric_iat_is_treated_as_revoked() -> None:
    redis = _FakeRedis({"auth_epoch:u": "2000"})
    assert await is_token_revoked(lambda: redis, "u", "not-a-number") is True


@pytest.mark.asyncio
async def test_bytes_epoch_from_redis_is_handled() -> None:
    """Some clients return bytes rather than str depending on decode_responses."""
    redis = _FakeRedis({"auth_epoch:u": b"2000"})
    assert await is_token_revoked(lambda: redis, "u", 1999) is True


# ---------------------------------------------------------------------------
# Fail-open — deliberate, and load-bearing for availability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_error_fails_open() -> None:
    """A cache outage must not lock every user out of the platform.

    The real auth control is the signature and `exp`, both verified locally with
    no Redis involved; the epoch only accelerates revocation. Failing closed
    would make Upstash availability equal platform availability, turning a
    bounded 15-minute revocation delay into a total outage.

    If you are changing this to fail closed, that is a deliberate posture change
    — not a cleanup. Read the reasoning in is_token_revoked first.
    """
    assert await is_token_revoked(lambda: _BrokenRedis(), "u", 1000) is False


@pytest.mark.asyncio
async def test_a_factory_that_itself_raises_fails_open() -> None:
    """The regression this signature exists to prevent.

    Every service's ``get_redis()`` raises RuntimeError("Redis not initialised")
    when the app has no lifespan, and the local copies of this check all called
    it INSIDE their try block — so that error was part of what fail-open
    absorbed. An earlier version of this helper took an already-resolved client,
    which moved the call outside the protected region and turned three services'
    auth into a 500 on every request.
    """

    def _uninitialised() -> Any:
        raise RuntimeError("Redis not initialised. Call init_redis() first.")

    assert await is_token_revoked(_uninitialised, "u", 1000) is False


@pytest.mark.asyncio
async def test_corrupt_epoch_value_fails_open() -> None:
    """A garbage epoch must not permanently lock a user out of their account."""
    redis = _FakeRedis({"auth_epoch:u": "not-an-int"})
    assert await is_token_revoked(lambda: redis, "u", 1000) is False
