"""Unit tests for InterviewOrchestrator — the transport-agnostic turn loop.

Fully offline with fakes: a streaming LLM adapter, a fake TTS that returns the
sentence text as 'audio' bytes, the real VoiceOnlyAvatar, and a CapturingSink
that records what got played. Verifies greeting+question on begin(), follow-up
on candidate turn, sentence-by-sentence TTS, the bound voice, and barge-in.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.agent.orchestrator import InterviewOrchestrator
from app.avatar.voice_only import VoiceOnlyAvatar
from app.graph.brain import InterviewBrain
from app.llm.base import LLMMessage, LLMResponse
from app.speech.base import TTSResult


class FakeStreamingAdapter:
    """Streams a two-sentence interviewer line so the splitter emits 2 clips."""

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(  # pragma: no cover - brain uses stream
        self, system_prompt: str, messages: list[LLMMessage], max_tokens: int | None = None
    ) -> LLMResponse:
        return LLMResponse(text="x", prompt_tokens=1, candidates_tokens=1, thoughts_tokens=None, finish_reason="STOP")

    async def generate_stream(
        self, system_prompt: str, messages: list[LLMMessage], max_tokens: int | None = None
    ) -> AsyncIterator[str]:
        self.call_count += 1
        n = self.call_count
        # Two complete sentences -> SentenceBuffer should emit two clips.
        yield f"Question {n} first part. "
        yield "Second sentence here."


class FakeTTS:
    """Returns the input text encoded as bytes; records the voice used."""

    def __init__(self) -> None:
        self.voices: list[str] = []
        self.texts: list[str] = []

    async def synthesize(
        self, text: str, *, language: str, voice=None, pace=None, temperature=None
    ) -> TTSResult:
        self.voices.append(voice or "")
        self.texts.append(text)
        return TTSResult(audio_bytes=text.encode("utf-8"), format="wav", sample_rate=24000)


class CapturingSink:
    def __init__(self) -> None:
        self.played: list[bytes] = []

    async def play(self, audio_bytes: bytes, *, sample_rate: int) -> None:
        self.played.append(audio_bytes)


def _orch(max_turns: int = 2) -> tuple[InterviewOrchestrator, FakeTTS, CapturingSink]:
    brain, _greeting = InterviewBrain.start(
        adapter=FakeStreamingAdapter(),
        session_id="11111111-1111-1111-1111-111111111111",
        job_id="22222222-2222-2222-2222-222222222222",
        job_title="Java Developer",
        language="en",
        max_turns=max_turns,
    )
    tts = FakeTTS()
    sink = CapturingSink()
    orch = InterviewOrchestrator(
        brain=brain, tts=tts, avatar=VoiceOnlyAvatar(), sink=sink, voice="kavya"
    )
    return orch, tts, sink


@pytest.mark.asyncio
async def test_begin_speaks_greeting_then_first_question() -> None:
    orch, tts, sink = _orch()
    await orch.begin()
    # Greeting (>=1 clip) + first question (2 sentences = 2 clips).
    assert len(sink.played) >= 3
    # The streamed question's two sentences were both synthesized.
    joined = " ".join(tts.texts)
    assert "Question 1 first part." in joined
    assert "Second sentence here." in joined


@pytest.mark.asyncio
async def test_bound_voice_used_for_every_clip() -> None:
    """Voice glued to avatar: every TTS call uses the same bound speaker."""
    orch, tts, sink = _orch()
    await orch.begin()
    assert tts.voices, "no TTS calls made"
    assert set(tts.voices) == {"kavya"}


@pytest.mark.asyncio
async def test_candidate_turn_then_closing_completes() -> None:
    orch, tts, sink = _orch(max_turns=1)
    await orch.begin()
    assert not orch.is_complete
    # turn_count reaches max_turns=1 -> closing (static, no LLM).
    await orch.on_candidate_turn("my answer")
    assert orch.is_complete


@pytest.mark.asyncio
async def test_sentences_split_into_separate_clips() -> None:
    """The two-sentence question produces two distinct TTS clips."""
    orch, tts, sink = _orch()
    await orch.begin()
    # Among the synthesized texts, the question's two sentences are separate.
    assert "Question 1 first part." in tts.texts
    assert "Second sentence here." in tts.texts


@pytest.mark.asyncio
async def test_interrupt_is_safe_when_idle() -> None:
    """interrupt() with no active turn must not raise."""
    orch, _tts, _sink = _orch()
    await orch.interrupt()  # nothing in flight
    assert orch._active_turn is None


class _BlockingTTS:
    """TTS that blocks when synthesizing the streamed QUESTION (not the static
    greeting) until released — lets the first_question turn stay in-flight so
    interrupt() can cancel it mid-stream (C1 barge-in). The greeting is spoken
    via _speak_text and is NOT a cancellable turn, so we must block on the
    question's TTS, identified by the 'Question' marker from FakeStreamingAdapter."""

    def __init__(self) -> None:
        import asyncio

        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def synthesize(self, text, *, language, voice=None, pace=None, temperature=None):
        if "Question" in text:
            self.entered.set()
            await self.release.wait()  # hang inside the cancellable turn
        return TTSResult(audio_bytes=b"x", format="wav", sample_rate=24000)


@pytest.mark.asyncio
async def test_bargein_cancels_only_the_turn_not_the_caller() -> None:
    """C1: interrupt() mid-turn cancels the speaking turn cleanly and the
    orchestrator stays usable (the awaiting caller is NOT cancelled)."""
    import asyncio

    brain, _g = InterviewBrain.start(
        adapter=FakeStreamingAdapter(),
        session_id="11111111-1111-1111-1111-111111111111",
        job_id="22222222-2222-2222-2222-222222222222",
        job_title="Java Developer",
        language="en",
        max_turns=3,
    )
    tts = _BlockingTTS()
    orch = InterviewOrchestrator(
        brain=brain, tts=tts, avatar=VoiceOnlyAvatar(), sink=CapturingSink(), voice="kavya"
    )

    # begin() speaks the greeting (not cancellable) then streams Q1 via
    # _speak_turn (cancellable). The TTS blocks inside Q1.
    begin_task = asyncio.create_task(orch.begin())
    await asyncio.wait_for(tts.entered.wait(), timeout=2)  # Q1 turn now in-flight
    assert orch._active_turn is not None  # a cancellable turn IS active

    # Barge-in: cancels the in-flight Q1 turn WITHOUT raising and WITHOUT
    # cancelling begin_task's awaiter (begin() swallows the CancelledError).
    await orch.interrupt()
    tts.release.set()  # unblock any straggler
    await asyncio.wait_for(begin_task, timeout=2)  # returns cleanly, no raise

    assert orch._active_turn is None
    # The interrupted Q1 line is NOT committed (brain commits only after the
    # stream fully drains — the cancel happened before that). Only the static
    # greeting (turn 0) is present.
    interviewer = [t for t in brain.state["turns"] if t["speaker"] == "interviewer"]
    assert all(t["turn_number"] == 0 for t in interviewer)
