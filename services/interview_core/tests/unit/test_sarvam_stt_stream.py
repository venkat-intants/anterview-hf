"""Unit tests for the Sarvam streaming STT adapter — S4-004.

All tests are fully offline. The ``websockets`` library's ``connect()``
function is patched to return a controlled async context manager / mock
connection object so no real network calls are made.

Coverage:
  test_connect_url_carries_language_code_and_no_handshake_message
      start("en") → connect URL has ?language-code=en-IN, auth header set, and
      NO config message is sent (the original InvalidStatus root-cause fix).

  test_connect_url_uses_hindi_language_code
      start("hi") → connect URL carries ?language-code=hi-IN.

  test_send_audio_forwards_base64_json_frame
      send_audio(pcm) → a JSON {"audio":{"data":<base64>,...}} frame is sent,
      NOT raw binary.

  test_partials_yields_received_messages
      Mock WS sends 3 {"type":"data","data":{"transcript":...}} frames →
      partials() yields 3 strings.

  test_finalize_returns_final_transcript
      finalize() → returns the last/most-complete transcript; sends no EOS.

  test_handshake_invalid_status_surfaces_http_code
      A non-101 upgrade (InvalidStatus) → STTStreamError naming the HTTP code.

  test_parse_frame_reads_nested_data_transcript
      _parse_frame reads data.transcript, ignores VAD/event frames.

  test_reconnect_once_on_transient_disconnect
      First send_audio triggers ConnectionClosedError → adapter reconnects
      once, retries the send successfully.

  test_no_reconnect_on_auth_failure
      connect() raises ConnectionClosedError immediately → STTStreamError
      surfaced, no second connect attempt.

  test_no_api_key_raises
      Empty api_key → STTStreamError at construction time.

  test_unsupported_language_raises
      start() with unsupported language code → STTStreamError.

PII rules respected in tests: no actual transcript text appears in log
assertions; only lengths and event names are checked.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.speech.sarvam_stt_stream import (
    SARVAM_STT_WS_URL,
    SarvamStreamingSTT,
    STTStreamError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stream(
    api_key: str = "test-key",
    model: str = "saaras:v3",
    ws_url: str = SARVAM_STT_WS_URL,
) -> SarvamStreamingSTT:
    return SarvamStreamingSTT(api_key=api_key, model=model, ws_url=ws_url)


def _make_json_frame(transcript: str, frame_type: str = "data") -> str:
    """Build a Sarvam-shaped transcript frame: {"type":"data","data":{"transcript":...}}."""
    return json.dumps({"type": frame_type, "data": {"transcript": transcript}})


class _FakeWSConnection:
    """Controllable fake WS connection for unit tests.

    Mimics the ``websockets.asyncio.client.ClientConnection`` interface that
    ``SarvamStreamingSTT`` uses: ``send()``, ``close()``, and async iteration.

    ``inbound_frames`` is a list of text strings that will be yielded by the
    async iterator (simulating frames received from Sarvam).  An empty string
    sentinel triggers StopAsyncIteration (clean close).
    """

    def __init__(self, inbound_frames: list[str] | None = None) -> None:
        self.sent_messages: list[Any] = []
        self._inbound: list[str] = inbound_frames or []
        self._closed = False

    async def send(self, data: Any) -> None:
        self.sent_messages.append(data)

    async def close(self) -> None:
        self._closed = True

    def __aiter__(self) -> AsyncIterator[str]:
        return self._aiter()

    async def _aiter(self) -> AsyncIterator[str]:
        for frame in self._inbound:
            await asyncio.sleep(0)  # yield control so event loop can progress
            yield frame


def _patch_connect(fake_ws: _FakeWSConnection) -> Any:
    """Return a context manager patch that makes ``connect()`` return ``fake_ws``."""
    return patch(
        "app.speech.sarvam_stt_stream.connect",
        new_callable=AsyncMock,
        return_value=fake_ws,
    )


def _patch_connect_capture(fake_ws: _FakeWSConnection, captured: dict[str, Any]) -> Any:
    """Patch ``connect`` to return ``fake_ws`` and record the URL + headers used."""

    async def _connect(url: str, **kwargs: Any) -> Any:
        captured["url"] = url
        captured["headers"] = kwargs.get("additional_headers", {})
        return fake_ws

    return patch("app.speech.sarvam_stt_stream.connect", side_effect=_connect)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_url_carries_language_code_and_no_handshake_message() -> None:
    """start("en") → connect URL has ?language-code=en-IN; NO config message sent.

    The required language-code query param is the fix for the original
    InvalidStatus handshake rejection. The endpoint expects no config frame.
    """
    fake_ws = _FakeWSConnection(inbound_frames=[])
    stream = _make_stream()
    captured: dict[str, Any] = {}

    with _patch_connect_capture(fake_ws, captured):
        await stream.start(language="en")

    # Cancel the background task so the test doesn't hang.
    if stream._partial_reader_task is not None:
        stream._partial_reader_task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await stream._partial_reader_task

    assert captured["url"] == f"{SARVAM_STT_WS_URL}?language-code=en-IN"
    assert captured["headers"].get("api-subscription-key") == "test-key"
    # No handshake/config message is sent — language is in the URL.
    assert fake_ws.sent_messages == []


@pytest.mark.asyncio
async def test_connect_url_uses_hindi_language_code() -> None:
    """start("hi") → connect URL must carry ?language-code=hi-IN."""
    fake_ws = _FakeWSConnection(inbound_frames=[])
    stream = _make_stream()
    captured: dict[str, Any] = {}

    with _patch_connect_capture(fake_ws, captured):
        await stream.start(language="hi")

    if stream._partial_reader_task is not None:
        stream._partial_reader_task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await stream._partial_reader_task

    assert captured["url"] == f"{SARVAM_STT_WS_URL}?language-code=hi-IN"


@pytest.mark.asyncio
async def test_send_audio_forwards_base64_json_frame() -> None:
    """send_audio(pcm) → a JSON frame with base64 PCM is sent (NOT raw bytes)."""
    import base64

    fake_ws = _FakeWSConnection(inbound_frames=[])
    stream = _make_stream()

    with _patch_connect(fake_ws):
        await stream.start(language="en")
        pcm = b"\x01\x02\x03\x04" * 50  # 200 bytes of fake PCM
        await stream.send_audio(pcm)

    if stream._partial_reader_task is not None:
        stream._partial_reader_task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await stream._partial_reader_task

    # No config precedes it — the audio frame is the first (and only) message.
    assert len(fake_ws.sent_messages) == 1
    frame = json.loads(fake_ws.sent_messages[0])
    assert frame["audio"]["data"] == base64.b64encode(pcm).decode("ascii")
    assert frame["audio"]["sample_rate"] == 16000
    assert frame["audio"]["encoding"] == "audio/wav"


@pytest.mark.asyncio
async def test_partials_yields_received_messages() -> None:
    """Mock WS sends 3 partial JSON frames → partials() yields 3 text strings."""
    partial_frames = [
        _make_json_frame("I am", "partial"),
        _make_json_frame("I am a", "partial"),
        _make_json_frame("I am a developer", "partial"),
    ]
    fake_ws = _FakeWSConnection(inbound_frames=partial_frames)
    stream = _make_stream()

    collected: list[str] = []

    with _patch_connect(fake_ws):
        await stream.start(language="en")
        # Collect partials after a short delay so the reader task can fill the queue.
        await asyncio.sleep(0.05)
        # Drain the queue via the partials() iterator.
        # Since the fake WS exhausts all frames and then the reader exits putting None,
        # partials() should yield the 3 items and then stop.
        async for text in stream.partials():
            collected.append(text)

    assert len(collected) == 3
    assert collected[0] == "I am"
    assert collected[1] == "I am a"
    assert collected[2] == "I am a developer"


@pytest.mark.asyncio
async def test_finalize_returns_final_transcript() -> None:
    """finalize() → returns the last (most complete) transcript; sends no EOS frame.

    Sarvam's transcribe-streaming contract has no EOS marker — finalize drains
    the trailing transcript and closes. The last transcript frame wins.
    """
    final_text = "I am a backend developer."
    inbound_frames = [
        _make_json_frame("I am a"),
        _make_json_frame(final_text),
    ]
    fake_ws = _FakeWSConnection(inbound_frames=inbound_frames)
    stream = _make_stream()

    with _patch_connect(fake_ws):
        await stream.start(language="en")
        result = await stream.finalize()

    # No EOS / control frame should ever be sent on the transcribe endpoint.
    assert all("eos" not in str(m) for m in fake_ws.sent_messages), (
        f"Unexpected control frame sent: {fake_ws.sent_messages}"
    )
    assert result == final_text


@pytest.mark.asyncio
async def test_finalize_empty_when_no_transcript_frames() -> None:
    """finalize() with zero inbound frames returns empty string (no speech)."""
    fake_ws = _FakeWSConnection(inbound_frames=[])
    stream = _make_stream()

    with _patch_connect(fake_ws):
        await stream.start(language="en")
        result = await stream.finalize()

    assert result == ""


@pytest.mark.asyncio
async def test_reconnect_once_on_transient_disconnect() -> None:
    """Mid-stream ConnectionClosedError on send_audio triggers one reconnect.

    Strategy: patch ``connect`` to succeed twice (original + reconnect). Patch
    ``send`` on the first fake WS to raise ``ConnectionClosedError`` on the
    first PCM send, simulating a transient disconnect.  The adapter should:
    1. Catch the error.
    2. Call ``connect`` a second time.
    3. Re-send the failed PCM on the new connection.
    """
    import base64

    from websockets import ConnectionClosedError
    from websockets.frames import Close

    pcm = b"\xAB\xCD" * 100
    expected_b64 = base64.b64encode(pcm).decode("ascii")

    # First WS: raises on the audio-frame send (simulates drop mid-stream).
    first_ws = _FakeWSConnection(inbound_frames=[])

    async def _raise_on_audio_frame(data: Any) -> None:
        # Audio frames are JSON strings containing the base64 payload.
        if isinstance(data, str) and "audio" in data:
            raise ConnectionClosedError(
                rcvd=Close(code=1006, reason="abnormal"),
                sent=None,
            )
        first_ws.sent_messages.append(data)

    first_ws.send = _raise_on_audio_frame  # type: ignore[method-assign]

    # Second WS (reconnect): normal operation.
    second_ws = _FakeWSConnection(inbound_frames=[])

    connect_call_count = 0

    async def _mock_connect(url: str, **kwargs: Any) -> Any:
        nonlocal connect_call_count
        connect_call_count += 1
        return first_ws if connect_call_count == 1 else second_ws

    stream = _make_stream()

    with patch("app.speech.sarvam_stt_stream.connect", side_effect=_mock_connect):
        await stream.start(language="en")
        # This should trigger the reconnect silently.
        await stream.send_audio(pcm)

    # Two connect calls: initial + one reconnect.
    assert connect_call_count == 2

    # The audio frame (base64 PCM) must have been forwarded to the reconnected WS.
    assert any(
        isinstance(m, str) and expected_b64 in m for m in second_ws.sent_messages
    )


@pytest.mark.asyncio
async def test_no_reconnect_on_auth_failure() -> None:
    """connect() raising immediately → STTStreamError; no second attempt made.

    This covers auth failures where the WS server refuses the handshake
    before any data is exchanged.
    """
    from websockets import ConnectionClosedError
    from websockets.frames import Close

    connect_call_count = 0

    async def _always_fail(url: str, **kwargs: Any) -> Any:
        nonlocal connect_call_count
        connect_call_count += 1
        raise ConnectionClosedError(
            rcvd=Close(code=4001, reason="Unauthorized"),
            sent=None,
        )

    stream = _make_stream()

    with (
        patch("app.speech.sarvam_stt_stream.connect", side_effect=_always_fail),
        pytest.raises(STTStreamError) as exc_info,
    ):
        await stream.start(language="en")

    # Only one connect attempt — no retry on auth failure.
    assert connect_call_count == 1
    assert "closed at handshake" in str(exc_info.value).lower() or "4001" in str(
        exc_info.value
    )


def test_no_api_key_raises() -> None:
    """Empty api_key → STTStreamError at construction time."""
    with pytest.raises(STTStreamError) as exc_info:
        SarvamStreamingSTT(api_key="", model="saaras:v3")
    assert "api_key is required" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_unsupported_language_raises() -> None:
    """start() with unsupported language → STTStreamError (no connect attempted)."""
    stream = _make_stream()
    connect_called = False

    async def _should_not_connect(url: str, **kwargs: Any) -> Any:
        nonlocal connect_called
        connect_called = True
        return _FakeWSConnection()

    with (
        patch("app.speech.sarvam_stt_stream.connect", side_effect=_should_not_connect),
        pytest.raises(STTStreamError) as exc_info,
    ):
        await stream.start(language="fr")

    assert "unsupported language" in str(exc_info.value).lower()
    assert not connect_called  # validation happens before connect


@pytest.mark.asyncio
async def test_handshake_invalid_status_surfaces_http_code() -> None:
    """A non-101 WS upgrade (InvalidStatus) → STTStreamError naming the HTTP code.

    This is the exact failure that silently fell back to batch before the fix.
    The error must now carry the real status (e.g. 403) for diagnosability.
    """
    from websockets.exceptions import InvalidStatus

    class _Resp:
        status_code = 403

    async def _reject(url: str, **kwargs: Any) -> Any:
        raise InvalidStatus(_Resp())  # type: ignore[arg-type]

    stream = _make_stream()
    with (
        patch("app.speech.sarvam_stt_stream.connect", side_effect=_reject),
        pytest.raises(STTStreamError) as exc_info,
    ):
        await stream.start(language="en")

    assert "403" in str(exc_info.value)


def test_parse_frame_reads_nested_data_transcript() -> None:
    """_parse_frame extracts data.transcript and ignores non-transcript frames."""
    parse = SarvamStreamingSTT._parse_frame
    assert parse(json.dumps({"type": "data", "data": {"transcript": "hello"}})) == "hello"
    # VAD/event frames carry no transcript.
    assert parse(json.dumps({"type": "events", "data": {"vad": "speech_start"}})) is None
    assert parse(json.dumps({"type": "data", "data": {"transcript": ""}})) is None
    # Lenient fallback: a top-level transcript still parses.
    assert parse(json.dumps({"transcript": "fallback"})) == "fallback"
    # Garbage is tolerated.
    assert parse("not json") is None


@pytest.mark.asyncio
async def test_finalize_before_start_raises() -> None:
    """finalize() without start() → STTStreamError."""
    stream = _make_stream()
    with pytest.raises(STTStreamError) as exc_info:
        await stream.finalize()
    assert "not started" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_double_finalize_raises() -> None:
    """Calling finalize() twice → STTStreamError on the second call."""
    fake_ws = _FakeWSConnection(inbound_frames=[])
    stream = _make_stream()

    with _patch_connect(fake_ws):
        await stream.start(language="en")
        await stream.finalize()
        with pytest.raises(STTStreamError) as exc_info:
            await stream.finalize()

    assert "already finalized" in str(exc_info.value).lower()
