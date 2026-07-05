"""TypedDict state schema for the interview LangGraph (S2-004 scaffold).

Sprint 2 uses a deliberately minimal lifecycle (``greeting -> in_progress ->
closing -> done``) so the scaffold compiles and unit-tests without needing
the full LLD §6 phase model (INIT / INTRO / TECH_Q / BEHAV_Q / CAND_Q /
CLOSE / SCORED). The richer phases land in Sprint 3 once we have real
prompts, real STT/TTS, and a Redis checkpointer wired in.
"""

from __future__ import annotations

from typing import Literal

# NOTE: pydantic v2 schema generation requires ``typing_extensions.TypedDict``
# on Python < 3.12 — stdlib ``typing.TypedDict`` does not expose enough
# runtime metadata for nested-TypedDict introspection. LangGraph's
# ``get_graph()`` introspection trips on stdlib TypedDict for that reason.
from typing_extensions import TypedDict

from app.graph.personas import Persona, select_persona

# Phase lifecycle constants. Listed in canonical forward order — every
# session must traverse this sequence (no skipping, no looping back).
PHASE_GREETING: Literal["greeting"] = "greeting"
PHASE_IN_PROGRESS: Literal["in_progress"] = "in_progress"
PHASE_CLOSING: Literal["closing"] = "closing"
PHASE_DONE: Literal["done"] = "done"

Phase = Literal["greeting", "in_progress", "closing", "done"]
Speaker = Literal["interviewer", "candidate"]
Language = Literal["en", "hi", "te"]


class TurnRecord(TypedDict):
    """A single line in the interview transcript."""

    turn_number: int
    speaker: Speaker
    text: str


class InterviewState(TypedDict):
    """LangGraph state for the interview turn loop."""

    # Identity / context — set once at session creation, never mutated.
    session_id: str
    job_id: str
    # Cached so the LLM nodes do not re-query data_gateway every turn.
    job_title: str
    language: Language

    # Turn counters. ``turn_count`` is the number of completed
    # candidate-input rounds (0 before the candidate has said anything).
    turn_count: int
    max_turns: int

    # Transcript — appended in chronological order.
    turns: list[TurnRecord]

    # Lifecycle phase. See PHASE_* constants above.
    phase: Phase

    # Live turn buffers. ``last_candidate_input`` is set by the WebSocket
    # layer before resuming the graph; ``next_interviewer_message`` is set
    # by greeting / ask_question / follow_up / closing for the WS layer to
    # ship to the client.
    last_candidate_input: str | None
    next_interviewer_message: str | None

    # S3-008: wall-clock latency (milliseconds) of the most recent LLM
    # call. Populated by ``ask_question`` / ``follow_up`` / ``closing``
    # after their ``adapter.generate()`` returns. ws.py reads it to build
    # the per-turn ``turn_latency`` event. None on greeting (no LLM call).
    last_llm_latency_ms: int | None

    # S4-003: which interviewer persona is driving the session. Deterministic
    # on ``session_id`` (see ``app.graph.personas.select_persona``) and
    # constant for the lifetime of the session. Read by ``ask_question`` /
    # ``follow_up`` to append the persona delta to the system prompt and
    # persisted into ``session_metadata`` at session close for offline
    # per-persona analytics.
    persona: Persona

    # B-033: interview context enrichment. All optional — populated from the
    # DB session + job record at WS session start (see app.routers.ws). Empty
    # defaults keep the [CONTEXT] block out of the system prompt entirely, so
    # any caller that does not pass context (unit tests, legacy bootstrap)
    # reproduces the pre-B-033 prompt byte-for-byte.
    company_name: str  # e.g. "APSSDC IT Cell" — for "at <company>" phrasing
    department: str  # e.g. "Engineering" — for role context
    interview_type: str  # "screening" | "technical" | "hr"
    experience_level: str  # "entry" | "mid" | "senior"
    required_skills: list[str]  # from jobs.required_skills
    resume_text: str  # extracted from candidate's uploaded resume (may be empty)
    jd_text: str  # parsed JD document text (may be empty)


def build_initial_state(
    *,
    session_id: str,
    job_id: str,
    job_title: str,
    language: Language = "en",
    max_turns: int = 5,
    persona: Persona | None = None,
    company_name: str = "",
    department: str = "",
    interview_type: str = "screening",
    experience_level: str = "",
    required_skills: list[str] | None = None,
    resume_text: str = "",
    jd_text: str = "",
) -> InterviewState:
    """Construct a fresh ``InterviewState`` for a brand-new session.

    Centralised so callers (WebSocket handler, unit tests, future REST
    bootstrap) all start from the same baseline. Defaults: English, 5
    candidate-input turns, persona auto-selected from ``session_id``.

    Args:
        persona: Optional persona override. ``None`` (default) selects via
            the deterministic ``session_id`` hash — same session_id always
            picks the same persona, which is what production and the eval
            harness both want. Tests that want to pin a specific persona
            pass it explicitly.
    """
    chosen_persona = persona if persona is not None else select_persona(session_id)
    return InterviewState(
        session_id=session_id,
        job_id=job_id,
        job_title=job_title,
        language=language,
        turn_count=0,
        max_turns=max_turns,
        turns=[],
        phase=PHASE_GREETING,
        last_candidate_input=None,
        next_interviewer_message=None,
        last_llm_latency_ms=None,
        persona=chosen_persona,
        # B-033: interview context — empty defaults are intentionally inert
        # (no [CONTEXT] block rendered) for backwards compatibility.
        company_name=company_name,
        department=department,
        interview_type=interview_type,
        experience_level=experience_level,
        required_skills=required_skills if required_skills is not None else [],
        resume_text=resume_text,
        jd_text=jd_text,
    )
