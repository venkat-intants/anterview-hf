"""S5-001: feedback_billing health endpoint smoke tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

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
    body = resp.json()
    assert body["status"] == "ok"
    assert body["postgres"]["ok"] is True
    assert body["redis"]["ok"] is True


def test_deep_health_postgres_down(client: TestClient) -> None:
    with (
        patch(
            "app.health._check_postgres",
            new_callable=AsyncMock,
            return_value={"ok": False, "error": "OperationalError: connection refused"},
        ),
        patch("app.health._check_redis", new_callable=AsyncMock, return_value={"ok": True}),
    ):
        resp = client.get("/health/deep")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"
    assert resp.json()["postgres"]["ok"] is False


def test_deep_health_redis_down(client: TestClient) -> None:
    with (
        patch("app.health._check_postgres", new_callable=AsyncMock, return_value={"ok": True}),
        patch(
            "app.health._check_redis",
            new_callable=AsyncMock,
            return_value={"ok": False, "error": "ConnectionError: refused"},
        ),
    ):
        resp = client.get("/health/deep")
    assert resp.status_code == 503
    assert resp.json()["redis"]["ok"] is False
