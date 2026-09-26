"""Pydantic models for the evidence graph — PH5-E5.

FIRST-CLASS MEANS DECLARED AND QUERYABLE, NOT STORED. There is no
``evidence_edges`` table anywhere in this codebase, and this module never
writes one. Every :class:`EvidenceNode` here is derived, at read time, from
rows the existing screens already show (``interviewer_scorecards``,
``round_results``, ``offers``, ``stage_transitions``, …); every
:class:`EvidenceEdge` is computed from timestamps and foreign keys already on
those rows. Nothing is copied, so DPDP erasure, retention, redaction and
consent withdrawal are inherited for free for every source row this graph
reads — they show up on the very next read of this graph, with no erasure
step and no retention job of this module's own to keep in sync. One named
exception: a decision's free-text rationale (``stage_transitions.reason``) is
redacted at its source once erasure actually runs, but an erasure REQUEST
marks the candidate erased immediately — up to 30 days before that (AR-5,
closed 2026-09-26), so ``app/evidence_graph.py`` withholds it itself, at
render time, for the whole window ``candidate.erased`` is true — see
``_ERASED_REASON_HIDDEN``/``_ERASED_REASON_OMISSION`` there.

"First-class" is expressed here as a closed, typed vocabulary (this module)
plus one declared loader per node kind and two pure functions,
``assemble()``/``trace()`` (``app/evidence_graph.py``) — not as a table.

HONEST SEMANTICS, non-negotiable
---------------------------------
The decision edge is ``available_at_decision``, computed purely by time:
``node.recorded_at <= decision.decided_at``. The system can prove what
existed when a person decided. It cannot prove what they actually read or
relied on — so nothing here is ever called ``supported_by``. Evidence
recorded strictly after the decision goes in ``after_decision[]`` and is
flagged there, never silently merged into ``evidence[]``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

#: Where a piece of evidence sits in the hiring funnel. Closed and small on
#: purpose: analytics and the frontend group by these six names, and adding a
#: seventh is a deliberate, reviewed change to this Literal, never a string
#: invented at a call site.
EvidenceStage = Literal[
    "application", "screening", "assessment", "interview", "offer", "decision"
]

#: Every kind of node this graph can produce. Closed: a caller cannot invent
#: a thirteenth kind, and every reader (the frontend, the copilot tool, the
#: parity test) can enumerate them exhaustively.
EvidenceNodeKind = Literal[
    "application",
    "screening_ats",
    "screening_answers",
    "stage_move",
    "exam_attempt",
    "round_result",
    "ai_interview",
    "interview_session",
    "human_scorecard",
    "task_submission",
    "offer",
    "decision",
]

#: Every kind of edge this graph can produce. Emitted as real ``EvidenceEdge``
#: objects — in ``EvidenceGraph.edges`` (one set per decision in
#: ``decisions[]``) and in ``DecisionTrace.edges`` (one set for the traced
#: decision) — never reconstructed by a reader from a list split.
#:
#: ``available_at_decision`` is the one honest link between a decision and a
#: piece of evidence: it means only that the evidence existed (by timestamp)
#: when the decision was recorded, never that the decision-maker read or
#: relied on it. ``follows_decision`` is the ordinary "this came after that"
#: link (an offer following a hire). Nothing here is ever named
#: ``supported_by``.
EvidenceEdgeKind = Literal[
    "part_of", "originates_from", "supersedes", "available_at_decision", "follows_decision"
]

#: Who or what produced a piece of evidence.
ProducedBy = Literal["human", "ai", "candidate", "system"]

#: Whether a node is tied to this application because a foreign key says so
#: (``direct``), or because a pre-B5 row carries no application at all and the
#: applicant has never held more than one live application at once
#: (``inferred_single_application`` — the rule in migration
#: ``e2a4c6b8d0f1``). A legacy row attributed this way is never presented as
#: certain.
Attribution = Literal["direct", "inferred_single_application"]

#: What has happened to a node since it was written. ``live`` is the default;
#: every other value is a fact this module reads off the base tables, never
#: something it decides on its own — a purge, a redaction or a consent
#: withdrawal all happen elsewhere (DPDP erasure, retention, the candidate's
#: own action) and are simply reflected here on the next read.
Lifecycle = Literal["live", "superseded", "withdrawn", "redacted", "purged", "consent_withdrawn"]


# ---------------------------------------------------------------------------
# Shared shapes
# ---------------------------------------------------------------------------


class StageInfo(BaseModel):
    """Where in the funnel this node sits, and which round (if any)."""

    name: EvidenceStage
    round_id: str | None = None
    round_title: str | None = None
    round_kind: str | None = None
    workflow_version: int | None = None


class SourceRef(BaseModel):
    """The exact row this node was read from — for a reviewer who wants to
    check the primary source, not just this graph's rendering of it."""

    table: str
    id: str


class ActorRef(BaseModel):
    user_id: str | None = None
    name: str | None = None
    role: str | None = None


class Provenance(BaseModel):
    produced_by: ProducedBy
    actor: ActorRef | None = None
    method: str
    attribution: Attribution = "direct"


class EvidenceNode(BaseModel):
    """One piece of evidence. ``id`` is ``"{kind}:{source_id}"`` — stable and
    derived, never stored anywhere."""

    id: str
    kind: EvidenceNodeKind
    stage: StageInfo
    source: SourceRef
    provenance: Provenance
    occurred_at: datetime
    recorded_at: datetime
    lifecycle: Lifecycle = "live"
    content: dict[str, object] | None = None
    content_hidden_reason: str | None = None
    href: str


class EvidenceEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    kind: EvidenceEdgeKind


class OmittedItem(BaseModel):
    """Something this graph deliberately never shows, and why."""

    kind: str
    reason: str


class EvidenceScope(BaseModel):
    company_id: str
    enrolment_id: str
    applicant_id: str


class CandidateRef(BaseModel):
    name: str
    erased: bool


class RequisitionRef(BaseModel):
    id: str | None = None
    title: str | None = None


class WorkflowRef(BaseModel):
    id: str | None = None
    version: int | None = None


class DecisionSummary(BaseModel):
    """One row of the enrolment's decision ledger, for the graph's top-level
    ``decisions[]`` list (a picker, not the trace itself)."""

    id: int
    outcome: Literal["hired", "rejected"]
    decided_at: datetime


class EvidenceGraph(BaseModel):
    """``GET /hr/enrolments/{enrolment_id}/evidence-graph``.

    ``edges`` carries the structural edges (``part_of``, ``originates_from``,
    ``supersedes``, ``follows_decision``) plus, for every entry in
    ``decisions[]``, that decision's own ``available_at_decision`` edges to
    every node that existed (by timestamp) when it was recorded — the same
    honest, time-only relationship ``DecisionTrace.edges`` narrows to one
    decision at a time.
    """

    schema_version: int = 1
    generated_at: datetime
    scope: EvidenceScope
    candidate: CandidateRef
    requisition: RequisitionRef
    workflow: WorkflowRef
    nodes: list[EvidenceNode]
    edges: list[EvidenceEdge]
    decisions: list[DecisionSummary]
    omitted: list[OmittedItem]


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


class TracePathStep(BaseModel):
    node_id: str
    edge: EvidenceEdgeKind | None = None


class TraceEvidenceItem(BaseModel):
    node: EvidenceNode
    path: list[TracePathStep]
    #: A short reason when this node's CONTENT is known to differ from what
    #: it was at decision time (a re-score, a correction opened later) —
    #: never silently absorbed into an unqualified "available" claim.
    changed_after_decision: str | None = None


class AfterDecisionItem(BaseModel):
    node: EvidenceNode
    reason: str = "submitted after the decision"


class DecisionActor(ActorRef):
    pass


class DecisionDetail(BaseModel):
    id: int
    enrolment_id: str
    outcome: Literal["hired", "rejected"]
    reversal: bool
    decided_at: datetime
    decided_by: ActorRef
    automated: bool = False
    reason_code: str | None = None
    reason_label: str | None = None
    reason: str | None = None


class OtherDecisionRef(BaseModel):
    id: int
    outcome: Literal["hired", "rejected"]
    decided_at: datetime
    reversal: bool


class AiInvolvement(BaseModel):
    ai_produced_evidence: int
    #: Always "human" — this route 404s on anything else (D-05, structural).
    decided_by: Literal["human"] = "human"


class DecisionTrace(BaseModel):
    """``GET /hr/decisions/{decision_id}/trace``.

    ``edges`` literalises the relationships ``evidence[]`` already encodes as
    a list and a per-item ``path``, so a reader can walk decision → evidence →
    origin from ``edges`` alone, without reconstructing anything: one
    ``available_at_decision`` edge (this decision's node id → each evidence
    node's id) per item in ``evidence[]`` that is honestly available by time,
    plus every ``originates_from`` edge already present on those items'
    ``path``. ``evidence[]``/``after_decision[]`` stay exactly as they are —
    the readable form; ``edges`` is the same facts, walkable.

    An ``available_at_decision`` edge means only that the target existed
    (by timestamp) when this decision was recorded — never that the
    decision-maker read or relied on it. ``screening_ats`` is the one node
    kind that can appear in ``evidence[]`` without a matching edge here: it is
    always shown (a single mutable field has no history to show "what it
    was"), but honesty about the edge takes priority over completeness of the
    edge list, so no edge is asserted for it unless it truly predates the
    decision.
    """

    schema_version: int = 1
    decision: DecisionDetail
    other_decisions: list[OtherDecisionRef]
    evidence: list[TraceEvidenceItem]
    after_decision: list[AfterDecisionItem]
    edges: list[EvidenceEdge]
    by_stage: dict[str, int]
    ai_involvement: AiInvolvement
    omitted: list[OmittedItem]


__all__ = [
    "ActorRef",
    "AfterDecisionItem",
    "AiInvolvement",
    "Attribution",
    "CandidateRef",
    "DecisionActor",
    "DecisionDetail",
    "DecisionSummary",
    "EvidenceEdge",
    "EvidenceEdgeKind",
    "EvidenceGraph",
    "EvidenceNode",
    "EvidenceNodeKind",
    "EvidenceScope",
    "EvidenceStage",
    "Lifecycle",
    "OmittedItem",
    "OtherDecisionRef",
    "Provenance",
    "ProducedBy",
    "RequisitionRef",
    "SourceRef",
    "StageInfo",
    "TraceEvidenceItem",
    "TracePathStep",
    "WorkflowRef",
]
