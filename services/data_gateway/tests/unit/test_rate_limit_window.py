"""``app.rate_limit.rate_limit_window`` — the sustained cap.

WHY THIS FILE EXISTS. This limiter was added as the compensating control for a
deliberate change: `submit_application` now stores the CV BEFORE it consults
the reapplication gate, so that the work the endpoint does cannot be timed to
learn whether an address has applied here and been turned down. The cost of
that ordering is that an anonymous caller can make the service write an object
it immediately deletes, and the stated bound on that cost is this function.

It shipped with no tests at all. On a branch whose recurring defect is a fix
that is claimed, passes its suite, and does nothing, an untested compensating
control is the shape of the next one.

Exercises the dependency directly with a fake Redis, like
``test_rate_limit_company``: no real Redis, no HTTP, no auth.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.rate_limit import rate_limit, rate_limit_window


class _FakeRedis:
    """Enough of redis-py's async interface, and it REMEMBERS the TTL.

    `expiries` is the point of the fake: the bug this guards against is a key
    that ends up without one.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.counts: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self.fail = fail
        #: The command sequence, which is the only thing that tells the atomic
        #: form apart from the one it replaced. See the ordering test.
        self.calls: list[str] = []

    async def set(self, key: str, value: int, *, ex: int, nx: bool) -> bool:
        if self.fail:
            raise ConnectionError("redis is down")
        self.calls.append("set")
        if nx and key in self.counts:
            return False
        self.counts[key] = value
        self.expiries[key] = ex
        return True

    async def incr(self, key: str) -> int:
        if self.fail:
            raise ConnectionError("redis is down")
        self.calls.append("incr")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        if self.fail:
            raise ConnectionError("redis is down")
        self.calls.append("expire")
        self.expiries[key] = seconds


class _Req:
    """A Request as far as `extract_client_ip` is concerned."""

    def __init__(self, ip: str = "203.0.113.9") -> None:
        self.headers: dict[str, str] = {}
        self.client = type("C", (), {"host": ip})()


def _dep(bucket: str, limit: int, window: int) -> Any:
    return rate_limit_window(bucket, limit, window).dependency


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_it_allows_up_to_the_cap_and_refuses_the_next_one(
    redis: _FakeRedis,
) -> None:
    dep = _dep("b", 3, 3600)
    for _ in range(3):
        await dep(_Req())  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc:
        await dep(_Req())  # type: ignore[arg-type]
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_the_ttl_is_the_window_and_it_is_set_with_the_counter(
    redis: _FakeRedis,
) -> None:
    """The bug this exists for.

    `incr` then `expire` on the first hit is two commands, and anything that
    interrupts between them leaves a key with NO expiry — after which that
    address is refused for ever, because the counter never resets. At a
    3600-second window the key is long-lived by design, so a stranded one is
    both likelier to happen and far worse than it would be at sixty seconds.

    Asserting the TTL is 3600 also pins the thing a copy-paste from the
    per-minute limiter would get wrong: a hard-coded 60.
    """
    dep = _dep("hourly", 5, 3600)
    await dep(_Req())  # type: ignore[arg-type]

    assert redis.expiries == {"rl:hourly:203.0.113.9": 3600}

    # And it is not pushed out by later requests, or the window would slide
    # forward for as long as somebody keeps calling and never reset.
    await dep(_Req())  # type: ignore[arg-type]
    assert redis.expiries == {"rl:hourly:203.0.113.9": 3600}


@pytest.mark.asyncio
async def test_each_address_gets_its_own_budget(redis: _FakeRedis) -> None:
    dep = _dep("b", 1, 3600)
    await dep(_Req("198.51.100.1"))  # type: ignore[arg-type]
    # A different address is unaffected by the first one's spend.
    await dep(_Req("198.51.100.2"))  # type: ignore[arg-type]
    with pytest.raises(HTTPException):
        await dep(_Req("198.51.100.1"))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_it_does_not_share_a_key_with_the_per_minute_limiter(
    redis: _FakeRedis,
) -> None:
    """Both caps sit on the same routes. Sharing a key would make the tighter
    one consume the looser one's budget and vice versa."""
    burst = rate_limit("public_apply_submit", 6).dependency
    sustained = _dep("public_apply_submit_hourly", 60, 3600)
    await burst(_Req())  # type: ignore[arg-type]
    await sustained(_Req())  # type: ignore[arg-type]
    assert sorted(redis.counts) == [
        "rl:public_apply_submit:203.0.113.9",
        "rl:public_apply_submit_hourly:203.0.113.9",
    ]


@pytest.mark.asyncio
async def test_it_fails_open_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliberate, and worth pinning so the trade-off stays visible.

    The module docstring argues that a limiter which refuses everyone during a
    Redis outage is worse than one that lets traffic through. The consequence
    for THIS limiter is that the bound it provides is conditional on Redis
    being up — which is a fact the decision record has to carry, not something
    a test should quietly assert away.
    """
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: _FakeRedis(fail=True))
    dep = _dep("b", 1, 3600)
    for _ in range(5):
        await dep(_Req())  # type: ignore[arg-type]  # no raise


@pytest.mark.asyncio
async def test_the_refusal_is_indistinguishable_from_the_burst_limiter(
    redis: _FakeRedis,
) -> None:
    """Which cap was hit is not something an anonymous caller needs to learn."""
    burst = rate_limit("a", 0).dependency
    sustained = _dep("b", 0, 3600)

    with pytest.raises(HTTPException) as first:
        await burst(_Req())  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as second:
        await sustained(_Req())  # type: ignore[arg-type]

    assert first.value.status_code == second.value.status_code
    assert first.value.detail == second.value.detail


@pytest.mark.asyncio
async def test_the_ttl_is_attached_by_the_command_that_creates_the_key(
    redis: _FakeRedis,
) -> None:
    """THE atomicity property, and the test that was missing.

    The form this replaced was `incr`, then `expire(key, window_seconds)` on
    the first hit. Two commands, and anything interrupting between them — a
    dropped connection, a client disconnect cancelling the dependency — leaves
    the key with NO expiry; the counter then never resets and that address is
    refused for ever with no recovery but manual Redis surgery.

    None of the other tests in this file can tell the two forms apart. They
    read `counts` and `expiries`, and both forms leave those identical:
    `{key: 1}` and `{key: 3600}`. I checked by restoring the old form and
    running them — all six passed. The commit that introduced this limiter
    claimed "regressing to the old form fails them", and that was false; the
    mutation actually run had changed the window to a hard-coded 60, which
    tests a different bug.

    The difference is observable only in the ORDER of commands: the TTL must
    be attached by the same command that creates the key, so there is no
    window in which the key exists without one. Hence `SET key 0 EX w NX`
    first, then `INCR` — and `EXPIRE` never used at all.
    """
    dep = _dep("atomic", 5, 3600)
    await dep(_Req())  # type: ignore[arg-type]

    assert redis.calls == ["set", "incr"], (
        "the TTL is not attached by the command that creates the key, so an "
        f"interruption can strand it without one: {redis.calls}"
    )
    assert "expire" not in redis.calls, (
        "EXPIRE is back, which means the create-then-expire window is back"
    )
