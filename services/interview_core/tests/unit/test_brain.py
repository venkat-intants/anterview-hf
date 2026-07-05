"""Unit tests for InterviewBrain — the per-turn streaming policy driver.

Fully offline: a FakeStreamingAdapter satisfies the LLMAdapter protocol and
yields canned chunks, so no network / Gemini call is made. These tests pin the
behaviour that must match the compiled graph (build.py) topology:

  greeting (static) -> first_question (LLM) -> [respond=follow_up (LLM)]* ->
  respond=closing (static) once turn_count reaches max_turns.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.graph.brain import InterviewBrain
from app.llm.base import LLMMessage, LLMResponse


class FakeStreamingAdapter:
    """Offline LLMAdapter: streams a canned question in two chunks per call."""

    def __init__(self) -> None:
        self.call_count = 0
        self.system_prompts: list[str] = []

    async def generate(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
    ) -> LLMResponse:  # pragma: no cover - brain uses generate_stream only
        self.call_count += 1
        return LLMResponse(
            text=f"Question {self.call_count}.",
            prompt_tokens=5,
            candidates_tokens=5,
            thoughts_tokens=None,
            finish_reason="STOP",
        )

    async def generate_stream(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        self.call_count += 1
        self.system_prompts.append(system_prompt)
        n = self.call_count
        # Two chunks so the brain's accumulate-and-commit is exercised.
        yield f"Question {n}, "
        yield "part two."


def _start(max_turns: int = 3) -> tuple[InterviewBrain, str, FakeStreamingAdapter]:
    adapter = FakeStreamingAdapter()
    brain, greeting = InterviewBrain.start(
        adapter=adapter,
        session_id="11111111-1111-1111-1111-111111111111",
        job_id="22222222-2222-2222-2222-222222222222",
        job_title="Junior Java Developer",
        language="en",
        max_turns=max_turns,
    )
    return brain, greeting, adapter


@pytest.mark.asyncio
async def test_start_emits_static_greeting_no_llm() -> None:
    """start() returns a non-empty greeting and makes NO LLM call."""
    brain, greeting, adapter = _start()
    assert "Junior Java Developer" in greeting
    assert adapter.call_count == 0  # greeting is static
    assert brain.turn_count == 0
    assert not brain.is_complete
    # Greeting committed as turn 0, interviewer.
    assert brain.state["turns"][0]["speaker"] == "interviewer"
    assert brain.state["turns"][0]["turn_number"] == 0


@pytest.mark.asyncio
async def test_first_question_streams_and_commits() -> None:
    """first_question yields chunks and commits the full text to turns once."""
    brain, _greeting, adapter = _start()
    chunks = [c async for c in brain.first_question()]

    assert chunks == ["Question 1, ", "part two."]
    assert adapter.call_count == 1
    # Full text committed exactly once as an interviewer turn.
    interviewer_turns = [t for t in brain.state["turns"] if t["speaker"] == "interviewer"]
    assert interviewer_turns[-1]["text"] == "Question 1, part two."
    assert brain.last_llm_latency_ms is not None


@pytest.mark.asyncio
async def test_respond_follow_up_then_closing_routing() -> None:
    """respond() streams follow-ups until max_turns, then a static closing."""
    brain, _greeting, adapter = _start(max_turns=2)
    _ = [c async for c in brain.first_question()]  # Q1
    calls_after_q1 = adapter.call_count

    # Turn 1: candidate answers -> follow_up (turn_count 1 < 2)
    out1 = "".join([c async for c in brain.respond("My first answer.")])
    assert out1 == "Question 2, part two."
    assert brain.turn_count == 1
    assert not brain.is_complete
    assert adapter.call_count == calls_after_q1 + 1  # follow_up hit the LLM

    # Turn 2: candidate answers -> turn_count reaches 2 == max_turns -> closing
    out2 = "".join([c async for c in brain.respond("My second answer.")])
    assert out2  # non-empty closing line
    assert brain.turn_count == 2
    assert brain.is_complete
    # Closing is STATIC — no extra LLM call beyond the follow-up.
    assert adapter.call_count == calls_after_q1 + 1


@pytest.mark.asyncio
async def test_candidate_turns_recorded_in_transcript() -> None:
    """Candidate answers land in the transcript in order."""
    brain, _g, _a = _start(max_turns=3)
    _ = [c async for c in brain.first_question()]
    _ = [c async for c in brain.respond("answer one")]
    _ = [c async for c in brain.respond("answer two")]

    candidate_texts = [t["text"] for t in brain.state["turns"] if t["speaker"] == "candidate"]
    assert candidate_texts == ["answer one", "answer two"]


@pytest.mark.asyncio
async def test_barge_in_does_not_persist_interrupted_turn() -> None:
    """If the agent stops consuming mid-stream, the turn is NOT committed.

    Simulates barge-in: break out of the async-for after the first chunk. The
    interrupted interviewer line must NOT appear in turns (architecture rule).
    """
    brain, _g, _a = _start()
    turns_before = len(brain.state["turns"])
    async for _chunk in brain.first_question():
        break  # candidate interrupted after the first chunk

    # No new committed turn (commit only happens after the stream completes).
    assert len(brain.state["turns"]) == turns_before


@pytest.mark.asyncio
async def test_persona_in_system_prompt() -> None:
    """The streamed call carries the persona-bearing system prompt."""
    brain, _g, adapter = _start()
    _ = [c async for c in brain.first_question()]
    assert adapter.system_prompts, "no system prompt captured"
    assert "[PERSONA:" in adapter.system_prompts[0]
