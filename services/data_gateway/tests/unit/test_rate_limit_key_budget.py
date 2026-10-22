"""``enforce_key_budget``: a fixed-window budget on a key the handler resolves
itself (used for set-password resends to one account)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import rate_limit


class _Redis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttl: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.ttl[key] = seconds


async def _charge(key: str = "u1") -> None:
    await rate_limit.enforce_key_budget(
        bucket="b", key=key, limit=3, window_seconds=600, detail="Wait.",
    )


@pytest.mark.asyncio
async def test_the_call_after_the_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _Redis()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: redis)
    for _ in range(3):
        await _charge()
    with pytest.raises(HTTPException) as exc:
        await _charge()
    assert exc.value.status_code == 429
    assert exc.value.detail == "Wait."
    assert redis.ttl == {"rl:b:u1": 600}


@pytest.mark.asyncio
async def test_each_key_has_its_own_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _Redis()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: redis)
    for _ in range(3):
        await _charge("u1")
    await _charge("u2")


@pytest.mark.asyncio
async def test_redis_down_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    def _down() -> object:
        raise ConnectionError("no redis")

    monkeypatch.setattr(rate_limit, "get_redis", _down)
    for _ in range(10):
        await _charge()
