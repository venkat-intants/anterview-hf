"""SimliAvatar — AvatarTransport for the Simli real-time lip-synced face.

DEMO-ONLY (memory project_simli_avatar): Simli has no India residency + is over
the ₹12/session bid cap. The bid uses the client-side RPM viseme path. Simli is
the demo real-time avatar.

HOW IT WORKS (verified by spike 2026-05-31, replicating livekit-plugins-simli
WITHOUT its agent-worker framework — keeps our thin custom agent):
  start_session():
    1. POST {base}/compose/token  (x-simli-api-key header) -> session_token
    2. mint a LiveKit token for identity 'simli-avatar-agent' (kind=agent,
       publishing on behalf of our interviewer participant)
    3. POST {base}/integrations/livekit/agents  with session_token +
       livekit_token + livekit_url  -> Simli joins OUR room as a participant and
       publishes the lip-synced VIDEO track itself (zero frontend needed —
       any LiveKit client, incl. the Playground, sees the face)
    4. open a DataStreamAudioOutput targeting 'simli-avatar-agent' @ 16 kHz
  render():
    resample our Sarvam 24 kHz PCM -> 16 kHz and push it to the data-stream
    output; Simli lip-syncs to it and drives the published video.

AUDIO ROUTING NOTE: in Simli mode the avatar IS the audio destination (Simli
republishes audio+video). The agent therefore gives the orchestrator a NO-OP
sink so audio is not also published on a second track (which would double it).

Because the video is published by Simli's own server-side participant, ``render``
returns ``visemes=None`` (mode=vendor_video) — there is nothing for the agent to
forward.

PII: never log audio bytes — event + counters only.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import aiohttp
import structlog
from livekit import api, rtc
from livekit.agents.voice.avatar import DataStreamAudioOutput
from livekit.agents.voice.room_io import ATTRIBUTE_PUBLISH_ON_BEHALF

from app.agent.audio_resample import resample_pcm16
from app.avatar.base import AvatarError, AvatarMode, AvatarSpeechResult

log = structlog.get_logger(__name__)

_AVATAR_IDENTITY = "simli-avatar-agent"
_SIMLI_SAMPLE_RATE = 16000
# Default Trinity "happy" emotion (from the Simli plugin source).
_DEFAULT_EMOTION_ID = "92f24a0c-f046-45df-8df0-af7449c04571"


class SimliAvatar:
    """AvatarTransport that drives a Simli face inside our LiveKit room."""

    def __init__(
        self,
        *,
        room: rtc.Room,
        local_identity: str,
        api_key: str,
        face_id: str,
        livekit_url: str,
        livekit_api_key: str,
        livekit_api_secret: str,
        base_url: str = "https://api.simli.ai",
        max_session_length: int = 600,
        max_idle_time: int = 30,
    ) -> None:
        self._room = room
        self._local_identity = local_identity
        self._api_key = api_key
        self._face_id = face_id
        self._livekit_url = livekit_url
        self._lk_key = livekit_api_key
        self._lk_secret = livekit_api_secret
        self._base_url = base_url.rstrip("/")
        self._max_session_length = max_session_length
        self._max_idle_time = max_idle_time

        self._http: aiohttp.ClientSession | None = None
        self._out: DataStreamAudioOutput | None = None

    @property
    def mode(self) -> AvatarMode:
        return "vendor_video"

    async def start_session(
        self,
        *,
        session_id: str,
        room_name: str,
        avatar_id: str,
        language: str,
    ) -> None:
        """Make the Simli avatar join the room + open the audio output.

        ``avatar_id``, if non-empty, overrides the configured face_id (lets the
        6-avatar picker choose a face per session). Raises ``AvatarError`` on any
        failure — the agent falls back to voice-only.
        """
        face_id = avatar_id or self._face_id
        self._http = aiohttp.ClientSession()

        # 1. Simli compose token.
        cfg = {
            "faceId": f"{face_id}/{_DEFAULT_EMOTION_ID}",
            "handleSilence": True,
            "maxSessionLength": self._max_session_length,
            "maxIdleTime": self._max_idle_time,
        }
        try:
            r1 = await self._http.post(
                f"{self._base_url}/compose/token",
                json=cfg,
                headers={"x-simli-api-key": self._api_key},
            )
            b1 = await r1.text()
            if r1.status != 200:
                raise AvatarError("simli compose token failed", status=r1.status, body=b1)
            session_token = json.loads(b1)["session_token"]
        except AvatarError:
            await self._cleanup_http()
            raise
        except Exception as exc:  # noqa: BLE001
            await self._cleanup_http()
            raise AvatarError(f"simli compose token error: {type(exc).__name__}") from exc

        # 2. Avatar LiveKit token (joins as agent, publishes on our behalf).
        avatar_token = (
            api.AccessToken(self._lk_key, self._lk_secret)
            .with_kind("agent")
            .with_identity(_AVATAR_IDENTITY)
            .with_name(_AVATAR_IDENTITY)
            .with_grants(api.VideoGrants(room_join=True, room=room_name))
            .with_attributes({ATTRIBUTE_PUBLISH_ON_BEHALF: self._local_identity})
            .to_jwt()
        )

        # Watch for the avatar participant joining.
        joined = asyncio.Event()

        def _on_join(p: rtc.RemoteParticipant) -> None:
            if p.identity == _AVATAR_IDENTITY:
                joined.set()

        self._room.on("participant_connected", _on_join)

        # 3. Tell Simli to join our room.
        try:
            r2 = await self._http.post(
                f"{self._base_url}/integrations/livekit/agents",
                json={
                    "session_token": session_token,
                    "livekit_token": avatar_token,
                    "livekit_url": self._livekit_url,
                },
            )
            b2 = await r2.text()
            if r2.status >= 400:
                raise AvatarError("simli livekit integration failed", status=r2.status, body=b2)
        except AvatarError:
            await self._cleanup_http()
            raise
        except Exception as exc:  # noqa: BLE001
            await self._cleanup_http()
            raise AvatarError(f"simli integration error: {type(exc).__name__}") from exc

        # 4. Wait for the avatar to actually appear in the room.
        try:
            await asyncio.wait_for(joined.wait(), timeout=20)
        except TimeoutError as exc:
            await self._cleanup_http()
            raise AvatarError("simli avatar did not join room within 20s") from exc

        self._out = DataStreamAudioOutput(
            room=self._room,
            destination_identity=_AVATAR_IDENTITY,
            sample_rate=_SIMLI_SAMPLE_RATE,
        )
        log.info("avatar.simli.started", session_id=session_id, face_id=face_id)

    async def render(
        self,
        audio_bytes: bytes,
        *,
        sample_rate: int,
        language: str,
        is_first: bool = False,
    ) -> AvatarSpeechResult:
        """Resample our TTS audio to 16 kHz and push it to the Simli avatar."""
        if self._out is None:
            raise AvatarError("simli render before start_session")

        pcm = audio_bytes[44:] if audio_bytes[:4] == b"RIFF" else audio_bytes
        pcm16k = resample_pcm16(pcm, sample_rate, _SIMLI_SAMPLE_RATE)
        samples = len(pcm16k) // 2
        if samples == 0:
            return AvatarSpeechResult(visemes=None, duration_ms=0)

        frame = rtc.AudioFrame(
            data=pcm16k,
            sample_rate=_SIMLI_SAMPLE_RATE,
            num_channels=1,
            samples_per_channel=samples,
        )
        try:
            await self._out.capture_frame(frame)
            self._out.flush()
        except Exception as exc:  # noqa: BLE001
            raise AvatarError(f"simli push error: {type(exc).__name__}") from exc

        duration_ms = int(samples * 1000 / _SIMLI_SAMPLE_RATE)
        # vendor_video: Simli publishes the video itself — nothing to forward.
        return AvatarSpeechResult(visemes=None, duration_ms=duration_ms)

    async def interrupt(self) -> None:
        """Barge-in: drop any buffered avatar audio immediately."""
        if self._out is not None:
            try:
                self._out.clear_buffer()
            except Exception as exc:  # noqa: BLE001
                log.warning("avatar.simli.interrupt_error", error=type(exc).__name__)

    async def close(self) -> None:
        await self._cleanup_http()

    async def _cleanup_http(self) -> None:
        if self._http is not None and not self._http.closed:
            with contextlib.suppress(Exception):
                await self._http.close()
        self._http = None
