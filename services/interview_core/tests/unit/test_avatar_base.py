"""Unit tests for the AvatarTransport interface + VoiceOnlyAvatar.

Verifies:
  - VoiceOnlyAvatar satisfies the AvatarTransport Protocol (runtime_checkable).
  - It reports mode "none" and is a safe no-op across the lifecycle.
  - WAV duration estimation is sane and never raises.
"""

from __future__ import annotations

import pytest

from app.avatar.base import AvatarSpeechResult, AvatarTransport, VisemeFrame
from app.avatar.voice_only import VoiceOnlyAvatar, _wav_duration_ms


def test_voice_only_satisfies_protocol() -> None:
    """VoiceOnlyAvatar is a structural AvatarTransport."""
    assert isinstance(VoiceOnlyAvatar(), AvatarTransport)


def test_mode_is_none() -> None:
    assert VoiceOnlyAvatar().mode == "none"


@pytest.mark.asyncio
async def test_lifecycle_is_safe_noop() -> None:
    """start -> render -> interrupt -> close all run without error."""
    av = VoiceOnlyAvatar()
    await av.start_session(
        session_id="s1", room_name="r1", avatar_id="a1", language="en"
    )
    # 1 second of 16-bit mono PCM @ 24000 Hz = 48000 bytes -> ~1000 ms.
    pcm = b"\x00\x00" * 24000
    result = await av.render(pcm, sample_rate=24000, language="en", is_first=True)
    assert isinstance(result, AvatarSpeechResult)
    assert result.visemes is None  # none-mode emits no visemes
    assert result.duration_ms == 1000
    await av.interrupt()  # idempotent no-op
    await av.close()
    await av.close()  # safe to call twice


def test_wav_duration_strips_riff_header() -> None:
    """A 44-byte RIFF header is stripped before the duration estimate."""
    header = b"RIFF" + b"\x00" * 40  # 44-byte fake header
    payload = b"\x00\x00" * 12000  # 12000 samples @ 24000 Hz = 500 ms
    assert _wav_duration_ms(header + payload, 24000) == 500


def test_wav_duration_handles_garbage_without_raising() -> None:
    assert _wav_duration_ms(b"", 24000) is None
    assert _wav_duration_ms(b"\x00\x00", 0) is None
    assert _wav_duration_ms(b"x", 24000) is None  # < 2 bytes payload


def test_viseme_frame_is_frozen() -> None:
    """VisemeFrame is immutable (safe to share across coroutines)."""
    vf = VisemeFrame(viseme="aa", offset_ms=120)
    assert vf.weight == 1.0
    with pytest.raises(Exception):  # noqa: B017 — frozen dataclass raises FrozenInstanceError/AttributeError
        vf.offset_ms = 5  # type: ignore[misc]


def test_avatar_speech_result_defaults() -> None:
    r = AvatarSpeechResult()
    assert r.visemes is None
    assert r.duration_ms is None
    assert r.extra == {}
