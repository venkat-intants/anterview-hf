"""Unit tests for the Sarvam TTS adapter (S3-002).

All tests are fully offline — the HTTP layer is mocked via
``unittest.mock.AsyncMock`` patching ``httpx.AsyncClient``, matching the
exact same pattern used in ``test_sarvam_stt.py`` (S3-001).

No real Sarvam API calls are made here.

Coverage:
  - ``test_synthesize_success_en``              happy path, English
  - ``test_synthesize_success_te``              happy path, Telugu
  - ``test_synthesize_http_error_raises_ttserror``  429 -> TTSError
  - ``test_synthesize_500_raises_ttserror``     500 -> TTSError
  - ``test_network_error_raises_ttserror``      httpx.ConnectTimeout -> TTSError
  - ``test_empty_text_raises``                  "" -> TTSError(status=400) no HTTP call
  - ``test_empty_audios_array_raises``          200 + audios=[] -> TTSError
  - ``test_language_code_sent_in_request``      language="hi" -> target_language_code="hi-IN"
  - ``test_model_sent_in_request``              model="bulbul:v2" in JSON body
  - ``test_unsupported_language_raises``        language="fr" -> TTSError before HTTP
  - ``test_no_api_key_raises``                  empty key -> RuntimeError at construction
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.speech.base import TTSError, TTSResult
from app.speech.sarvam_tts import (
    _LANGUAGE_CODE_MAP,
    _LANGUAGE_VOICE_MAP,
    AUTH_HEADER,
    SARVAM_TTS_URL,
    SarvamTTSAdapter,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "bulbul:v2"

# Minimal fake WAV payload — the adapter decodes base64 and returns bytes
# verbatim, so any bytes work here.
_FAKE_AUDIO: bytes = b"WAVDATA"
_FAKE_AUDIO_B64: str = base64.b64encode(_FAKE_AUDIO).decode()


def _make_adapter(
    api_key: str = "test-key-abc123",
    model: str = _DEFAULT_MODEL,
) -> SarvamTTSAdapter:
    return SarvamTTSAdapter(api_key=api_key, model=model)


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


def _mock_client(post_return: httpx.Response | None = None,
                 post_side_effect: Exception | None = None) -> AsyncMock:
    """Return a fully wired AsyncMock for httpx.AsyncClient context manager."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    else:
        mock_client.post = AsyncMock(return_value=post_return)
    return mock_client


# ---------------------------------------------------------------------------
# Success path tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_success_en() -> None:
    """Happy path: English text returns TTSResult with decoded audio bytes."""
    sarvam_response = {
        "request_id": "req-tts-001",
        "audios": [_FAKE_AUDIO_B64],
    }
    mock_resp = _make_mock_response(200, sarvam_response)
    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client(post_return=mock_resp)
        result = await adapter.synthesize("Hello, how are you?", language="en")

    assert isinstance(result, TTSResult)
    assert result.audio_bytes == _FAKE_AUDIO
    assert result.format == "wav"
    # v3 default sample rate (verified live 2026-05-31); v2 used 22050.
    assert result.sample_rate == 24000


@pytest.mark.asyncio
async def test_synthesize_success_te() -> None:
    """Happy path: Telugu text returns TTSResult with decoded audio bytes."""
    sarvam_response = {
        "request_id": "req-tts-002",
        "audios": [_FAKE_AUDIO_B64],
    }
    mock_resp = _make_mock_response(200, sarvam_response)
    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client(post_return=mock_resp)
        result = await adapter.synthesize("నమస్కారం", language="te")

    assert isinstance(result, TTSResult)
    assert result.audio_bytes == _FAKE_AUDIO


# ---------------------------------------------------------------------------
# HTTP error path tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_http_error_raises_ttserror() -> None:
    """HTTP 429 from Sarvam must raise TTSError with .status == 429."""
    mock_resp = _make_mock_response(429, "Too Many Requests")
    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client(post_return=mock_resp)
        with pytest.raises(TTSError) as exc_info:
            await adapter.synthesize("Hello.", language="en")

    assert exc_info.value.status == 429
    assert exc_info.value.body is not None


@pytest.mark.asyncio
async def test_synthesize_500_raises_ttserror() -> None:
    """HTTP 500 must raise TTSError with .status == 500."""
    mock_resp = _make_mock_response(500, "Internal Server Error")
    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client(post_return=mock_resp)
        with pytest.raises(TTSError) as exc_info:
            await adapter.synthesize("Hello.", language="en")

    assert exc_info.value.status == 500


@pytest.mark.asyncio
async def test_network_error_raises_ttserror() -> None:
    """httpx.ConnectTimeout must be wrapped in TTSError (no status)."""
    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client(
            post_side_effect=httpx.ConnectTimeout("Connection timed out")
        )
        with pytest.raises(TTSError) as exc_info:
            await adapter.synthesize("Hello.", language="hi")

    assert "network:" in str(exc_info.value)
    assert exc_info.value.status is None


# ---------------------------------------------------------------------------
# Input validation tests (no HTTP call must be made)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_text_raises() -> None:
    """synthesize("") must raise TTSError(status=400) WITHOUT making an HTTP call."""
    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = _mock_client()
        mock_cls.return_value = mock_client

        with pytest.raises(TTSError) as exc_info:
            await adapter.synthesize("", language="en")

    assert exc_info.value.status == 400
    # HTTP call must NOT have been made — the guard fires before the client is used.
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_empty_text_whitespace_raises() -> None:
    """synthesize("   ") (whitespace-only) must also raise TTSError(status=400)."""
    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = _mock_client()
        mock_cls.return_value = mock_client

        with pytest.raises(TTSError) as exc_info:
            await adapter.synthesize("   ", language="en")

    assert exc_info.value.status == 400
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_unsupported_language_raises() -> None:
    """language="fr" must raise TTSError before any HTTP call."""
    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = _mock_client()
        mock_cls.return_value = mock_client

        with pytest.raises(TTSError) as exc_info:
            await adapter.synthesize("Bonjour.", language="fr")

    assert "unsupported language" in str(exc_info.value).lower()
    mock_client.post.assert_not_called()


# ---------------------------------------------------------------------------
# Response parsing tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_audios_array_raises() -> None:
    """A 200 response with audios=[] must raise TTSError(status=200)."""
    sarvam_response = {"request_id": "req-tts-003", "audios": []}
    mock_resp = _make_mock_response(200, sarvam_response)
    adapter = _make_adapter()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client(post_return=mock_resp)
        with pytest.raises(TTSError) as exc_info:
            await adapter.synthesize("Hello.", language="en")

    assert exc_info.value.status == 200
    assert exc_info.value.body is not None
    assert "empty" in (exc_info.value.body or "").lower()


# ---------------------------------------------------------------------------
# Request shape tests — assert what we send to Sarvam
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_language_code_sent_in_request() -> None:
    """Outgoing JSON must contain target_language_code="hi-IN" when language="hi"."""
    sarvam_response = {"request_id": "req-tts-004", "audios": [_FAKE_AUDIO_B64]}
    mock_resp = _make_mock_response(200, sarvam_response)
    adapter = _make_adapter()
    captured_kwargs: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured_kwargs.update(kwargs)
        return mock_resp

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _capture_post
        mock_cls.return_value = mock_client

        await adapter.synthesize("नमस्ते", language="hi")

    payload = captured_kwargs.get("json", {})
    assert payload.get("target_language_code") == "hi-IN", (
        f"Expected 'hi-IN', got {payload.get('target_language_code')!r}"
    )


@pytest.mark.asyncio
async def test_model_sent_in_request() -> None:
    """Outgoing JSON must contain model="bulbul:v2"."""
    sarvam_response = {"request_id": "req-tts-005", "audios": [_FAKE_AUDIO_B64]}
    mock_resp = _make_mock_response(200, sarvam_response)
    adapter = _make_adapter(model="bulbul:v2")
    captured_kwargs: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured_kwargs.update(kwargs)
        return mock_resp

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _capture_post
        mock_cls.return_value = mock_client

        await adapter.synthesize("Hello.", language="en")

    payload = captured_kwargs.get("json", {})
    assert payload.get("model") == "bulbul:v2", (
        f"Expected 'bulbul:v2', got {payload.get('model')!r}"
    )


@pytest.mark.asyncio
async def test_default_voice_sent_in_request() -> None:
    """When voice kwarg is None, the per-language default speaker is sent."""
    sarvam_response = {"request_id": "req-tts-006", "audios": [_FAKE_AUDIO_B64]}
    mock_resp = _make_mock_response(200, sarvam_response)
    adapter = _make_adapter()
    captured_kwargs: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured_kwargs.update(kwargs)
        return mock_resp

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _capture_post
        mock_cls.return_value = mock_client

        await adapter.synthesize("Hello.", language="en", voice=None)

    payload = captured_kwargs.get("json", {})
    assert payload.get("speaker") == _LANGUAGE_VOICE_MAP["en"]


@pytest.mark.asyncio
async def test_custom_voice_sent_in_request() -> None:
    """When voice kwarg is provided it must override the default speaker."""
    sarvam_response = {"request_id": "req-tts-007", "audios": [_FAKE_AUDIO_B64]}
    mock_resp = _make_mock_response(200, sarvam_response)
    adapter = _make_adapter()
    captured_kwargs: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured_kwargs.update(kwargs)
        return mock_resp

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _capture_post
        mock_cls.return_value = mock_client

        await adapter.synthesize("Hello.", language="en", voice="meera")

    payload = captured_kwargs.get("json", {})
    assert payload.get("speaker") == "meera"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "expected_speaker"),
    [(lang, _LANGUAGE_VOICE_MAP[lang]) for lang in ("en", "hi", "te")],
)
async def test_per_language_default_speaker(
    language: str, expected_speaker: str
) -> None:
    """B-038/B-039: with no explicit voice, the speaker is chosen per language.

    Asserts against the live _LANGUAGE_VOICE_MAP so it stays correct when the
    founder-selected voices change. Speakers must be valid for the configured
    Sarvam model (currently bulbul:v3: pooja/shreya/kavya).
    """
    sarvam_response = {"request_id": "req-tts-lang", "audios": [_FAKE_AUDIO_B64]}
    mock_resp = _make_mock_response(200, sarvam_response)
    adapter = _make_adapter()
    captured_kwargs: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured_kwargs.update(kwargs)
        return mock_resp

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _capture_post
        mock_cls.return_value = mock_client

        await adapter.synthesize("Test.", language=language)

    payload = captured_kwargs.get("json", {})
    assert payload.get("speaker") == expected_speaker


# ---------------------------------------------------------------------------
# Construction-time validation tests
# ---------------------------------------------------------------------------


def test_no_api_key_raises() -> None:
    """Empty api_key must raise RuntimeError at construction time."""
    with pytest.raises(RuntimeError) as exc_info:
        SarvamTTSAdapter(api_key="", model=_DEFAULT_MODEL)

    assert "api_key is required" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Module-level constant tests
# ---------------------------------------------------------------------------


def test_language_code_map() -> None:
    """Confirm the three Day-1 language mappings are correct."""
    assert _LANGUAGE_CODE_MAP["en"] == "en-IN"
    assert _LANGUAGE_CODE_MAP["hi"] == "hi-IN"
    assert _LANGUAGE_CODE_MAP["te"] == "te-IN"


def test_auth_header_constant() -> None:
    """Auth header must match Sarvam's documented header name."""
    assert AUTH_HEADER == "api-subscription-key"


def test_tts_url_constant() -> None:
    """TTS endpoint URL must match Sarvam docs."""
    assert SARVAM_TTS_URL == "https://api.sarvam.ai/text-to-speech"
