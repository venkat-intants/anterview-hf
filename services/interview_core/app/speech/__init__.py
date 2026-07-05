"""Speech adapter package (S3-001 / S3-002).

Provides provider-neutral ``STTAdapter`` and ``TTSAdapter`` protocols consumed
by the LangGraph nodes, plus the concrete Sarvam implementations used in
Sprint 3.

Future providers (Bhashini, OpenAI Whisper, ElevenLabs) drop in here as
additional ``app/speech/<provider>_stt.py`` / ``app/speech/<provider>_tts.py``
modules. ``build_default_stt_adapter()`` and ``build_default_tts_adapter()``
are the single places that read the provider env vars and decide which class
to construct, so the rest of the codebase stays provider-agnostic.

Provider swap:
  - STT: set ``SPEECH_STT_PROVIDER=bhashini`` in ``.env`` (Sprint 4+)
  - TTS: set ``SPEECH_TTS_PROVIDER=elevenlabs`` in ``.env`` (Sprint 4+)
"""

from __future__ import annotations

from app.config import settings
from app.speech.base import STTAdapter, STTError, STTResult, TTSAdapter, TTSError, TTSResult
from app.speech.sarvam_stt import SarvamSTTAdapter
from app.speech.sarvam_stt_stream import SarvamStreamingSTT, STTStreamError
from app.speech.sarvam_tts import SarvamTTSAdapter
from app.speech.sentence_splitter import SentenceBuffer

__all__ = [
    "SarvamSTTAdapter",
    "SarvamStreamingSTT",
    "SarvamTTSAdapter",
    "SentenceBuffer",
    "STTAdapter",
    "STTError",
    "STTResult",
    "STTStreamError",
    "TTSAdapter",
    "TTSError",
    "TTSResult",
    "build_default_stt_adapter",
    "build_default_tts_adapter",
    "get_stt_adapter",
    "get_tts_adapter",
    "reset_stt_adapter",
    "reset_tts_adapter",
]

# Module-level singleton — built lazily on first ``get_stt_adapter()`` call
# so the WebSocket handler does NOT spin up a fresh httpx client per turn
# (mirrors the pattern used by ``app.llm`` for its default adapter).
_stt_adapter_singleton: STTAdapter | None = None

# Same lazy-build/cache pattern for the TTS adapter (S3-007). The WS handler
# calls ``get_tts_adapter()`` once per session on accept, so a singleton is
# enough to avoid per-turn httpx client construction.
_tts_adapter_singleton: TTSAdapter | None = None


def build_default_stt_adapter() -> STTAdapter:
    """Construct the STT adapter selected by ``settings.speech_stt_provider``.

    Centralises the provider switch so nodes / app bootstrap never need an
    ``if/elif`` chain. Add a new ``elif`` branch here when wiring Bhashini
    or OpenAI Whisper; nothing else changes.

    Raises:
        NotImplementedError: For any provider not yet implemented.
        STTError: If the selected provider is mis-configured (e.g., empty key).
    """
    provider = settings.speech_stt_provider.lower()
    if provider == "sarvam":
        return SarvamSTTAdapter(
            api_key=settings.sarvam_api_key,
            model=settings.sarvam_stt_model,
        )
    # Bhashini / OpenAI Whisper / Google land in Sprint 4+ — keep the
    # SPEECH_STT_PROVIDER swap hot but raise clearly until implemented.
    raise NotImplementedError(
        f"STT provider {settings.speech_stt_provider!r} is not yet implemented. "
        "Supported in Sprint 3: 'sarvam'. "
        "Bhashini and OpenAI Whisper land in Sprint 4+."
    )


def build_default_tts_adapter() -> TTSAdapter:
    """Construct the TTS adapter selected by ``settings.speech_tts_provider``.

    Centralises the provider switch so nodes / app bootstrap never need an
    ``if/elif`` chain. Add a new ``elif`` branch here when wiring ElevenLabs
    or Bhashini TTS; nothing else changes.

    Raises:
        NotImplementedError: For any provider not yet implemented.
        RuntimeError: If the selected provider is mis-configured (e.g., empty key).
    """
    provider = settings.speech_tts_provider.lower()
    if provider == "sarvam":
        return SarvamTTSAdapter(
            api_key=settings.sarvam_api_key,
            model=settings.sarvam_tts_model,
        )
    # ElevenLabs / Bhashini TTS land in Sprint 4+ — keep the
    # SPEECH_TTS_PROVIDER swap hot but raise clearly until implemented.
    raise NotImplementedError(
        f"TTS provider {settings.speech_tts_provider!r} is not yet implemented. "
        "Supported in Sprint 3: 'sarvam'. "
        "ElevenLabs and Bhashini TTS land in Sprint 4+."
    )


def get_stt_adapter() -> STTAdapter:
    """Return the process-wide STT adapter, building it on first call.

    The WebSocket handler calls this on every ``turn_end`` — we MUST NOT
    construct a new ``httpx.AsyncClient`` (and its connection pool) per
    turn or we burn the latency budget on TLS handshakes. By caching the
    adapter, we reuse the underlying client across the session and across
    concurrent sessions in the same worker process.

    Tests substitute the singleton via ``patch("app.routers.ws.get_stt_adapter",
    return_value=FakeAdapter())`` — see ``test_ws_protocol_v2.py``.
    """
    global _stt_adapter_singleton
    if _stt_adapter_singleton is None:
        _stt_adapter_singleton = build_default_stt_adapter()
    return _stt_adapter_singleton


def reset_stt_adapter() -> None:
    """Clear the cached STT adapter — test-only helper, not for production.

    Used by integration tests that need to swap the adapter between cases
    without restarting the process.
    """
    global _stt_adapter_singleton
    _stt_adapter_singleton = None


def get_tts_adapter() -> TTSAdapter:
    """Return the process-wide TTS adapter, building it on first call.

    The WebSocket handler calls this once per session (on accept) and reuses
    the returned adapter for every interviewer turn. Caching avoids both the
    repeated ``settings`` lookup and — more importantly — fresh
    ``httpx.AsyncClient`` construction (TLS handshakes per turn would
    obliterate the p95 < 2s latency budget).

    Tests substitute the singleton via
    ``patch("app.routers.ws.get_tts_adapter", return_value=FakeAdapter())``
    — see ``test_ws_protocol_v2.py``.
    """
    global _tts_adapter_singleton
    if _tts_adapter_singleton is None:
        _tts_adapter_singleton = build_default_tts_adapter()
    return _tts_adapter_singleton


def reset_tts_adapter() -> None:
    """Clear the cached TTS adapter — test-only helper, not for production.

    Used by integration tests that need to swap the adapter between cases
    without restarting the process.
    """
    global _tts_adapter_singleton
    _tts_adapter_singleton = None
