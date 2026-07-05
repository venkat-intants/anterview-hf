"""LangGraph node functions for the interview turn loop (S2-005).

Each node is an async function ``InterviewState -> InterviewState``
(LangGraph merges any partial dict back into state). Nodes are kept
side-effect-free apart from the single LLM call in ``ask_question`` and
``follow_up`` — no DB writes, no Redis, no logging-of-PII here. Sprint 3
will add the WebSocket emit hook and the Redis checkpointer.

LLM adapter injection (S4-013)
-------------------------------
``ask_question`` and ``follow_up`` accept an explicit ``adapter: LLMAdapter``
parameter rather than reading from a module-level singleton. Production code
reads the adapter from ``app.state.llm_adapter`` (set once at startup) and
passes it as a keyword argument. The compiled-graph path (tests, offline runs)
uses ``functools.partial`` in ``build.py`` to bind the adapter before wiring
the nodes into the StateGraph.
"""

from __future__ import annotations

import time

import structlog

from app.graph.prompts import (
    render_ask_question_user_prompt,
    render_closing,
    render_follow_up_user_prompt,
    render_greeting,
    render_interviewer_system_prompt,
)
from app.graph.state import (
    PHASE_CLOSING,
    PHASE_DONE,
    PHASE_IN_PROGRESS,
    InterviewState,
    TurnRecord,
)
from app.llm.base import LLMAdapter, LLMMessage

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _build_history_messages(state: InterviewState) -> list[LLMMessage]:
    """Convert ``state['turns']`` into the adapter's ``messages`` shape.

    Mapping:
        speaker='interviewer' -> role='model'
        speaker='candidate'   -> role='user'

    The greeting (turn_number=0) is INCLUDED so the model sees the opener
    it (conceptually) said; this keeps follow-ups grounded in the actual
    transcript rather than inventing context.
    """
    messages: list[LLMMessage] = []
    for turn in state["turns"]:
        role: str = "model" if turn["speaker"] == "interviewer" else "user"
        # mypy: role is a Literal — but it's narrowed via the ternary above.
        messages.append(LLMMessage(role=role, text=turn["text"]))  # type: ignore[arg-type]
    return messages


# ---------------------------------------------------------------------------
# greeting — opens the session. No LLM call: uses static per-language copy.
# ---------------------------------------------------------------------------
async def greeting(state: InterviewState) -> InterviewState:
    """Open the session with a fixed welcome line."""
    message = render_greeting(state["language"], state["job_title"])
    log.info(
        "graph.greeting",
        session_id=state["session_id"],
        language=state["language"],
    )
    state["phase"] = PHASE_IN_PROGRESS
    state["next_interviewer_message"] = message
    state["turns"].append(
        TurnRecord(turn_number=0, speaker="interviewer", text=message)
    )
    return state


# ---------------------------------------------------------------------------
# ask_question — produces the FIRST real interview question via Gemini.
#
# Conversation shape sent to the model:
#   systemInstruction = INTERVIEWER_SYSTEM_PROMPT_{lang} (persona + rules,
#                       picked by render_interviewer_system_prompt based on
#                       state["language"]; S3-012)
#   messages = [
#       (model)  <greeting>             # so the model sees what it "said"
#       (user)   <ask-question instruction>
#   ]
# ---------------------------------------------------------------------------
async def ask_question(state: InterviewState, *, adapter: LLMAdapter) -> InterviewState:
    """Produce the opening interview question."""

    system_prompt = render_interviewer_system_prompt(
        job_title=state["job_title"],
        language=state["language"],
        max_turns=state["max_turns"],
        persona=state["persona"],
        company_name=state.get("company_name", ""),
        department=state.get("department", ""),
        interview_type=state.get("interview_type", "screening"),
        experience_level=state.get("experience_level", ""),
        required_skills=state.get("required_skills", []),
        resume_text=state.get("resume_text", ""),
        jd_text=state.get("jd_text", ""),
    )
    history = _build_history_messages(state)
    history.append(
        LLMMessage.user(render_ask_question_user_prompt(state["turn_count"]))
    )

    # S3-008: time the LLM call for the per-turn latency event in ws.py.
    t_start = time.monotonic()
    response = await adapter.generate(system_prompt, history)
    latency_ms = int((time.monotonic() - t_start) * 1000)
    state["last_llm_latency_ms"] = latency_ms

    log.info(
        "graph.ask_question",
        session_id=state["session_id"],
        turn_count=state["turn_count"],
        persona=state["persona"],
        latency_ms=latency_ms,
        prompt_tokens=response.prompt_tokens,
        candidates_tokens=response.candidates_tokens,
        thoughts_tokens=response.thoughts_tokens,
        finish_reason=response.finish_reason,
    )

    state["next_interviewer_message"] = response.text
    state["turns"].append(
        TurnRecord(
            turn_number=state["turn_count"] + 1,
            speaker="interviewer",
            text=response.text,
        )
    )
    return state


# ---------------------------------------------------------------------------
# await_candidate_input — pause-point.
#
# In production this node yields back to the WebSocket layer, which resumes
# the graph once a candidate utterance lands. For Sprint 2 unit testing we
# treat it as a synchronous "ingest whatever is in last_candidate_input and
# advance the counter" step.
# ---------------------------------------------------------------------------
async def await_candidate_input(state: InterviewState) -> InterviewState:
    """Record the candidate's reply and bump the turn counter."""
    candidate_text = state.get("last_candidate_input")
    if candidate_text is None:
        # Defensive: in tests a missing input means we have nothing to
        # record. Still bump the counter so the loop can terminate on
        # max_turns regardless of caller hygiene.
        log.warning(
            "graph.await_candidate_input.missing_input",
            session_id=state["session_id"],
            turn_count=state["turn_count"],
        )
    else:
        state["turns"].append(
            TurnRecord(
                turn_number=state["turn_count"] + 1,
                speaker="candidate",
                text=candidate_text,
            )
        )

    state["turn_count"] = state["turn_count"] + 1
    # Consume the input so the next invocation cannot accidentally re-use it.
    state["last_candidate_input"] = None
    log.info(
        "graph.await_candidate_input",
        session_id=state["session_id"],
        turn_count=state["turn_count"],
    )
    return state


# ---------------------------------------------------------------------------
# follow_up — produces a context-aware follow-up via Gemini.
#
# Conversation shape sent to the model:
#   systemInstruction = INTERVIEWER_SYSTEM_PROMPT_{lang}  (S3-012 — picked
#                       by render_interviewer_system_prompt from
#                       state["language"])
#   messages = [
#       ... full prior transcript (greeting + Q1 + A1 + Q2 + A2 + ...)
#       (user) <follow-up instruction quoting last candidate text>
#   ]
# Sending full history (not just last turn) lets the model:
#   - avoid re-asking what it already asked, and
#   - reference earlier candidate claims for deeper probing.
# ---------------------------------------------------------------------------
async def follow_up(state: InterviewState, *, adapter: LLMAdapter) -> InterviewState:
    """Produce a follow-up question grounded in the running transcript."""

    # The candidate text we just ingested is the LAST entry in state['turns']
    # (await_candidate_input appended it). Fall back to empty string if
    # something upstream forgot to populate it — the system prompt + history
    # are still enough to keep the conversation moving.
    last_candidate_input = ""
    for turn in reversed(state["turns"]):
        if turn["speaker"] == "candidate":
            last_candidate_input = turn["text"]
            break

    system_prompt = render_interviewer_system_prompt(
        job_title=state["job_title"],
        language=state["language"],
        max_turns=state["max_turns"],
        persona=state["persona"],
        company_name=state.get("company_name", ""),
        department=state.get("department", ""),
        interview_type=state.get("interview_type", "screening"),
        experience_level=state.get("experience_level", ""),
        required_skills=state.get("required_skills", []),
        resume_text=state.get("resume_text", ""),
        jd_text=state.get("jd_text", ""),
    )
    history = _build_history_messages(state)
    history.append(
        LLMMessage.user(
            render_follow_up_user_prompt(
                last_candidate_input=last_candidate_input,
                turn_count=state["turn_count"],
                max_turns=state["max_turns"],
            )
        )
    )

    # S3-008: time the LLM call for the per-turn latency event in ws.py.
    t_start = time.monotonic()
    response = await adapter.generate(system_prompt, history)
    latency_ms = int((time.monotonic() - t_start) * 1000)
    state["last_llm_latency_ms"] = latency_ms

    log.info(
        "graph.follow_up",
        session_id=state["session_id"],
        turn_count=state["turn_count"],
        persona=state["persona"],
        latency_ms=latency_ms,
        prompt_tokens=response.prompt_tokens,
        candidates_tokens=response.candidates_tokens,
        thoughts_tokens=response.thoughts_tokens,
        finish_reason=response.finish_reason,
    )

    state["next_interviewer_message"] = response.text
    state["turns"].append(
        TurnRecord(
            turn_number=state["turn_count"] + 1,
            speaker="interviewer",
            text=response.text,
        )
    )
    return state


# ---------------------------------------------------------------------------
# closing — wraps the session. Two-phase: emit the closing line in
# ``phase=closing``, then immediately mark ``phase=done`` so the conditional
# edge in ``build.py`` terminates the graph.
# ---------------------------------------------------------------------------
async def closing(state: InterviewState) -> InterviewState:
    """Emit the closing message and mark the session done."""
    message = render_closing(state["language"])
    log.info(
        "graph.closing",
        session_id=state["session_id"],
        turn_count=state["turn_count"],
    )
    state["phase"] = PHASE_CLOSING
    state["next_interviewer_message"] = message
    state["turns"].append(
        TurnRecord(
            turn_number=state["turn_count"] + 1,
            speaker="interviewer",
            text=message,
        )
    )
    # Immediately advance to ``done`` — the closing line is the last
    # interviewer utterance the candidate sees.
    state["phase"] = PHASE_DONE
    return state
