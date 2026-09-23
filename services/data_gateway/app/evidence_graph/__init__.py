"""The evidence graph — PH5-E5, "First-Class Evidence Graph".

FIRST-CLASS MEANS DECLARED AND QUERYABLE, NOT STORED. There is no
``evidence_edges`` table, no materialised graph, and nothing here ever writes
one. Every :class:`~app.schemas.evidence.EvidenceNode` is derived, at read
time, from rows the existing screens already show (``interviewer_scorecards``,
``round_results``, ``offers``, ``stage_transitions``, ``task_submissions``,
…); every edge is computed from timestamps and foreign keys already on those
rows. Because nothing is copied, DPDP erasure, retention, redaction and
consent withdrawal are inherited for free FOR EVERY SOURCE ROW THIS PACKAGE
READS — they show up on the very next read of this graph, with no erasure
step and no retention job of this package's own. There is exactly ONE named
exception, and it lives in ``loaders.py``'s own docstring: a decision's
free-text rationale, withheld once the candidate is erased even though its
source row is not itself redacted (AR-5).

"First-class" is expressed as: a closed, typed vocabulary
(``app/schemas/evidence.py``), one ``STAGE_OF_ROUND_KIND`` map covering every
``ck_workflow_rounds_kind`` value, one declared loader per node kind
(``loaders.py``), and two pure functions — :func:`assemble` (structural
edges) and :func:`trace` (what existed at a decision, and what did not),
in ``assemble.py``. ``graph.py`` is the orchestration layer that gathers the
loaders into one graph and traces one decision through it — ``build_graph()``
and ``decision_trace_from_graph()`` are what the router and the copilot tool
actually call.

This package is read-only throughout: it never calls ``record_transition``,
``record_round_move``, ``record_result``, ``record_final_decision`` or
anything else that writes, and it never inserts an audit row itself —
callers (the router, never a tool) own that, so it stays safe for the
read-only agent layer to call directly.

Package layout (a pure split of what was one file — no behaviour changed by
the split; see each module's own docstring for its design notes):
    loaders.py   one loader per node kind, the stage map, the omitted-item
                 table, the href/anchor contract with CandidateDrawer.tsx,
                 and the AR-5 erasure exception.
    assemble.py  the pure edge/trace algebra and its honest-semantics notes.
    graph.py     _gather(), build_graph(), resolve_decision(),
                 latest_decision_id(), decision_trace_from_graph().

Every name below — including the underscore-prefixed ones the test suite
reaches into directly — is re-exported here so nothing outside this package
had to change when it stopped being a single file.
"""

from __future__ import annotations

from app.schemas.evidence import TracePathStep

from .assemble import assemble, availability_edges, trace
from .graph import (
    _DecisionRow,
    _gather,
    _Gathered,
    blinded_round_count,
    build_graph,
    decision_trace_from_graph,
    latest_decision_id,
    resolve_decision,
)
from .loaders import (
    _DECISION_STATUSES,
    _ERASED_REASON_HIDDEN,
    _ERASED_REASON_OMISSION,
    _GRADED_BY_PRODUCED_BY,
    HREF_ANCHOR,
    OMITTED_ALWAYS,
    STAGE_OF_ROUND_KIND,
    EvidenceGraphError,
    _ai_interview_nodes,
    _application_node,
    _decision_nodes,
    _EdgeLink,
    _erased_expr_true,
    _exam_attempt_nodes,
    _href,
    _human_scorecard_nodes,
    _interview_session_nodes,
    _latest_hire_at_or_before,
    _load_scope,
    _node_href,
    _offer_nodes,
    _round_result_nodes,
    _screening_answers_node,
    _screening_ats_node,
    _stage_move_nodes,
    _task_submission_nodes,
)

__all__ = [
    "_DECISION_STATUSES",
    "_DecisionRow",
    "_ERASED_REASON_HIDDEN",
    "_ERASED_REASON_OMISSION",
    "_GRADED_BY_PRODUCED_BY",
    "EvidenceGraphError",
    "HREF_ANCHOR",
    "OMITTED_ALWAYS",
    "STAGE_OF_ROUND_KIND",
    "TracePathStep",
    "_EdgeLink",
    "_Gathered",
    "_ai_interview_nodes",
    "_application_node",
    "_decision_nodes",
    "_erased_expr_true",
    "_exam_attempt_nodes",
    "_gather",
    "_href",
    "_human_scorecard_nodes",
    "_interview_session_nodes",
    "_latest_hire_at_or_before",
    "_load_scope",
    "_node_href",
    "_offer_nodes",
    "_round_result_nodes",
    "_screening_answers_node",
    "_screening_ats_node",
    "_stage_move_nodes",
    "_task_submission_nodes",
    "assemble",
    "availability_edges",
    "blinded_round_count",
    "build_graph",
    "decision_trace_from_graph",
    "latest_decision_id",
    "resolve_decision",
    "trace",
]
