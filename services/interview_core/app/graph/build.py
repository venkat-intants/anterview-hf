"""Graph wiring + compilation for the interview turn loop (S2-005).

Topology (Sprint 2 simplified — full LLD §6 phase machine ships in Sprint 3+)::

    START
      -> greeting
      -> ask_question
      -> await_candidate_input
           |-- (turn_count < max_turns) --> follow_up -> await_candidate_input
           |-- (turn_count >= max_turns) --> closing -> END

The conditional edge out of ``await_candidate_input`` is the only branching
point. It loops back through ``follow_up`` until the candidate has provided
``max_turns`` answers, then jumps to ``closing``.

NOTE on checkpointing: LLD §6.4 calls for ``RedisSaver`` so a paused graph
(at ``await_candidate_input``) can survive a WebSocket reconnect. Sprint 2
ships without a checkpointer to keep the scaffold dependency-free; Sprint 3
wires the Redis saver when the WS handler lands.

LLM adapter injection (S4-013)
------------------------------
``compile_graph(llm)`` receives the adapter as a REQUIRED argument and binds it
into the two LLM-backed nodes via ``functools.partial``. This replaces the old
module-level singleton approach: the compiled graph carries its own adapter
reference and no external ``set_default_adapter`` call is needed.
"""

from __future__ import annotations

import functools
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    ask_question,
    await_candidate_input,
    closing,
    follow_up,
    greeting,
)
from app.graph.state import InterviewState
from app.llm.base import LLMAdapter

# Node name constants — keep wiring and tests free of magic strings. Typed
# as ``Literal[...]`` so the conditional-edge router below can return them
# without losing mypy's narrow string-set guarantee.
NODE_GREETING: Literal["greeting"] = "greeting"
NODE_ASK_QUESTION: Literal["ask_question"] = "ask_question"
NODE_AWAIT_CANDIDATE_INPUT: Literal["await_candidate_input"] = "await_candidate_input"
NODE_FOLLOW_UP: Literal["follow_up"] = "follow_up"
NODE_CLOSING: Literal["closing"] = "closing"


def _route_after_candidate_input(
    state: InterviewState,
) -> Literal["follow_up", "closing"]:
    """Branch out of ``await_candidate_input``.

    Loop while the candidate still owes us turns; otherwise terminate via
    ``closing``. ``max_turns`` counts candidate-input rounds, not
    interviewer utterances.
    """
    if state["turn_count"] < state["max_turns"]:
        return NODE_FOLLOW_UP
    return NODE_CLOSING


def build_graph(llm: LLMAdapter) -> StateGraph:
    """Construct the (uncompiled) ``StateGraph`` for the interview loop.

    ``llm`` is bound into the two LLM-backed nodes via ``functools.partial``
    so the compiled graph carries its own adapter reference without relying on
    any module-level singleton.

    Exposed separately from ``compile_graph`` so tests can inspect the
    pre-compile graph if needed.
    """
    g: StateGraph = StateGraph(InterviewState)

    g.add_node(NODE_GREETING, greeting)
    g.add_node(NODE_ASK_QUESTION, functools.partial(ask_question, adapter=llm))
    g.add_node(NODE_AWAIT_CANDIDATE_INPUT, await_candidate_input)
    g.add_node(NODE_FOLLOW_UP, functools.partial(follow_up, adapter=llm))
    g.add_node(NODE_CLOSING, closing)

    g.add_edge(START, NODE_GREETING)
    g.add_edge(NODE_GREETING, NODE_ASK_QUESTION)
    g.add_edge(NODE_ASK_QUESTION, NODE_AWAIT_CANDIDATE_INPUT)

    g.add_conditional_edges(
        NODE_AWAIT_CANDIDATE_INPUT,
        _route_after_candidate_input,
        {
            NODE_FOLLOW_UP: NODE_FOLLOW_UP,
            NODE_CLOSING: NODE_CLOSING,
        },
    )

    g.add_edge(NODE_FOLLOW_UP, NODE_AWAIT_CANDIDATE_INPUT)
    g.add_edge(NODE_CLOSING, END)

    return g


def compile_graph(llm: LLMAdapter) -> Any:
    """Compile and return the runnable interview graph.

    Args:
        llm: The LLM adapter to bind into ``ask_question`` / ``follow_up``
            via ``functools.partial``. Required — no module-level singleton
            is read or written (S4-013).

    Returns ``Any`` because LangGraph's ``CompiledGraph`` is not exported as
    a public, mypy-friendly type across the 0.2.x line; downstream callers
    treat the result as a runnable (``.invoke()`` / ``.stream()``).
    """
    return build_graph(llm).compile()
