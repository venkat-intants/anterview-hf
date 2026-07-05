"""Tests for feedback_billing observability additions.

Covers:
  1. PII redaction processor — drops email/password/phone/full_name from log events.
  2. Prometheus /metrics endpoint — returns 200 with valid text/plain content.
  3. PDF render runs in a thread (asyncio.to_thread) — verifies the sync
     _build_pdf_bytes is not called on the event loop thread directly.
  4. Sentry init is a no-op when sentry_dsn is empty.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# 1. PII redaction processor unit test
# ---------------------------------------------------------------------------


def _make_event_dict(**kwargs: Any) -> MutableMapping[str, Any]:
    return dict(kwargs)


def test_pii_redaction_drops_known_fields() -> None:
    """_redact_pii_processor must drop email/password/phone/full_name."""
    from app.main import _redact_pii_processor

    event: MutableMapping[str, Any] = _make_event_dict(
        event="user.login",
        email="candidate@example.com",
        password="s3cr3t",
        phone="+91-9876543210",
        full_name="Ravi Kumar",
        session_id="abc-123",
        composite_score=7.5,
    )
    result = _redact_pii_processor(None, "info", event)  # type: ignore[arg-type]

    # PII fields must not appear in the result.
    assert "email" not in result
    assert "password" not in result
    assert "phone" not in result
    assert "full_name" not in result

    # Non-PII fields must be preserved.
    assert result["event"] == "user.login"
    assert result["session_id"] == "abc-123"
    assert result["composite_score"] == 7.5


def test_pii_redaction_is_idempotent_when_no_pii_present() -> None:
    """If no PII keys are present the event dict passes through unchanged."""
    from app.main import _redact_pii_processor

    event: MutableMapping[str, Any] = _make_event_dict(
        event="scorer.complete",
        scorecard_id="xyz-456",
        composite_score=8.0,
        model="gemini-2.5-flash",
    )
    original_keys = set(event.keys())
    result = _redact_pii_processor(None, "info", event)  # type: ignore[arg-type]
    assert set(result.keys()) == original_keys


def test_pii_redaction_handles_partial_pii() -> None:
    """Only the PII fields that are present get dropped; others are untouched."""
    from app.main import _redact_pii_processor

    event: MutableMapping[str, Any] = _make_event_dict(
        event="something",
        email="x@y.com",
        session_id="s1",
    )
    result = _redact_pii_processor(None, "info", event)  # type: ignore[arg-type]
    assert "email" not in result
    assert result["session_id"] == "s1"


# ---------------------------------------------------------------------------
# 2. Prometheus /metrics endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_metrics_endpoint_returns_200(client: TestClient) -> None:
    """/metrics must respond with HTTP 200."""
    resp = client.get("/metrics")
    assert resp.status_code == 200


def test_metrics_endpoint_content_type(client: TestClient) -> None:
    """/metrics must return text/plain (Prometheus exposition format)."""
    resp = client.get("/metrics")
    assert resp.headers["content-type"].startswith("text/plain")


def test_metrics_endpoint_contains_http_histogram(client: TestClient) -> None:
    """/metrics must include the default HTTP request duration histogram."""
    # Trigger a real request first so a metric is recorded.
    client.get("/health/live")
    resp = client.get("/metrics")
    body = resp.text
    # prometheus-fastapi-instrumentator always exposes this metric.
    assert "http_request_duration_seconds" in body or "http_requests_total" in body


# ---------------------------------------------------------------------------
# 3. PDF render runs _build_pdf_bytes in a thread (asyncio.to_thread)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdf_render_uses_thread_for_build() -> None:
    """render_scorecard_pdf must call asyncio.to_thread to offload the sync
    ReportLab build.  We patch asyncio.to_thread and verify it is invoked.
    """
    from app.config import Settings
    from app.pdf_render import render_scorecard_pdf

    settings = Settings(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        gemini_api_key="test-key",
        jwt_secret="test-secret-that-is-at-least-32-chars-long!!",
        s3_access_key_id="key-id",
        s3_secret_access_key="secret",
        s3_endpoint_url="https://fake.r2.cloudflarestorage.com",
        s3_scorecard_bucket="intants-interview-scorecards",
    )
    fake_pdf_bytes = b"%PDF-1.4 fake"

    to_thread_called: list[bool] = []

    async def _fake_to_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
        to_thread_called.append(True)
        # Actually invoke the partial so the rest of the pipeline works.
        return fn()

    with (
        patch("app.pdf_render.asyncio.to_thread", side_effect=_fake_to_thread),
        patch(
            "app.pdf_render._build_pdf_bytes",
            return_value=fake_pdf_bytes,
        ),
        patch(
            "app.pdf_render._upload_to_s3",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await render_scorecard_pdf(
            "sc-id-1",
            "sess-id-1",
            "Ravi Kumar",
            "Junior Java Dev",
            "en",
            {"communication": 7, "technical": 6, "problem_solving": 8, "confidence": 7},
            7.05,
            ["Strength A"],
            [{"area": "Area B", "suggestion": "Do X"}],
            "A solid candidate.",
            settings=settings,
        )

    assert result == "scorecards/sc-id-1/report.pdf"
    assert to_thread_called, "asyncio.to_thread was not called — PDF build is blocking the loop"


@pytest.mark.asyncio
async def test_pdf_render_returns_none_on_thread_exception() -> None:
    """If asyncio.to_thread raises (build failure), None is returned — no exception propagates."""
    from app.config import Settings
    from app.pdf_render import render_scorecard_pdf

    settings = Settings(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        gemini_api_key="test-key",
        jwt_secret="test-secret-that-is-at-least-32-chars-long!!",
        s3_access_key_id="key-id",
        s3_secret_access_key="secret",
        s3_scorecard_bucket="intants-interview-scorecards",
    )

    async def _raising_to_thread(fn: Any, *args: Any, **kwargs: Any) -> bytes:
        raise MemoryError("ReportLab OOM in thread")

    with patch("app.pdf_render.asyncio.to_thread", side_effect=_raising_to_thread):
        result = await render_scorecard_pdf(
            "sc-id-2",
            "sess-id-2",
            "Priya Singh",
            "Data Analyst",
            "hi",
            {"communication": 5, "technical": 5, "problem_solving": 5, "confidence": 5},
            5.0,
            [],
            [],
            "Average performance.",
            settings=settings,
        )

    assert result is None


# ---------------------------------------------------------------------------
# 4. Sentry is no-op when sentry_dsn is empty
# ---------------------------------------------------------------------------


def test_sentry_init_noop_when_dsn_empty() -> None:
    """init_sentry must return False (no-op) when DSN is empty."""
    from shared.observability.sentry import init_sentry

    result = init_sentry("", environment="test", service_name="feedback_billing")
    assert result is False


def test_sentry_init_noop_when_dsn_is_whitespace() -> None:
    """init_sentry must return False when DSN is only whitespace."""
    from shared.observability.sentry import init_sentry

    result = init_sentry("   ", environment="test", service_name="feedback_billing")
    assert result is False


def test_sentry_init_noop_when_dsn_is_none() -> None:
    """init_sentry must return False when DSN is None."""
    from shared.observability.sentry import init_sentry

    result = init_sentry(None, environment="test", service_name="feedback_billing")
    assert result is False
