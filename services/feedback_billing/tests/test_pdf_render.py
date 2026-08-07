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
  6. test_build_pdf_bytes_escapes_candidate_markup
     — candidate-controlled markup never reaches ReportLab's paraparser live
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
# test_build_pdf_bytes_escapes_candidate_markup
# ---------------------------------------------------------------------------


def test_build_pdf_bytes_escapes_candidate_markup() -> None:
    """Candidate-controlled text is inert: no live markup reaches paraparser.

    The dangerous tag is <img src="...">, which ReportLab resolves through
    ImageReader — an SSRF from inside the container, on a name the candidate
    edits themselves via PATCH /auth/me/profile.
    """
    from reportlab.platypus import Paragraph as _RealParagraph  # noqa: PLC0415

    hostile_name = '<img src="http://169.254.169.254/latest/meta-data/"/>'
    hostile_summary = "Answered <b>well</b> & <font color=red>clearly</font>"

    with patch(
        "app.pdf_render.Paragraph", wraps=_RealParagraph
    ) as mock_paragraph:
        pdf = _build_pdf_bytes(
            scorecard_id=_SCORECARD_ID,
            candidate_name=hostile_name,
            job_title="Welder & Fabricator <script>",
            language="en",
            scores=_SAMPLE_SCORES,
            composite_score=7.05,
            strengths=["Bench work <b>strong</b>"],
            improvements=[{"area": "Depth & rigour", "suggestion": "<i>Practise</i>"}],
            summary=hostile_summary,
        )

    assert pdf[:5] == b"%PDF-"

    rendered = [call.args[0] for call in mock_paragraph.call_args_list if call.args]
    hostile_fragments = ("<img", "<script", "<font", "<i>")
    for text in rendered:
        for fragment in hostile_fragments:
            assert fragment not in text, f"live markup reached paraparser: {text!r}"

    # The escaped forms are present, i.e. the text is rendered, not dropped.
    joined = "\n".join(rendered)
    assert "&lt;img" in joined
    assert "Welder &amp; Fabricator" in joined
    assert "Depth &amp; rigour" in joined
    # The template's own bold markup must stay live.
    assert any(t.startswith("<b>Candidate</b>") or t == "<b>Candidate</b>" for t in rendered)


# ---------------------------------------------------------------------------
# test_build_pdf_bytes_escapes_language_fallback
# ---------------------------------------------------------------------------


def test_build_pdf_bytes_escapes_language_fallback() -> None:
    """The `language` fallback branch (unrecognised code) is untrusted input too.

    ScoreRequest constrains language to en/hi/te at the API boundary (Literal),
    but _build_pdf_bytes has no such guarantee of its own — call it directly
    with a hostile value and confirm the fallback still escapes before the
    paraparser sees it, exactly like candidate_name/job_title/summary.
    """
    from xml.sax.saxutils import escape as _xml_escape  # noqa: PLC0415

    from reportlab.platypus import Paragraph as _RealParagraph  # noqa: PLC0415

    hostile_language = '<img src="http://169.254.169.254/latest/meta-data/"/>'

    with patch("app.pdf_render.Paragraph", wraps=_RealParagraph) as mock_paragraph:
        pdf = _build_pdf_bytes(
            scorecard_id=_SCORECARD_ID,
            candidate_name="Ravi Kumar",
            job_title="Junior Java Developer",
            language=hostile_language,
            scores=_SAMPLE_SCORES,
            composite_score=7.05,
            strengths=_SAMPLE_STRENGTHS,
            improvements=_SAMPLE_IMPROVEMENTS,
            summary=_SAMPLE_SUMMARY,
        )

    assert pdf[:5] == b"%PDF-"

    rendered = [call.args[0] for call in mock_paragraph.call_args_list if call.args]
    for text in rendered:
        assert "<img" not in text.lower(), f"live markup reached paraparser: {text!r}"

    # The fallback branch is `language.upper()` escaped — confirm the exact
    # escaped form is present, i.e. the text is rendered, not dropped.
    expected_escaped = _xml_escape(hostile_language.upper())
    assert expected_escaped in "\n".join(rendered)


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


# ---------------------------------------------------------------------------
# _upload_to_s3 — the shared client factory (SVC-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_to_s3_uses_path_style_addressing_for_a_custom_endpoint() -> None:
    """R2 / MinIO need PATH-style addressing; this call site used to omit it.

    Virtual-host style resolves to `<bucket>.<endpoint-host>`, which does not
    exist for R2 — the upload fails at DNS in exactly one deployment, which is
    why the drift survived review. Asserted through the real shared.s3 factory
    (only aioboto3.Session itself is faked) so a regression in either half is
    caught here.
    """
    from app.pdf_render import _upload_to_s3  # noqa: PLC0415

    settings = _make_settings(with_s3=True)
    mock_s3 = AsyncMock()
    mock_s3.__aenter__ = AsyncMock(return_value=mock_s3)
    mock_s3.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.client = MagicMock(return_value=mock_s3)

    with patch("aioboto3.Session", return_value=mock_session) as mock_session_cls:
        await _upload_to_s3(
            pdf_bytes=b"%PDF-1.4 fake",
            s3_key=f"scorecards/{_SCORECARD_ID}/report.pdf",
            settings=settings,
        )

    client_kwargs = mock_session.client.call_args.kwargs
    assert client_kwargs["endpoint_url"] == settings.s3_endpoint_url
    assert client_kwargs["config"].s3["addressing_style"] == "path"
    # feedback_billing has no s3_use_ssl setting and talks to R2 over TLS, so
    # the factory's default must not silently downgrade it.
    assert client_kwargs["use_ssl"] is True
    # Credentials still come from Settings, not from botocore's chain — on a
    # non-AWS host the instance-metadata leg of that chain hangs for minutes.
    session_kwargs = mock_session_cls.call_args.kwargs
    assert session_kwargs["aws_access_key_id"] == settings.s3_access_key_id
    assert session_kwargs["aws_secret_access_key"] == settings.s3_secret_access_key

    put_kwargs = mock_s3.put_object.call_args.kwargs
    assert put_kwargs["Bucket"] == settings.s3_scorecard_bucket
    assert put_kwargs["Key"] == f"scorecards/{_SCORECARD_ID}/report.pdf"
    assert put_kwargs["ContentType"] == "application/pdf"
    assert put_kwargs["ContentDisposition"] == "inline"
