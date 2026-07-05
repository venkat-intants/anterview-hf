"""InterviewOrchestrator — transport-agnostic real-time turn loop.

This is the 'conductor' (docs/ARCH-realtime-interview.md §5) with ALL the
LiveKit-specific plumbing removed, so the turn logic is correct and unit-testable
independent of any transport. The LiveKit shell (livekit_agent.py) owns VAD /
turn-final detection and feeds this class; this class owns the STREAM:

    greeting/question  -> SentenceBuffer -> Sarvam TTS (per sentence)
                       -> AvatarTransport.render -> AudioSink.play

Flow the transport drives:
    orch = InterviewOrchestrator(brain, tts, avatar, sink, voice="...")
    await orch.begin()                     # greeting + first question
    await orch.on_candidate_turn(text)     # follow-up OR closing
    ... repeat until orch.is_complete ...
    await orch.interrupt()                 # on barge-in (cancels in-flight)

Why per-sentence: TTS fires sentence-by-sentence so the avatar starts speaking
the first sentence while the LLM is still streaming the rest — the overlap that
keeps p95 under 2s (docs/ARCH §7).

VOICE BINDING (memory project_voice_per_avatar): ONE fixed Sarvam ``voice`` per
session, passed at construction. Emotion varies via pace/temperature (per-moment,
optional), NEVER by swapping the speaker.

BARGE-IN: each spoken turn runs under a cancellation guard. ``interrupt()``
cancels the in-flight turn; the brain does NOT commit an interrupted interviewer
line (its commit happens only after the stream fully drains — see brain.py), so
an interrupted line is treated as never-said, exactly per the architecture.

PII: never log candidate/interviewer text or audio bytes — event + counters only.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Protocol, runtime_checkable

import structlog

from app.avatar.base import AvatarError, AvatarTransport
from app.graph.brain import InterviewBrain
from app.speech.base import TTSAdapter, TTSError
from app.speech.sentence_splitter import SentenceBuffer

log = structlog.get_logger(__name__)


@runtime_checkable
class AudioSink(Protocol):
    """How the orchestrator emits interviewer audio to the candidate.

    The LiveKit shell implements this by publishing the bytes to the room's
    audio track. Tests implement it by capturing bytes in a list.
    """

    async def play(self, audio_bytes: bytes, *, sample_rate: int) -> None:
        """Play one audio clip (one sentence) to the candidate."""
        ...


@runtime_checkable
class OrchestratorHooks(Protocol):
    """Optional observability / persistence callbacks.

    All methods are optional in spirit — pass a no-op impl if not needed. The
    LiveKit shell uses these to persist committed turns to Postgres and to emit
    the §12 latency metrics. Kept out of the core loop so the loop stays pure.
    """

    async def on_interviewer_text(self, text: str, *, turn_number: int) -> None:
        """A full interviewer line was committed (greeting/question/closing)."""
        ...

    async def on_complete(self) -> None:
        """The interview reached its closing line (brain.is_complete)."""
        ...


class _NoopHooks:
    """Default hooks — does nothing. Keeps the loop usable without wiring."""

    async def on_interviewer_text(self, text: str, *, turn_number: int) -> None:
        return None

    async def on_complete(self) -> None:
        return None


class InterviewOrchestrator:
    """Drives one interview session's spoken turns. Transport-agnostic."""

    def __init__(
        self,
        *,
        brain: InterviewBrain,
        tts: TTSAdapter,
        avatar: AvatarTransport,
        sink: AudioSink,
        voice: str,
        hooks: OrchestratorHooks | None = None,
        pace: float | None = None,
        temperature: float | None = None,
    ) -> None:
        self._brain = brain
        self._tts = tts
        self._avatar = avatar
        self._sink = sink
        self._voice = voice  # fixed Sarvam speaker bound to this avatar
        self._hooks: OrchestratorHooks = hooks or _NoopHooks()
        self._pace = pace
        self._temperature = temperature
        # Barge-in: the currently-speaking turn task, if any.
        self._active_turn: asyncio.Task[None] | None = None

    @property
    def is_complete(self) -> bool:
        return self._brain.is_complete

    # ------------------------------------------------------------------
    # Turn entry points (driven by the transport)
    # ------------------------------------------------------------------
    async def begin(self) -> None:
        """Speak the greeting (static) then stream the first question."""
        greeting = self._brain.state["turns"][0]["text"]  # committed by start()
        await self._speak_text(greeting, turn_number=0)
        await self._hooks.on_interviewer_text(greeting, turn_number=0)
        await self._speak_turn(self._brain.first_question())

    async def on_candidate_turn(self, candidate_text: str) -> None:
        """Ingest the candidate's answer and speak the next interviewer line.

        Routes through the brain: a follow-up (streamed) or the static closing.
        """
        await self._speak_turn(self._brain.respond(candidate_text))
        if self._brain.is_complete:
            await self._hooks.on_complete()

    async def _speak_turn(self, chunk_iter) -> None:  # type: ignore[no-untyped-def]
        """Run one interviewer turn as a SEPARATE cancellable task.

        C1 fix: ``interrupt()`` cancels ``self._active_turn``. If the stream ran
        directly under ``begin``/``on_candidate_turn`` (which are awaited by the
        outer agent task), ``asyncio.current_task()`` would be the AGENT task and
        a barge-in would kill the whole interview. Wrapping in its own task makes
        the turn the correct unit of cancellation. A cancelled turn raises
        CancelledError here, which we swallow (barge-in is expected).
        """
        turn = asyncio.create_task(self._run_stream(chunk_iter))
        self._active_turn = turn
        try:
            await turn
        except asyncio.CancelledError:
            pass  # barge-in cancelled this turn — expected
        finally:
            if self._active_turn is turn:
                self._active_turn = None

    async def interrupt(self) -> None:
        """Barge-in: cancel the in-flight interviewer turn + avatar output."""
        # Stop avatar/audio output first (the visible/audible stop matters most).
        try:
            await self._avatar.interrupt()
        except Exception as exc:  # noqa: BLE001 — never let interrupt raise
            log.warning("orchestrator.avatar_interrupt_error", error=type(exc).__name__)
        # Cancel the in-flight turn task; brain won't commit the interrupted line.
        if self._active_turn is not None and not self._active_turn.done():
            self._active_turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._active_turn
        self._active_turn = None
        log.info("orchestrator.interrupted", session_id=self._brain.state["session_id"])

    async def close(self) -> None:
        """Tear down the avatar transport at session end."""
        try:
            await self._avatar.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("orchestrator.avatar_close_error", error=type(exc).__name__)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _run_stream(self, chunk_iter) -> None:  # type: ignore[no-untyped-def]
        """Consume a brain text-stream, split into sentences, speak each.

        Runs as the body of ``_active_turn`` (created in ``_speak_turn``). The
        brain commits the interviewer line only AFTER the stream fully drains, so
        a mid-stream cancel (barge-in) leaves no half-turn in the transcript and
        skips the persist hook (S4) — both correct.
        """
        buf = SentenceBuffer()
        turn_number = self._brain.turn_count  # for logging only
        first = True
        async for delta in chunk_iter:
            for sentence in buf.feed(delta):
                await self._speak_sentence(sentence, is_first=first)
                first = False
        tail = buf.flush()
        if tail.strip():
            await self._speak_sentence(tail, is_first=first)
        # Persist the committed line (brain has appended it to turns). Only
        # reached if the stream drained without cancellation.
        committed = self._brain.state["next_interviewer_message"]
        if committed:
            await self._hooks.on_interviewer_text(
                committed, turn_number=self._brain.turn_count
            )
        log.info(
            "orchestrator.turn_spoken",
            session_id=self._brain.state["session_id"],
            turn_count=turn_number,
        )

    async def _speak_text(self, text: str, *, turn_number: int) -> None:
        """Speak a single static line (greeting/closing) as whole sentences."""
        buf = SentenceBuffer()
        first = True
        for sentence in buf.feed(text):
            await self._speak_sentence(sentence, is_first=first)
            first = False
        tail = buf.flush()
        if tail.strip():
            await self._speak_sentence(tail, is_first=first)

    async def _speak_sentence(self, sentence: str, *, is_first: bool) -> None:
        """TTS one sentence in the bound voice, render avatar, play to sink.

        Degrades gracefully: a TTS failure skips the sentence (logged); an
        avatar failure falls through to audio-only (the audio still plays).
        """
        text = sentence.strip()
        if not text:
            return
        try:
            res = await self._tts.synthesize(
                text,
                language=self._brain.language,
                voice=self._voice,
                pace=self._pace,
                temperature=self._temperature,
            )
        except TTSError as exc:
            log.warning("orchestrator.tts_failed", status=exc.status)
            return  # skip this sentence; keep the conversation alive

        # Avatar render is best-effort — never block audio on it.
        try:
            await self._avatar.render(
                res.audio_bytes,
                sample_rate=res.sample_rate,
                language=self._brain.language,
                is_first=is_first,
            )
        except AvatarError as exc:
            log.warning("orchestrator.avatar_render_failed", status=exc.status)

        await self._sink.play(res.audio_bytes, sample_rate=res.sample_rate)
