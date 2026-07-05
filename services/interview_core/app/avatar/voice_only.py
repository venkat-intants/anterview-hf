"""VoiceOnlyAvatar — the ``none`` mode AvatarTransport.

The default/baseline transport: a working VOICE interview with NO avatar. This
is the FIRST real-time milestone (docs/ARCH §6) — prove the LiveKit + agent +
brain + voice loop end-to-end before any avatar vendor is wired or paid for.

It is also the universal fallback: if a vendor transport raises ``AvatarError``
mid-session, the agent swaps to this so the interview continues as voice-only
rather than dying.

No external calls, no keys, no cost. Pure no-op that satisfies the Protocol.
"""

from __future__ import annotations

import structlog

from app.avatar.base import AvatarMode, AvatarSpeechResult

log = structlog.get_logger(__name__)


class VoiceOnlyAvatar:
    """No-op avatar transport — voice plays, no face renders."""

    @property
    def mode(self) -> AvatarMode:
        return "none"

    async def start_session(
        self,
        *,
        session_id: str,
        room_name: str,
        avatar_id: str,
        language: str,
    ) -> None:
        log.info("avatar.voice_only.start", session_id=session_id)

    async def render(
        self,
        audio_bytes: bytes,
        *,
        sample_rate: int,
        language: str,
        is_first: bool = False,
    ) -> AvatarSpeechResult:
        # Nothing to render — the agent still plays the audio to the room.
        # Report the clip duration so the agent can schedule/▸barge-in window.
        duration_ms = _wav_duration_ms(audio_bytes, sample_rate)
        return AvatarSpeechResult(visemes=None, duration_ms=duration_ms)

    async def interrupt(self) -> None:
        # No avatar output to stop.
        return None

    async def close(self) -> None:
        return None


def _wav_duration_ms(audio_bytes: bytes, sample_rate: int) -> int | None:
    """Best-effort PCM/WAV duration estimate (16-bit mono) for scheduling.

    Returns ``None`` if it cannot be computed — the agent treats None as
    "unknown" and falls back to its own timing. Never raises.
    """
    if not audio_bytes or sample_rate <= 0:
        return None
    # Strip a 44-byte RIFF header if present; assume 16-bit mono PCM payload.
    payload = audio_bytes
    if len(audio_bytes) > 44 and audio_bytes[:4] == b"RIFF":
        payload = audio_bytes[44:]
    n_samples = len(payload) // 2  # 16-bit = 2 bytes/sample
    if n_samples == 0:
        return None
    return int(n_samples * 1000 / sample_rate)
