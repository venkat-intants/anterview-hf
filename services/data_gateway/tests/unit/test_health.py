"""Unit tests for GET /health/deep — S5-009.

The two private check functions (_check_postgres, _check_redis) are patched at
the ``app.health`` module level, so these tests run without any real DB or Redis.

Test matrix:
1. test_deep_health_all_ok        — both checks pass → 200, status "ok"
2. test_deep_health_postgres_down — Postgres check returns degraded → 503, status "degraded"
3. test_deep_health_redis_down    — Redis check returns degraded → 503, status "degraded"
4. the leak tests at the bottom   — a driver exception must not put its message
                                    (which carries the DSN) in the response body
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

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
    degraded_pg = AsyncMock(return_value={"ok": False, "error": "OperationalError"})
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
    assert body["postgres"]["error"] == "OperationalError"
    assert body["redis"] == {"ok": True}


@pytest.mark.asyncio
async def test_deep_health_redis_down(client: AsyncClient) -> None:
    """Redis check returns degraded → HTTP 503, status 'degraded', redis.ok is false."""
    healthy_pg = AsyncMock(return_value={"ok": True})
    degraded_redis = AsyncMock(return_value={"ok": False, "error": "ConnectionError"})

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
    assert body["redis"]["error"] == "ConnectionError"


# ---------------------------------------------------------------------------
# S-6 — /health/deep is UNAUTHENTICATED, so a driver exception message is
# published to anyone who curls it. asyncpg and redis-py both put the host,
# port, database and sometimes the user into str(exc), so a DB outage used to
# hand out a DSN fragment. These tests drive the REAL check functions (the ones
# above stub them out entirely, so they would pass either way) and assert on
# what a stranger can read.
# ---------------------------------------------------------------------------

# A stand-in for what asyncpg actually says when Neon refuses a connection.
_DSN_MESSAGE = (
    "connection failed: postgresql://intants:s3cr3tpw@"
    "ep-cool-forest-1234.ap-southeast-1.aws.neon.tech:5432/intants"
)


class _DriverError(Exception):
    """Named so the tests can assert the TYPE is still reported."""


@pytest.mark.asyncio
async def test_postgres_failure_reports_type_not_the_dsn(client: AsyncClient) -> None:
    """A Postgres failure yields the exception class name and nothing else."""
    with (
        patch(
            "app.health.get_session_factory",
            MagicMock(side_effect=_DriverError(_DSN_MESSAGE)),
        ),
        patch("app.health._check_redis", AsyncMock(return_value={"ok": True})),
    ):
        resp = await client.get("/health/deep")

    assert resp.status_code == 503
    body = resp.json()
    assert body["postgres"] == {"ok": False, "error": "_DriverError"}
    raw = json.dumps(body)
    assert "s3cr3tpw" not in raw
    assert "neon.tech" not in raw


@pytest.mark.asyncio
async def test_redis_failure_reports_type_not_the_url(client: AsyncClient) -> None:
    """Same guarantee on the Redis leg — the Upstash URL embeds the password."""
    upstash = "rediss://default:AX9sTOKEN@fine-mongoose-12345.upstash.io:6379"
    with (
        patch("app.health._check_postgres", AsyncMock(return_value={"ok": True})),
        patch("app.health.get_redis", MagicMock(side_effect=_DriverError(upstash))),
    ):
        resp = await client.get("/health/deep")

    assert resp.status_code == 503
    body = resp.json()
    assert body["redis"] == {"ok": False, "error": "_DriverError"}
    assert "upstash.io" not in json.dumps(body)


@pytest.mark.asyncio
async def test_full_failure_detail_still_reaches_the_logs() -> None:
    """Redacting the response must not blind the operator.

    The point of the fix is *where* the detail goes, not that it is discarded —
    a probe that says only "_DriverError" with nothing in the log would be a
    worse outage than the leak.
    """
    from app import health

    with (
        patch(
            "app.health.get_session_factory",
            MagicMock(side_effect=_DriverError(_DSN_MESSAGE)),
        ),
        patch.object(health, "log", MagicMock()) as fake_log,
    ):
        result = await health._check_postgres()

    assert result == {"ok": False, "error": "_DriverError"}
    assert fake_log.warning.call_args.args == ("health.postgres.fail",)
    assert fake_log.warning.call_args.kwargs["exc_msg"] == _DSN_MESSAGE
