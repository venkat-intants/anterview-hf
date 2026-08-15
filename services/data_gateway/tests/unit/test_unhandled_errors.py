"""Unhandled 500s are countable and do not leak — DG-8, now via the shared layer.

``http_requests_total`` is labelled by status_code, but the original metrics
middleware returned early on the happy path only: an exception propagated
straight past it and was never counted, so the series could not *structurally*
contain a 5xx and "alert when 500s climb" was unimplementable no matter what the
alerting config said.

That fix used to live in ``app/main.py`` and existed in this service alone
(XS-01/XS-05). It now comes from ``shared/http_observability.py``, which also
closes the cardinality hole this service's version still had: an unmatched route
was labelled with its RAW path (XS-06).

What is tested WHERE:
  * ``shared/tests/test_http_observability.py`` owns the layer's own semantics.
  * This file owns the two things that file cannot see — that data_gateway's real
    ``app`` is actually WIRED to it, and that the DG-8 behaviours still hold
    end-to-end through the wiring, so a future main.py edit that drops the
    install call fails here rather than in production.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY
from shared.http_observability import (
    UNMATCHED_PATH,
    HTTPObservabilityMiddleware,
    install_http_observability,
)

from app.config import settings
from app.main import app as real_app

_SERVICE = settings.service_name


class _DriverError(RuntimeError):
    """Stands in for the asyncpg error whose str() carries SQL and parameters."""


_LEAKY_MESSAGE = (
    "insert or update on table \"users\" violates foreign key constraint; "
    "SQL: INSERT INTO users (email) VALUES ('candidate@example.com')"
)


def _app() -> FastAPI:
    """A throwaway app wired the same way ``app/main.py`` wires the real one.

    Booting the real app for the exploding-route cases is not an option — its
    lifespan wants Postgres and Redis, and none of that is under test. The
    wiring assertions below run against the real ``app`` object instead.
    """
    app = FastAPI()
    install_http_observability(app, service_name=_SERVICE)

    @app.get("/boom")
    async def _boom() -> dict[str, str]:
        raise _DriverError(_LEAKY_MESSAGE)

    @app.get("/fine")
    async def _fine() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/applicants/{applicant_id}")
    async def _applicant(applicant_id: str) -> dict[str, str]:
        return {"id": applicant_id}

    return app


def _requests_total(method: str, path: str, status_code: str) -> float:
    value = REGISTRY.get_sample_value(
        "http_requests_total",
        {
            "service": _SERVICE,
            "method": method,
            "path": path,
            "status_code": status_code,
        },
    )
    return float(value or 0.0)


async def _client(app: FastAPI, **kw: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, **kw), base_url="http://test")


# ---------------------------------------------------------------------------
# The wiring itself — what breaks if someone deletes the install call
# ---------------------------------------------------------------------------
def test_the_real_app_installs_the_shared_observability_layer() -> None:
    assert any(
        m.cls is HTTPObservabilityMiddleware for m in real_app.user_middleware
    ), "app/main.py must call install_http_observability()"


def test_the_real_app_has_a_generic_exception_handler() -> None:
    """CWE-209: without it an unhandled exception reaches the ASGI server, whose
    body is server-dependent and can echo asyncpg's SQL and parameters."""
    assert Exception in real_app.exception_handlers


def test_main_no_longer_defines_its_own_http_collectors() -> None:
    """prometheus_client refuses a duplicate metric name on one registry, so a
    leftover local Counter would make the shared install raise at import. Pin the
    absence explicitly rather than relying on that error being noticed."""
    import app.main as main_module

    assert not hasattr(main_module, "_http_requests_total")
    assert not hasattr(main_module, "_http_request_duration_seconds")
    assert not hasattr(main_module, "_prometheus_middleware")


# ---------------------------------------------------------------------------
# DG-8 behaviours, end to end through the shared layer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unhandled_exception_is_counted_as_a_500() -> None:
    before = _requests_total("GET", "/boom", "500")

    async with await _client(_app(), raise_app_exceptions=False) as ac:
        resp = await ac.get("/boom")

    assert resp.status_code == 500
    assert _requests_total("GET", "/boom", "500") == before + 1


@pytest.mark.asyncio
async def test_the_500_body_carries_no_exception_text() -> None:
    """asyncpg puts the offending SQL and its parameters into str(exc), and
    parameters are candidate PII. The client gets a fixed string.

    This docstring used to end "the detail goes to the log stream, which the PII
    processor already redacts". That was false and is now corrected here as well
    as in the shared module: ``shared/observability/pii.py`` pops known PII *key
    names* off the event dict and never inspects a value, and ``exc_msg`` is not
    one of those keys. The masking that makes the log side safe is
    ``_safe_exc_message`` at the shared call site, and
    ``shared/tests/test_http_observability.py`` owns asserting it. What this test
    owns is the wire: nothing about the exception reaches the client body.
    """
    async with await _client(_app(), raise_app_exceptions=False) as ac:
        resp = await ac.get("/boom")

    body = resp.text
    assert resp.json() == {"detail": "Internal server error."}
    assert "candidate@example.com" not in body
    assert "INSERT INTO users" not in body
    assert "_DriverError" not in body
    assert "Traceback" not in body


@pytest.mark.asyncio
async def test_the_exception_still_propagates_to_the_server() -> None:
    """Swallowing it would take Sentry — and every test client's failure
    signal — down with the stack trace we are keeping off the wire."""
    async with await _client(_app()) as ac:  # raise_app_exceptions defaults True
        with pytest.raises(_DriverError):
            await ac.get("/boom")


@pytest.mark.asyncio
async def test_a_normal_response_is_still_counted_once() -> None:
    """The exception path must not double-count or drop the success path."""
    before = _requests_total("GET", "/fine", "200")

    async with await _client(_app()) as ac:
        assert (await ac.get("/fine")).status_code == 200

    assert _requests_total("GET", "/fine", "200") == before + 1


@pytest.mark.asyncio
async def test_a_500_observes_its_own_latency() -> None:
    """A request that took 30 seconds and then blew up used to contribute no
    latency sample at all, so the histogram was biased toward the healthy path."""
    labels = {"service": _SERVICE, "method": "GET", "path": "/boom"}
    before = REGISTRY.get_sample_value("http_request_duration_seconds_count", labels)

    async with await _client(_app(), raise_app_exceptions=False) as ac:
        await ac.get("/boom")

    after = REGISTRY.get_sample_value("http_request_duration_seconds_count", labels)
    assert float(after or 0) == float(before or 0) + 1


# ---------------------------------------------------------------------------
# XS-06 — cardinality is now bounded by construction, not by a regex
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_id_segments_are_collapsed_to_the_route_template() -> None:
    """The old version regex-collapsed UUIDs only, so an integer id, a slug or an
    e-mail in a path each still minted their own series. The label is now the
    template the router matched, which cannot enumerate wrong."""
    before = _requests_total("GET", "/applicants/{applicant_id}", "200")

    async with await _client(_app()) as ac:
        await ac.get("/applicants/2b1f9c14-0b6a-4c2f-9a1e-1b2c3d4e5f60")
        await ac.get("/applicants/12345")

    assert _requests_total("GET", "/applicants/{applicant_id}", "200") == before + 2
    assert _requests_total("GET", "/applicants/12345", "200") == 0.0


@pytest.mark.asyncio
async def test_unmatched_paths_share_one_constant_label() -> None:
    """CWE-770: an unauthenticated caller looping /aaa, /aab, … used to mint a
    new time series per request against the scrape target and the TSDB."""
    before = _requests_total("GET", UNMATCHED_PATH, "404")

    async with await _client(_app()) as ac:
        for suffix in ("aaa", "aab", "aac"):
            await ac.get(f"/{suffix}")

    assert _requests_total("GET", UNMATCHED_PATH, "404") == before + 3
    assert _requests_total("GET", "/aaa", "404") == 0.0


@pytest.mark.asyncio
async def test_metrics_is_still_excluded_from_its_own_counter() -> None:
    """At a 15s scrape interval /metrics is otherwise the busiest endpoint in the
    service and drowns the data being scraped."""
    app = FastAPI()
    install_http_observability(app, service_name=_SERVICE)

    @app.get("/metrics")
    async def _metrics() -> dict[str, str]:
        return {"ok": "yes"}

    before = _requests_total("GET", "/metrics", "200")
    async with await _client(app) as ac:
        await ac.get("/metrics")

    assert _requests_total("GET", "/metrics", "200") == before
