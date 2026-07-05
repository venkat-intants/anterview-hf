"""LangGraph orchestrator for the interview turn loop (S2-005).

This package contains the state machine that drives a single interview
session. Sprint 2 ships a working linear loop:

    START -> greeting -> ask_question -> await_candidate_input
          -> (turn_count < max_turns ? follow_up : closing) -> END

``ask_question`` and ``follow_up`` are backed by the Gemini adapter from
``app.llm``. Tests pass the adapter explicitly via
``compile_graph(llm=<adapter>)`` so unit runs never hit the network (S4-013).

LLD §6 specifies a richer phase model (INIT / INTRO / TECH_Q / BEHAV_Q /
CAND_Q / CLOSE / SCORED). Sprint 2 deliberately collapses that to the simpler
``greeting / in_progress / closing / done`` lifecycle per the story
acceptance criteria — the richer phase model lands in Sprint 3+.
"""

from app.graph.build import compile_graph
from app.graph.state import InterviewState, build_initial_state

__all__ = [
    "InterviewState",
    "build_initial_state",
    "compile_graph",
]
