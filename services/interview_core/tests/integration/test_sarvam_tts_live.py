"""Live integration test for the Sarvam TTS adapter (S3-002).

This test hits the REAL Sarvam ``/text-to-speech`` endpoint with a short
English phrase. Its purpose is to verify our request shape matches what
Sarvam actually accepts — catching field renames or breaking API changes
before they become demo-day surprises.

SKIP BY DEFAULT: only runs when explicitly selected with ``pytest -m integration``.

Run command (from services/interview_core/):
    .venv/Scripts/python.exe -m pytest tests/integration/test_sarvam_tts_live.py -v -m integration

Prerequisites:
  - ``SARVAM_API_KEY`` must be set in ``.env`` or the environment.
  - ``SARVAM_TTS_MODEL`` should be ``bulbul:v2`` (default after Sprint 3).

The synthesized audio is discarded after basic validation — we never log or
store it, keeping this test PII-safe.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.speech.base import TTSError, TTSResult
from app.speech.sarvam_tts import SarvamTTSAdapter


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sarvam_tts_live_english() -> None:
    """Hit real Sarvam TTS endpoint with a short English phrase.

    Verifies:
      1. Our auth header name is accepted (not 401/403).
      2. The JSON field names are correct (not 422).
      3. The response contains non-empty audio bytes.
      4. The first 4 bytes are ``b"RIFF"`` — confirming WAV format.
    """
    if not settings.sarvam_api_key:
        pytest.skip("SARVAM_API_KEY not set — skipping live Sarvam TTS test")

    adapter = SarvamTTSAdapter(
        api_key=settings.sarvam_api_key,
        model=settings.sarvam_tts_model,
    )

    try:
        result = await adapter.synthesize(
            "Hello, this is a test.",
            language="en",
        )
        assert isinstance(result, TTSResult)
        assert len(result.audio_bytes) > 0, "audio_bytes must be non-empty"
        # WAV files start with the RIFF header.
        assert result.audio_bytes[:4] == b"RIFF", (
            f"Expected WAV RIFF header, got {result.audio_bytes[:4]!r}"
        )
        assert result.format == "wav"
        assert result.sample_rate == 22050
    except TTSError as exc:
        pytest.fail(
            f"Sarvam TTS returned an error — "
            f"status={exc.status}, body={exc.body!r}. "
            "Check request shape and API key."
        )
