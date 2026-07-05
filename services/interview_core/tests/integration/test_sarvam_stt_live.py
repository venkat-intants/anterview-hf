"""Live integration test for the Sarvam STT adapter (S3-001).

This test hits the REAL Sarvam ``/speech-to-text`` endpoint with a synthetic
1-second silence WAV. Its purpose is to verify our request shape matches what
Sarvam actually accepts — catching header name changes or field renames before
they become demo-day surprises.

SKIP BY DEFAULT: only runs when explicitly selected with ``pytest -m integration``.

Run command (from services/interview_core/):
    poetry run pytest tests/integration/test_sarvam_stt_live.py -v -m integration

Prerequisites:
  - ``SARVAM_API_KEY`` must be set in ``.env`` or the environment.
  - ``SARVAM_STT_MODEL`` should be ``saaras:v3`` (default after Sprint 3).

WAV fixture: a minimal 1-second PCM silence (16 kHz, 16-bit, mono).
  Format: RIFF/WAVE header + 32000 bytes of 0x00.
  This exercises the full request path without exposing any real PII.
"""

from __future__ import annotations

import struct

import pytest

from app.config import settings
from app.speech.base import STTError, STTResult
from app.speech.sarvam_stt import SarvamSTTAdapter


def _make_silence_wav(sample_rate: int = 16000, duration_seconds: int = 1) -> bytes:
    """Generate a valid WAV header + silence PCM payload.

    WAV format:
      - RIFF header (12 bytes)
      - fmt  chunk (24 bytes): PCM, mono, 16-bit
      - data chunk header (8 bytes)
      - data payload: sample_rate * duration * 2 bytes (16-bit samples)

    This produces the smallest valid WAV that Sarvam will accept without
    rejecting due to malformed headers.
    """
    num_samples = sample_rate * duration_seconds
    data_size = num_samples * 2  # 16-bit = 2 bytes per sample
    riff_size = 36 + data_size  # RIFF chunk size = header remainder + data

    wav = bytearray()
    # RIFF chunk descriptor
    wav += b"RIFF"
    wav += struct.pack("<I", riff_size)
    wav += b"WAVE"
    # fmt sub-chunk
    wav += b"fmt "
    wav += struct.pack("<I", 16)          # subchunk1 size (PCM = 16)
    wav += struct.pack("<H", 1)           # audio format: PCM = 1
    wav += struct.pack("<H", 1)           # num channels: mono = 1
    wav += struct.pack("<I", sample_rate) # sample rate
    wav += struct.pack("<I", sample_rate * 2)  # byte rate = sampleRate * channels * bitsPerSample/8
    wav += struct.pack("<H", 2)           # block align = channels * bitsPerSample/8
    wav += struct.pack("<H", 16)          # bits per sample
    # data sub-chunk
    wav += b"data"
    wav += struct.pack("<I", data_size)
    wav += b"\x00" * data_size            # silence

    return bytes(wav)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sarvam_stt_live_silence() -> None:
    """Hit real Sarvam endpoint with a 1-second silence WAV.

    Verifies:
      1. Our auth header name is accepted (not 401/403).
      2. The multipart field names are correct (not 422).
      3. The response parses into an STTResult without an exception.

    Note: Silence may return an empty transcript (Sarvam may 200 with
    transcript=""). We allow that here — we only assert no HTTP error is
    raised. The empty-transcript guard is tested in unit tests.
    """
    if not settings.sarvam_api_key:
        pytest.skip("SARVAM_API_KEY not set — skipping live Sarvam test")

    silence_wav = _make_silence_wav(sample_rate=16000, duration_seconds=1)
    adapter = SarvamSTTAdapter(
        api_key=settings.sarvam_api_key,
        model=settings.sarvam_stt_model,
    )

    try:
        result = await adapter.transcribe(silence_wav, language="en")
        # If Sarvam returns a non-empty transcript for silence (unlikely),
        # make sure the result still has the right shape.
        assert isinstance(result, STTResult)
        assert isinstance(result.transcript, str)
        assert isinstance(result.language, str)
        assert 0.0 <= result.confidence <= 1.0
    except STTError as exc:
        # An empty transcript STTError is acceptable for silence input.
        # Any HTTP error (401, 422, 500) indicates a request shape problem.
        if exc.status is not None and exc.status not in (200,):
            pytest.fail(
                f"Sarvam returned HTTP {exc.status} — "
                f"check request shape. body={exc.body!r}"
            )
        # STTError("empty transcript") with no status is fine for silence.
