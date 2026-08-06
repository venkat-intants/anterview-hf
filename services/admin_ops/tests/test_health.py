"""S5-002: admin_ops health endpoint smoke tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import health as health_mod
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_liveness_returns_ok(client: TestClient) -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


def test_deep_health_all_ok(client: TestClient) -> None:
    with (
        patch("app.health._check_postgres", new_callable=AsyncMock, return_value={"ok": True}),
        patch("app.health._check_redis", new_callable=AsyncMock, return_value={"ok": True}),
    ):
        resp = client.get("/health/deep")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_deep_health_postgres_down(client: TestClient) -> None:
    with (
        patch(
            "app.health._check_postgres",
            new_callable=AsyncMock,
            return_value={"ok": False, "error": "connection refused"},
        ),
        patch("app.health._check_redis", new_callable=AsyncMock, return_value={"ok": True}),
    ):
        resp = client.get("/health/deep")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"


@pytest.mark.asyncio
async def test_check_postgres_failure_returns_exception_type_not_message() -> None:
    """asyncpg/SQLAlchemy failures embed the Neon host/port/db name in str(exc);
    the API-facing dict must carry only the exception type, never that string."""

    class _FakeFactory:
        def __call__(self) -> _FakeFactory:
            return self

        async def __aenter__(self) -> _FakeFactory:
            raise ConnectionRefusedError(
                "connection to server at host db.neon-example.tech:5432 failed"
            )

        async def __aexit__(self, *_exc: object) -> bool:
            return False

    with patch(
        "app.health.get_session_factory", MagicMock(return_value=_FakeFactory())
    ):
        result = await health_mod._check_postgres()

    assert result["ok"] is False
    assert result["error"] == "ConnectionRefusedError"
    assert "neon-example" not in result["error"]
    assert "5432" not in result["error"]


@pytest.mark.asyncio
async def test_check_redis_failure_returns_exception_type_not_message() -> None:
    class _FakeRedis:
        async def ping(self) -> bool:
            raise TimeoutError("redis-12345.upstash.io:6379 unreachable")

    with patch("app.health.get_redis", MagicMock(return_value=_FakeRedis())):
        result = await health_mod._check_redis()

    assert result["ok"] is False
    assert result["error"] == "TimeoutError"
    assert "upstash" not in result["error"]
