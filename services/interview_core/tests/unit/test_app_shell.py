"""App-shell invariants: the lifespan path (SH-6) and /metrics auth (M-6).

Both are things that fail silently. A deprecated ``on_event`` hook keeps working
right up until the Starlette major bump that drops it, at which point the
service boots with no engine, no Redis and no LLM adapter. An unauthenticated
``/metrics`` never errors at all — it just hands the route table, traffic volume
and error rates to anyone who asks.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings
from app.llm.base import LLMAdapter
from app.main import app

# ---------------------------------------------------------------------------
# SH-6 — lifespan replaces the deprecated on_event hooks
# ---------------------------------------------------------------------------


@pytest.fixture
def _recorded_lifecycle(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the four lifecycle calls with recorders, in call order.

    Patching them keeps the test offline (no engine, no pool) and lets it assert
    the thing that actually matters: that startup and shutdown still run, and
    still run in the order the originals required.
    """
    calls: list[str] = []

    async def _dispose_engine() -> None:
        calls.append("dispose_engine")

    async def _close_redis() -> None:
        calls.append("close_redis")

    monkeypatch.setattr(main, "init_engine", lambda: calls.append("init_engine"))
    monkeypatch.setattr(main, "init_redis", lambda: calls.append("init_redis"))
    monkeypatch.setattr(main, "dispose_engine", _dispose_engine)
    monkeypatch.setattr(main, "close_redis", _close_redis)
    return calls


def test_app_starts_and_stops_through_the_lifespan_path(
    _recorded_lifecycle: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entering and leaving the TestClient context must run both halves.

    Ordering is asserted, not just membership: the engine is brought up before
    Redis and disposed before Redis is closed, which is what the two handlers
    this replaced did.
    """
    dummy_adapter = MagicMock(spec=LLMAdapter)
    monkeypatch.setattr(main, "build_default_adapter", lambda: dummy_adapter)

    with TestClient(app) as client:
        assert _recorded_lifecycle == ["init_engine", "init_redis"]
        # The app is genuinely serving while inside the context.
        assert client.get("/health/live").status_code == 200
        assert app.state.llm_adapter is dummy_adapter

    assert _recorded_lifecycle == [
        "init_engine",
        "init_redis",
        "dispose_engine",
        "close_redis",
    ]


def test_no_deprecated_on_event_handlers_remain() -> None:
    """Nothing may re-register startup/shutdown via ``@app.on_event``.

    Starlette runs ``on_event`` handlers *and* the lifespan context, so a
    reverted or duplicated hook would double-initialise rather than fail loudly.
    This is the only cheap way to notice.
    """
    assert app.router.on_startup == []
    assert app.router.on_shutdown == []


# ---------------------------------------------------------------------------
# M-6 — /metrics is not world-readable
# ---------------------------------------------------------------------------

_TOKEN = "metrics-token-for-tests"


@pytest.fixture
def client() -> TestClient:
    """A client that does NOT enter the lifespan — /metrics needs no DB."""
    return TestClient(app)


def test_metrics_rejects_a_wrong_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "metrics_token", _TOKEN)

    response = client.get("/metrics", headers={"Authorization": "Bearer not-the-token"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    # The refusal must not leak the exposition payload it was guarding.
    assert "# HELP" not in response.text


def test_metrics_rejects_a_missing_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "metrics_token", _TOKEN)

    response = client.get("/metrics")

    assert response.status_code == 401
    assert "# HELP" not in response.text


def test_metrics_accepts_the_configured_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "metrics_token", _TOKEN)

    response = client.get("/metrics", headers={"Authorization": f"Bearer {_TOKEN}"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    # Prometheus text exposition format — proves the real payload came back,
    # not an empty 200.
    assert "# HELP" in response.text


def test_metrics_is_refused_in_production_when_no_token_is_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed, but as a 404 — an unauthenticated prober learns nothing."""
    monkeypatch.setattr(settings, "metrics_token", "")
    monkeypatch.setattr(settings, "app_env", "production")

    response = client.get("/metrics")

    assert response.status_code == 404
    assert "# HELP" not in response.text


def test_metrics_stays_open_in_development_with_no_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dev-up.ps1, docker-compose and CI must keep scraping with no config."""
    monkeypatch.setattr(settings, "metrics_token", "")
    monkeypatch.setattr(settings, "app_env", "development")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "# HELP" in response.text


def test_metrics_auth_is_evaluated_per_request_not_at_import(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turning METRICS_TOKEN on must take effect without a code change.

    If the handler captured the token in a dependency default or a module
    constant, the second call below would still succeed unauthenticated.
    """
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "metrics_token", "")
    assert client.get("/metrics").status_code == 200

    monkeypatch.setattr(settings, "metrics_token", _TOKEN)
    assert client.get("/metrics").status_code == 401


def test_metrics_survives_a_non_ascii_authorization_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A latin-1 header byte must be a 401, never a 500.

    starlette decodes raw header bytes as latin-1; a naive str comparison in the
    auth helper would raise and turn an unauthorised request into a trivially
    reachable server error.

    Sent as bytes because httpx refuses to ASCII-encode a str header — which is
    also the only way a real client could put these bytes on the wire.
    """
    monkeypatch.setattr(settings, "metrics_token", _TOKEN)

    response = client.get(
        "/metrics",
        headers={"Authorization": b"Bearer \xff\xfe"},  # type: ignore[dict-item]
    )

    assert response.status_code == 401


def test_root_endpoint_needs_no_auth(client: TestClient) -> None:
    """The metrics gate must not have been attached to the whole app."""
    body: dict[str, Any] = client.get("/").json()
    assert body["service"] == settings.service_name
