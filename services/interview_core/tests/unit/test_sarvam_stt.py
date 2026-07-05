"""Unit tests for the Sarvam STT adapter (S3-001).

All tests are fully offline — the HTTP layer is mocked via
``httpx.MockTransport``. No real Sarvam API calls are made here.

The audio fixture is ``b"\\x00" * 1024`` (silent bytes) since we're mocking
the HTTP layer; the adapter never inspects the audio content itself.

Coverage:
  - ``test_transcribe_success_en``          happy path, English response
  - ``test_transcribe_success_te``          happy path, Telugu response
  - ``test_transcribe_http_error_raises_stterror``  429 -> STTError
  - ``test_language_code_translation``      en/hi/te -> *-IN mapping in request
  - ``test_no_api_key_raises``              empty key -> STTError at construction
  - ``test_empty_transcript_raises``        blank transcript -> STTError
  - ``test_unknown_language_raises``        unsupported lang code -> STTError
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.speech.base import STTError, STTResult
from app.speech.sarvam_stt import (
    _LANGUAGE_CODE_MAP,
    AUTH_HEADER,
    SARVAM_STT_URL,
    SarvamSTTAdapter,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SILENT_AUDIO: bytes = b"\x00" * 1024  # 1 KB of silence — HTTP layer is mocked

_DEFAULT_MODEL = "saaras:v3"


def _make_adapter(
    api_key: str = "test-key-abc123",
    model: str = _DEFAULT_MODEL,
) -> SarvamSTTAdapter:
    return SarvamSTTAdapter(api_key=api_key, model=model)


def _make_mock_response(
    status_code: int,
    body: dict[str, Any] | str,
) -> httpx.Response:
    """Build a synthetic httpx.Response without hitting the network."""
    if isinstance(body, dict):
        content = json.dumps(body).encode()
        headers = {"content-type": "application/json"}
    else:
        content = body.encode()
        headers = {"content-type": "text/plain"}
    return httpx.Response(status_code=status_code, content=content, headers=headers)


# ---------------------------------------------------------------------------
# Success path tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transcribe_success_en() -> None:
    """Happy path: English audio returns STTResult with transcript and language='en'."""
    sarvam_response = {
        "request_id": "req-001",
        "transcript": "hello world",
        "language_code": "en-IN",
        "language_probability": 0.97,
    }
    mock_resp = _make_mock_response(200, sarvam_response)

    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await adapter.transcribe(SILENT_AUDIO, language="en")

    assert isinstance(result, STTResult)
    assert result.transcript == "hello world"
    assert result.language == "en"
    assert isinstance(result.confidence, float)
    assert 0.0 <= result.confidence <= 1.0
    assert result.confidence == pytest.approx(0.97)


@pytest.mark.asyncio
async def test_transcribe_success_te() -> None:
    """Happy path: Telugu audio returns STTResult with language='te'."""
    sarvam_response = {
        "request_id": "req-002",
        "transcript": "నమస్కారం",
        "language_code": "te-IN",
        "language_probability": 0.91,
    }
    mock_resp = _make_mock_response(200, sarvam_response)

    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await adapter.transcribe(SILENT_AUDIO, language="te")

    assert result.transcript == "నమస్కారం"
    assert result.language == "te"
    assert result.confidence == pytest.approx(0.91)


@pytest.mark.asyncio
async def test_transcribe_confidence_defaults_to_1_when_absent() -> None:
    """When Sarvam omits language_probability, confidence must be 1.0."""
    sarvam_response = {
        "request_id": "req-003",
        "transcript": "hello",
        "language_code": "en-IN",
        # no language_probability field
    }
    mock_resp = _make_mock_response(200, sarvam_response)

    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await adapter.transcribe(SILENT_AUDIO, language="en")

    assert result.confidence == 1.0


# ---------------------------------------------------------------------------
# Error path tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transcribe_http_error_raises_stterror() -> None:
    """HTTP 429 from Sarvam must raise STTError with .status == 429."""
    mock_resp = _make_mock_response(429, "Too Many Requests")

    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        with pytest.raises(STTError) as exc_info:
            await adapter.transcribe(SILENT_AUDIO, language="en")

    assert exc_info.value.status == 429
    # Body must be present but we don't assert exact content — it's for logs.
    assert exc_info.value.body is not None


@pytest.mark.asyncio
async def test_transcribe_http_500_raises_stterror() -> None:
    """HTTP 500 must also raise STTError with .status == 500."""
    mock_resp = _make_mock_response(500, "Internal Server Error")

    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        with pytest.raises(STTError) as exc_info:
            await adapter.transcribe(SILENT_AUDIO, language="en")

    assert exc_info.value.status == 500


@pytest.mark.asyncio
async def test_network_error_raises_stterror() -> None:
    """httpx.HTTPError (e.g., timeout, DNS failure) must be wrapped in STTError."""
    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectTimeout("Connection timed out")
        )
        mock_client_cls.return_value = mock_client

        with pytest.raises(STTError) as exc_info:
            await adapter.transcribe(SILENT_AUDIO, language="hi")

    assert "network:" in str(exc_info.value)
    assert exc_info.value.status is None  # network errors have no HTTP status


@pytest.mark.asyncio
async def test_empty_transcript_raises() -> None:
    """A 200 response with an empty transcript must raise STTError."""
    sarvam_response = {
        "request_id": "req-004",
        "transcript": "",  # empty — happens with silent audio
        "language_code": "en-IN",
    }
    mock_resp = _make_mock_response(200, sarvam_response)

    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        with pytest.raises(STTError) as exc_info:
            await adapter.transcribe(SILENT_AUDIO, language="en")

    assert "empty transcript" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Language code translation tests
# ---------------------------------------------------------------------------


def test_language_code_translation() -> None:
    """Confirm en->en-IN, hi->hi-IN, te->te-IN in the module-level map."""
    assert _LANGUAGE_CODE_MAP["en"] == "en-IN"
    assert _LANGUAGE_CODE_MAP["hi"] == "hi-IN"
    assert _LANGUAGE_CODE_MAP["te"] == "te-IN"


@pytest.mark.asyncio
async def test_language_code_sent_in_request() -> None:
    """The outgoing multipart body must contain the correct vendor language code."""
    sarvam_response = {
        "request_id": "req-005",
        "transcript": "नमस्ते",
        "language_code": "hi-IN",
        "language_probability": 0.99,
    }
    mock_resp = _make_mock_response(200, sarvam_response)

    adapter = _make_adapter()
    captured_kwargs: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured_kwargs.update(kwargs)
        return mock_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _capture_post
        mock_client_cls.return_value = mock_client

        await adapter.transcribe(SILENT_AUDIO, language="hi")

    # Verify the files dict contains language_code mapped to hi-IN
    files = captured_kwargs.get("files", {})
    assert "language_code" in files, "language_code must be a multipart field"
    # files["language_code"] is a tuple (None, value) for non-file form fields
    lang_value = files["language_code"]
    assert lang_value[1] == "hi-IN", (
        f"Expected hi-IN in multipart, got {lang_value[1]!r}"
    )


@pytest.mark.asyncio
async def test_unsupported_language_raises_stterror() -> None:
    """An unsupported language code (e.g., 'fr') must raise STTError immediately."""
    adapter = _make_adapter()

    with pytest.raises(STTError) as exc_info:
        await adapter.transcribe(SILENT_AUDIO, language="fr")

    assert "unsupported language" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Construction-time validation tests
# ---------------------------------------------------------------------------


def test_no_api_key_raises() -> None:
    """Empty SARVAM_API_KEY must raise STTError at construction time with a clear message."""
    with pytest.raises(STTError) as exc_info:
        SarvamSTTAdapter(api_key="", model=_DEFAULT_MODEL)

    assert "api_key is required" in str(exc_info.value).lower()
    # Confirm it's a construction-time error — status is None
    assert exc_info.value.status is None


def test_auth_header_constant() -> None:
    """The auth header name must be the documented Sarvam header."""
    assert AUTH_HEADER == "api-subscription-key"


def test_stt_url_constant() -> None:
    """The STT endpoint URL must match the Sarvam docs."""
    assert SARVAM_STT_URL == "https://api.sarvam.ai/speech-to-text"


# ---------------------------------------------------------------------------
# Model field tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_sent_in_request() -> None:
    """The multipart body must include the configured model name."""
    sarvam_response = {
        "request_id": "req-006",
        "transcript": "test",
        "language_code": "en-IN",
    }
    mock_resp = _make_mock_response(200, sarvam_response)

    adapter = SarvamSTTAdapter(api_key="test-key", model="saaras:v3")
    captured_kwargs: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured_kwargs.update(kwargs)
        return mock_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _capture_post
        mock_client_cls.return_value = mock_client

        await adapter.transcribe(SILENT_AUDIO, language="en")

    files = captured_kwargs.get("files", {})
    assert "model" in files
    assert files["model"][1] == "saaras:v3"
