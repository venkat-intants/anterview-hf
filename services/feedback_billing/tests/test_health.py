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


# ---------------------------------------------------------------------------
# S-6 (CWE-209): /health/deep is unauthenticated, so the driver's own message
# must not reach the body. asyncpg and redis-py both put the host, port and
# database/instance name in str(exc) — free infrastructure reconnaissance for
# anyone who can reach the probe, which on render.yaml's proxy-less topology is
# the whole internet.
# ---------------------------------------------------------------------------

_LEAKY_DETAIL = (
    "connection to server at ep-secret-9921.ap-southeast-1.aws.neon.tech:5432 refused"
)


class OperationalError(Exception):
    """Stands in for the asyncpg/redis-py exception classes."""


def test_deep_health_reports_the_exception_type_not_its_message(client: TestClient) -> None:
    with (
        patch("app.health.get_session_factory", side_effect=OperationalError(_LEAKY_DETAIL)),
        patch("app.health.get_redis", side_effect=OperationalError(_LEAKY_DETAIL)),
    ):
        resp = client.get("/health/deep")

    assert resp.status_code == 503
    assert _LEAKY_DETAIL not in resp.text
    assert "neon.tech" not in resp.text
    body = resp.json()
    # The type alone is still enough for an operator to tell a config error from
    # an outage — the detail is in structlog, which is authenticated.
    assert body["postgres"]["error"] == "OperationalError"
    assert body["redis"]["error"] == "OperationalError"
