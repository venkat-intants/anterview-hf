"""Sarvam STT adapter — primary speech-to-text provider for Sprint 3.

We hit the Sarvam ``/speech-to-text`` REST endpoint directly via ``httpx``
rather than any Sarvam SDK. Rationale:

  - One fewer transitive dep (faster CI installs, smaller container image).
  - Same call pattern as the LLM adapters — one less wire format to know.
  - httpx async gives us clean timeout + cancellation semantics needed for
    the p95 < 2s turn-latency NFR.

Model choice (from research/sarvam-pricing-2026-05.md §4):
  - **Use ``saaras:v3`` with ``mode="transcribe"``** — production-ready,
    covers EN/HI/TE, 11 Indian languages total.
  - Saarika v2.5 is **being deprecated** — do NOT switch back to it.

Auth header: ``api-subscription-key`` (Sarvam's documented header name,
verified against docs.sarvam.ai/llms-full.txt 2026-05-27).

Language code translation: callers pass short ISO codes (``en``, ``hi``,
``te``); this adapter translates to Sarvam's ``<lang>-IN`` format internally.

Confidence: Sarvam returns ``language_probability`` (0.0–1.0) which we map
directly to ``STTResult.confidence``. If the field is absent (older API
versions or when language is pinned) we fall back to 1.0.

Error taxonomy — every failure surfaces as ``STTError``:
  - HTTP non-2xx       -> ``STTError(status=<code>, body=<snippet>)``
  - empty transcript   -> ``STTError("empty transcript")``
  - ``httpx.HTTPError`` / timeout -> ``STTError("network: ...")``
  - empty api_key at construction -> ``STTError("api_key is required")``

PII / privacy rules:
  - NEVER log audio bytes.
  - NEVER log the transcript text.
  - Log only: event name, latency_ms, language, model, status codes.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from app.speech.base import STTAdapter, STTError, STTResult

log = structlog.get_logger(__name__)

# Sarvam batch STT endpoint (non-streaming).
# Streaming WebSocket endpoint is a different URL — will be wired in a later sprint.
SARVAM_STT_URL: str = "https://api.sarvam.ai/speech-to-text"

# Conservative timeout: STT requests include audio upload + transcription latency.
# 10 s gives enough headroom for a 30-second chunk cap while keeping overall
# turn latency within the p95 < 2s NFR for the typical 3–5 second utterance.
DEFAULT_TIMEOUT_SECONDS: float = 10.0

# Sarvam's authentication header name (verified against official docs 2026-05-27).
AUTH_HEADER: str = "api-subscription-key"

# Mapping from our short ISO codes to Sarvam's <lang>-IN format.
# Day-1 languages (EN / HI / TE) per CLAUDE.md. Extend when adding more languages.
_LANGUAGE_CODE_MAP: dict[str, str] = {
    "en": "en-IN",
    "hi": "hi-IN",
    "te": "te-IN",
}

# Reverse map: Sarvam's language_code field -> our short ISO code.
# Used when normalising the detected language in the response.
_REVERSE_LANGUAGE_MAP: dict[str, str] = {v: k for k, v in _LANGUAGE_CODE_MAP.items()}


class SarvamSTTAdapter(STTAdapter):
    """REST adapter for Sarvam ``/speech-to-text`` (batch, non-streaming)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            # Fail loud at construction — far better than discovering at
            # the first candidate utterance that the env var was empty.
            raise STTError("SarvamSTTAdapter: api_key is required")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------
    # Public API (STTAdapter Protocol)
    # ------------------------------------------------------------------

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str,
        sample_rate: int = 16000,
    ) -> STTResult:
        """POST audio to Sarvam STT and return a normalised ``STTResult``.

        Args:
            audio_bytes: WAV or PCM 16-bit LE bytes. Must NOT be compressed.
            language: Short ISO code (``"en"``, ``"hi"``, ``"te"``).
            sample_rate: Sample rate hint (default 16000 Hz). Not sent to Sarvam
                directly (Sarvam auto-detects from WAV headers) but kept in the
                signature for interface uniformity with future streaming adapters.

        Returns:
            ``STTResult`` with normalised language code and confidence.

        Raises:
            STTError: On any failure — network, HTTP non-2xx, or empty transcript.
        """
        vendor_lang = self._translate_language(language)

        log.info(
            "stt.sarvam.start",
            model=self._model,
            language=language,
            vendor_language=vendor_lang,
            audio_bytes_len=len(audio_bytes),
            # NOTE: never log audio_bytes content — raw audio is PII-adjacent.
        )

        t_start = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    SARVAM_STT_URL,
                    headers={AUTH_HEADER: self._api_key},
                    files=self._build_multipart(audio_bytes, vendor_lang),
                )
        except httpx.HTTPError as exc:
            # Network-layer failure (DNS, connect, read timeout).
            raise STTError(f"network: {type(exc).__name__}: {exc}") from exc

        latency_ms = int((time.monotonic() - t_start) * 1000)

        if response.status_code < 200 or response.status_code >= 300:
            log.warning(
                "stt.sarvam.http_error",
                status=response.status_code,
                latency_ms=latency_ms,
                model=self._model,
            )
            raise STTError(
                f"sarvam stt http {response.status_code}",
                status=response.status_code,
                body=response.text,
            )

        result = self._parse_response(response.json(), language=language)

        log.info(
            "stt.sarvam.done",
            latency_ms=latency_ms,
            model=self._model,
            language=result.language,
            confidence=result.confidence,
            # NOTE: never log result.transcript — transcript text is PII.
        )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _translate_language(self, language: str) -> str:
        """Translate short ISO code to Sarvam's ``<lang>-IN`` format.

        Raises ``STTError`` for unsupported codes so the caller gets a clear
        message rather than a cryptic Sarvam 400 response.
        """
        vendor_code = _LANGUAGE_CODE_MAP.get(language.lower())
        if vendor_code is None:
            supported = list(_LANGUAGE_CODE_MAP.keys())
            raise STTError(
                f"SarvamSTTAdapter: unsupported language {language!r}; "
                f"supported: {supported}"
            )
        return vendor_code

    def _build_multipart(
        self,
        audio_bytes: bytes,
        vendor_language: str,
    ) -> dict[str, Any]:
        """Assemble the multipart/form-data body for the Sarvam STT endpoint.

        Field names verified against Sarvam API documentation (2026-05-27):
          - ``file``          — the audio file bytes (WAV/PCM)
          - ``model``         — model identifier (e.g., ``saaras:v3``)
          - ``language_code`` — Sarvam's ``<lang>-IN`` format
          - ``with_diarization`` — disabled (adds cost, not needed for interviews)
          - ``mode``          — ``"transcribe"`` (plain transcript, no translation)
        """
        return {
            "file": ("audio.wav", audio_bytes, "audio/wav"),
            "model": (None, self._model),
            "language_code": (None, vendor_language),
            "with_diarization": (None, "false"),
            "mode": (None, "transcribe"),
        }

    def _parse_response(
        self,
        data: dict[str, Any],
        *,
        language: str,
    ) -> STTResult:
        """Extract transcript + confidence from Sarvam's JSON response.

        Sarvam response shape (verified 2026-05-27):
          {
            "request_id": "<uuid>",
            "transcript": "<text>",
            "language_code": "en-IN",
            "language_probability": 0.97   # optional
          }

        We use ``language_probability`` as confidence if present; fall back
        to 1.0 if absent (older versions or pinned language responses).

        We normalise ``language_code`` back to our short ISO code using the
        reverse map so callers are never exposed to vendor-specific codes.
        If Sarvam returns a code we don't recognise, we fall back to the
        caller's original ``language`` argument (best-effort).
        """
        transcript: str = data.get("transcript") or ""
        if not transcript.strip():
            raise STTError(
                "sarvam stt returned empty transcript",
                body=str(data)[:500],
            )

        raw_lang_code: str = data.get("language_code") or ""
        normalised_lang = _REVERSE_LANGUAGE_MAP.get(raw_lang_code, language)

        raw_confidence = data.get("language_probability")
        confidence: float = float(raw_confidence) if raw_confidence is not None else 1.0

        return STTResult(
            transcript=transcript,
            language=normalised_lang,
            confidence=confidence,
        )
