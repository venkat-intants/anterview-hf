"""M-6 (CWE-497): GET /metrics must not be world-readable.

The policy itself is tested in shared/tests/test_metrics_auth.py; what these
tests pin is that admin_ops actually *applies* it, and that it keeps applying it
after someone edits the handler — the endpoint shipped with a docstring saying
"bind this port behind a firewall", which render.yaml's proxy-less topology does
not provide.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

TOKEN = "unit-test-metrics-token-0123456789"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_correct_bearer_token_gets_metrics(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "metrics_token", TOKEN)
    resp = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    # A 200 that returned nothing would pass a status-only assertion while
    # having broken the scrape.
    assert "admin_ops_erasure_requests_completed_total" in resp.text


def test_wrong_bearer_token_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "metrics_token", TOKEN)
    resp = client.get("/metrics", headers={"Authorization": "Bearer not-the-token"})
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    assert "admin_ops_erasure" not in resp.text


def test_missing_authorization_header_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "metrics_token", TOKEN)
    resp = client.get("/metrics")
    assert resp.status_code == 401


def test_non_ascii_authorization_header_is_rejected_not_a_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starlette decodes header bytes as latin-1, so a non-ASCII header used to
    be a trivially reachable 500 in a naive constant-time comparison. Sent as
    raw bytes because httpx refuses to encode a non-ASCII str header at all —
    a real client on the wire has no such scruples."""
    monkeypatch.setattr(settings, "metrics_token", TOKEN)
    resp = client.get("/metrics", headers={"Authorization": b"Bearer \xff\xfe"})
    assert resp.status_code == 401


def test_unset_token_outside_production_stays_open(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local dev, docker-compose and CI scrape with no METRICS_TOKEN set; turning
    the gate on must not have broken them."""
    monkeypatch.setattr(settings, "metrics_token", "")
    monkeypatch.setattr(settings, "app_env", "test")
    resp = client.get("/metrics")
    assert resp.status_code == 200


def test_unset_token_in_production_fails_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """404, not 403: an unauthenticated prober should not learn that the
    endpoint exists. And a response, not a boot crash — a forgotten token must
    never turn a rolling deploy into an outage."""
    monkeypatch.setattr(settings, "metrics_token", "")
    monkeypatch.setattr(settings, "app_env", "production")
    resp = client.get("/metrics")
    assert resp.status_code == 404
    assert "admin_ops_erasure" not in resp.text
