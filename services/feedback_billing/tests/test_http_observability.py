"""XS-01 / XS-05 / XS-06 as they land in feedback_billing.

The layer itself is tested in shared/tests/; these tests are about the wiring,
because that is what was actually wrong here. This service got its HTTP metrics
from prometheus-fastapi-instrumentator, so:

* its label set (``handler``, grouped status codes) matched none of the other
  three and no dashboard ported between them (XS-05);
* an unhandled exception had no application-level handler, so it reached the
  ASGI server, whose body is server-dependent and can echo driver text —
  asyncpg puts the failing SQL *and its bound parameters* into ``str(exc)``, and
  this service's parameters are candidate names and transcripts (XS-01,
  CWE-209).

Assertions go through the live app and the live registry rather than inspecting
``app.user_middleware``: a middleware present but installed inside the CORS
layer, or an exception handler registered on a sub-application, would satisfy a
structural check and still not do the job.

Counters are process-global and every other test module in this suite makes
requests through the same app, so nothing here asserts an absolute count — only
that a specific labelled series exists, and that it moved.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from shared.http_observability import UNMATCHED_PATH

from app.main import app

_SERVICE = "feedback_billing"


@pytest.fixture()
def client() -> TestClient:
    # raise_server_exceptions=False so the 500 the client would really receive
    # is returned instead of the exception being re-raised into the test.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def boom_route() -> Iterator[str]:
    """A route that raises, attached for the lifetime of one test.

    No real endpoint in this service can be made to fail without also reaching
    Postgres or Gemini, and what is under test is the app-level handler rather
    than any one endpoint — so the route is added here and removed again rather
    than shipped in the router.

    The message imitates what asyncpg actually produces, PII and all, because
    the assertion is that none of it reaches the client.
    """
    path = "/__boom__"

    async def boom() -> None:
        raise RuntimeError(
            'relation "scorecards" does not exist: '
            "INSERT INTO scorecards (candidate_email) VALUES ('ravi@example.com')"
        )

    app.add_api_route(path, boom, methods=["GET"], include_in_schema=False)
    try:
        yield path
    finally:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", "") != path]


def _requests_total(*, method: str, path: str, status_code: str) -> float | None:
    return REGISTRY.get_sample_value(
        "http_requests_total",
        {"service": _SERVICE, "method": method, "path": path, "status_code": status_code},
    )


# ---------------------------------------------------------------------------
# XS-01 — unhandled exceptions
# ---------------------------------------------------------------------------


def test_unhandled_exception_returns_a_generic_500(client: TestClient, boom_route: str) -> None:
    """No exception text, no SQL, no candidate address in the response body."""
    resp = client.get(boom_route)

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error."}
    body = resp.text
    assert "scorecards" not in body
    assert "ravi@example.com" not in body
    assert "RuntimeError" not in body


def test_a_500_is_recorded_in_the_request_counter(
    client: TestClient, boom_route: str
) -> None:
    """The counter must be structurally *able* to record a 5xx.

    It previously was not: with no application-level handler the exception
    escaped before anything recorded a status, so
    ``sum(rate(http_requests_total{status_code=~"5.."}))`` could not fire for
    this service no matter how badly it was failing.
    """
    before = _requests_total(method="GET", path=boom_route, status_code="500") or 0.0

    client.get(boom_route)

    after = _requests_total(method="GET", path=boom_route, status_code="500")
    assert after == pytest.approx(before + 1.0)


def test_a_failed_request_is_not_double_counted(client: TestClient, boom_route: str) -> None:
    """The middleware and the exception handler both call the same recorder.

    Without the per-request idempotency flag in the shared layer every 500 would
    be counted twice, which inflates exactly the number an error-rate alert
    fires on.
    """
    before = _requests_total(method="GET", path=boom_route, status_code="500") or 0.0

    client.get(boom_route)
    client.get(boom_route)

    after = _requests_total(method="GET", path=boom_route, status_code="500")
    assert after == pytest.approx(before + 2.0)


# ---------------------------------------------------------------------------
# XS-05 — the series exist at all, with the shared label set
# ---------------------------------------------------------------------------


def test_a_successful_request_is_counted_with_the_route_template(client: TestClient) -> None:
    before = _requests_total(method="GET", path="/health/live", status_code="200") or 0.0

    assert client.get("/health/live").status_code == 200

    after = _requests_total(method="GET", path="/health/live", status_code="200")
    assert after == pytest.approx(before + 1.0)


def test_health_live_is_no_longer_excluded_from_metrics(client: TestClient) -> None:
    """A deliberate change from the instrumentator config, which excluded it.

    The point of the shared layer is that the four services emit the same
    series; probe volume is filterable at query time (``path!="/health/live"``)
    whereas a series that was never recorded cannot be recovered. Pinned so
    re-adding the exclusion is a decision rather than a copy-paste.
    """
    client.get("/health/live")

    assert _requests_total(method="GET", path="/health/live", status_code="200") is not None


def test_latency_is_observed_for_the_nfr_bucket(client: TestClient) -> None:
    """The p95 < 2s NFR needs a bucket boundary ON 2.0 to be answerable exactly
    rather than by interpolation inside a bucket."""
    client.get("/health/live")

    assert (
        REGISTRY.get_sample_value(
            "http_request_duration_seconds_bucket",
            {"service": _SERVICE, "method": "GET", "path": "/health/live", "le": "2.0"},
        )
        is not None
    )


def test_metrics_endpoint_stays_out_of_its_own_counters(client: TestClient) -> None:
    """At a 15s scrape interval /metrics is otherwise the busiest endpoint in the
    service and drowns the data being scraped."""
    client.get("/metrics")

    assert _requests_total(method="GET", path="/metrics", status_code="200") is None


# ---------------------------------------------------------------------------
# XS-06 — label cardinality is bounded by construction
# ---------------------------------------------------------------------------


def test_unmatched_paths_collapse_to_one_label(client: TestClient) -> None:
    """CWE-770: an unauthenticated caller looping /aaa, /aab, … must not be able
    to mint a time series per request. The raw path must appear nowhere in the
    exposition — a regex that collapses only the ID shapes someone remembered is
    what this replaced."""
    for suffix in ("aaa", "aab", "aac"):
        assert client.get(f"/__nope__{suffix}").status_code == 404

    assert _requests_total(method="GET", path=UNMATCHED_PATH, status_code="404") is not None
    for suffix in ("aaa", "aab", "aac"):
        assert _requests_total(method="GET", path=f"/__nope__{suffix}", status_code="404") is None


def test_an_unknown_method_collapses_to_one_label(client: TestClient) -> None:
    """The method is as attacker-controlled as the path — h11 accepts any RFC
    9110 token, so "X-1", "X-2", … is the same cardinality vector.

    The path label stays the real template: a 405 is a partial route match, so
    the request is still attributed to the endpoint it was aimed at instead of
    disappearing into the unmatched bucket.
    """
    for verb in ("PROPFIND", "MKCOL", "REPORT"):
        assert client.request(verb, "/health/live").status_code == 405

    assert _requests_total(
        method="__unknown__", path="/health/live", status_code="405"
    ) is not None
    for verb in ("PROPFIND", "MKCOL", "REPORT"):
        assert _requests_total(method=verb, path="/health/live", status_code="405") is None
