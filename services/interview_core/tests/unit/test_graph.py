"""Unit tests for the S2-005 LangGraph + Gemini adapter integration.

These tests MUST NOT hit the network. Every test receives a
``MockLLMAdapter`` from the autouse ``_install_mock_adapter`` fixture and
passes it explicitly to node functions / ``compile_graph`` (S4-013 DI).
``test_no_llm_calls_in_unit_test`` enforces the no-network invariant by
sentinel-patching ``httpx``.

Coverage:
  - ``test_graph_compiles``               graph builds and is runnable
  - ``test_state_initializes_correctly``  fresh state starts in greeting/0
  - ``test_terminates_after_5_turns``     loop closes once max_turns reached
  - ``test_phase_transitions``            phases progress in canonical order
  - ``test_no_llm_calls_in_unit_test``    httpx never instantiated
  - ``test_mock_adapter_sees_full_history`` follow_up sends full transcript
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import patch

import pytest

from app.graph import build_initial_state, compile_graph
from app.graph.nodes import (
    ask_question,
    await_candidate_input,
    closing,
    follow_up,
    greeting,
)
from app.graph.state import (
    PHASE_CLOSING,
    PHASE_DONE,
    PHASE_GREETING,
    PHASE_IN_PROGRESS,
)
from app.llm.base import LLMAdapter, LLMMessage, LLMResponse

# ---------------------------------------------------------------------------
# Mock adapter
# ---------------------------------------------------------------------------


class MockLLMAdapter(LLMAdapter):
    """Deterministic adapter used by every unit test.

    Returns canned text scaled to the number of times it's been called so
    each interviewer turn is distinct and easy to assert on. Records every
    ``(system_prompt, messages, max_tokens)`` triple in ``self.calls`` for
    history-aware tests.
    """

    def __init__(self, canned: list[str] | None = None) -> None:
        # Default to a generous pool so 5-turn tests never run out.
        self._canned = canned or [
            "What interests you about this role?",
            "Can you walk me through a recent project?",
            "How did you handle a difficult teammate?",
            "What's a technical area you want to grow in?",
            "Where do you see yourself in two years?",
            "Any other examples that highlight your fit?",
        ]
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
    ) -> LLMResponse:
        idx = len(self.calls)
        text = self._canned[idx % len(self._canned)]
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": messages,
                "max_tokens": max_tokens,
            }
        )
        return LLMResponse(
            text=text,
            prompt_tokens=10 + len(messages),
            candidates_tokens=len(text.split()),
            thoughts_tokens=5,
            finish_reason="STOP",
        )

    async def generate_stream(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """S4-005 stub: yields full canned response as a single chunk."""
        response = await self.generate(system_prompt, messages, max_tokens)
        yield response.text


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _fresh_state(*, max_turns: int = 5, language: str = "en") -> Any:
    return build_initial_state(
        session_id="sess-test-001",
        job_id="job-test-001",
        job_title="Junior Java Developer",
        language=language,  # type: ignore[arg-type]
        max_turns=max_turns,
    )


async def _drive_to_completion(
    state: Any,
    candidate_inputs: list[str],
    adapter: MockLLMAdapter,
) -> Any:
    """Step the nodes manually, injecting candidate inputs between rounds.

    LangGraph's ``invoke()`` runs to completion in one shot; for tests where
    we want to simulate a real ``await_candidate_input`` pause we drive the
    nodes individually so each round can be primed with the next utterance.
    The ``adapter`` is passed explicitly to ``ask_question`` / ``follow_up``
    (S4-013 DI).
    """
    state = await greeting(state)
    state = await ask_question(state, adapter=adapter)

    for _idx, candidate_text in enumerate(candidate_inputs):
        state["last_candidate_input"] = candidate_text
        state = await await_candidate_input(state)
        if state["turn_count"] < state["max_turns"]:
            state = await follow_up(state, adapter=adapter)
        else:
            # Loop is over; emulate the conditional edge -> closing.
            state = await closing(state)
            break
    return state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _install_mock_adapter() -> Iterator[MockLLMAdapter]:
    """Provide a fresh MockLLMAdapter for every test.

    Autouse so every test that calls ``_drive_to_completion`` or calls a node
    directly has a ready adapter to pass. Tests that use ``compile_graph``
    receive a compiled graph with the adapter already bound via
    ``functools.partial`` (S4-013 DI).
    """
    adapter = MockLLMAdapter()
    yield adapter


@pytest.fixture(autouse=True)
def _quiet_logs() -> Iterator[None]:
    """Silence structlog INFO chatter so pytest output stays readable."""
    import logging

    prior = logging.getLogger().level
    logging.getLogger().setLevel(logging.WARNING)
    try:
        yield
    finally:
        logging.getLogger().setLevel(prior)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_graph_compiles(_install_mock_adapter: MockLLMAdapter) -> None:
    """``compile_graph(llm)`` returns a runnable with all expected nodes."""
    g = compile_graph(_install_mock_adapter)
    assert hasattr(g, "invoke"), "compiled graph must expose .invoke()"
    assert hasattr(g, "stream"), "compiled graph must expose .stream()"

    node_names = set(g.get_graph().nodes.keys())
    assert {
        "greeting",
        "ask_question",
        "await_candidate_input",
        "follow_up",
        "closing",
    }.issubset(node_names)


def test_state_initializes_correctly() -> None:
    """Fresh ``InterviewState`` starts in phase=greeting, turn_count=0."""
    state = _fresh_state()
    assert state["phase"] == PHASE_GREETING
    assert state["turn_count"] == 0
    assert state["turns"] == []
    assert state["language"] == "en"
    assert state["max_turns"] == 5
    assert state["last_candidate_input"] is None
    assert state["next_interviewer_message"] is None


async def test_terminates_after_5_turns(_install_mock_adapter: MockLLMAdapter) -> None:
    """Five candidate inputs -> phase becomes 'done'."""
    state = _fresh_state(max_turns=5)
    candidate_inputs = [f"answer {i}" for i in range(5)]
    result = await _drive_to_completion(state, candidate_inputs, _install_mock_adapter)

    assert result["phase"] == PHASE_DONE
    assert result["turn_count"] == 5
    # 1 greeting + 1 ask + 5 candidate + 4 follow_ups + 1 closing = 12
    assert len(result["turns"]) == 12

    # Last interviewer line must be the closing message.
    last_interviewer = next(
        t for t in reversed(result["turns"]) if t["speaker"] == "interviewer"
    )
    assert "Thank you" in last_interviewer["text"]

    # Mock adapter must have been called exactly 5 times:
    # 1 for ask_question + 4 for follow_up (rounds 1..4 inside the loop;
    # round 5 hits max_turns and goes straight to closing).
    assert len(_install_mock_adapter.calls) == 5


async def test_phase_transitions(_install_mock_adapter: MockLLMAdapter) -> None:
    """Phase progresses greeting -> in_progress -> closing -> done in order."""
    state = _fresh_state(max_turns=2)
    observed: list[str] = [state["phase"]]

    state = await greeting(state)
    observed.append(state["phase"])
    state = await ask_question(state, adapter=_install_mock_adapter)
    observed.append(state["phase"])

    # Round 1: candidate answers, follow-up issued. Still in_progress.
    state["last_candidate_input"] = "first answer"
    state = await await_candidate_input(state)
    state = await follow_up(state, adapter=_install_mock_adapter)
    observed.append(state["phase"])

    # Round 2: candidate answers; turn_count==max_turns -> closing.
    state["last_candidate_input"] = "second answer"
    state = await await_candidate_input(state)
    state = await closing(state)
    observed.append(state["phase"])

    # Canonical order is greeting -> in_progress -> ... -> done.
    # PHASE_CLOSING is set transiently inside ``closing`` before flipping to
    # PHASE_DONE, so the externally observed end phase is PHASE_DONE.
    assert observed[0] == PHASE_GREETING
    assert observed[1] == PHASE_IN_PROGRESS
    assert observed[2] == PHASE_IN_PROGRESS
    assert observed[3] == PHASE_IN_PROGRESS
    assert observed[4] == PHASE_DONE

    # Sanity: phases never went backwards along the canonical order.
    canonical = [PHASE_GREETING, PHASE_IN_PROGRESS, PHASE_CLOSING, PHASE_DONE]
    indices = [canonical.index(p) for p in observed]
    assert indices == sorted(indices), f"phase regressed: {observed!r}"


async def test_no_llm_calls_in_unit_test(
    _install_mock_adapter: MockLLMAdapter,
) -> None:
    """Confirm running the scaffold never opens an HTTP connection.

    The MockLLMAdapter answers every ``generate()`` call locally, so no
    httpx client should ever be instantiated. If a future refactor
    accidentally bypasses the adapter and calls Gemini directly, the
    sentinel below catches it.
    """
    sentinel_calls: list[str] = []

    def _boom(name: str) -> Any:
        def _ctor(*_a: Any, **_kw: Any) -> Any:
            sentinel_calls.append(name)
            raise AssertionError(
                f"unit test attempted to instantiate {name} — "
                "graph must not make network calls"
            )

        return _ctor

    with (
        patch("httpx.AsyncClient", side_effect=_boom("httpx.AsyncClient")),
        patch("httpx.Client", side_effect=_boom("httpx.Client")),
    ):
        g = compile_graph(_install_mock_adapter)
        state = _fresh_state(max_turns=3)
        # ``ainvoke`` drives the async graph end-to-end so any node touching
        # the network would trigger the sentinel. We prime the candidate
        # input once; the graph doesn't actually pause (no checkpointer in
        # Sprint 2) so it consumes the same input each loop — fine for this
        # network-isolation check.
        state["last_candidate_input"] = "mock answer"
        await g.ainvoke(state)

    assert sentinel_calls == [], (
        f"network clients instantiated during unit test: {sentinel_calls!r}"
    )


async def test_mock_adapter_sees_full_history(
    _install_mock_adapter: MockLLMAdapter,
) -> None:
    """``follow_up`` must send the full prior transcript to the adapter.

    Regression guard: an earlier draft of the scaffold only sent the last
    candidate utterance, which made the model re-ask the same opening
    question on every turn. The fix is to convert ``state['turns']`` into
    ``messages`` and append the per-turn instruction at the end.
    """
    state = _fresh_state(max_turns=3)
    await _drive_to_completion(
        state,
        ["I have 2 years of Java", "I built a REST API", "I deployed to AWS"],
        _install_mock_adapter,
    )

    # Call 1 = ask_question, Calls 2..3 = follow_up. Each follow_up call's
    # messages list should be strictly longer than the previous one — the
    # transcript grows monotonically.
    assert len(_install_mock_adapter.calls) >= 3
    history_lengths = [len(c["messages"]) for c in _install_mock_adapter.calls]
    assert history_lengths == sorted(history_lengths), (
        f"history must grow monotonically across turns: {history_lengths}"
    )

    # The last LLM call (follow_up for round 2) must include the candidate's
    # second answer in its history. The third candidate input ("AWS") is
    # never sent to the LLM because turn_count hits max_turns immediately
    # after it lands and the graph routes straight to closing.
    last_call_messages: list[LLMMessage] = _install_mock_adapter.calls[-1]["messages"]
    candidate_texts = [m.text for m in last_call_messages if m.role == "user"]
    assert any("REST API" in t for t in candidate_texts), (
        f"second candidate utterance missing from history: {candidate_texts}"
    )
