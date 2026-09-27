"""Public entry points: gather every loader into one graph, and trace one
decision through it.

This is the orchestration layer — it owns no SQL of its own (that is
``loaders.py``) and no edge/trace algebra of its own (that is
``assemble.py``); it just wires the two together into the two shapes the
router and the copilot tool actually consume: :class:`EvidenceGraph` and
:class:`DecisionTrace`.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.evidence import (
    ActorRef,
    AiInvolvement,
    CandidateRef,
    DecisionDetail,
    DecisionSummary,
    DecisionTrace,
    EvidenceEdge,
    EvidenceGraph,
    EvidenceNode,
    EvidenceScope,
    OtherDecisionRef,
    RequisitionRef,
    WorkflowRef,
)

from .assemble import assemble, availability_edges, trace
from .loaders import (
    _DECISION_STATUSES,
    _ERASED_REASON_OMISSION,
    OMITTED_ALWAYS,
    _ai_interview_nodes,
    _application_node,
    _decision_nodes,
    _EdgeLink,
    _erased_expr_true,
    _exam_attempt_nodes,
    _human_scorecard_nodes,
    _interview_session_nodes,
    _load_scope,
    _offer_nodes,
    _round_result_nodes,
    _screening_answers_node,
    _screening_ats_node,
    _stage_move_nodes,
    _task_submission_nodes,
)


@dataclass
class _Gathered:
    scope: dict[str, Any]
    erased: bool = False
    nodes: list[EvidenceNode] = field(default_factory=list)
    links: list[_EdgeLink] = field(default_factory=list)


async def _gather(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, viewer_user_id: uuid.UUID
) -> _Gathered:
    scope = await _load_scope(db, company_id=company_id, enrolment_id=enrolment_id)
    erased = _erased_expr_true(scope["full_name"], scope["email"], bool(scope["has_erasure_request"]))
    g = _Gathered(scope=scope, erased=erased)
    g.nodes.append(_application_node(scope, enrolment_id))

    ats_node = _screening_ats_node(scope, enrolment_id)
    if ats_node is not None:
        g.nodes.append(ats_node)

    answers_node = await _screening_answers_node(
        db, company_id=company_id, enrolment_id=enrolment_id, scope=scope
    )
    if answers_node is not None:
        g.nodes.append(answers_node)

    g.nodes.extend(await _stage_move_nodes(db, company_id=company_id, enrolment_id=enrolment_id, scope=scope))
    g.nodes.extend(await _exam_attempt_nodes(db, company_id=company_id, enrolment_id=enrolment_id, scope=scope))

    ai_nodes, scorecard_id_to_invite = await _ai_interview_nodes(
        db, company_id=company_id, enrolment_id=enrolment_id, scope=scope
    )
    g.nodes.extend(ai_nodes)

    rr_nodes, rr_links = await _round_result_nodes(
        db, company_id=company_id, enrolment_id=enrolment_id, scope=scope,
        scorecard_id_to_invite=scorecard_id_to_invite,
    )
    g.nodes.extend(rr_nodes)
    g.links.extend(rr_links)

    g.nodes.extend(
        await _interview_session_nodes(db, company_id=company_id, enrolment_id=enrolment_id, scope=scope)
    )

    hs_nodes, hs_links = await _human_scorecard_nodes(
        db, company_id=company_id, enrolment_id=enrolment_id, viewer_user_id=viewer_user_id, scope=scope,
    )
    g.nodes.extend(hs_nodes)
    g.links.extend(hs_links)

    ts_nodes, ts_links = await _task_submission_nodes(
        db, company_id=company_id, enrolment_id=enrolment_id, scope=scope
    )
    g.nodes.extend(ts_nodes)
    g.links.extend(ts_links)

    decision_nodes = await _decision_nodes(
        db, company_id=company_id, enrolment_id=enrolment_id, scope=scope, erased=erased,
    )
    g.nodes.extend(decision_nodes)

    offer_nodes, offer_links = await _offer_nodes(
        db, company_id=company_id, enrolment_id=enrolment_id, scope=scope, decision_nodes=decision_nodes,
    )
    g.nodes.extend(offer_nodes)
    g.links.extend(offer_links)

    return g


async def build_graph(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, viewer_user_id: uuid.UUID
) -> EvidenceGraph:
    """Assemble the full, declared evidence graph for one application.

    Read-only: writes nothing, and never raises for an authorisation reason a
    caller (a route or a read-only agent tool) should not be trusted to
    interpret consistently — a wrong company or a soft-deleted enrolment both
    raise :class:`~app.evidence_graph.loaders.EvidenceGraphError` (404). Any
    audit row is the CALLER's responsibility — this function must stay safe
    for a read-only tool to call directly.
    """
    g = await _gather(db, company_id=company_id, enrolment_id=enrolment_id, viewer_user_id=viewer_user_id)
    edges = assemble(g.nodes, g.links)
    decision_nodes = [n for n in g.nodes if n.kind == "decision" and isinstance(n.content, dict)]
    # Literalised, not reconstructed: every decision's available_at_decision
    # edges are real EvidenceEdge objects here, not just an implicit ordering
    # a reader would have to infer from timestamps (E5-1, E5-10).
    for decision_node in decision_nodes:
        edges.extend(availability_edges(g.nodes, decision=decision_node))
    decisions = [
        DecisionSummary(id=int(n.source.id), outcome=n.content["outcome"], decided_at=n.occurred_at)  # type: ignore[index]
        for n in decision_nodes
    ]
    scope = g.scope
    omitted = list(OMITTED_ALWAYS)
    if g.erased:
        omitted.append(_ERASED_REASON_OMISSION)
    return EvidenceGraph(
        generated_at=datetime.now(tz=UTC),
        scope=EvidenceScope(
            company_id=str(company_id), enrolment_id=str(enrolment_id), applicant_id=str(scope["applicant_id"])
        ),
        candidate=CandidateRef(name=scope["full_name"], erased=g.erased),
        requisition=RequisitionRef(
            id=str(scope["requisition_id"]) if scope.get("requisition_id_checked") else None,
            title=scope["requisition_title"],
        ),
        workflow=WorkflowRef(
            id=str(scope["workflow_id"]) if scope["workflow_id"] else None,
            version=scope["workflow_version"],
        ),
        nodes=g.nodes, edges=edges, decisions=decisions, omitted=omitted,
    )


def blinded_round_count(graph: EvidenceGraph) -> int:
    """How many rounds this graph hid content for, on independence grounds —
    for the audit row only, never shown to the caller of the route itself."""
    return len(
        {
            n.stage.round_id
            for n in graph.nodes
            if n.kind == "human_scorecard"
            and n.content_hidden_reason is not None
            and "independence" in n.content_hidden_reason
        }
    )


@dataclass(frozen=True)
class _DecisionRow:
    enrolment_id: uuid.UUID
    outcome: str
    reversal: bool
    decided_at: datetime
    decided_by: ActorRef
    reason_code: str | None
    reason_label: str | None
    reason: str | None


async def resolve_decision(
    db: AsyncSession, *, company_id: uuid.UUID, decision_id: int
) -> _DecisionRow | None:
    """The stage_transitions row a decision id names, if — and only if — it is
    this company's, a real (non-automated) hire or reject, and its enrolment
    is still live. None otherwise, including "no such id" and "wrong
    company", so a caller (404, never 403) cannot tell those apart.
    """
    row = (
        await db.execute(
            text(
                "SELECT t.id, t.enrolment_id, t.from_status, t.to_status, t.actor_user_id,"
                "       t.occurred_at, t.reason, t.reason_code, t.reason_label,"
                "       u.full_name AS actor_name"
                "  FROM stage_transitions t"
                "  JOIN enrolments e ON e.id = t.enrolment_id AND e.company_id = t.company_id"
                "  LEFT JOIN users u ON u.id = t.actor_user_id"
                " WHERE t.id = :id AND t.company_id = :c"
                "   AND t.to_status = ANY(:decisions) AND NOT t.automated"
                "   AND e.deleted_at IS NULL"
            ),
            {"id": decision_id, "c": company_id, "decisions": sorted(_DECISION_STATUSES)},
        )
    ).mappings().first()
    if row is None:
        return None
    return _DecisionRow(
        enrolment_id=row["enrolment_id"],
        outcome=row["to_status"],
        reversal=row["from_status"] == "hired" and row["to_status"] == "rejected",
        decided_at=row["occurred_at"],
        decided_by=ActorRef(user_id=str(row["actor_user_id"]) if row["actor_user_id"] else None, name=row["actor_name"]),
        reason_code=row["reason_code"], reason_label=row["reason_label"], reason=row["reason"],
    )


async def latest_decision_id(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID
) -> int | None:
    """The most recent real decision on this application, or None — for the
    copilot tool's ``decision="latest"``. Scoped to ``company_id`` exactly
    like every other read here."""
    return await db.scalar(
        text(
            "SELECT t.id FROM stage_transitions t"
            "  JOIN enrolments e ON e.id = t.enrolment_id AND e.company_id = t.company_id"
            " WHERE t.enrolment_id = :e AND t.company_id = :c"
            "   AND t.to_status = ANY(:decisions) AND NOT t.automated"
            "   AND e.deleted_at IS NULL"
            " ORDER BY t.occurred_at DESC, t.id DESC LIMIT 1"
        ),
        {"e": enrolment_id, "c": company_id, "decisions": sorted(_DECISION_STATUSES)},
    )


def decision_trace_from_graph(
    graph: EvidenceGraph, *, decision: _DecisionRow, decision_id: int
) -> DecisionTrace:
    """Pure: the trace view over an already-assembled graph.

    A plain function, kept trivially unit-testable over fixtures, per the
    design's "pure assemble()/trace()" requirement.
    """
    edges = graph.edges  # already assembled by build_graph(), availability edges included
    decision_node = next(n for n in graph.nodes if n.kind == "decision" and n.source.id == str(decision_id))
    evidence, after = trace(graph.nodes, edges, decision=decision_node)
    other = [
        OtherDecisionRef(
            id=int(n.source.id), outcome=n.content["outcome"],  # type: ignore[index]
            decided_at=n.occurred_at, reversal=bool(n.content["reversal"]),  # type: ignore[index]
        )
        for n in graph.nodes
        if n.kind == "decision" and n.source.id != str(decision_id)
    ]
    by_stage: dict[str, int] = dict(Counter(item.node.stage.name for item in evidence))
    ai_count = sum(1 for item in evidence if item.node.provenance.produced_by == "ai")

    # Literalise decision -> evidence -> origin as real edges (E5-1, E5-10):
    # build_graph() already computed this decision's own available_at_decision
    # edges (pure, honest, time-only) — reuse them rather than recompute, and
    # add the originates_from edge already on each evidence item's own path,
    # so a reader can walk the whole chain from `edges` alone. screening_ats,
    # when shown in evidence[] past its honest window, gets no edge here —
    # completeness of the edge list never overrides its honesty.
    availability = [
        e for e in edges if e.kind == "available_at_decision" and e.from_ == decision_node.id
    ]
    origin_edges = [
        EvidenceEdge(from_=item.node.id, to=step.node_id, kind=step.edge)
        for item in evidence
        for step in item.path
        if step.edge is not None
    ]
    trace_edges = [*availability, *origin_edges]

    # The traced decision's own node already has its `reason` withheld by
    # _decision_nodes when the candidate is erased — read it from there
    # rather than from `decision` (a separate raw read with no erasure check
    # of its own), so there is exactly one place this rule is applied.
    node_content = decision_node.content if isinstance(decision_node.content, dict) else {}
    reason = node_content.get("reason", decision.reason)

    omitted = list(OMITTED_ALWAYS)
    if graph.candidate.erased:
        omitted.append(_ERASED_REASON_OMISSION)

    return DecisionTrace(
        decision=DecisionDetail(
            id=decision_id, enrolment_id=str(decision.enrolment_id), outcome=decision.outcome,  # type: ignore[arg-type]
            reversal=decision.reversal, decided_at=decision.decided_at, decided_by=decision.decided_by,
            reason_code=decision.reason_code, reason_label=decision.reason_label, reason=reason,
        ),
        other_decisions=other, evidence=evidence, after_decision=after, edges=trace_edges,
        by_stage=by_stage, ai_involvement=AiInvolvement(ai_produced_evidence=ai_count),
        omitted=omitted,
    )
