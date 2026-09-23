"""Pure edge/trace functions — ``assemble()``, ``availability_edges()``,
``trace()``. No I/O, no database: every input here is already-loaded nodes
and links, which is what keeps this module trivially unit-testable over
fixtures.

HONEST SEMANTICS, non-negotiable
---------------------------------
The decision edge is ``available_at_decision``, computed purely by time:
``node.recorded_at <= decision.occurred_at``. We can prove what existed in
the system when a person decided. We cannot prove what they read or relied
on — so nothing here is ever called ``supported_by``. Evidence recorded after
the decision is never silently folded into ``evidence[]``; it goes in
``after_decision[]``, flagged.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.schemas.evidence import (
    AfterDecisionItem,
    EvidenceEdge,
    EvidenceNode,
    TraceEvidenceItem,
    TracePathStep,
)

from .loaders import _EdgeLink


def assemble(nodes: Sequence[EvidenceNode], links: Sequence[_EdgeLink] = ()) -> list[EvidenceEdge]:
    """Pure: every node (but the application itself) is ``part_of`` it, plus
    whatever ``supersedes`` / ``originates_from`` / ``follows_decision`` links
    the loaders recorded. An edge whose target is not among ``nodes`` is
    silently dropped — never a dangling reference.
    """
    ids = {n.id for n in nodes}
    application_id = next((n.id for n in nodes if n.kind == "application"), None)
    edges: list[EvidenceEdge] = []
    for n in nodes:
        if n.kind != "application" and application_id is not None and application_id in ids:
            edges.append(EvidenceEdge(from_=n.id, to=application_id, kind="part_of"))
    for link in links:
        if link.node_id not in ids:
            continue
        if link.supersedes is not None and link.supersedes in ids:
            edges.append(EvidenceEdge(from_=link.node_id, to=link.supersedes, kind="supersedes"))
        if link.originates_from is not None and link.originates_from in ids:
            edges.append(
                EvidenceEdge(from_=link.node_id, to=link.originates_from, kind="originates_from")
            )
        if link.follows_decision_of is not None and link.follows_decision_of in ids:
            edges.append(
                EvidenceEdge(from_=link.node_id, to=link.follows_decision_of, kind="follows_decision")
            )
    return edges


def availability_edges(nodes: Sequence[EvidenceNode], *, decision: EvidenceNode) -> list[EvidenceEdge]:
    """Pure: the honest ``available_at_decision`` edges for one decision.

    ``recorded_at <= decision.occurred_at`` and nothing else — this is a
    statement about what existed, never about what was read or relied on.
    Equal timestamps count as available (``<=``, not ``<``): a decision and
    the evidence it was based on are frequently recorded in the same
    transaction.
    """
    return [
        EvidenceEdge(from_=decision.id, to=n.id, kind="available_at_decision")
        for n in nodes
        if n.kind != "decision" and n.recorded_at <= decision.occurred_at
    ]


def trace(
    nodes: Sequence[EvidenceNode], edges: Sequence[EvidenceEdge], *, decision: EvidenceNode
) -> tuple[list[TraceEvidenceItem], list[AfterDecisionItem]]:
    """Pure: split every non-decision node into what existed at the decision
    and what came strictly after it, with a one-hop path back to whatever a
    node ``originates_from``.

    ``screening_ats`` is the one deliberate exception to the time cutoff
    (Q12): the ATS score is a single mutable field with no history of its
    own, so there is no way to show "what it was" at decision time — only
    whether it has been recomputed since. It always appears in ``evidence``
    (never silently dropped into ``after_decision``), flagged via
    ``changed_after_decision`` when it was in fact recorded later, so it is
    never *silently* mixed in as though it certainly existed unchanged.
    """
    originates = {e.from_: e.to for e in edges if e.kind == "originates_from"}
    superseded_by: dict[str, list[str]] = {}
    for e in edges:
        if e.kind == "supersedes":
            superseded_by.setdefault(e.to, []).append(e.from_)
    by_id = {n.id: n for n in nodes}
    available_ids = {e.to for e in availability_edges(nodes, decision=decision)}

    def _changed_after(node: EvidenceNode) -> str | None:
        if node.kind == "screening_ats" and node.recorded_at > decision.occurred_at:
            return "re-scored after the decision"
        # Walk the FULL supersedes chain, not just the immediate successor:
        # a live scorecard correction is refused once a decision exists, so
        # today's longest live chain is two deep — but a round_result retake
        # or a legacy row can chain further, and an intermediate link that is
        # itself still before the decision must not hide a later one that
        # is not. Cycle-guarded, though a real supersedes chain is acyclic
        # by construction.
        stack = list(superseded_by.get(node.id, ()))
        seen: set[str] = set()
        while stack:
            newer_id = stack.pop()
            if newer_id in seen:
                continue
            seen.add(newer_id)
            newer = by_id.get(newer_id)
            if newer is not None and newer.recorded_at > decision.occurred_at:
                return "corrected after the decision"
            stack.extend(superseded_by.get(newer_id, ()))
        return None

    evidence: list[TraceEvidenceItem] = []
    after: list[AfterDecisionItem] = []
    for node in nodes:
        if node.kind == "decision":
            continue
        path = [TracePathStep(node_id=node.id)]
        target = originates.get(node.id)
        if target is not None and target in by_id:
            path.append(TracePathStep(node_id=target, edge="originates_from"))
        if node.kind == "screening_ats" or node.id in available_ids:
            evidence.append(
                TraceEvidenceItem(node=node, path=path, changed_after_decision=_changed_after(node))
            )
        else:
            after.append(AfterDecisionItem(node=node))
    return evidence, after
