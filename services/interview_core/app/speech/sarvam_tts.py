"""Sarvam TTS adapter — primary text-to-speech provider for Sprint 3.

We hit the Sarvam ``/text-to-speech`` REST endpoint directly via ``httpx``
rather than any Sarvam SDK. Rationale mirrors the STT adapter:

  - One fewer transitive dep (faster CI installs, smaller container image).
  - Same call pattern as the LLM adapters — one less wire format to know.
  - httpx async gives us clean timeout + cancellation semantics needed for
    the p95 < 2s turn-latency NFR.

Model choice:
  - DEMO runs ``bulbul:v3`` (set via ``SARVAM_TTS_MODEL`` in .env) for the
    realism the interview UX needs. v3 is ~2× v2 cost — covered by Sarvam free
    credits for the demo. For the L1 bid, revisit cost with ``cfo-cost-watcher``
    (v2 voices or AI4Bharat).
  - v3 param facts VERIFIED live against api.sarvam.ai on 2026-05-31:
      * ``temperature`` (0.01–1.0) and ``pace`` (0.5–2.0) WORK on plain
        ``bulbul:v3`` (not just ``-beta``). pace has a real, monotonic effect
        on audio length; temperature varies prosody. NOTE: the live API caps
        temperature at 1.0 (400 above it) — web docs claiming 2.0 are WRONG.
      * ``enable_preprocessing``, ``pitch``, ``loudness`` are v2-only — v3
        accepts the request (200) but IGNORES them. We do not send them on v3.
      * ``speech_sample_rate`` default for v3 is 24000 Hz (we send 24000).
      * Speaker names are case-sensitive. WARNING: Sarvam's 400 "valid
        speakers" error message is STALE — it lists v2 names and is NOT a
        reliable allow-list. Ground truth = a 200 response. Our voices
        (kavya/shreya/pooja) are confirmed-valid v3 speakers; v2 names like
        ``anushka`` correctly 400 on v3 (the B-038 error class).

Auth header: ``api-subscription-key`` (same as STT adapter — Sarvam-wide
header, verified against docs.sarvam.ai/llms-full.txt 2026-05-27).

Response shape: Sarvam returns JSON ``{"request_id": str, "audios": [<base64>]}``.
The ``audios`` list contains base64-encoded WAV strings. We decode the first
element and return it as raw bytes.

Language code translation: callers pass short ISO codes (``en``, ``hi``,
``te``); this adapter translates to Sarvam's ``<lang>-IN`` format internally.

Error taxonomy — every failure surfaces as ``TTSError``:
  - empty text at call site  -> ``TTSError(status=400, body="text input must be non-empty")``
  - unsupported language      -> ``TTSError(status=400, body="unsupported language ...")``
  - HTTP non-2xx              -> ``TTSError(status=<code>, body=<snippet>)``
  - empty audios array (200)  -> ``TTSError(status=200, body="empty audios array")``
  - ``httpx.HTTPError``       -> ``TTSError("network: ...")``
  - empty api_key at ctor     -> ``RuntimeError("SarvamTTSAdapter: api_key is required")``

PII / privacy rules:
  - NEVER log the text being synthesized.
  - NEVER log the audio bytes.
  - Log only: event name, latency_ms, text_length, language, model.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx
import structlog

from app.speech.base import TTSAdapter, TTSError, TTSResult

log = structlog.get_logger(__name__)

# Sarvam TTS REST endpoint.
SARVAM_TTS_URL: str = "https://api.sarvam.ai/text-to-speech"

# TTS is heavier than STT (text is tiny, but inference + audio encoding takes
# longer). 15 s gives comfortable headroom while staying within the overall
# p95 < 2s NFR for typical short questions (≤ ~100 chars).
DEFAULT_TIMEOUT_SECONDS: float = 15.0

# Sarvam's authentication header name (same as STT adapter).
AUTH_HEADER: str = "api-subscription-key"

# Default speaker when the caller does not specify a voice and the language is
# unmapped. Must be valid for the configured model (currently bulbul:v3).
DEFAULT_VOICE: str = "pooja"

# v3 expressiveness defaults — validated live 2026-05-31. Sourced from the
# Sarvam-realism research baseline (pace 0.90 = unhurried/warm; temperature
# 0.78 = natural variation above Sarvam's 0.6 default). Callers may override
# per moment (Rule 9 of the realism prompt) without changing the speaker.
DEFAULT_PACE: float = 0.90
DEFAULT_TEMPERATURE: float = 0.78

# Output sample rate. v3 default is 24000 Hz (higher quality than v2's 22050).
SPEECH_SAMPLE_RATE: int = 24000

# Per-language speaker (B-038 / B-039). Founder-selected bulbul:v3 voices —
# pooja (te), shreya (hi), kavya (en). These require SARVAM_TTS_MODEL=bulbul:v3
# (set in .env); they 400 on bulbul:v2. v3 is ~2× v2 cost — chosen for the DEMO
# (covered by Sarvam's free credits). For the L1 bid, revisit with
# cfo-cost-watcher (v2 voices or AI4Bharat). v2 voices were: en=anushka,
# hi=manisha, te=vidya, plus arya/abhilash/karun/hitesh.
_LANGUAGE_VOICE_MAP: dict[str, str] = {
    "en": "kavya",
    "hi": "shreya",
    "te": "pooja",
}

# Mapping from our short ISO codes to Sarvam's <lang>-IN format.
# Day-1 languages (EN / HI / TE) per CLAUDE.md. Extend when adding more.
# Intentionally duplicated from sarvam_stt.py (3 entries — not worth the
# import coupling; both files own their own translation table).
_LANGUAGE_CODE_MAP: dict[str, str] = {
    "en": "en-IN",
    "hi": "hi-IN",
    "te": "te-IN",
}


class SarvamTTSAdapter(TTSAdapter):
    """REST adapter for Sarvam ``/text-to-speech`` using ``bulbul:v2``."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            # Fail loud at construction — far better than discovering at the
            # first question that the env var was empty.
            raise RuntimeError("SarvamTTSAdapter: api_key is required")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------
    # Public API (TTSAdapter Protocol)
    # ------------------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        *,
        language: str,
        voice: str | None = None,
        pace: float | None = None,
        temperature: float | None = None,
    ) -> TTSResult:
        """POST text to Sarvam TTS and return a normalised ``TTSResult``.

        Args:
            text: Text to synthesize. Must be non-empty.
            language: Short ISO code (``"en"``, ``"hi"``, ``"te"``).
            voice: Sarvam speaker name. Defaults to the per-language speaker
                (kavya/shreya/pooja), falling back to ``DEFAULT_VOICE``.
            pace: Speech pace 0.5–2.0. Defaults to ``DEFAULT_PACE`` (0.90).
            temperature: Prosody variation 0.01–1.0. Defaults to
                ``DEFAULT_TEMPERATURE`` (0.78). Both are v3 params (verified
                live 2026-05-31; temperature caps at 1.0) and let callers vary
                delivery per moment without changing the speaker.

        Returns:
            ``TTSResult`` with WAV bytes at 24000 Hz.

        Raises:
            TTSError: On any failure — empty text, unsupported language,
                network error, HTTP non-2xx, or empty audio response.
        """
        # Guard: reject empty text before making any HTTP call.
        if not text.strip():
            raise TTSError(
                "SarvamTTSAdapter: text input must be non-empty",
                status=400,
                body="text input must be non-empty",
            )

        vendor_lang = self._translate_language(language)
        # Explicit caller override wins; otherwise pick the per-language default
        # speaker, falling back to the global default for any unmapped language.
        speaker = voice or _LANGUAGE_VOICE_MAP.get(language.lower(), DEFAULT_VOICE)
        resolved_pace = pace if pace is not None else DEFAULT_PACE
        resolved_temp = temperature if temperature is not None else DEFAULT_TEMPERATURE

        log.info(
            "tts.sarvam.start",
            model=self._model,
            language=language,
            vendor_language=vendor_lang,
            text_length=len(text),
            # NOTE: never log text — synthesized text may contain candidate PII.
        )

        t_start = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    SARVAM_TTS_URL,
                    headers={
                        AUTH_HEADER: self._api_key,
                        "Content-Type": "application/json",
                    },
                    json=self._build_payload(
                        text, vendor_lang, speaker, resolved_pace, resolved_temp
                    ),
                )
        except httpx.HTTPError as exc:
            # Network-layer failure (DNS, connect, read timeout).
            raise TTSError(f"network: {type(exc).__name__}: {exc}") from exc

        latency_ms = int((time.monotonic() - t_start) * 1000)

        if response.status_code < 200 or response.status_code >= 300:
            log.warning(
                "tts.sarvam.http_error",
                status=response.status_code,
                latency_ms=latency_ms,
                model=self._model,
            )
            raise TTSError(
                f"sarvam tts http {response.status_code}",
                status=response.status_code,
                body=response.text,
            )

        audio_bytes = self._parse_response(response.json())

        log.info(
            "tts.sarvam.done",
            latency_ms=latency_ms,
            model=self._model,
            language=language,
            audio_bytes_len=len(audio_bytes),
            # NOTE: never log audio_bytes — raw audio is PII-adjacent.
        )

        return TTSResult(
            audio_bytes=audio_bytes, format="wav", sample_rate=SPEECH_SAMPLE_RATE
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _translate_language(self, language: str) -> str:
        """Translate short ISO code to Sarvam's ``<lang>-IN`` format.

        Raises ``TTSError`` for unsupported codes so the caller gets a clear
        message rather than a cryptic Sarvam 400 response.
        """
        vendor_code = _LANGUAGE_CODE_MAP.get(language.lower())
        if vendor_code is None:
            supported = list(_LANGUAGE_CODE_MAP.keys())
            raise TTSError(
                f"SarvamTTSAdapter: unsupported language {language!r}; "
                f"supported: {supported}",
                status=400,
                body=f"unsupported language {language!r}",
            )
        return vendor_code

    def _build_payload(
        self,
        text: str,
        vendor_language: str,
        speaker: str,
        pace: float,
        temperature: float,
    ) -> dict[str, Any]:
        """Assemble the JSON body for the Sarvam TTS endpoint.

        Field names verified live against api.sarvam.ai (2026-05-31):
          - ``inputs``               — list of text strings to synthesize
          - ``target_language_code`` — Sarvam's ``<lang>-IN`` format
          - ``model``                — model identifier (``bulbul:v3``)
          - ``speaker``              — voice/speaker name (e.g., ``"pooja"``)
          - ``speech_sample_rate``   — output sample rate in Hz (24000 for v3)
          - ``pace``                 — speech pace 0.5–2.0 (v3-supported)
          - ``temperature``          — prosody variation 0.01–1.0 (v3-supported)

        Deliberately NOT sent (v3 accepts but ignores them — confirmed live):
          - ``enable_preprocessing``, ``pitch``, ``loudness`` (all v2-only).

        The ``speaker`` field is optional but we always send it explicitly so
        Sarvam never falls back to an arbitrary default that could change
        across API versions.
        """
        return {
            "inputs": [text],
            "target_language_code": vendor_language,
            "model": self._model,
            "speaker": speaker,
            "speech_sample_rate": SPEECH_SAMPLE_RATE,
            "pace": pace,
            "temperature": temperature,
        }

    def _parse_response(self, data: dict[str, Any]) -> bytes:
        """Decode base64 WAV audio from Sarvam's JSON response.

        Sarvam TTS response shape (verified 2026-05-27):
          {
            "request_id": "<uuid>",
            "audios": ["<base64-encoded-wav>"]
          }

        We decode the first element of ``audios`` with standard base64 and
        return the raw WAV bytes. An empty ``audios`` list on a 200 response
        is treated as an error — callers expect non-empty bytes.
        """
        audios = data.get("audios")
        if not audios:
            raise TTSError(
                "sarvam tts returned empty audios array",
                status=200,
                body="empty audios array",
            )
        # audios[0] is a base64-encoded WAV string.
        raw_b64: str = str(audios[0])
        return base64.b64decode(raw_b64)
