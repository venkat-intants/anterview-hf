"""Unit tests for GET /health/deep — S5-009.

The two private check functions (_check_postgres, _check_redis) are patched at
the ``app.health`` module level, so these tests run without any real DB or Redis.

Test matrix:
1. test_deep_health_all_ok        — both checks pass → 200, status "ok"
2. test_deep_health_postgres_down — Postgres check returns degraded → 503, status "degraded"
3. test_deep_health_redis_down    — Redis check returns degraded → 503, status "degraded"
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app

# ---------------------------------------------------------------------------
# Fixture — lightweight ASGI client without a lifespan context.
# The lifespan (init_engine / init_redis / scheduler) is intentionally NOT
# started; we patch _check_postgres and _check_redis at the module level so
# the endpoint never touches real infrastructure.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncClient:  # type: ignore[misc]
    """Minimal ASGI test client — no lifespan (unit tests need no infra)."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deep_health_all_ok(client: AsyncClient) -> None:
    """Both Postgres and Redis healthy → HTTP 200, status 'ok'."""
    healthy_pg = AsyncMock(return_value={"ok": True})
    healthy_redis = AsyncMock(return_value={"ok": True})

    with (
        patch("app.health._check_postgres", healthy_pg),
        patch("app.health._check_redis", healthy_redis),
    ):
        resp = await client.get("/health/deep")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["postgres"] == {"ok": True}
    assert body["redis"] == {"ok": True}


@pytest.mark.asyncio
async def test_deep_health_postgres_down(client: AsyncClient) -> None:
    """Postgres check returns degraded → HTTP 503, status 'degraded', postgres.ok is false."""
    degraded_pg = AsyncMock(
        return_value={"ok": False, "error": "OperationalError: connection refused"}
    )
    healthy_redis = AsyncMock(return_value={"ok": True})

    with (
        patch("app.health._check_postgres", degraded_pg),
        patch("app.health._check_redis", healthy_redis),
    ):
        resp = await client.get("/health/deep")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["postgres"]["ok"] is False
    assert "connection refused" in body["postgres"]["error"]
    assert body["redis"] == {"ok": True}


@pytest.mark.asyncio
async def test_deep_health_redis_down(client: AsyncClient) -> None:
    """Redis check returns degraded → HTTP 503, status 'degraded', redis.ok is false."""
    healthy_pg = AsyncMock(return_value={"ok": True})
    degraded_redis = AsyncMock(
        return_value={"ok": False, "error": "ConnectionError: redis timeout"}
    )

    with (
        patch("app.health._check_postgres", healthy_pg),
        patch("app.health._check_redis", degraded_redis),
    ):
        resp = await client.get("/health/deep")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["postgres"] == {"ok": True}
    assert body["redis"]["ok"] is False
    assert "redis timeout" in body["redis"]["error"]
