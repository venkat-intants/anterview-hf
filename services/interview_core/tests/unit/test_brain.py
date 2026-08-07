"""Unit tests for InterviewBrain — the per-turn streaming policy driver.

Fully offline: a FakeStreamingAdapter satisfies the LLMAdapter protocol and
yields canned chunks, so no network / Gemini call is made. These tests pin the
behaviour that must match the compiled graph (build.py) topology:

  greeting (static) -> first_question (LLM) -> [respond=follow_up (LLM)]* ->
  respond=closing (static) once turn_count reaches max_turns.

The second half of the file pins the LLM-failure policy: a transient failure is
retried, a permanent one is not, a retry never replays text the candidate has
already heard, and an exhausted turn degrades to a canned Day-1-language line
instead of ending the interview.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.graph import brain as brain_mod
from app.graph.brain import LLM_FALLBACK_TEMPLATES, InterviewBrain, render_llm_fallback
from app.graph.state import Language
from app.llm.base import LLMError, LLMMessage, LLMResponse


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


# ---------------------------------------------------------------------------
# LLM failure policy
# ---------------------------------------------------------------------------


class ScriptedStreamAdapter:
    """Offline LLMAdapter that replays a scripted outcome per streaming call.

    Each script entry is ``(chunks, error)``: yield ``chunks`` in order, then
    raise ``error`` if it is not None. Calls past the end of the script reuse
    the LAST entry, so a one-entry script means "always do this".
    """

    def __init__(self, script: list[tuple[list[str], LLMError | None]]) -> None:
        self._script = script
        self.calls = 0

    async def generate(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
    ) -> LLMResponse:  # pragma: no cover - brain uses generate_stream only
        raise AssertionError("brain must stream, not call generate()")

    async def generate_stream(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        chunks, error = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        for chunk in chunks:
            yield chunk
        if error is not None:
            raise error


def _rate_limited() -> LLMError:
    """The failure this project actually hits: Gemini free-tier 429."""
    return LLMError("gemini http 429", status=429)


def _bad_api_key() -> LLMError:
    """A permanent failure — the same request will fail the same way forever."""
    return LLMError("gemini http 401", status=401)


def _network_drop() -> LLMError:
    """Statusless transient failure, exactly as the adapters wrap httpx errors."""
    return LLMError("network: ReadTimeout: timed out")


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the retry backoff instead of actually waiting it out."""
    recorded: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(brain_mod.asyncio, "sleep", _fake_sleep)
    return recorded


def _start_scripted(
    script: list[tuple[list[str], LLMError | None]],
    *,
    language: Language = "en",
    max_turns: int = 3,
) -> tuple[InterviewBrain, ScriptedStreamAdapter]:
    adapter = ScriptedStreamAdapter(script)
    brain, _greeting = InterviewBrain.start(
        adapter=adapter,
        session_id="33333333-3333-3333-3333-333333333333",
        job_id="44444444-4444-4444-4444-444444444444",
        job_title="Junior Java Developer",
        language=language,
        max_turns=max_turns,
    )
    return brain, adapter


@pytest.mark.asyncio
async def test_transient_error_is_retried_then_succeeds(sleeps: list[float]) -> None:
    """A 429 on the first attempt is retried, and the candidate hears only the
    successful answer — no error text, no fallback."""
    brain, adapter = _start_scripted(
        [
            ([], _rate_limited()),
            (["Tell me about ", "your background."], None),
        ]
    )

    chunks = [c async for c in brain.first_question()]

    assert chunks == ["Tell me about ", "your background."]
    assert adapter.calls == 2, "transient failure must be retried"
    assert sleeps == [0.5], "one backoff before the single retry"
    interviewer = [t for t in brain.state["turns"] if t["speaker"] == "interviewer"]
    assert interviewer[-1]["text"] == "Tell me about your background."
    assert render_llm_fallback("en") not in interviewer[-1]["text"]


@pytest.mark.asyncio
async def test_permanent_error_is_not_retried(sleeps: list[float]) -> None:
    """A 401 is the provider's verdict on the request — retrying is pointless,
    so the turn degrades immediately."""
    brain, adapter = _start_scripted([([], _bad_api_key())])

    chunks = [c async for c in brain.first_question()]

    assert adapter.calls == 1, "permanent failure must not be retried"
    assert sleeps == [], "no backoff for a permanent failure"
    assert chunks == [render_llm_fallback("en")]


@pytest.mark.asyncio
async def test_retry_does_not_replay_already_yielded_text(sleeps: list[float]) -> None:
    """A failure AFTER chunks were emitted must not restart the stream.

    Chunks are spoken the moment they leave the brain, so a retry here would
    make the candidate hear the opening of the question twice.
    """
    brain, adapter = _start_scripted(
        [
            (["So, walk me through "], _network_drop()),
            (["SHOULD NEVER BE HEARD"], None),
        ]
    )

    chunks = [c async for c in brain.first_question()]
    spoken = "".join(chunks)

    assert adapter.calls == 1, "must not re-call the LLM after partial output"
    assert sleeps == []
    assert "SHOULD NEVER BE HEARD" not in spoken
    assert spoken.count("So, walk me through ") == 1, "already-heard text replayed"
    # The turn still ends with an invitation to speak, so the session continues.
    assert chunks[-1] == render_llm_fallback("en")
    # Transcript records exactly what the candidate heard, partial included.
    interviewer = [t for t in brain.state["turns"] if t["speaker"] == "interviewer"]
    assert interviewer[-1]["text"] == spoken


@pytest.mark.asyncio
async def test_exhausted_retries_degrade_instead_of_raising(sleeps: list[float]) -> None:
    """Every attempt 429s: the turn yields the canned line and does NOT raise."""
    brain, adapter = _start_scripted([([], _rate_limited())])

    chunks = [c async for c in brain.first_question()]

    assert chunks == [render_llm_fallback("en")]
    assert adapter.calls == 3, "bounded at 3 attempts"
    assert sleeps == [0.5, 1.0], "exponential backoff, bounded"
    interviewer = [t for t in brain.state["turns"] if t["speaker"] == "interviewer"]
    assert interviewer[-1]["text"] == render_llm_fallback("en")


@pytest.mark.asyncio
async def test_degraded_turn_keeps_the_interview_alive(sleeps: list[float]) -> None:
    """After a totally failed first question the session still runs to closing.

    This is the whole point of the degradation: one transient LLM failure must
    not end the candidate's interview.
    """
    brain, _adapter = _start_scripted([([], _rate_limited())], max_turns=2)

    _ = [c async for c in brain.first_question()]
    assert not brain.is_complete

    follow_up = "".join([c async for c in brain.respond("I have two years of Java.")])
    assert follow_up == render_llm_fallback("en")
    assert brain.turn_count == 1
    assert not brain.is_complete

    closing = "".join([c async for c in brain.respond("That is all from me.")])
    assert closing, "closing line must still be produced"
    assert brain.is_complete


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "hi", "te"])
async def test_fallback_is_spoken_in_the_session_language(
    language: Language, sleeps: list[float]
) -> None:
    """The degraded line is candidate-facing, so it exists in all Day-1 languages."""
    brain, _adapter = _start_scripted([([], _rate_limited())], language=language)

    chunks = [c async for c in brain.first_question()]

    assert chunks == [LLM_FALLBACK_TEMPLATES[language]]
    assert LLM_FALLBACK_TEMPLATES[language].strip(), "fallback must not be empty"


def test_fallback_templates_are_distinct_per_language() -> None:
    """Each Day-1 language has its own copy — not the English line three times."""
    assert set(LLM_FALLBACK_TEMPLATES) == {"en", "hi", "te"}
    assert len(set(LLM_FALLBACK_TEMPLATES.values())) == 3


def test_fallback_for_unknown_language_falls_back_to_english() -> None:
    """A future language code must degrade to English, never raise."""
    assert render_llm_fallback("fr") == LLM_FALLBACK_TEMPLATES["en"]  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_stream_with_no_text_degrades_rather_than_stalling(
    sleeps: list[float],
) -> None:
    """A stream that drains with zero visible text is dead air to the candidate.

    An adapter that returns nothing instead of raising must still leave the turn
    ending in something the candidate can answer.
    """
    brain, _adapter = _start_scripted([([], None)])

    chunks = [c async for c in brain.first_question()]

    assert chunks == [render_llm_fallback("en")]
