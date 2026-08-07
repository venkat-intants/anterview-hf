"""Unhandled 500s are now countable and do not leak — DG-8.

``http_requests_total`` is labelled by status_code, but the metrics middleware
returned early on the happy path only: an exception propagated straight past it
and was never counted. The series therefore could not *structurally* contain a
5xx, so "alert when 500s climb" was unimplementable no matter what the alerting
config said.

These tests mount the handler and middleware from ``app.main`` onto a throwaway
app with one exploding route, rather than booting the real one — the real app's
lifespan wants Postgres and Redis, and none of that is what is under test.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY

from app.main import _http_requests_total, _normalise_path, _prometheus_middleware
from app.main import _unhandled_exception_handler as unhandled_handler


class _DriverError(RuntimeError):
    """Stands in for the asyncpg error whose str() carries SQL and parameters."""


_LEAKY_MESSAGE = (
    "insert or update on table \"users\" violates foreign key constraint; "
    "SQL: INSERT INTO users (email) VALUES ('candidate@example.com')"
)


def _app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(_prometheus_middleware)
    app.add_exception_handler(Exception, unhandled_handler)

    @app.get("/boom")
    async def _boom() -> dict[str, str]:
        raise _DriverError(_LEAKY_MESSAGE)

    @app.get("/fine")
    async def _fine() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def _requests_total(method: str, path: str, status_code: str) -> float:
    value = REGISTRY.get_sample_value(
        "http_requests_total",
        {"method": method, "path": path, "status_code": status_code},
    )
    return float(value or 0.0)


async def _client(app: FastAPI, **kw: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, **kw), base_url="http://test")


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
    parameters are candidate PII. The client gets a fixed string; the detail
    goes to the log stream, which the PII processor already redacts."""
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
    """The try/except must not double-count or drop the success path."""
    before = _requests_total("GET", "/fine", "200")

    async with await _client(_app()) as ac:
        assert (await ac.get("/fine")).status_code == 200

    assert _requests_total("GET", "/fine", "200") == before + 1


@pytest.mark.asyncio
async def test_a_500_observes_its_own_latency() -> None:
    """A request that took 30 seconds and then blew up used to contribute no
    latency sample at all, so the histogram was biased toward the healthy path."""
    from app.main import _http_request_duration_seconds  # noqa: PLC0415

    before = REGISTRY.get_sample_value(
        "http_request_duration_seconds_count", {"method": "GET", "path": "/boom"}
    )

    async with await _client(_app(), raise_app_exceptions=False) as ac:
        await ac.get("/boom")

    after = REGISTRY.get_sample_value(
        "http_request_duration_seconds_count", {"method": "GET", "path": "/boom"}
    )
    assert float(after or 0) == float(before or 0) + 1
    assert _http_request_duration_seconds is not None


def test_uuid_paths_are_collapsed_before_they_reach_a_label() -> None:
    """Shared by both branches now, so a 500 on /hr/applicants/{uuid} cannot
    mint one metric series per applicant."""
    assert (
        _normalise_path("/hr/applicants/2b1f9c14-0b6a-4c2f-9a1e-1b2c3d4e5f60/rescore")
        == "/hr/applicants/{id}/rescore"
    )
    assert _normalise_path("/consent/status") == "/consent/status"


def test_metrics_endpoint_is_still_excluded_from_its_own_counter() -> None:
    from app.main import _record  # noqa: PLC0415

    before = _requests_total("GET", "/metrics", "500")
    _record("GET", "/metrics", "500", 0.1)
    assert _requests_total("GET", "/metrics", "500") == before
    assert _http_requests_total is not None
