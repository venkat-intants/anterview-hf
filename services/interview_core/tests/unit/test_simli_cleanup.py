"""Unit tests for SimliAvatar transport release (RT-3).

The gap these pin: ``render()`` awaits a push into the Simli data stream, and
the orchestrator cancels that turn task on every barge-in. Without a
cancellation guard the open data-stream writer is abandoned — one leak per
barge-in, and a talkative candidate barges in a lot. ``interrupt()`` is a
separate entry point from ``close()``, so cleanup has to be idempotent and
order-independent too.

Fully offline: ``start_session`` needs a live LiveKit room and the Simli API, so
these tests inject fakes for the two collaborators it would have built
(``_out``, ``_http``) and drive the lifecycle methods directly. That reaches
past the public surface on purpose — there is no other way to cancel a render
without a real room — but every assertion is about observable behaviour
(transport released, no double close, CancelledError propagates).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.avatar.base import AvatarError
from app.avatar.simli import SimliAvatar

# 24 kHz Sarvam-shaped PCM16 (no RIFF header) — enough samples to resample.
_PCM = b"\x00\x01" * 1000
_SRC_RATE = 24000


class FakeOutput:
    """Stand-in for DataStreamAudioOutput.

    ``block=True`` makes ``capture_frame`` hang forever so a test can cancel
    ``render()`` while the push is genuinely in flight.
    """

    def __init__(self, *, block: bool = False, clear_raises: bool = False) -> None:
        self._block = block
        self._clear_raises = clear_raises
        self.captured = 0
        self.flushes = 0
        self.clears = 0
        self.entered = asyncio.Event()

    async def capture_frame(self, frame: Any) -> None:
        self.captured += 1
        self.entered.set()
        if self._block:
            await asyncio.Event().wait()  # never completes; cancellable

    def flush(self) -> None:
        self.flushes += 1

    def clear_buffer(self) -> None:
        self.clears += 1
        if self._clear_raises:
            raise RuntimeError("vendor stream already gone")


class FakeHTTP:
    """Stand-in for aiohttp.ClientSession — records closes."""

    def __init__(self) -> None:
        self.closed = False
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


def _avatar(
    out: FakeOutput | None = None, http: FakeHTTP | None = None
) -> SimliAvatar:
    avatar = SimliAvatar(
        room=MagicMock(),
        local_identity="interviewer",
        api_key="test-key",
        face_id="test-face",
        livekit_url="wss://example.invalid",
        livekit_api_key="lk-key",
        livekit_api_secret="lk-secret",
    )
    # Stand in for what a successful start_session would have built.
    avatar._out = out  # type: ignore[assignment]
    avatar._http = http  # type: ignore[assignment]
    return avatar


async def _render(avatar: SimliAvatar) -> None:
    await avatar.render(_PCM, sample_rate=_SRC_RATE, language="en")


# ---------------------------------------------------------------------------
# Cancellation during render (barge-in)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_cancelled_mid_push_releases_the_transport() -> None:
    """Barge-in during a push must release the data-stream writer."""
    out = FakeOutput(block=True)
    avatar = _avatar(out, FakeHTTP())

    task = asyncio.create_task(_render(avatar))
    await out.entered.wait()  # push is genuinely in flight
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert out.clears == 1, "cancelled render leaked the avatar transport"


@pytest.mark.asyncio
async def test_render_cancellation_still_propagates() -> None:
    """Cleanup must not swallow CancelledError — that breaks the turn task."""
    out = FakeOutput(block=True)
    avatar = _avatar(out, FakeHTTP())

    task = asyncio.create_task(_render(avatar))
    await out.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled(), "task must end cancelled, not completed"


@pytest.mark.asyncio
async def test_render_cancellation_survives_a_failing_release() -> None:
    """A vendor error while releasing must not mask the cancellation."""
    out = FakeOutput(block=True, clear_raises=True)
    avatar = _avatar(out, FakeHTTP())

    task = asyncio.create_task(_render(avatar))
    await out.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert out.clears == 1


@pytest.mark.asyncio
async def test_close_after_cancelled_render_releases_http() -> None:
    """The full barge-in-then-teardown sequence leaves nothing open."""
    out = FakeOutput(block=True)
    http = FakeHTTP()
    avatar = _avatar(out, http)

    task = asyncio.create_task(_render(avatar))
    await out.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await avatar.close()
    assert http.closed is True
    assert http.close_calls == 1


@pytest.mark.asyncio
async def test_uncancelled_render_does_not_release_the_transport() -> None:
    """The guard must not fire on the happy path — normal pushes keep flowing."""
    out = FakeOutput()
    avatar = _avatar(out, FakeHTTP())

    result = await avatar.render(_PCM, sample_rate=_SRC_RATE, language="en")

    assert out.captured == 1
    assert out.flushes == 1
    assert out.clears == 0
    assert result.duration_ms is not None and result.duration_ms > 0


# ---------------------------------------------------------------------------
# Idempotent teardown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupt_then_close_is_safe() -> None:
    """The two entry points can be used in sequence without a double close."""
    out = FakeOutput()
    http = FakeHTTP()
    avatar = _avatar(out, http)

    await avatar.interrupt()
    await avatar.close()

    assert out.clears >= 1
    assert http.close_calls == 1


@pytest.mark.asyncio
async def test_close_is_idempotent() -> None:
    """Repeated close() must close the HTTP session exactly once."""
    http = FakeHTTP()
    avatar = _avatar(FakeOutput(), http)

    await avatar.close()
    await avatar.close()
    await avatar.close()

    assert http.close_calls == 1


@pytest.mark.asyncio
async def test_interrupt_is_idempotent_and_never_raises() -> None:
    """Repeated barge-in, including on a vendor error, must stay quiet."""
    out = FakeOutput(clear_raises=True)
    avatar = _avatar(out, FakeHTTP())

    await avatar.interrupt()
    await avatar.interrupt()

    assert out.clears == 2


@pytest.mark.asyncio
async def test_teardown_without_a_session_is_safe() -> None:
    """close()/interrupt() before start_session must not raise."""
    avatar = _avatar()

    await avatar.interrupt()
    await avatar.close()


@pytest.mark.asyncio
async def test_close_on_an_already_closed_http_session_does_not_reclose() -> None:
    """A session the vendor already closed must not be closed a second time."""
    http = FakeHTTP()
    http.closed = True
    avatar = _avatar(FakeOutput(), http)

    await avatar.close()

    assert http.close_calls == 0


@pytest.mark.asyncio
async def test_render_after_close_raises_avatar_error() -> None:
    """Pushing into a torn-down transport would silently re-open a writer."""
    avatar = _avatar(FakeOutput(), FakeHTTP())
    await avatar.close()

    with pytest.raises(AvatarError):
        await _render(avatar)


@pytest.mark.asyncio
async def test_render_before_start_session_raises_avatar_error() -> None:
    """Unchanged pre-existing guard — still distinct from the after-close one."""
    avatar = _avatar()

    with pytest.raises(AvatarError):
        await _render(avatar)
