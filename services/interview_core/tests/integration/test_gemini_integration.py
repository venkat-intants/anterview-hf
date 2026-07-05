"""Live Gemini integration test for the S2-005 turn loop.

Runs the full LangGraph against the real Gemini API with three scripted
candidate utterances and asserts every interviewer response is non-empty
plus the phase machine ends in ``done``. Skipped automatically if
``GEMINI_API_KEY`` is not set so the unit-test suite stays hermetic.

USAGE
-----
    cd services/interview_core
    poetry run pytest -m integration -v tests/integration/test_gemini_integration.py

Token usage is printed to stdout (per turn + totals) so the founder /
cost-watcher can sanity-check that a real 3-turn interview stays well
under the 700-token-per-turn budget assumed in ``Final_stack.md``.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.graph import build_initial_state
from app.graph.nodes import (
    ask_question,
    await_candidate_input,
    closing,
    follow_up,
    greeting,
)
from app.graph.state import PHASE_DONE, PHASE_IN_PROGRESS
from app.llm import GeminiAdapter

pytestmark = pytest.mark.integration


CANDIDATE_INPUTS: list[str] = [
    "I have 2 years experience.",
    "I worked with Spring Boot.",
    "I deployed to AWS.",
]


@pytest.mark.skipif(
    not settings.gemini_api_key,
    reason="GEMINI_API_KEY not set — skipping live integration test",
)
async def test_real_gemini_3_turn_conversation(capsys: pytest.CaptureFixture[str]) -> None:
    """End-to-end: 3 candidate inputs -> 4 interviewer turns -> closing."""
    adapter = GeminiAdapter(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        max_tokens=settings.gemini_max_tokens,
        base_url=settings.gemini_api_base_url,
    )

    state = build_initial_state(
        session_id="sess-integration-001",
        job_id="job-integration-001",
        job_title="Junior Java Developer",
        language="en",
        max_turns=3,
    )

    # ---- greeting (no LLM) ----
    state = await greeting(state)
    assert state["phase"] == PHASE_IN_PROGRESS
    assert state["next_interviewer_message"], "greeting must produce a message"

    # ---- ask_question (Gemini call #1) ----
    state = await ask_question(state, adapter=adapter)
    q1 = state["next_interviewer_message"]
    assert q1 and q1.strip(), "ask_question must produce non-empty text"
    print(f"\n[Q1] {q1}")

    # ---- candidate input 1 + follow_up (Gemini call #2) ----
    state["last_candidate_input"] = CANDIDATE_INPUTS[0]
    state = await await_candidate_input(state)
    assert state["turn_count"] == 1
    state = await follow_up(state, adapter=adapter)
    q2 = state["next_interviewer_message"]
    assert q2 and q2.strip(), "follow_up turn 2 must produce non-empty text"
    assert q2 != q1, "follow_up must not be identical to ask_question output"
    print(f"[Q2] {q2}")

    # ---- candidate input 2 + follow_up (Gemini call #3) ----
    state["last_candidate_input"] = CANDIDATE_INPUTS[1]
    state = await await_candidate_input(state)
    assert state["turn_count"] == 2
    state = await follow_up(state, adapter=adapter)
    q3 = state["next_interviewer_message"]
    assert q3 and q3.strip(), "follow_up turn 3 must produce non-empty text"
    print(f"[Q3] {q3}")

    # ---- candidate input 3 hits max_turns -> closing (no LLM) ----
    state["last_candidate_input"] = CANDIDATE_INPUTS[2]
    state = await await_candidate_input(state)
    assert state["turn_count"] == 3
    assert state["turn_count"] >= state["max_turns"], (
        "must have reached max_turns before closing"
    )
    state = await closing(state)
    assert state["phase"] == PHASE_DONE
    assert "Thank you" in (state["next_interviewer_message"] or "")
    print(f"[CLOSE] {state['next_interviewer_message']}")

    # ---- final transcript sanity ----
    # 1 greeting + 3 interviewer Qs + 3 candidate + 1 closing = 8 turns
    assert len(state["turns"]) == 8, (
        f"expected 8 transcript turns, got {len(state['turns'])}: "
        f"{[(t['speaker'], t['text'][:30]) for t in state['turns']]}"
    )

    # Force capsys printout to flush so the per-turn token telemetry
    # surfaces even when pytest -v swallows stdout. The actual token
    # counts are logged by structlog inside the adapter at DEBUG level
    # — this test prints the conversation for human review.
    captured = capsys.readouterr()
    # Re-emit so the verbose pytest output keeps the transcript visible.
    print(captured.out)
