"""XS-05 / XS-01: interview_core now emits HTTP metrics and hides driver text.

Until ``install_http_observability`` was wired into ``app/main.py`` this service
exposed only default process metrics — CPU, memory, open FDs, GC — and no HTTP
series at all. It is the real-time interview service and the one the platform
NFR (p95 turn latency < 2s) is written about, so the single service whose
latency anyone would want to alert on was the one with nothing to alert on:
``sum(rate(http_requests_total{status_code=~"5.."}[5m]))`` could not fire here.

The middleware's own semantics (route templates, unmatched-path collapsing,
idempotent recording) are tested in ``shared/tests``. What is pinned here is
this service's WIRING: that the layer is installed, that its series carry this
service's name, and that an unhandled exception produces a generic body rather
than an asyncpg message carrying SQL and its bound parameters (CWE-209 — this
service's parameters are session ids, resume text and transcripts, DPDP §8).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.config import settings
from app.main import app

_TOKEN = "metrics-token-for-tests"


@pytest.fixture
def client() -> TestClient:
    """No lifespan — none of these routes touch the DB or Redis.

    ``raise_server_exceptions=False`` is required for the 500 test: the default
    re-raises inside the test client, which would assert on Starlette's
    behaviour instead of on the response a real client receives.
    """
    return TestClient(app, raise_server_exceptions=False)


def _counter(path: str, status_code: str) -> float:
    value = REGISTRY.get_sample_value(
        "http_requests_total",
        {
            "service": settings.service_name,
            "method": "GET",
            "path": path,
            "status_code": status_code,
        },
    )
    return float(value or 0.0)


def test_a_request_is_counted_against_its_route_template(client: TestClient) -> None:
    """The series that did not exist before this wiring."""
    before = _counter("/", "200")

    assert client.get("/").status_code == 200

    assert _counter("/", "200") == before + 1


def test_latency_is_observed_for_the_nfr_bucket(client: TestClient) -> None:
    """The p95 < 2s NFR is unanswerable without a duration histogram.

    Asserting the ``le="2.0"`` bucket specifically, because that boundary is
    the one the NFR is stated at — ``histogram_quantile`` interpolates within a
    bucket, so a threshold with no boundary on it can only ever be estimated.
    """
    labels = {"service": settings.service_name, "method": "GET", "path": "/", "le": "2.0"}
    before = REGISTRY.get_sample_value("http_request_duration_seconds_bucket", labels) or 0.0

    client.get("/")

    after = REGISTRY.get_sample_value("http_request_duration_seconds_bucket", labels) or 0.0
    assert after == before + 1, (
        "a request that completed in well under 2s must land in the le=2.0 "
        "bucket; without it the NFR cannot be measured from these series"
    )


def test_unmatched_paths_collapse_to_one_series(client: TestClient) -> None:
    """CWE-770: an unauthenticated caller must not be able to mint series.

    A loop over /aaa, /aab, … is the standard Prometheus memory-exhaustion
    vector against the scrape target and the TSDB.
    """
    before = _counter("__unmatched__", "404")

    for suffix in ("aaa", "aab", "aac"):
        assert client.get(f"/{suffix}").status_code == 404

    assert _counter("__unmatched__", "404") == before + 3
    assert REGISTRY.get_sample_value(
        "http_requests_total",
        {
            "service": settings.service_name,
            "method": "GET",
            "path": "/aaa",
            "status_code": "404",
        },
    ) is None, "the raw path became a label — cardinality is caller-controlled again"


def test_metrics_endpoint_exposes_the_http_series(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The series must reach the scrape, not just the registry."""
    monkeypatch.setattr(settings, "metrics_token", _TOKEN)
    client.get("/")

    body = client.get("/metrics", headers={"Authorization": f"Bearer {_TOKEN}"}).text

    assert "http_requests_total" in body
    assert "http_request_duration_seconds_bucket" in body
    assert f'service="{settings.service_name}"' in body


def test_metrics_endpoint_does_not_count_itself(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At a 15s scrape interval /metrics is otherwise the busiest endpoint in
    the service and drowns the data being scraped."""
    monkeypatch.setattr(settings, "metrics_token", _TOKEN)
    before = _counter("/metrics", "200")

    client.get("/metrics", headers={"Authorization": f"Bearer {_TOKEN}"})

    assert _counter("/metrics", "200") == before


def test_an_unhandled_exception_returns_a_generic_body_and_a_500_series(
    client: TestClient,
) -> None:
    """CWE-209 plus the counter that made a 5xx alert impossible here.

    asyncpg puts the offending SQL AND its bound parameter values into
    ``str(exc)``; on this service those parameters are candidate data. The
    client must get a fixed body, and the failure must still be visible as a
    500 in the metrics.
    """
    secret = "candidate-transcript-fragment"

    @app.get("/__boom__")
    async def _boom() -> None:  # pragma: no cover — invoked via the client
        raise RuntimeError(f"SELECT * FROM users WHERE resume_text = '{secret}'")

    try:
        before = _counter("/__boom__", "500")

        response = client.get("/__boom__")

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error."}
        assert secret not in response.text, (
            "the driver message reached the client — this is exactly the "
            "asyncpg SQL-and-parameters leak the handler exists to stop"
        )
        assert _counter("/__boom__", "500") == before + 1
    finally:
        app.router.routes = [
            route for route in app.router.routes if getattr(route, "path", "") != "/__boom__"
        ]
