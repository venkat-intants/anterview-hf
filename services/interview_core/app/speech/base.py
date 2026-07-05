"""STT and TTS adapter protocols — provider-neutral surface used by the LangGraph nodes.

The interview graph never imports a concrete STT/TTS provider. It depends only
on ``STTAdapter`` / ``TTSAdapter`` so that swapping Sarvam -> Bhashini -> OpenAI
is a constructor change in ``app.main`` (or a test fixture) — no node code edits.

Each adapter implementation is responsible for:

  - Translating our neutral input shape into its native wire format.
  - Surfacing a uniform ``STTError`` / ``TTSError`` for any non-success path so
    callers have a single exception type to catch.
  - Returning an ``STTResult`` / ``TTSResult`` with the fields the graph and
    observability layers care about.

NOTE on language codes: callers pass short ISO codes (``en``, ``hi``, ``te``).
Each adapter translates internally to its vendor format (e.g., Sarvam uses
``en-IN``, ``hi-IN``, ``te-IN``). The graph should never know vendor formats.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class STTResult:
    """Adapter-neutral transcription result returned by ``STTAdapter.transcribe``.

    Frozen so it can be safely shared across coroutines without defensive copies.
    """

    transcript: str
    language: str  # Short ISO code as returned by the adapter (e.g., "en")
    confidence: float  # 0.0–1.0; adapters that don't return a score report 1.0


class STTError(Exception):
    """Single exception type for ANY STT adapter failure.

    Carries an optional status code (HTTP for REST adapters, ``None`` for
    SDK / network errors) and an optional raw body snippet for debugging.

    Callers should catch ``STTError`` and surface it as a uniform
    ``{"type":"error","code":"STT_FAILURE"}`` to the client; the message
    string is for logs only — never echo it to the candidate.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        # Truncate aggressively — provider bodies can be multi-KB.
        self.body = body[:500] if body else None


@runtime_checkable
class STTAdapter(Protocol):
    """Provider-neutral async STT surface used by the LangGraph nodes."""

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str,
        sample_rate: int = 16000,
    ) -> STTResult:
        """Transcribe audio bytes and return a neutral ``STTResult``.

        Args:
            audio_bytes: Raw WAV or PCM 16-bit LE audio. Callers must NOT pass
                compressed formats (MP3, AAC, webm/opus) — adapters assume
                PCM-compatible input. See research/sarvam-pricing-2026-05.md §4.
            language: Short ISO language code (``"en"``, ``"hi"``, ``"te"``).
                Adapters translate to their vendor format internally.
            sample_rate: Sample rate in Hz. Default 16000 (recommended by Sarvam).
                8000 Hz is also supported for telephony.

        Returns:
            ``STTResult`` with the transcript text, detected language code
            (normalised back to short ISO), and a confidence score (0.0–1.0).

        Raises:
            STTError: Any non-success path — non-2xx HTTP, empty transcript,
                network timeout, or construction-time misconfiguration (e.g.
                missing API key). Callers should NOT introspect the failure
                further; the adapter has already classified it.
        """
        ...


# ---------------------------------------------------------------------------
# TTS surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TTSResult:
    """Adapter-neutral speech synthesis result returned by ``TTSAdapter.synthesize``.

    Frozen so it can be safely shared across coroutines without defensive copies.
    """

    audio_bytes: bytes
    format: str = field(default="wav")
    sample_rate: int = field(default=22050)


class TTSError(Exception):
    """Single exception type for ANY TTS adapter failure.

    Carries an optional status code (HTTP for REST adapters, ``None`` for
    SDK / network errors) and an optional raw body snippet for debugging.

    Callers should catch ``TTSError`` and surface it as a uniform
    ``{"type":"error","code":"TTS_FAILURE"}`` to the client; the message
    string is for logs only — never echo it to the candidate.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        # Truncate aggressively — provider bodies can be multi-KB.
        self.body = body[:500] if body else None


@runtime_checkable
class TTSAdapter(Protocol):
    """Provider-neutral async TTS surface used by the LangGraph nodes."""

    async def synthesize(
        self,
        text: str,
        *,
        language: str,
        voice: str | None = None,
        pace: float | None = None,
        temperature: float | None = None,
    ) -> TTSResult:
        """Synthesize text to speech and return a neutral ``TTSResult``.

        Args:
            text: The text to synthesize. Must be non-empty. Callers should
                NOT pass raw PII (e.g., transcripts with names) unless DPDP
                consent has been recorded for the session.
            language: Short ISO language code (``"en"``, ``"hi"``, ``"te"``).
                Adapters translate to their vendor format internally.
            voice: Optional voice/speaker identifier. When ``None``, each
                adapter uses its documented default speaker.
            pace: Optional speech pace (typically 0.5–2.0). Lets callers vary
                delivery per moment (slower for a hard question, brisker for a
                quick acknowledgement) WITHOUT changing the speaker — the voice
                stays glued to the avatar for the whole session. Adapters that
                don't support it should ignore it gracefully.
            temperature: Optional prosody variation (typically 0.01–1.0).
                Same intent as ``pace``; ignored by adapters that lack it.

        Returns:
            ``TTSResult`` with raw audio bytes, format (default ``"wav"``),
            and the sample rate the bytes are encoded at.

        Raises:
            TTSError: Any non-success path — non-2xx HTTP, empty audio
                response, network timeout, empty input text, or
                construction-time misconfiguration (e.g. missing API key).
                Callers should NOT introspect the failure further; the
                adapter has already classified it.
        """
        ...
