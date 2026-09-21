"""``app.rate_limit.rate_limit_company`` — MEDIUM-4 (PH4-D3): the on-demand
code-analysis route is capped per COMPANY, not per client IP, so many HR
seats at one company share one budget rather than each getting their own via
a different laptop's IP.

Exercises the dependency function directly (``Depends(...).dependency``) with
a fake in-memory Redis — no real Redis, no HTTP round trip, no auth
machinery. ``app.rate_limit.rate_limit``'s own IP-keyed behaviour is
integration-tested indirectly through the routes that use it; this file
covers only the new company-keyed variant.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException

from app.rate_limit import rate_limit_company


class _FakeRedis:
    """Just enough of the redis-py async interface for ``rate_limit_company``:
    ``incr`` and ``expire``, backed by a plain dict."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expired: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expired.append((key, seconds))


def _dep(bucket: str, per_minute: int) -> Any:
    """The bare async callable FastAPI would otherwise inject ``Depends``-style."""
    return rate_limit_company(bucket, per_minute).dependency


@pytest.mark.asyncio
async def test_rate_limit_company_caps_the_company_not_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: fake)
    dep = _dep("test_bucket", 2)
    company = uuid.uuid4()
    ctx = (uuid.uuid4(), company)

    await dep(ctx=ctx)
    await dep(ctx=ctx)
    with pytest.raises(HTTPException) as exc_info:
        await dep(ctx=ctx)
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_company_gives_each_company_its_own_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of keying on company rather than IP: two different HR
    seats at the SAME company share a budget, but two DIFFERENT companies
    never share each other's."""
    fake = _FakeRedis()
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: fake)
    dep = _dep("test_bucket", 1)
    company_a, company_b = uuid.uuid4(), uuid.uuid4()

    await dep(ctx=(uuid.uuid4(), company_a))  # company_a now at its cap
    await dep(ctx=(uuid.uuid4(), company_b))  # company_b has its own, untouched budget
    with pytest.raises(HTTPException):
        await dep(ctx=(uuid.uuid4(), company_a))


@pytest.mark.asyncio
async def test_rate_limit_company_keys_by_company_id_not_by_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: fake)
    dep = _dep("test_bucket", 1)
    company = uuid.uuid4()

    await dep(ctx=(uuid.uuid4(), company))  # HR user 1
    with pytest.raises(HTTPException):
        await dep(ctx=(uuid.uuid4(), company))  # HR user 2, same company -- still capped


@pytest.mark.asyncio
async def test_rate_limit_company_fails_open_when_redis_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same fail-open posture as ``rate_limit`` — a Redis blip must not lock
    every HR seat out of the console."""

    def _broken_get_redis() -> None:
        raise RuntimeError("redis unreachable")

    monkeypatch.setattr("app.rate_limit.get_redis", _broken_get_redis)
    dep = _dep("test_bucket", 1)
    await dep(ctx=(uuid.uuid4(), uuid.uuid4()))  # must not raise
