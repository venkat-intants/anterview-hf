"""Sarvam streaming STT adapter — S4-004 (streaming pipeline, headline latency).

Replaces the one-shot batch POST path for real-time transcription. As each
browser ``audio_chunk`` arrives, raw PCM bytes are forwarded to the Sarvam
streaming WebSocket **without** waiting for ``turn_end``. The latency win
comes from overlapping audio upload with transcription: by the time the
candidate stops speaking the transcript is already ~90 % complete.

Architecture:
  One ``SarvamStreamingSTT`` instance is created per candidate turn (or lazily
  on the first audio_chunk of the first turn; caller decides).  The instance
  holds the open Sarvam WS connection and a background task that continuously
  reads partial transcripts from it.

Lifecycle:
  1. ``await session.start(language="en")``
       Opens the Sarvam WS, sends the handshake config message, starts the
       background partial-reader task.
  2. ``await session.send_audio(pcm_bytes)``
       Forwards raw PCM bytes as a binary frame.  Non-blocking from caller POV.
  3. ``async for partial in session.partials()``
       AsyncIterator[str] — yields each partial transcript string as Sarvam
       emits it.  Runs in the background; use ``asyncio.create_task`` to drain
       concurrently with audio forwarding.
  4. ``final = await session.finalize()``
       Sends the Sarvam EOS marker, awaits the final transcript frame, closes
       the WS, cancels the partial-reader task.  Returns the final transcript.

Error handling:
  - ``STTStreamError`` (subclass of ``STTError``) is raised on any
    non-recoverable failure: auth rejection (WS close code 4001 / 401),
    double reconnect failure, or timeout.
  - Transient disconnect (WS close code 1006) during streaming: one automatic
    reconnect attempt.  On reconnect success, streaming continues seamlessly.
    On reconnect failure, ``STTStreamError`` is raised and the caller (ws.py)
    falls back to the existing one-shot batch path.
  - ``start()`` raising any exception signals the caller to fall back to batch.

Sarvam streaming WS contract (verified against the official reference example
``sarvamai/sarvam-streaming-apis`` / python-scripts/stt-streaming_saarika.py,
2026-05-28). This is the *transcription* endpoint (same-language transcript),
NOT the translate endpoint (which forces English output — wrong for a
multilingual interview).
  Endpoint:   wss://api.sarvam.ai/speech-to-text/ws?language-code=<lang>-IN
              language-code is a REQUIRED query parameter. Omitting it makes
              Sarvam reject the WS upgrade with a non-101 status, which the
              websockets lib raises as InvalidStatus. That was the original
              "InvalidStatus -> fall back to batch" bug.
  Auth:       ``api-subscription-key`` request header on the handshake
              (same as the batch endpoint). ``websockets >= 13`` sets it via
              the ``additional_headers`` kwarg.
  Handshake:  NONE. The endpoint expects no config message — language is in
              the URL and audio config travels inside each audio frame.
  Audio:      JSON text frames, NOT raw binary. Each frame:
                {"audio": {"data": "<base64 pcm_s16le>",
                           "sample_rate": 16000, "encoding": "audio/wav"}}
  Transcript: Server sends JSON frames during streaming:
                {"type": "data", "data": {"transcript": "..."}}
              VAD/event frames (e.g. {"type": "events", ...}) may also arrive
              and are ignored by ``_parse_frame()``.
  EOS:        No explicit end-of-stream marker in the documented contract. We
              stop sending audio at turn-end and drain any trailing transcript
              frame within a short grace window before closing the socket.

  Model note: this endpoint selects its ASR model server-side (saarika family)
  and takes NO ``model`` query param, so ``settings.sarvam_stt_model``
  (saaras:v3, used by the batch path) is accepted for interface parity but
  intentionally not sent here.

  TODO: confirm trailing-transcript flush timing and whether data.transcript
  frames are cumulative vs per-segment against the live API on first staging
  deploy. If the frame schema changes, update ``_parse_frame()`` only.

PII / privacy rules (project-wide):
  - NEVER log audio bytes or PCM content.
  - NEVER log partial or final transcript text — log lengths and latencies only.
  - Log only: event names, latency_ms, language, byte counts, error types.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
import urllib.parse
from collections.abc import AsyncIterator
from typing import Any

import structlog
from websockets import ConnectionClosedError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidHandshake, InvalidStatus

from app.speech.base import STTError

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SARVAM_STT_WS_URL: str = "wss://api.sarvam.ai/speech-to-text/ws"
AUTH_HEADER: str = "api-subscription-key"

# Mapping: short ISO codes -> Sarvam's <lang>-IN format (matches batch adapter).
_LANGUAGE_CODE_MAP: dict[str, str] = {
    "en": "en-IN",
    "hi": "hi-IN",
    "te": "te-IN",
}

# Timeout constants.
# Connect budget: a standalone handshake completes in <1s, but inside the live
# WS handler the event loop is contended by concurrent avatar (D-ID) signalling
# — many in-flight HTTP calls — which can starve a tight 5s budget and trip a
# spurious connect timeout (→ STTStreamError → batch fallback). 10s gives the
# TLS+upgrade handshake headroom without meaningfully delaying a real failure.
_CONNECT_TIMEOUT_SECONDS: float = 10.0
_SESSION_TIMEOUT_SECONDS: float = 30.0

# Grace window at turn-end to catch the trailing transcript frame for the last
# bit of audio. Bounded so finalize never blocks a turn for long. The endpoint
# has no explicit EOS marker, so we drain rather than signal. Tune against the
# live API (see module TODO).
_FINALIZE_GRACE_SECONDS: float = 1.0

# Reconnect budget: one attempt per session lifetime (Sprint 4 out-of-scope
# to allow more; two total attempts before giving up).
_MAX_RECONNECT_ATTEMPTS: int = 1


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class STTStreamError(STTError):
    """Raised when the streaming STT session fails non-recoverably.

    Callers in ``ws.py`` catch this and fall back to the one-shot batch path
    so the candidate still receives a transcript even if streaming breaks.
    """


# ---------------------------------------------------------------------------
# Streaming session
# ---------------------------------------------------------------------------


class SarvamStreamingSTT:
    """Single-use Sarvam streaming STT session — one instance per candidate turn.

    Not thread-safe.  Designed for single-consumer async use within one
    FastAPI WebSocket handler coroutine.

    Usage::

        session = SarvamStreamingSTT(api_key="...", model="saaras:v3")
        await session.start(language="en")

        # On each audio_chunk from browser:
        await session.send_audio(pcm_bytes)

        # Background task draining partials (emit to browser):
        async for partial in session.partials():
            await browser_ws.send_json({"type": "partial_transcript", "text": partial})

        # On turn_end:
        final = await session.finalize()
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        ws_url: str = SARVAM_STT_WS_URL,
        connect_timeout: float = _CONNECT_TIMEOUT_SECONDS,
        session_timeout: float = _SESSION_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            raise STTStreamError("SarvamStreamingSTT: api_key is required")
        self._api_key = api_key
        self._model = model
        self._ws_url = ws_url
        self._connect_timeout = connect_timeout
        self._session_timeout = session_timeout

        # Set during start()
        self._ws: ClientConnection | None = None
        self._language: str = "en"  # short ISO code
        self._vendor_language: str = "en-IN"
        self._session_start_time: float = 0.0
        self._first_audio_time: float | None = None
        self._reconnect_count: int = 0

        # Queue populated by the background _partial_reader_task and consumed
        # by partials().  Using asyncio.Queue so the producer and consumer
        # can run concurrently without a shared lock.
        self._partial_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._partial_reader_task: asyncio.Task[None] | None = None
        self._finalized: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self, language: str) -> None:
        """Open the Sarvam WS and send the handshake config message.

        Args:
            language: Short ISO code (``"en"``, ``"hi"``, ``"te"``).

        Raises:
            STTStreamError: On auth failure, unsupported language, or connect
                timeout.  Caller MUST fall back to batch path on this exception.
        """
        vendor_lang = _LANGUAGE_CODE_MAP.get(language.lower())
        if vendor_lang is None:
            raise STTStreamError(
                f"SarvamStreamingSTT: unsupported language {language!r}; "
                f"supported: {list(_LANGUAGE_CODE_MAP.keys())}"
            )
        self._language = language
        self._vendor_language = vendor_lang
        self._session_start_time = time.monotonic()

        # language-code is carried as a query param on the connection URL
        # (see _connect); the endpoint expects NO config handshake message.
        self._ws = await self._connect()

        # Start background partial reader.
        self._partial_reader_task = asyncio.create_task(
            self._partial_reader_loop(), name="stt_stream_partial_reader"
        )

        log.info(
            "stt.stream.started",
            language=language,
            vendor_language=vendor_lang,
            model=self._model,
        )

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Forward raw PCM bytes to the Sarvam WS as a binary frame.

        No WAV wrapping — the streaming endpoint accepts raw pcm_s16le directly.

        Args:
            pcm_bytes: Raw 16 kHz mono PCM bytes (16-bit little-endian).

        Raises:
            STTStreamError: If the WS is not open (``start()`` not called or
                already finalized) OR if a reconnect after transient disconnect
                also fails.
        """
        if self._finalized:
            raise STTStreamError("SarvamStreamingSTT: session already finalized")
        if self._ws is None:
            raise STTStreamError("SarvamStreamingSTT: session not started")
        if not pcm_bytes:
            return  # nothing to forward

        if self._first_audio_time is None:
            self._first_audio_time = time.monotonic()

        # Sarvam expects base64 PCM wrapped in a JSON frame, NOT raw binary.
        audio_frame = self._audio_frame(pcm_bytes)

        try:
            await self._ws.send(audio_frame)
        except ConnectionClosedError as exc:
            # Transient disconnect — attempt one reconnect.
            log.warning(
                "stt.stream.disconnect_mid_stream",
                code=exc.code,
                reason=str(exc.reason)[:200],
                reconnect_count=self._reconnect_count,
            )
            await self._reconnect_once()
            # Retry the send on the fresh connection.
            assert self._ws is not None
            await self._ws.send(audio_frame)

        # Log bytes forwarded but NEVER log content (PII-adjacent raw audio).
        log.debug(
            "stt.stream.audio_forwarded",
            pcm_bytes_len=len(pcm_bytes),
        )

    async def partials(self) -> AsyncIterator[str]:
        """AsyncIterator that yields partial transcript strings from Sarvam.

        Yields until the stream is finalized.  Each yielded string is a partial
        transcript text (NOT logged — PII).  Callers should forward these to the
        browser as ``{"type":"partial_transcript","text":"<partial>"}``.

        Usage::

            async for partial in session.partials():
                await websocket.send_json({"type": "partial_transcript", "text": partial})

        The iterator exits naturally when ``finalize()`` is called (the reader
        task puts ``None`` as a sentinel).
        """
        while True:
            item = await self._partial_queue.get()
            if item is None:  # sentinel from finalize() or error
                break
            yield item

    async def finalize(self) -> str:
        """Signal end-of-stream, await the final transcript, close the WS.

        Returns:
            The final transcript string (empty string if Sarvam returned nothing,
            which the caller should treat as a no-speech turn).

        Raises:
            STTStreamError: If the WS connection was never opened or already
                finalized, or if awaiting the final transcript times out.
        """
        if self._finalized:
            raise STTStreamError("SarvamStreamingSTT: already finalized")
        if self._ws is None:
            raise STTStreamError("SarvamStreamingSTT: session not started")

        self._finalized = True
        t_finalize_start = time.monotonic()

        # No explicit EOS marker in Sarvam's documented contract. Transcripts
        # are emitted continuously as audio arrives, so by turn-end the
        # transcript for the spoken audio is usually already queued. We (1)
        # drain everything already queued, then (2) wait a short grace window
        # for any trailing frame, then close. The last transcript seen is the
        # most complete one.
        final_text = ""
        reader_ended = False

        # Phase 1 — drain frames already queued (non-blocking).
        while True:
            try:
                item = self._partial_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is None:  # reader already ended (connection closed)
                reader_ended = True
                break
            final_text = item

        # Phase 2 — brief bounded wait for a trailing transcript frame.
        if not reader_ended:
            grace_budget = self._session_timeout - (
                time.monotonic() - self._session_start_time
            )
            grace = max(min(_FINALIZE_GRACE_SECONDS, grace_budget), 0.0)
            if grace > 0:
                try:
                    async with asyncio.timeout(grace):
                        while True:
                            item = await self._partial_queue.get()
                            if item is None:
                                break
                            final_text = item
                except TimeoutError:
                    # No further frames within the grace window — expected on a
                    # live stream; use the most complete transcript we have.
                    log.debug(
                        "stt.stream.finalize_grace_elapsed",
                        elapsed_ms=int((time.monotonic() - t_finalize_start) * 1000),
                    )
        try:
            await self._cancel_reader()
        finally:
            with contextlib.suppress(Exception):
                await self._ws.close()

        finalize_ms = int((time.monotonic() - t_finalize_start) * 1000)
        log.info(
            "stt.stream.finalized",
            finalize_ms=finalize_ms,
            transcript_len=len(final_text),
            # NEVER log transcript text — PII.
        )
        return final_text

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _connect(self) -> ClientConnection:
        """Open the Sarvam streaming WS with auth header + connect timeout.

        Raises:
            STTStreamError: On connect timeout or auth rejection (WS close ≤ 1003
                or 4xxx code from Sarvam).
        """
        url = self._build_ws_url()
        try:
            async with asyncio.timeout(self._connect_timeout):
                ws = await connect(
                    url,
                    additional_headers={AUTH_HEADER: self._api_key},
                )
        except TimeoutError as exc:
            raise STTStreamError(
                f"SarvamStreamingSTT: connect timeout after {self._connect_timeout}s"
            ) from exc
        except InvalidStatus as exc:
            # The WS upgrade was rejected with a non-101 HTTP status. Surface
            # the actual code (401/403 = auth, 400/404 = bad URL/params) so the
            # failure is diagnosable instead of an opaque "InvalidStatus".
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            raise STTStreamError(
                f"SarvamStreamingSTT: handshake rejected with HTTP {status_code}"
            ) from exc
        except InvalidHandshake as exc:
            # Any other handshake-level failure (malformed upgrade, etc.).
            raise STTStreamError(
                f"SarvamStreamingSTT: handshake failed: {type(exc).__name__}: {exc}"
            ) from exc
        except OSError as exc:
            # DNS failure, TLS error, refused connection, etc.
            raise STTStreamError(
                f"SarvamStreamingSTT: connect error: {type(exc).__name__}: {exc}"
            ) from exc
        except ConnectionClosedError as exc:
            # Server closed immediately — usually auth failure.
            raise STTStreamError(
                f"SarvamStreamingSTT: connection closed at handshake "
                f"(code={exc.code}): {exc.reason}"
            ) from exc

        return ws

    def _build_ws_url(self) -> str:
        """Build the connection URL with the REQUIRED language-code query param.

        Sarvam's transcribe-streaming endpoint rejects the WS upgrade if
        language-code is absent. The base URL must carry no existing query
        string; we append ``?language-code=<lang>-IN`` (e.g. ``hi-IN``).
        """
        query = urllib.parse.urlencode({"language-code": self._vendor_language})
        return f"{self._ws_url}?{query}"

    @staticmethod
    def _audio_frame(pcm_bytes: bytes) -> str:
        """Wrap raw PCM into the JSON audio frame Sarvam's streaming WS expects.

        Shape (verified against the official reference example):
          {"audio": {"data": "<base64 pcm_s16le>",
                     "sample_rate": 16000, "encoding": "audio/wav"}}
        """
        return json.dumps(
            {
                "audio": {
                    "data": base64.b64encode(pcm_bytes).decode("ascii"),
                    "sample_rate": 16000,
                    "encoding": "audio/wav",
                }
            }
        )

    async def _reconnect_once(self) -> None:
        """Attempt a single reconnect after a transient disconnect.

        Increments ``_reconnect_count``.  If the budget is exhausted, raises
        ``STTStreamError`` so the caller falls back to the batch path.

        After reconnect, the handshake config is re-sent so Sarvam knows the
        language and sample rate on the fresh connection.
        """
        if self._reconnect_count >= _MAX_RECONNECT_ATTEMPTS:
            raise STTStreamError(
                "SarvamStreamingSTT: reconnect budget exhausted — "
                "falling back to batch STT"
            )
        self._reconnect_count += 1

        log.info(
            "stt.stream.reconnecting",
            attempt=self._reconnect_count,
            max_attempts=_MAX_RECONNECT_ATTEMPTS,
        )

        # Cancel the stale partial reader — it will error on the closed WS.
        if self._partial_reader_task is not None and not self._partial_reader_task.done():
            self._partial_reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._partial_reader_task

        # _connect() rebuilds the URL with the language-code query param, so no
        # config message is needed on the fresh connection.
        self._ws = await self._connect()

        # Restart the partial reader on the new connection.
        self._partial_reader_task = asyncio.create_task(
            self._partial_reader_loop(), name="stt_stream_partial_reader"
        )

        log.info(
            "stt.stream.reconnected",
            attempt=self._reconnect_count,
        )

    async def _partial_reader_loop(self) -> None:
        """Background task: read frames from Sarvam WS and enqueue partials.

        Runs until:
          - The WS closes (normal or error).
          - The task is cancelled.

        Puts ``None`` sentinel into the queue when done so ``partials()`` and
        ``finalize()`` can exit their wait loops.

        Frame shapes expected from Sarvam:
          Partial: ``{"type": "partial", "transcript": "<text>"}``
          Final:   ``{"type": "final",   "transcript": "<text>"}``

        We treat any frame with a non-empty ``"transcript"`` key as useful
        content, regardless of ``"type"``, so we're robust to minor API
        variations.
        """
        first_partial_logged = False
        connect_time = time.monotonic()

        try:
            assert self._ws is not None
            async for raw_frame in self._ws:
                if isinstance(raw_frame, bytes):
                    # Binary frame from Sarvam — unexpected in the partial stream,
                    # but tolerated gracefully (skip rather than crash).
                    log.debug(
                        "stt.stream.unexpected_binary_frame",
                        byte_len=len(raw_frame),
                    )
                    continue

                # Text frame — parse as JSON.
                transcript_text = self._parse_frame(raw_frame)
                if transcript_text is not None:
                    if not first_partial_logged:
                        first_partial_logged = True
                        latency_ms = int((time.monotonic() - connect_time) * 1000)
                        log.info(
                            "stt.stream.first_partial",
                            latency_ms=latency_ms,
                            # p95 target: <500 ms (S4-004 acceptance criterion)
                        )
                    # Enqueue the text — NEVER log it (PII).
                    await self._partial_queue.put(transcript_text)

        except asyncio.CancelledError:
            # Normal shutdown — finalize() or error path cancelled us.
            pass
        except ConnectionClosedError as exc:
            log.warning(
                "stt.stream.reader_connection_closed",
                code=exc.code,
                reason=str(exc.reason)[:200],
            )
        except Exception as exc:
            log.error(
                "stt.stream.reader_unexpected_error",
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
        finally:
            # Always put the sentinel so consumers unblock.
            await self._partial_queue.put(None)

    @staticmethod
    def _parse_frame(raw_frame: str) -> str | None:
        """Extract transcript text from a Sarvam JSON text frame.

        Returns the transcript string on success (may be empty), or ``None``
        if the frame does not contain a transcript.

        Expected shape (verified against the official reference example):
          ``{"type": "data", "data": {"transcript": "..."}}``

        VAD/event frames such as ``{"type": "events", ...}`` or
        ``{"type": "speech_start"}`` carry no transcript and return None.

        We are intentionally lenient: we accept the nested
        ``data.transcript`` form first, then fall back to a top-level
        ``transcript`` key, so minor API variations don't break us.

        TODO: verify exact frame shapes against live API on first staging deploy.
        """
        try:
            frame: dict[str, Any] = json.loads(raw_frame)
        except json.JSONDecodeError:
            log.debug("stt.stream.unparseable_frame", frame_len=len(raw_frame))
            return None

        # Primary contract: {"type": "data", "data": {"transcript": "..."}}
        inner = frame.get("data")
        if isinstance(inner, dict):
            transcript = inner.get("transcript")
            if isinstance(transcript, str) and transcript:
                return transcript

        # Fallback: top-level transcript (tolerate minor API variations).
        transcript = frame.get("transcript")
        if isinstance(transcript, str) and transcript:
            return transcript

        return None

    async def _cancel_reader(self) -> None:
        """Cancel and await the partial reader task if still running."""
        if self._partial_reader_task is not None and not self._partial_reader_task.done():
            self._partial_reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._partial_reader_task
