"""M-6 — GET /metrics must not be world-readable.

Prometheus exposition is not innocuous: the per-route counters and latency
histograms enumerate the whole route table (including which admin paths exist)
and leak traffic volume and error spikes. Caddy gates /metrics at the edge in
the Space and compose topologies, but ``render.yaml`` gives each backend its own
public hostname with no proxy in front — so the check has to exist in the app.

The policy itself lives in ``shared/metrics_auth.py`` and is unit-tested there.
What these tests pin is that data_gateway's handler is actually WIRED to it, and
that the wiring did not break local development or CI (both scrape with no token
configured).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app

_TOKEN = "s3cret-scrape-token"


@pytest_asyncio.fixture
async def client() -> AsyncClient:  # type: ignore[misc]
    """ASGI client with no lifespan — /metrics touches no infrastructure."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac  # type: ignore[misc]


@pytest.mark.asyncio
async def test_metrics_requires_the_bearer_token_when_one_is_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "metrics_token", _TOKEN)

    resp = await client.get("/metrics")

    assert resp.status_code == 401
    # Without this header a scrape job cannot tell "wrong credential" from
    # "endpoint moved", so the operator debugs the wrong thing.
    assert resp.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_metrics_rejects_a_wrong_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "metrics_token", _TOKEN)

    resp = await client.get("/metrics", headers={"Authorization": "Bearer nope"})

    assert resp.status_code == 401
    assert "http_requests_total" not in resp.text


@pytest.mark.asyncio
async def test_metrics_serves_the_scrape_on_the_right_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "metrics_token", _TOKEN)

    resp = await client.get("/metrics", headers={"Authorization": f"Bearer {_TOKEN}"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    # Proves it is the real exposition and not an empty 200 from a short-circuit.
    assert "http_request_duration_seconds" in resp.text


@pytest.mark.asyncio
async def test_metrics_stays_open_in_development_with_no_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dev-up.ps1, docker-compose and CI scrape with METRICS_TOKEN unset.

    Failing closed everywhere would have made this a breaking change nobody
    could deploy without touching four env files first.
    """
    monkeypatch.setattr(settings, "metrics_token", "")
    monkeypatch.setattr(settings, "app_env", "development")

    resp = await client.get("/metrics")

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_metrics_fails_closed_in_production_with_no_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forgotten token in production must close the endpoint, not open it.

    404 rather than 403 so an unauthenticated prober cannot even confirm the
    route exists; and a *response* rather than a startup assertion, so a missing
    env var never turns a rolling deploy into an outage.
    """
    monkeypatch.setattr(settings, "metrics_token", "")
    monkeypatch.setattr(settings, "app_env", "production")

    resp = await client.get("/metrics")

    assert resp.status_code == 404
    assert "http_requests_total" not in resp.text
