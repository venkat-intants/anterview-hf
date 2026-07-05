"""Unit tests for feedback_billing.app.pdf_render — S5-007.

Tests:
  1. test_render_scorecard_pdf_returns_s3_key
     — mock ReportLab + mock aioboto3 → key format 'scorecards/{id}/report.pdf'
  2. test_render_scorecard_pdf_returns_none_on_pdf_failure
     — mock ReportLab to raise → None returned, no exception propagated
  3. test_render_scorecard_pdf_returns_none_on_upload_failure
     — mock upload to raise → None returned, no exception propagated
  4. test_build_pdf_bytes_returns_bytes
     — end-to-end ReportLab call (no mocking) → returns non-empty bytes
  5. test_update_pdf_key_executes_update
     — mock DB session factory → verifies UPDATE is executed
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.pdf_render import _build_pdf_bytes, _update_pdf_key, render_scorecard_pdf

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SCORECARD_ID = str(uuid.uuid4())
_SESSION_ID = str(uuid.uuid4())

_SAMPLE_SCORES: dict[str, int] = {
    "communication": 7,
    "technical": 6,
    "problem_solving": 8,
    "confidence": 7,
}
_SAMPLE_STRENGTHS = [
    "Clear communication",
    "Good examples",
    "Structured thinking",
]
_SAMPLE_IMPROVEMENTS = [
    {"area": "Technical depth", "suggestion": "Practice system design"},
    {"area": "Confidence", "suggestion": "Speak more slowly"},
    {"area": "Problem solving", "suggestion": "State assumptions first"},
]
_SAMPLE_SUMMARY = "A solid entry-level candidate. Meets tier expectations on most axes."


def _make_settings(*, with_s3: bool = True) -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        gemini_api_key="test-key",
        jwt_secret="test-secret-that-is-at-least-32-chars-long!!",
        s3_access_key_id="test-key-id" if with_s3 else "",
        s3_secret_access_key="test-secret" if with_s3 else "",
        s3_endpoint_url="https://fake.r2.cloudflarestorage.com" if with_s3 else "",
        s3_scorecard_bucket="intants-interview-scorecards",
    )


# ---------------------------------------------------------------------------
# test_render_scorecard_pdf_returns_s3_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_scorecard_pdf_returns_s3_key() -> None:
    """Happy path: mock PDF build + mock S3 upload → returns expected key.

    We patch _build_pdf_bytes and _upload_to_s3 directly (aioboto3 is a local
    import inside _upload_to_s3, so we test at the helper boundary).
    """
    settings = _make_settings(with_s3=True)
    fake_pdf_bytes = b"%PDF-1.4 fake content"

    with (
        patch("app.pdf_render._build_pdf_bytes", return_value=fake_pdf_bytes),
        patch(
            "app.pdf_render._upload_to_s3",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_upload,
    ):
        result = await render_scorecard_pdf(
            _SCORECARD_ID,
            _SESSION_ID,
            "Ravi Kumar",
            "Junior Java Developer",
            "en",
            _SAMPLE_SCORES,
            7.05,
            _SAMPLE_STRENGTHS,
            _SAMPLE_IMPROVEMENTS,
            _SAMPLE_SUMMARY,
            settings=settings,
        )

    expected_key = f"scorecards/{_SCORECARD_ID}/report.pdf"
    assert result == expected_key
    mock_upload.assert_called_once()
    call_kwargs = mock_upload.call_args.kwargs
    assert call_kwargs["s3_key"] == expected_key
    assert call_kwargs["pdf_bytes"] == fake_pdf_bytes


# ---------------------------------------------------------------------------
# test_render_scorecard_pdf_returns_none_on_pdf_failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_scorecard_pdf_returns_none_on_pdf_failure() -> None:
    """If the PDF builder raises, None is returned and no exception propagates."""
    settings = _make_settings(with_s3=True)

    with patch(
        "app.pdf_render._build_pdf_bytes",
        side_effect=RuntimeError("ReportLab internal error"),
    ):
        result = await render_scorecard_pdf(
            _SCORECARD_ID,
            _SESSION_ID,
            "Ravi Kumar",
            "Junior Java Developer",
            "en",
            _SAMPLE_SCORES,
            7.05,
            _SAMPLE_STRENGTHS,
            _SAMPLE_IMPROVEMENTS,
            _SAMPLE_SUMMARY,
            settings=settings,
        )

    assert result is None


# ---------------------------------------------------------------------------
# test_render_scorecard_pdf_returns_none_on_upload_failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_scorecard_pdf_returns_none_on_upload_failure() -> None:
    """If the S3 upload raises, None is returned and no exception propagates."""
    settings = _make_settings(with_s3=True)
    fake_pdf_bytes = b"%PDF-1.4 fake content"

    with (
        patch("app.pdf_render._build_pdf_bytes", return_value=fake_pdf_bytes),
        patch(
            "app.pdf_render._upload_to_s3",
            new_callable=AsyncMock,
            side_effect=ConnectionError("S3 unreachable"),
        ),
    ):
        result = await render_scorecard_pdf(
            _SCORECARD_ID,
            _SESSION_ID,
            "Ravi Kumar",
            "Junior Java Developer",
            "en",
            _SAMPLE_SCORES,
            7.05,
            _SAMPLE_STRENGTHS,
            _SAMPLE_IMPROVEMENTS,
            _SAMPLE_SUMMARY,
            settings=settings,
        )

    assert result is None


# ---------------------------------------------------------------------------
# test_build_pdf_bytes_returns_bytes
# ---------------------------------------------------------------------------


def test_build_pdf_bytes_returns_bytes() -> None:
    """Real ReportLab call (no mocking) produces non-empty PDF bytes."""
    pdf = _build_pdf_bytes(
        scorecard_id=_SCORECARD_ID,
        candidate_name="Ravi Kumar",
        job_title="Junior Java Developer",
        language="en",
        scores=_SAMPLE_SCORES,
        composite_score=7.05,
        strengths=_SAMPLE_STRENGTHS,
        improvements=_SAMPLE_IMPROVEMENTS,
        summary=_SAMPLE_SUMMARY,
    )
    assert isinstance(pdf, bytes)
    # PDF magic bytes — every valid PDF starts with %PDF-
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000


# ---------------------------------------------------------------------------
# test_update_pdf_key_executes_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_pdf_key_executes_update() -> None:
    """_update_pdf_key opens a session and executes the UPDATE statement."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock(return_value=mock_session)

    await _update_pdf_key(_SCORECARD_ID, f"scorecards/{_SCORECARD_ID}/report.pdf", mock_factory)

    mock_session.execute.assert_called_once()
    mock_session.commit.assert_called_once()

    # Verify the bound parameters contain the right scorecard_id.
    call_args = mock_session.execute.call_args
    params: dict[str, Any] = call_args.args[1]
    assert params["scorecard_id"] == _SCORECARD_ID
    assert params["key"] == f"scorecards/{_SCORECARD_ID}/report.pdf"
