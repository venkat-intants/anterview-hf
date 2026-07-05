"""LiveKit shell for the interview agent — the thin transport layer.

This is the ONLY module that touches the LiveKit SDK (docs/ARCH §5). It joins a
room as the interviewer participant and bridges LiveKit media to the
transport-agnostic ``InterviewOrchestrator``:

    candidate mic track  --(LiveKit, resampled 16k)-->  SarvamStreamingSTT
        --(RMS turn-end)-->  orchestrator.on_candidate_turn(transcript)
            --> brain -> TTS -> LiveKitAudioSink --(publish 24k track)--> candidate

Deliberately lean: we use ``livekit.rtc`` directly (NOT the heavier
``livekit-agents`` framework). Turn-end is detected by server-side RMS silence on
the candidate's 16 kHz frames — the same simple, proven heuristic the old client
used, moved server-side so the agent owns cadence (the cto-review decision).

Voice-only first: the orchestrator is given a ``VoiceOnlyAvatar``. The Simli
avatar is a separate browser-side layer (Simli Compose), wired after voice works.

This module can only be SMOKE-TESTED live against a LiveKit server — it is not
unit-tested with fakes (the orchestrator/brain/avatar below it ARE).

PII: never log transcript/audio — event + counters only.
"""

from __future__ import annotations

import asyncio
import contextlib

import structlog
from livekit import rtc

from app.agent.orchestrator import InterviewOrchestrator, OrchestratorHooks
from app.avatar.base import AvatarError, AvatarTransport
from app.avatar.simli import SimliAvatar
from app.avatar.voice_only import VoiceOnlyAvatar
from app.config import settings
from app.graph.brain import InterviewBrain
from app.graph.personas import Persona
from app.graph.state import Language
from app.llm.base import LLMAdapter
from app.speech import build_default_tts_adapter
from app.speech.sarvam_stt_stream import SarvamStreamingSTT, STTStreamError

log = structlog.get_logger(__name__)


class _NoopSink:
    """AudioSink that drops audio — used in Simli mode where the avatar (Simli)
    is the audio destination and republishes audio+video itself."""

    async def play(self, audio_bytes: bytes, *, sample_rate: int) -> None:
        return None

# Candidate audio is pulled from LiveKit resampled to this rate — what Sarvam
# streaming STT expects (16 kHz mono PCM16). Our TTS output is 24 kHz (separate).
_STT_SAMPLE_RATE = 16000
_STT_CHANNELS = 1

# Identity of the Simli avatar participant (must match SimliAvatar). Tracks from
# this identity are the avatar's own audio/video, NOT candidate speech.
_AVATAR_IDENTITY = "simli-avatar-agent"

# Server-side RMS turn detection (mirrors the old client lib/pcmCapture.ts).
_SILENCE_RMS_THRESHOLD = 0.01     # normalized 0..1; below = silence
_SILENCE_MS_TO_END_TURN = 1500    # trailing silence that ends a candidate turn
_MIN_SPEECH_MS = 400              # ignore blips shorter than this


class LiveKitAudioSink:
    """AudioSink that publishes interviewer TTS audio into the LiveKit room.

    Holds an ``rtc.AudioSource`` at the TTS sample rate and pushes each clip as
    audio frames. Satisfies the orchestrator's ``AudioSink`` protocol.
    """

    def __init__(self, source: rtc.AudioSource, sample_rate: int) -> None:
        self._source = source
        self._sample_rate = sample_rate

    async def play(self, audio_bytes: bytes, *, sample_rate: int) -> None:
        pcm = _strip_wav_header(audio_bytes)
        if not pcm:
            return
        # One AudioFrame for the whole clip; LiveKit paces playout from the
        # samples_per_channel + sample_rate. 16-bit mono => 2 bytes/sample.
        samples = len(pcm) // 2
        if samples == 0:
            return
        frame = rtc.AudioFrame(
            data=pcm,
            sample_rate=sample_rate,
            num_channels=1,
            samples_per_channel=samples,
        )
        await self._source.capture_frame(frame)


class InterviewAgent:
    """Joins one LiveKit room and runs the interview for one session."""

    def __init__(
        self,
        *,
        session_id: str,
        job_id: str,
        job_title: str,
        llm_adapter: LLMAdapter,
        language: Language = "en",
        voice: str = "kavya",
        max_turns: int = 5,
        persona: Persona | None = None,
        avatar_id: str = "",
        hooks: OrchestratorHooks | None = None,
    ) -> None:
        self._session_id = session_id
        self._job_id = job_id
        self._job_title = job_title
        self._llm = llm_adapter
        self._language = language
        self._voice = voice
        self._max_turns = max_turns
        self._persona = persona
        self._avatar_id = avatar_id
        self._hooks = hooks

        self._room = rtc.Room()
        self._orch: InterviewOrchestrator | None = None
        self._tts_sample_rate = 24000  # Sarvam v3 default (matches sarvam_tts.py)
        self._candidate_task: asyncio.Task[None] | None = None
        self._done = asyncio.Event()
        # C4 fix: gate the candidate-audio consumer until the orchestrator is
        # built, so a fast-joining candidate's track can't drive a None orch.
        self._orch_ready = asyncio.Event()

    async def run(self, token: str) -> None:
        """Connect to the room, run the interview, then disconnect."""
        url = settings.livekit_url

        # 1. Wire candidate-audio handling before connect so no track is missed.
        self._room.on("track_subscribed", self._on_track_subscribed)

        # 2. Connect first — the avatar setup (Simli) needs a connected room and
        #    our local participant identity.
        await self._room.connect(url, token)
        local_identity = self._room.local_participant.identity
        log.info("agent.connected", session_id=self._session_id, room=self._room.name)

        # 2b. Wait for the candidate to actually join before speaking, so the
        #     greeting isn't played to an empty room AND the avatar's idle timer
        #     only starts once someone is present. Skipped if a candidate is
        #     already in the room (reconnect).
        await self._wait_for_candidate()

        # 3. Build the brain.
        brain, _greeting = InterviewBrain.start(
            adapter=self._llm,
            session_id=self._session_id,
            job_id=self._job_id,
            job_title=self._job_title,
            language=self._language,
            max_turns=self._max_turns,
            persona=self._persona,
        )

        # 4. Choose avatar + audio routing by provider. The avatar and the sink
        #    are coupled: in Simli mode the avatar IS the audio destination
        #    (Simli republishes audio+video), so we give the orchestrator a
        #    no-op sink to avoid publishing audio twice. In voice-only mode the
        #    avatar is a no-op and we publish our TTS on our own audio track.
        avatar, sink = await self._build_avatar_and_sink(local_identity)

        self._orch = InterviewOrchestrator(
            brain=brain,
            tts=build_default_tts_adapter(),
            avatar=avatar,
            sink=sink,
            voice=self._voice,
            hooks=self._hooks,
        )
        # Orchestrator now exists — release the candidate-audio consumer (C4).
        self._orch_ready.set()

        # 5. Speak greeting + first question.
        await self._orch.begin()

        # 6. Run until the interview completes (closing spoken) or room closes.
        try:
            await self._done.wait()
        finally:
            if self._candidate_task is not None:
                self._candidate_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._candidate_task
            if self._orch is not None:
                await self._orch.close()
            with contextlib.suppress(Exception):
                await self._room.disconnect()
        log.info("agent.finished", session_id=self._session_id)

    async def _build_avatar_and_sink(
        self, local_identity: str
    ) -> tuple[AvatarTransport, object]:
        """Pick the avatar transport + audio sink for the configured provider.

        Returns ``(avatar, sink)``. On any Simli setup failure, falls back to
        voice-only so the interview still runs (avatar failure must never abort).
        """
        provider = settings.avatar_provider.lower()
        if provider == "simli":
            try:
                avatar = SimliAvatar(
                    room=self._room,
                    local_identity=local_identity,
                    api_key=settings.simli_api_key,
                    face_id=settings.simli_face_id,
                    livekit_url=settings.livekit_url,
                    livekit_api_key=settings.livekit_api_key,
                    livekit_api_secret=settings.livekit_api_secret,
                    base_url=settings.simli_api_base_url,
                    # Keep the face alive while the candidate thinks between
                    # answers (default 30s is too aggressive for an interview).
                    max_idle_time=300,
                    max_session_length=900,
                )
                await avatar.start_session(
                    session_id=self._session_id,
                    room_name=self._room.name,
                    avatar_id=self._avatar_id or settings.simli_face_id,
                    language=self._language,
                )
                # Simli republishes audio+video — orchestrator must NOT also
                # publish on a second track.
                return avatar, _NoopSink()
            except AvatarError as exc:
                log.warning(
                    "agent.simli_setup_failed_falling_back_voice_only",
                    status=exc.status,
                )

        # Voice-only: publish our TTS on our own audio track.
        audio_source = rtc.AudioSource(self._tts_sample_rate, 1)
        track = rtc.LocalAudioTrack.create_audio_track("interviewer", audio_source)
        await self._room.local_participant.publish_track(
            track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        return VoiceOnlyAvatar(), LiveKitAudioSink(audio_source, self._tts_sample_rate)

    async def _wait_for_candidate(self, timeout: float = 300.0) -> None:
        """Block until a non-avatar remote participant joins the room.

        Prevents speaking the greeting to an empty room and starting the avatar
        idle timer before anyone is present. Returns immediately if a candidate
        is already connected (reconnect). Times out gracefully (proceeds anyway).
        """
        for p in self._room.remote_participants.values():
            if p.identity != _AVATAR_IDENTITY:
                return
        joined: asyncio.Event = asyncio.Event()

        def _on_join(p: rtc.RemoteParticipant) -> None:
            if p.identity != _AVATAR_IDENTITY:
                joined.set()

        self._room.on("participant_connected", _on_join)
        log.info("agent.waiting_for_candidate", session_id=self._session_id)
        try:
            await asyncio.wait_for(joined.wait(), timeout=timeout)
            log.info("agent.candidate_joined", session_id=self._session_id)
        except TimeoutError:
            log.info("agent.candidate_wait_timeout", session_id=self._session_id)

    def _on_track_subscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        """Start consuming the candidate's mic track (first audio track only)."""
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        if participant.identity == _AVATAR_IDENTITY:
            return  # ignore the Simli avatar's own audio track
        if self._candidate_task is not None:
            return  # already consuming a candidate track
        self._candidate_task = asyncio.create_task(
            self._consume_candidate_audio(track),
            name="candidate_audio_consumer",
        )

    async def _consume_candidate_audio(self, track: rtc.Track) -> None:
        """Read candidate frames, run STT per turn, drive the orchestrator.

        One ``SarvamStreamingSTT`` per detected turn: feed frames while the
        candidate speaks; when trailing silence crosses the threshold, finalize
        to get the transcript and hand it to the orchestrator. On any STT error,
        skip that turn (keep the session alive) and resume listening.
        """
        # C4: wait until the orchestrator is built before processing any audio,
        # so a fast-joining candidate can never drive a None orchestrator.
        await self._orch_ready.wait()
        assert self._orch is not None  # set before _orch_ready fires

        stream = rtc.AudioStream(
            track, sample_rate=_STT_SAMPLE_RATE, num_channels=_STT_CHANNELS
        )
        stt: SarvamStreamingSTT | None = None
        speaking = False
        speech_ms = 0.0
        silence_ms = 0.0

        try:
            async for event in stream:
                # C5: only stop when the interview has fully completed; checked
                # once per frame is cheap and _orch is guaranteed set here.
                if self._orch.is_complete:
                    self._done.set()
                    return
                frame = event.frame
                pcm = bytes(frame.data)
                ms = (frame.samples_per_channel / frame.sample_rate) * 1000.0
                rms = _frame_rms(pcm)

                if rms >= _SILENCE_RMS_THRESHOLD:
                    # Speech.
                    if not speaking:
                        speaking = True
                        speech_ms = 0.0
                        stt = SarvamStreamingSTT(
                            api_key=settings.sarvam_api_key,
                            model=settings.sarvam_stt_model,
                        )
                        try:
                            await stt.start(self._language)
                        except STTStreamError:
                            log.warning("agent.stt_start_failed")
                            stt = None
                            speaking = False
                            continue
                    speech_ms += ms
                    silence_ms = 0.0
                    if stt is not None:
                        with contextlib.suppress(STTStreamError):
                            await stt.send_audio(pcm)
                else:
                    # Silence.
                    if speaking:
                        silence_ms += ms
                        if stt is not None:
                            with contextlib.suppress(STTStreamError):
                                await stt.send_audio(pcm)
                        if (
                            silence_ms >= _SILENCE_MS_TO_END_TURN
                            and speech_ms >= _MIN_SPEECH_MS
                        ):
                            # Turn ended — finalize + drive the orchestrator.
                            await self._finish_turn(stt)
                            stt = None
                            speaking = False
                            speech_ms = 0.0
                            silence_ms = 0.0
        except asyncio.CancelledError:
            pass
        finally:
            if stt is not None:
                with contextlib.suppress(Exception):
                    await stt.finalize()

    async def _finish_turn(self, stt: SarvamStreamingSTT | None) -> None:
        """Finalize STT and feed the transcript to the orchestrator."""
        if stt is None or self._orch is None:
            return
        try:
            transcript = await stt.finalize()
        except STTStreamError:
            log.warning("agent.stt_finalize_failed")
            return
        if not transcript.strip():
            return  # no-speech turn; keep listening
        # Barge-in safety: if the interviewer is mid-line, cancel it first.
        await self._orch.interrupt()
        await self._orch.on_candidate_turn(transcript)
        if self._orch.is_complete:
            self._done.set()


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------
def _strip_wav_header(audio_bytes: bytes) -> bytes:
    """Return raw PCM payload, stripping a 44-byte RIFF/WAV header if present."""
    if len(audio_bytes) > 44 and audio_bytes[:4] == b"RIFF":
        return audio_bytes[44:]
    return audio_bytes


def _frame_rms(pcm: bytes) -> float:
    """Normalized RMS (0..1) of 16-bit little-endian mono PCM. 0.0 if empty.

    Pure-Python, no numpy on the hot path beyond what's already imported. For a
    20ms 16k frame (~640 bytes) this is cheap enough per frame.
    """
    n = len(pcm) // 2
    if n == 0:
        return 0.0
    import array

    samples = array.array("h")
    samples.frombytes(pcm[: n * 2])
    acc = 0
    for s in samples:
        acc += s * s
    mean_sq = acc / n
    # 32768 is full-scale for int16; normalize to 0..1.
    return (mean_sq ** 0.5) / 32768.0
