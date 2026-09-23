"""The evidence graph — PH5-E5. HR-only, company scoped through ``HrCtxDep``.

Two reads over the same read-time graph (``app/evidence_graph.py``):

  GET /hr/enrolments/{enrolment_id}/evidence-graph   every piece of evidence
                                                      an application has
  GET /hr/decisions/{decision_id}/trace              what existed when ONE
                                                      decision was recorded

Nothing here writes anything but the audit row for the read itself. Both
routes 404 — never 403 — on a wrong company, exactly like the rest of ``/hr``,
so an enrolment or a decision id from another tenant is indistinguishable
from one that never existed.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException

from app.database import DbSessionDep
from app.dependencies import HrCtxDep
from app.evidence_graph import (
    EvidenceGraphError,
    blinded_round_count,
    build_graph,
    decision_trace_from_graph,
    resolve_decision,
)
from app.models import AuditLog
from app.schemas.evidence import DecisionTrace, EvidenceGraph

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["evidence-graph"])


def _audit(*, actor_id: uuid.UUID, action: str, enrolment_id: uuid.UUID, details: dict[str, Any]) -> AuditLog:
    return AuditLog(
        actor_id=actor_id, actor_type="user", action=action, resource_type="enrolment",
        resource_id=enrolment_id, details=details, event_ts=datetime.now(tz=UTC),
    )


@router.get("/enrolments/{enrolment_id}/evidence-graph", response_model=EvidenceGraph)
async def get_evidence_graph(
    enrolment_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> EvidenceGraph:
    """Every declared piece of evidence for one application, and how it
    connects. ``edges`` includes, for each entry in ``decisions[]``, that
    decision's ``available_at_decision`` edges to every node that existed
    (by timestamp) when it was recorded — never a claim that it was relied
    on. 404 unless the application is this company's and live.
    """
    uid, company_id = ctx
    try:
        graph = await build_graph(
            db, company_id=company_id, enrolment_id=enrolment_id, viewer_user_id=uid
        )
    except EvidenceGraphError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    node_counts = Counter(n.kind for n in graph.nodes)
    db.add(
        _audit(
            actor_id=uid, action="evidence.graph_viewed", enrolment_id=enrolment_id,
            details={
                "company_id": str(company_id), "node_counts": dict(node_counts),
                "blinded_rounds": blinded_round_count(graph),
            },
        )
    )
    await db.commit()
    return graph


@router.get("/decisions/{decision_id}/trace", response_model=DecisionTrace)
async def get_decision_trace(
    decision_id: int, ctx: HrCtxDep, db: DbSessionDep
) -> DecisionTrace:
    """What existed — by timestamp — when one hire or reject was recorded.

    ``edges`` makes decision → evidence → origin walkable directly: one
    ``available_at_decision`` edge per item in ``evidence[]`` (from this
    decision to that evidence), plus every ``originates_from`` edge already
    on an item's own ``path``. An ``available_at_decision`` edge is never
    ``supported_by`` — it states only that the target existed when this
    decision was recorded, never that the decision-maker read or relied on
    it.

    404 unless ``decision_id`` names a ``stage_transitions`` row that is this
    company's, a real (non-automated) hire or reject, and its enrolment is
    still live. A legacy, applicant-level decision that exists only in
    ``audit_log`` has no such row and 404s here too — this module never reads
    ``audit_log`` (AR-5).
    """
    uid, company_id = ctx
    decision = await resolve_decision(db, company_id=company_id, decision_id=decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found.")

    try:
        graph = await build_graph(
            db, company_id=company_id, enrolment_id=decision.enrolment_id, viewer_user_id=uid
        )
    except EvidenceGraphError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    trace = decision_trace_from_graph(graph, decision=decision, decision_id=decision_id)
    db.add(
        _audit(
            actor_id=uid, action="evidence.trace_viewed", enrolment_id=decision.enrolment_id,
            details={
                "company_id": str(company_id), "decision_id": decision_id,
                "evidence_count": len(trace.evidence),
            },
        )
    )
    await db.commit()
    return trace
