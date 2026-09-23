"""The evidence graph — PH5-E5, "First-Class Evidence Graph".

FIRST-CLASS MEANS DECLARED AND QUERYABLE, NOT STORED. There is no
``evidence_edges`` table, no materialised graph, and nothing here ever writes
one. Every :class:`~app.schemas.evidence.EvidenceNode` is derived, at read
time, from rows the existing screens already show (``interviewer_scorecards``,
``round_results``, ``offers``, ``stage_transitions``, ``task_submissions``,
…); every edge is computed from timestamps and foreign keys already on those
rows. Because nothing is copied, DPDP erasure, retention, redaction and
consent withdrawal are inherited for free FOR EVERY SOURCE ROW THIS MODULE
READS — they show up on the very next read of this graph, with no erasure
step and no retention job of this module's own. There is exactly ONE named
exception: ``stage_transitions.reason`` (a decision's free-text rationale) is
deliberately NOT redacted at its source on erasure (``erasure_executor.py``
EXCLUDED_TABLES, AR-5) — this module compensates for that at render time,
withholding it (never the source row) once ``candidate.erased`` is true. See
``_ERASED_REASON_HIDDEN``/``_ERASED_REASON_OMISSION`` and criterion E5's
security review.

"First-class" is expressed as: a closed, typed vocabulary
(``app/schemas/evidence.py``), one ``STAGE_OF_ROUND_KIND`` map covering every
``ck_workflow_rounds_kind`` value, one declared loader per node kind, and two
pure functions — :func:`assemble` (structural edges) and :func:`trace`
(what existed at a decision, and what did not). This module is read-only: it
never calls ``record_transition``, ``record_round_move``, ``record_result``,
``record_final_decision`` or anything else that writes, and it never inserts
an audit row itself — callers (the router, never a tool) own that, so this
module stays safe for the read-only agent layer to call directly.

HONEST SEMANTICS, non-negotiable
---------------------------------
The decision edge is ``available_at_decision``, computed purely by time:
``node.recorded_at <= decision.occurred_at``. We can prove what existed in
the system when a person decided. We cannot prove what they read or relied
on — so nothing here is ever called ``supported_by``. Evidence recorded after
the decision is never silently folded into ``evidence[]``; it goes in
``after_decision[]``, flagged.

WHAT THIS MODULE NEVER READS
``audit_log`` (AR-5 trigger (b), ``docs/ACCEPTED-RISKS.md``) — the decision
rationale comes from ``stage_transitions.reason``, which HR already sees via
``/history``. Also never read: ``interviewer_notes``, ``hire_checkins``,
``candidate_accommodations``, and coding source / similarity pairs / task
response CONTENT (each has its own audited route). Also never SHOWN, though
some of the underlying columns are read: offer compensation figures, the text
of a candidate's screening answers, any AI-generated prose (an ATS summary, a
scorer's rationale, a round result's own evidence text — reduced to a
boolean), and candidate contact details. See ``OMITTED_ALWAYS``.

KNOWN, DELIBERATE LIMIT — independence and ``round_result``
A ``round_result`` node (a round's pass/fail outcome and per-criterion scores)
sits OUTSIDE the independence rule ``human_scorecard`` nodes get from
``scorecards_for_enrolment``: a viewer who still owes their own scorecard for
a round can see that round's numeric outcome here, though never a peer's
scorecard content. This is pre-existing and narrower than the existing
``round-results`` HR route (which carries the same property) — not a new gap
this module opened, but worth naming so the next reader is not misled into
thinking every round-scoped node here is independence-safe.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.code_evidence import summary_for_enrolments as code_evidence_summary
from app.interviewer_scorecards import scorecards_for_enrolment
from app.job_tasks import consent_withdrawn as _task_consent_withdrawn
from app.schemas.evidence import (
    ActorRef,
    AfterDecisionItem,
    AiInvolvement,
    CandidateRef,
    DecisionDetail,
    DecisionSummary,
    EvidenceEdge,
    EvidenceGraph,
    EvidenceNode,
    EvidenceNodeKind,
    EvidenceScope,
    EvidenceStage,
    OmittedItem,
    OtherDecisionRef,
    Provenance,
    RequisitionRef,
    SourceRef,
    StageInfo,
    TraceEvidenceItem,
    TracePathStep,
    WorkflowRef,
)
from app.workflows import load_criteria

log = structlog.get_logger(__name__)


class EvidenceGraphError(Exception):
    """Refused. Carries the HTTP status and a sentence for a person."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# The stage map — tested against ck_workflow_rounds_kind
# ---------------------------------------------------------------------------

#: Every workflow round kind maps to exactly one stage. mcq/coding/
#: job_simulation/portfolio are all a candidate doing assessed work;
#: ai_interview and human_review are both a conversation. A test
#: (``test_stage_of_round_kind_covers_vocabulary``) asserts this dict's keys
#: equal the CHECK constraint's vocabulary exactly, so a new round kind fails
#: loudly here rather than falling through to a guess.
STAGE_OF_ROUND_KIND: dict[str, EvidenceStage] = {
    "mcq": "assessment",
    "coding": "assessment",
    "job_simulation": "assessment",
    "portfolio": "assessment",
    "ai_interview": "interview",
    "human_review": "interview",
}

#: Always omitted from every graph and every trace, whatever the data —
#: listed with a reason rather than merely absent, so a reader knows this is a
#: deliberate boundary and not a bug.
OMITTED_ALWAYS: tuple[OmittedItem, ...] = (
    OmittedItem(
        kind="interviewer_notes",
        reason="Private to the interviewer; never shown to HR.",
    ),
    OmittedItem(
        kind="hire_checkins",
        reason=(
            "A post-hire outcome signal; never fed to a model and never shown "
            "as hiring evidence."
        ),
    ),
    OmittedItem(
        kind="candidate_accommodations",
        reason="A sensitive category; not exposed as evidence.",
    ),
    OmittedItem(
        kind="coding_source_and_similarity",
        reason=(
            "Coding source, similarity pairs and task response content have "
            "their own audited routes."
        ),
    ),
    OmittedItem(
        kind="audit_log",
        reason=(
            "This module never reads audit_log (AR-5 trigger (b), "
            "docs/ACCEPTED-RISKS.md). The decision rationale comes from "
            "stage_transitions.reason, which HR already sees via /history."
        ),
    ),
    OmittedItem(
        kind="offer_compensation",
        reason=(
            "An offer node carries status and timing only — never base salary, "
            "bonus, equity or any other compensation figure."
        ),
    ),
    OmittedItem(
        kind="screening_answer_text",
        reason=(
            "The screening_answers node is a count only; the candidate's actual "
            "answers are never read here."
        ),
    ),
    OmittedItem(
        kind="ai_prose",
        reason=(
            "No model-generated prose is exposed: not the ATS summary, not a "
            "scorer's rationale, and a round_result's own evidence text is "
            "reduced to the boolean has_rationale."
        ),
    ),
    OmittedItem(
        kind="candidate_contact_details",
        reason=(
            "Email is read only to compute the erasure check, never shown; phone "
            "and other contact details are never read here at all."
        ),
    ),
)


def _href(applicant_id: uuid.UUID, enrolment_id: uuid.UUID, anchor: str | None = None) -> str:
    base = f"/hr/applicants/{applicant_id}?enrolment={enrolment_id}"
    return f"{base}#{anchor}" if anchor else base


#: One anchor per node kind. This is HALF of a contract with
#: ``web/src/components/CandidateDrawer.tsx``: that component owns the other
#: half — a section carrying each of these as its DOM id. The drawer now
#: defines seven of them: ``screening``, ``answers``, ``round-results``,
#: ``human-interview``, ``tasks``, ``offers`` and ``history``. There is no
#: eighth or ninth section for a raw exam attempt or a raw AI interview
#: session — both surface through the round's own result instead — so
#: ``exam_attempt`` and ``ai_interview`` point at ``round-results`` too,
#: deliberately, not as a placeholder. Keep this mapping and the drawer's
#: section ids in sync; a mismatch here is silent (the browser just does not
#: jump).
HREF_ANCHOR: dict[EvidenceNodeKind, str] = {
    "application": "history",
    "screening_ats": "screening",
    "screening_answers": "answers",
    "stage_move": "history",
    "exam_attempt": "round-results",
    "round_result": "round-results",
    "ai_interview": "round-results",
    "interview_session": "human-interview",
    "human_scorecard": "human-interview",
    "task_submission": "tasks",
    "offer": "offers",
    "decision": "history",
}


def _node_href(kind: EvidenceNodeKind, applicant_id: uuid.UUID, enrolment_id: uuid.UUID) -> str:
    return _href(applicant_id, enrolment_id, HREF_ANCHOR[kind])


def _erased_expr_true(full_name: str | None, email: str | None, has_erasure_request: bool) -> bool:
    return bool((full_name == "[redacted]" and email is None) or has_erasure_request)


# ---------------------------------------------------------------------------
# Internal linking — how nodes connect, computed alongside each loader
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _EdgeLink:
    """What one node points at, for :func:`assemble` to turn into edges.

    Not returned to any caller — a loader's private notes to itself. Every
    field is a node id or None; ``assemble`` drops anything whose target is
    not in the node set (a superseded row erasure has since removed, a scorecard
    whose session was never scheduled, and so on), so a dangling reference
    never reaches an edge.
    """

    node_id: str
    supersedes: str | None = None
    originates_from: str | None = None
    follows_decision_of: str | None = None


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
        for newer_id in superseded_by.get(node.id, ()):
            newer = by_id.get(newer_id)
            if newer is not None and newer.recorded_at > decision.occurred_at:
                return "corrected after the decision"
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


# ---------------------------------------------------------------------------
# Loaders — one per node kind. Every query binds company_id explicitly, on
# top of whatever composite FK already enforces it (defence in depth).
# ---------------------------------------------------------------------------


async def _load_scope(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID
) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT e.id, e.applicant_id, e.requisition_id, e.workflow_id, e.status,"
                "       e.source, e.created_at, e.ats_overall, e.ats_recommendation,"
                "       e.scored_at,"
                "       a.full_name, a.email,"
                "       EXISTS (SELECT 1 FROM erasure_requests er WHERE er.user_id = a.user_id)"
                "         AS has_erasure_request,"
                "       COALESCE(jr.title, e.target_job_title) AS requisition_title,"
                "       jr.id AS requisition_id_checked,"
                "       wf.version AS workflow_version,"
                "       (SELECT count(*) FROM enrolments n"
                "         WHERE n.applicant_id = e.applicant_id AND n.deleted_at IS NULL)"
                "         AS live_applications"
                "  FROM enrolments e"
                "  JOIN applicants a ON a.id = e.applicant_id"
                "  LEFT JOIN job_requisitions jr ON jr.id = e.requisition_id"
                "  LEFT JOIN workflows wf ON wf.id = e.workflow_id"
                " WHERE e.id = :e AND e.company_id = :c AND e.deleted_at IS NULL"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise EvidenceGraphError(404, "Application not found.")
    return dict(row)


def _application_node(scope: dict[str, Any], enrolment_id: uuid.UUID) -> EvidenceNode:
    return EvidenceNode(
        id=f"application:{enrolment_id}",
        kind="application",
        stage=StageInfo(name="application"),
        source=SourceRef(table="enrolments", id=str(enrolment_id)),
        provenance=Provenance(produced_by="candidate", method="submitted an application"),
        occurred_at=scope["created_at"],
        recorded_at=scope["created_at"],
        content={
            "source": scope["source"],
            "applied_at": scope["created_at"].isoformat(),
            "status_now": scope["status"],
        },
        href=_node_href("application", scope["applicant_id"], enrolment_id),
    )


def _screening_ats_node(scope: dict[str, Any], enrolment_id: uuid.UUID) -> EvidenceNode | None:
    if scope["ats_overall"] is None and scope["ats_recommendation"] is None:
        return None
    when = scope["scored_at"] or scope["created_at"]
    return EvidenceNode(
        id=f"screening_ats:{enrolment_id}",
        kind="screening_ats",
        stage=StageInfo(name="screening"),
        source=SourceRef(table="enrolments", id=str(enrolment_id)),
        provenance=Provenance(produced_by="ai", method="automated resume screening against the role"),
        occurred_at=when,
        recorded_at=when,
        content={
            "ats_overall": scope["ats_overall"],
            "ats_recommendation": scope["ats_recommendation"],
        },
        href=_node_href("screening_ats", scope["applicant_id"], enrolment_id),
    )


async def _screening_answers_node(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, scope: dict[str, Any]
) -> EvidenceNode | None:
    row = (
        await db.execute(
            text(
                "SELECT count(*) AS n, max(created_at) AS latest FROM application_answers"
                " WHERE enrolment_id = :e AND company_id = :c"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().first()
    if row is None or not row["n"]:
        return None
    when = row["latest"] or scope["created_at"]
    return EvidenceNode(
        id=f"screening_answers:{enrolment_id}",
        kind="screening_answers",
        stage=StageInfo(name="screening"),
        source=SourceRef(table="application_answers", id=str(enrolment_id)),
        provenance=Provenance(produced_by="candidate", method="answered the opening's screening questions"),
        occurred_at=when,
        recorded_at=when,
        content={"count": int(row["n"])},
        href=_node_href("screening_answers", scope["applicant_id"], enrolment_id),
    )


_DECISION_STATUSES: frozenset[str] = frozenset({"hired", "rejected"})


async def _stage_move_nodes(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, scope: dict[str, Any]
) -> list[EvidenceNode]:
    rows = (
        await db.execute(
            text(
                "SELECT t.id, t.from_status, t.to_status, t.automated, t.actor_user_id,"
                "       t.occurred_at, t.from_round_id, t.to_round_id,"
                "       fr.title AS from_round_title, tr.title AS to_round_title,"
                "       tr.kind AS to_round_kind, fr.kind AS from_round_kind,"
                "       u.full_name AS actor_name"
                "  FROM stage_transitions t"
                "  LEFT JOIN workflow_rounds fr ON fr.id = t.from_round_id"
                "  LEFT JOIN workflow_rounds tr ON tr.id = t.to_round_id"
                "  LEFT JOIN users u ON u.id = t.actor_user_id"
                " WHERE t.enrolment_id = :e AND t.company_id = :c"
                "   AND NOT (t.to_status = ANY(:decisions) AND NOT t.automated)"
                " ORDER BY t.occurred_at, t.id"
            ),
            {"e": enrolment_id, "c": company_id, "decisions": sorted(_DECISION_STATUSES)},
        )
    ).mappings().all()
    out: list[EvidenceNode] = []
    for r in rows:
        round_kind = r["to_round_kind"] or r["from_round_kind"]
        stage: EvidenceStage = STAGE_OF_ROUND_KIND.get(round_kind, "screening")
        out.append(
            EvidenceNode(
                id=f"stage_move:{r['id']}",
                kind="stage_move",
                stage=StageInfo(
                    name=stage,
                    round_id=str(r["to_round_id"] or r["from_round_id"] or "") or None,
                    round_title=r["to_round_title"] or r["from_round_title"],
                    round_kind=round_kind,
                ),
                source=SourceRef(table="stage_transitions", id=str(r["id"])),
                provenance=Provenance(
                    produced_by="system" if r["automated"] else "human",
                    actor=(
                        ActorRef(user_id=str(r["actor_user_id"]), name=r["actor_name"])
                        if r["actor_user_id"]
                        else None
                    ),
                    method="pipeline stage move",
                ),
                occurred_at=r["occurred_at"],
                recorded_at=r["occurred_at"],
                content={
                    "from_status": r["from_status"],
                    "to_status": r["to_status"],
                    "from_round": r["from_round_title"],
                    "to_round": r["to_round_title"],
                },
                href=_node_href("stage_move", scope["applicant_id"], enrolment_id),
            )
        )
    return out


async def _exam_attempt_nodes(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, scope: dict[str, Any]
) -> list[EvidenceNode]:
    rows = (
        await db.execute(
            text(
                "SELECT t.id, t.score_percent, t.passed, t.submitted_at, t.created_at,"
                "       ex.title AS exam_title, ex.kind AS exam_kind,"
                "       wr.id AS round_id, wr.title AS round_title, wr.kind AS round_kind,"
                "       wf.version AS workflow_version, asg.enrolment_id AS assignment_enrolment_id"
                "  FROM exam_attempts t"
                "  JOIN exams ex ON ex.id = t.exam_id AND ex.company_id = t.company_id"
                "  LEFT JOIN exam_assignments asg ON asg.id = t.assignment_id AND asg.company_id = t.company_id"
                "  LEFT JOIN workflow_rounds wr ON wr.exam_round_id = t.round_id AND wr.company_id = t.company_id"
                "  LEFT JOIN workflows wf ON wf.id = wr.workflow_id"
                " WHERE t.applicant_id = :a AND t.company_id = :c"
                "   AND t.status = 'submitted' AND t.deleted_at IS NULL"
                "   AND (asg.enrolment_id = :e"
                "        OR (asg.enrolment_id IS NULL AND :single))"
                " ORDER BY t.submitted_at"
            ),
            {
                "a": scope["applicant_id"], "c": company_id, "e": enrolment_id,
                "single": int(scope["live_applications"] or 0) <= 1,
            },
        )
    ).mappings().all()
    if not rows:
        return []
    code_summary = await code_evidence_summary(db, company_id=company_id, enrolment_ids=[enrolment_id])
    counts = code_summary.get(str(enrolment_id))
    out: list[EvidenceNode] = []
    for r in rows:
        when = r["submitted_at"] or r["created_at"]
        content: dict[str, Any] = {
            "exam": r["exam_title"], "kind": r["exam_kind"],
            "score_percent": r["score_percent"], "passed": r["passed"],
        }
        if r["exam_kind"] == "coding" and counts is not None:
            content["code_evidence"] = counts
        out.append(
            EvidenceNode(
                id=f"exam_attempt:{r['id']}",
                kind="exam_attempt",
                stage=StageInfo(
                    name="assessment", round_id=str(r["round_id"]) if r["round_id"] else None,
                    round_title=r["round_title"], round_kind=r["round_kind"],
                    workflow_version=r["workflow_version"],
                ),
                source=SourceRef(table="exam_attempts", id=str(r["id"])),
                provenance=Provenance(
                    produced_by="candidate",
                    method="graded automatically against the exam's answer key",
                    attribution=(
                        "direct" if r["assignment_enrolment_id"] == enrolment_id
                        else "inferred_single_application"
                    ),
                ),
                occurred_at=when, recorded_at=when, content=content,
                href=_node_href("exam_attempt", scope["applicant_id"], enrolment_id),
            )
        )
    return out


_GRADED_BY_PRODUCED_BY: dict[str, str] = {"deterministic": "system", "ai": "ai", "human": "human"}


async def _round_result_nodes(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    enrolment_id: uuid.UUID,
    scope: dict[str, Any],
    scorecard_id_to_invite: dict[str, str],
) -> tuple[list[EvidenceNode], list[_EdgeLink]]:
    rows = (
        await db.execute(
            text(
                "SELECT rr.id, rr.round_id, rr.attempt_ref, rr.score, rr.percent, rr.passed,"
                "       rr.criterion_scores, rr.evidence, rr.graded_by, rr.grader_user_id,"
                "       rr.superseded_at, rr.created_at,"
                "       wr.title AS round_title, wr.kind AS round_kind, wf.version AS workflow_version,"
                "       u.full_name AS grader_name,"
                "       ts.id AS matched_task_submission_id"
                "  FROM round_results rr"
                "  JOIN workflow_rounds wr ON wr.id = rr.round_id AND wr.company_id = rr.company_id"
                "  LEFT JOIN workflows wf ON wf.id = wr.workflow_id"
                "  LEFT JOIN users u ON u.id = rr.grader_user_id"
                "  LEFT JOIN LATERAL ("
                "      SELECT s.id FROM task_submissions s"
                "       WHERE s.enrolment_id = rr.enrolment_id AND s.round_id = rr.round_id"
                "         AND s.company_id = rr.company_id AND s.submitted_at IS NOT NULL"
                "         AND s.submitted_at <= rr.created_at"
                "       ORDER BY s.submitted_at DESC LIMIT 1"
                "  ) ts ON wr.kind IN ('job_simulation', 'portfolio')"
                " WHERE rr.enrolment_id = :e AND rr.company_id = :c"
                " ORDER BY rr.round_id, rr.created_at"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().all()
    if not rows:
        return [], []

    round_ids = list({r["round_id"] for r in rows})
    criteria_by_round = await load_criteria(db, round_ids)
    names_by_round: dict[str, dict[str, str]] = {
        rid: {c["competency_id"]: c["competency_name"] for c in crit}
        for rid, crit in criteria_by_round.items()
    }

    nodes: list[EvidenceNode] = []
    links: list[_EdgeLink] = []
    last_id_by_round: dict[uuid.UUID, str] = {}
    for r in rows:
        node_id = f"round_result:{r['id']}"
        names = names_by_round.get(str(r["round_id"]), {})
        criterion_scores = r["criterion_scores"] if isinstance(r["criterion_scores"], dict) else {}
        content: dict[str, Any] = {
            "percent": float(r["percent"]) if r["percent"] is not None else None,
            "passed": r["passed"],
            "superseded": r["superseded_at"] is not None,
            "has_rationale": bool(r["evidence"]),
            "criteria": [
                {
                    "criterion_key": f"{r['round_id']}:{cid}",
                    "competency_name": names.get(cid, cid),
                    "score": score,
                }
                for cid, score in criterion_scores.items()
            ],
        }
        produced_by = _GRADED_BY_PRODUCED_BY.get(r["graded_by"], "system")
        actor = (
            ActorRef(user_id=str(r["grader_user_id"]), name=r["grader_name"])
            if r["grader_user_id"]
            else None
        )
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="round_result",
                stage=StageInfo(
                    name=STAGE_OF_ROUND_KIND.get(r["round_kind"], "assessment"),
                    round_id=str(r["round_id"]), round_title=r["round_title"],
                    round_kind=r["round_kind"], workflow_version=r["workflow_version"],
                ),
                source=SourceRef(table="round_results", id=str(r["id"])),
                provenance=Provenance(produced_by=produced_by, actor=actor, method=f"graded {r['graded_by']}"),
                occurred_at=r["created_at"], recorded_at=r["created_at"],
                lifecycle="superseded" if r["superseded_at"] is not None else "live",
                content=content,
                href=_node_href("round_result", scope["applicant_id"], enrolment_id),
            )
        )
        originates_from: str | None = None
        if r["round_kind"] in ("mcq", "coding") and r["attempt_ref"]:
            originates_from = f"exam_attempt:{r['attempt_ref']}"
        elif r["round_kind"] == "ai_interview" and r["attempt_ref"]:
            originates_from = scorecard_id_to_invite.get(str(r["attempt_ref"]))
        elif r["round_kind"] in ("job_simulation", "portfolio") and r["matched_task_submission_id"]:
            originates_from = f"task_submission:{r['matched_task_submission_id']}"

        prior = last_id_by_round.get(r["round_id"])
        links.append(
            _EdgeLink(
                node_id=node_id,
                supersedes=prior if r["superseded_at"] is not None or prior else None,
                originates_from=originates_from,
            )
        )
        last_id_by_round[r["round_id"]] = node_id
    return nodes, links


async def _ai_interview_nodes(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, scope: dict[str, Any]
) -> tuple[list[EvidenceNode], dict[str, str]]:
    rows = (
        await db.execute(
            text(
                "SELECT i.id, i.status, i.created_at, i.updated_at, i.session_id,"
                "       i.enrolment_id AS invite_enrolment_id,"
                "       s.status AS session_status, s.completed_at, s.started_at,"
                "       sc.scorecard_id, sc.composite_score, sc.created_at AS scorecard_created_at"
                "  FROM interview_invites i"
                "  LEFT JOIN sessions s ON s.id = i.session_id"
                "  LEFT JOIN scorecards sc ON sc.session_id = i.session_id"
                " WHERE i.company_id = :c AND i.applicant_id = :a AND i.deleted_at IS NULL"
                "   AND i.status <> 'revoked'"
                "   AND (i.enrolment_id = :e OR (i.enrolment_id IS NULL AND :single))"
                " ORDER BY i.created_at"
            ),
            {
                "c": company_id, "a": scope["applicant_id"], "e": enrolment_id,
                "single": int(scope["live_applications"] or 0) <= 1,
            },
        )
    ).mappings().all()
    if not rows:
        return [], {}

    round_row = (
        await db.execute(
            text(
                "SELECT id, title FROM workflow_rounds"
                " WHERE workflow_id = :w AND kind = 'ai_interview' AND deleted_at IS NULL"
                " ORDER BY position LIMIT 1"
            ),
            {"w": scope["workflow_id"]},
        )
    ).mappings().first() if scope["workflow_id"] else None

    nodes: list[EvidenceNode] = []
    scorecard_id_to_invite: dict[str, str] = {}
    for r in rows:
        node_id = f"ai_interview:{r['id']}"
        purged = r["status"] == "completed" and r["session_id"] is None
        when = r["scorecard_created_at"] or r["completed_at"] or r["updated_at"] or r["created_at"]
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="ai_interview",
                stage=StageInfo(
                    name="interview",
                    round_id=str(round_row["id"]) if round_row else None,
                    round_title=round_row["title"] if round_row else None,
                    round_kind="ai_interview",
                ),
                source=SourceRef(table="interview_invites", id=str(r["id"])),
                provenance=Provenance(
                    produced_by="ai", method="AI interview scored automatically by the scorer model",
                    attribution=(
                        "direct" if r["invite_enrolment_id"] == enrolment_id
                        else "inferred_single_application"
                    ),
                ),
                occurred_at=r["created_at"], recorded_at=when,
                lifecycle="purged" if purged else "live",
                content=None if purged else {
                    "status": r["status"],
                    "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
                    "composite_0_10": float(r["composite_score"]) if r["composite_score"] is not None else None,
                },
                content_hidden_reason=(
                    "the AI interview session was purged under the 90-day retention rule"
                    if purged else None
                ),
                href=_node_href("ai_interview", scope["applicant_id"], enrolment_id),
            )
        )
        if r["scorecard_id"]:
            scorecard_id_to_invite[str(r["scorecard_id"])] = node_id
    return nodes, scorecard_id_to_invite


async def _interview_session_nodes(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, scope: dict[str, Any]
) -> list[EvidenceNode]:
    rows = (
        await db.execute(
            text(
                "SELECT s.id, s.round_id, s.title, s.starts_at, s.status, s.created_at,"
                "       s.cancelled_at, wr.title AS round_title, wr.kind AS round_kind,"
                "       wf.version AS workflow_version,"
                "       COALESCE(json_agg(COALESCE(u.full_name, 'Interviewer'))"
                "         FILTER (WHERE si.interviewer_user_id IS NOT NULL), '[]') AS panel"
                "  FROM interview_sessions s"
                "  JOIN workflow_rounds wr ON wr.id = s.round_id AND wr.company_id = s.company_id"
                "  LEFT JOIN workflows wf ON wf.id = wr.workflow_id"
                "  LEFT JOIN interview_session_interviewers si ON si.session_id = s.id"
                "  LEFT JOIN users u ON u.id = si.interviewer_user_id"
                " WHERE s.enrolment_id = :e AND s.company_id = :c"
                " GROUP BY s.id, wr.title, wr.kind, wf.version"
                " ORDER BY s.created_at"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().all()
    out: list[EvidenceNode] = []
    for r in rows:
        when = r["starts_at"] or r["created_at"]
        out.append(
            EvidenceNode(
                id=f"interview_session:{r['id']}",
                kind="interview_session",
                stage=StageInfo(
                    name="interview", round_id=str(r["round_id"]), round_title=r["round_title"],
                    round_kind=r["round_kind"], workflow_version=r["workflow_version"],
                ),
                source=SourceRef(table="interview_sessions", id=str(r["id"])),
                provenance=Provenance(produced_by="human", method="scheduled interview session"),
                occurred_at=when, recorded_at=when,
                lifecycle="withdrawn" if r["status"] == "cancelled" else "live",
                content={
                    "title": r["title"],
                    "starts_at": r["starts_at"].isoformat() if r["starts_at"] else None,
                    "status": r["status"],
                    "panel": list(r["panel"] or []),
                },
                href=_node_href("interview_session", scope["applicant_id"], enrolment_id),
            )
        )
    return out


async def _human_scorecard_nodes(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    enrolment_id: uuid.UUID,
    viewer_user_id: uuid.UUID,
    scope: dict[str, Any],
) -> tuple[list[EvidenceNode], list[_EdgeLink]]:
    bundle = await scorecards_for_enrolment(
        db, company_id=company_id, enrolment_id=enrolment_id, viewer_user_id=viewer_user_id
    )
    rounds = bundle.get("rounds", [])
    if not rounds:
        return [], []

    # Metadata only (ids), so this does not weaken the independence rule
    # above: `is_correction` is already unblinded by scorecards_for_enrolment,
    # and this only resolves WHICH scorecard a correction points at.
    corrects_rows = (
        await db.execute(
            text(
                "SELECT id, corrects_id FROM interviewer_scorecards"
                " WHERE enrolment_id = :e AND company_id = :c"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).all()
    corrects_by_id = {str(r[0]): str(r[1]) for r in corrects_rows if r[1] is not None}

    session_rows = (
        await db.execute(
            text(
                "SELECT si.scorecard_id, si.session_id FROM interview_session_interviewers si"
                "  JOIN interview_sessions s ON s.id = si.session_id AND s.company_id = si.company_id"
                " WHERE s.enrolment_id = :e AND si.company_id = :c AND si.scorecard_id IS NOT NULL"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).all()
    session_by_scorecard = {str(r[0]): str(r[1]) for r in session_rows}

    # PH4-D4 widened human scorecards to job_simulation/portfolio rounds too
    # (SCORABLE_ROUND_KINDS), so the round's actual kind — not an assumed
    # "human_review" — decides the stage, and a task-round card originates
    # from the task submission it reviews, not from an interview session.
    round_ids = [uuid.UUID(str(r["round_id"])) for r in rounds]
    round_meta_rows = (
        await db.execute(
            text(
                "SELECT wr.id, wr.kind, wf.version FROM workflow_rounds wr"
                " LEFT JOIN workflows wf ON wf.id = wr.workflow_id"
                " WHERE wr.id = ANY(:ids) AND wr.company_id = :c"
            ),
            {"ids": round_ids, "c": company_id},
        )
    ).all() if round_ids else []
    round_meta = {str(r[0]): (r[1], r[2]) for r in round_meta_rows}

    task_submission_rows = (
        await db.execute(
            text(
                "SELECT s.round_id, s.id, s.submitted_at FROM task_submissions s"
                " WHERE s.enrolment_id = :e AND s.company_id = :c AND s.submitted_at IS NOT NULL"
                " ORDER BY s.round_id, s.submitted_at"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).all()
    task_submissions_by_round: dict[str, list[tuple[Any, Any]]] = {}
    for round_id, sub_id, submitted_at in task_submission_rows:
        task_submissions_by_round.setdefault(str(round_id), []).append((submitted_at, sub_id))

    def _task_submission_before(round_id: str, when: datetime) -> str | None:
        candidates = [s for s in task_submissions_by_round.get(round_id, []) if s[0] <= when]
        return f"task_submission:{candidates[-1][1]}" if candidates else None

    nodes: list[EvidenceNode] = []
    links: list[_EdgeLink] = []
    for round_ in rounds:
        round_kind, workflow_version = round_meta.get(round_["round_id"], (None, None))
        for entry in round_.get("scorecards", []):
            if entry.get("submitted_at") is None:
                continue  # a draft, or a withdrawal that never became evidence
            card_id = entry["scorecard_id"]
            node_id = f"human_scorecard:{card_id}"
            hidden_reason: str | None = None
            content: dict[str, Any] | None = None
            # Redaction and independence are two different states with two
            # different renderings, never conflated: a redacted card's SCORES
            # survive by design (only the free text is nulled at the source),
            # so it gets full content and its state is `lifecycle="redacted"`
            # alone; `content_hidden_reason` is reserved for content actually
            # withheld — independence, where scores/summary are None too.
            if entry["scores"] is None and entry["summary"] is None and round_["hidden_until_you_submit"]:
                hidden_reason = (
                    "hidden while you owe your own scorecard for this round (independence)"
                )
            else:
                content = {
                    "interviewer": entry["interviewer_name"], "scores": entry["scores"],
                    "summary": entry["summary"], "is_correction": entry["is_correction"],
                    "corrected_after_peers_visible": entry["corrected_after_peers_visible"],
                }
            submitted_at = datetime.fromisoformat(entry["submitted_at"])
            is_task_round = round_kind in ("job_simulation", "portfolio")
            originates_from = (
                _task_submission_before(round_["round_id"], submitted_at) if is_task_round
                else session_by_scorecard.get(card_id) and f"interview_session:{session_by_scorecard[card_id]}"
            )
            nodes.append(
                EvidenceNode(
                    id=node_id,
                    kind="human_scorecard",
                    stage=StageInfo(
                        name=STAGE_OF_ROUND_KIND.get(round_kind or "human_review", "interview"),
                        round_id=round_["round_id"], round_title=round_["round_title"],
                        round_kind=round_kind, workflow_version=workflow_version,
                    ),
                    source=SourceRef(table="interviewer_scorecards", id=card_id),
                    provenance=Provenance(
                        produced_by="human",
                        actor=ActorRef(user_id=entry["interviewer_user_id"], name=entry["interviewer_name"],
                                       role="interviewer"),
                        method="scorecard against the round's frozen criteria",
                    ),
                    occurred_at=submitted_at,
                    recorded_at=submitted_at,
                    lifecycle=(
                        "redacted" if entry["redacted"]
                        else "superseded" if entry["superseded"]
                        else "live"
                    ),
                    content=content,
                    content_hidden_reason=hidden_reason,
                    href=_node_href("human_scorecard", scope["applicant_id"], enrolment_id),
                )
            )
            links.append(
                _EdgeLink(
                    node_id=node_id,
                    supersedes=(
                        f"human_scorecard:{corrects_by_id[card_id]}" if card_id in corrects_by_id else None
                    ),
                    originates_from=originates_from,
                )
            )
    return nodes, links


async def _task_submission_nodes(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, scope: dict[str, Any]
) -> tuple[list[EvidenceNode], list[_EdgeLink]]:
    """Metadata only — status, timing and consent state. Deliberately a new,
    slim query rather than ``job_tasks.for_enrolment``, which returns the
    candidate's answers and writes an audit row on every read (PH4-D4)."""
    rows = (
        await db.execute(
            text(
                "SELECT s.id, s.round_id, s.status, s.submitted_at, s.started_at, s.consented_at,"
                "       s.superseded_at, s.superseded_by_id, s.redacted_at, s.created_at,"
                "       wr.title AS round_title, wr.kind AS round_kind, wf.version AS workflow_version"
                "  FROM task_submissions s"
                "  JOIN workflow_rounds wr ON wr.id = s.round_id AND wr.company_id = s.company_id"
                "  LEFT JOIN workflows wf ON wf.id = wr.workflow_id"
                " WHERE s.enrolment_id = :e AND s.company_id = :c"
                " ORDER BY s.round_id, s.created_at"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().all()
    nodes: list[EvidenceNode] = []
    links: list[_EdgeLink] = []
    for r in rows:
        node_id = f"task_submission:{r['id']}"
        when = r["submitted_at"] or r["created_at"]
        withdrawn = _task_consent_withdrawn({"started_at": r["started_at"], "consented_at": r["consented_at"]})
        if r["redacted_at"] is not None:
            lifecycle, hidden = "redacted", "redacted on erasure"
        elif withdrawn:
            lifecycle, hidden = "consent_withdrawn", "consent for this task was withdrawn"
        elif r["status"] == "withdrawn":
            lifecycle, hidden = "withdrawn", None
        elif r["superseded_at"] is not None:
            lifecycle, hidden = "superseded", None
        else:
            lifecycle, hidden = "live", None
        content = None if hidden else {"status": r["status"]}
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="task_submission",
                stage=StageInfo(
                    name=STAGE_OF_ROUND_KIND.get(r["round_kind"], "assessment"),
                    round_id=str(r["round_id"]), round_title=r["round_title"],
                    round_kind=r["round_kind"], workflow_version=r["workflow_version"],
                ),
                source=SourceRef(table="task_submissions", id=str(r["id"])),
                provenance=Provenance(produced_by="candidate", method="submitted task work"),
                occurred_at=when, recorded_at=when,
                lifecycle=lifecycle, content=content, content_hidden_reason=hidden,
                href=_node_href("task_submission", scope["applicant_id"], enrolment_id),
            )
        )
        if r["superseded_by_id"] is not None:
            links.append(_EdgeLink(node_id=f"task_submission:{r['superseded_by_id']}", supersedes=node_id))
    return nodes, links


async def _offer_nodes(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    enrolment_id: uuid.UUID,
    scope: dict[str, Any],
    decision_nodes: list[EvidenceNode],
) -> tuple[list[EvidenceNode], list[_EdgeLink]]:
    """Slim query: status and timing only, never compensation figures."""
    rows = (
        await db.execute(
            text(
                "SELECT id, status, sent_at, responded_at, created_at,"
                "       created_by_user_id, decided_by_user_id"
                "  FROM offers WHERE enrolment_id = :e AND company_id = :c"
                " ORDER BY created_at"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().all()
    if not rows:
        return [], []
    hires_by_time = sorted(
        (n for n in decision_nodes if isinstance(n.content, dict) and n.content.get("outcome") == "hired"),
        key=lambda n: n.occurred_at,
    )
    nodes: list[EvidenceNode] = []
    links: list[_EdgeLink] = []
    for r in rows:
        node_id = f"offer:{r['id']}"
        when = r["responded_at"] or r["sent_at"] or r["created_at"]
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="offer",
                stage=StageInfo(name="offer"),
                source=SourceRef(table="offers", id=str(r["id"])),
                provenance=Provenance(produced_by="human", method="an offer HR prepared and sent"),
                occurred_at=r["created_at"], recorded_at=when,
                lifecycle="withdrawn" if r["status"] == "withdrawn" else "live",
                content={"status": r["status"], "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None},
                href=_node_href("offer", scope["applicant_id"], enrolment_id),
            )
        )
        follows = next(
            (n.id for n in reversed(hires_by_time) if n.occurred_at <= r["created_at"]), None
        )
        links.append(_EdgeLink(node_id=node_id, follows_decision_of=follows))
    return nodes, links


#: AR-5: ``stage_transitions.reason`` is free text a person typed about the
#: candidate, and it is deliberately NOT redacted on erasure
#: (``erasure_executor.py`` EXCLUDED_TABLES) — the ledger's rationale is meant
#: to survive for audit. That is a defensible choice for HR reading their own
#: history, but not for a NEW surface that also names the candidate
#: (``candidate.erased``/``[redacted]``) right next to that prose and ships it
#: to an LLM (``get_decision_trace``). So this module withholds it — never the
#: source row, only this graph's rendering of it — once the candidate is
#: erased. ``reason_code``/``reason_label`` stay: they are the company's fixed
#: taxonomy, not personal data.
_ERASED_REASON_HIDDEN = "rationale withheld: the candidate has been erased (AR-5)"

_ERASED_REASON_OMISSION = OmittedItem(
    kind="decision_reason",
    reason=(
        "The candidate has been erased (AR-5). stage_transitions.reason is not "
        "itself redacted at the source, but its free text is withheld from this "
        "graph and from the copilot once the candidate is erased."
    ),
)


async def _decision_nodes(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, scope: dict[str, Any], erased: bool
) -> list[EvidenceNode]:
    rows = (
        await db.execute(
            text(
                "SELECT t.id, t.from_status, t.to_status, t.actor_user_id, t.occurred_at,"
                "       t.reason, t.reason_code, t.reason_label, u.full_name AS actor_name"
                "  FROM stage_transitions t"
                "  LEFT JOIN users u ON u.id = t.actor_user_id"
                " WHERE t.enrolment_id = :e AND t.company_id = :c"
                "   AND t.to_status = ANY(:decisions) AND NOT t.automated"
                " ORDER BY t.occurred_at, t.id"
            ),
            {"e": enrolment_id, "c": company_id, "decisions": sorted(_DECISION_STATUSES)},
        )
    ).mappings().all()
    return [
        EvidenceNode(
            id=f"decision:{r['id']}",
            kind="decision",
            stage=StageInfo(name="decision"),
            source=SourceRef(table="stage_transitions", id=str(r["id"])),
            provenance=Provenance(
                produced_by="human",
                actor=ActorRef(user_id=str(r["actor_user_id"]), name=r["actor_name"]) if r["actor_user_id"] else None,
                method="recorded final decision",
            ),
            occurred_at=r["occurred_at"], recorded_at=r["occurred_at"],
            content={
                "outcome": r["to_status"], "reversal": r["from_status"] == "hired" and r["to_status"] == "rejected",
                "reason_code": r["reason_code"], "reason_label": r["reason_label"],
                "reason": None if erased else r["reason"],
            },
            content_hidden_reason=_ERASED_REASON_HIDDEN if erased else None,
            href=_node_href("decision", scope["applicant_id"], enrolment_id),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


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
    raise :class:`EvidenceGraphError` (404). Any audit row is the CALLER's
    responsibility — this function must stay safe for a read-only tool to
    call directly.
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


def decision_trace_from_graph(graph: EvidenceGraph, *, decision: _DecisionRow, decision_id: int) -> Any:
    """Pure: the trace view over an already-assembled graph.

    Imported lazily to avoid a circular type reference at module load —
    kept as a plain function so it stays trivially unit-testable over
    fixtures, per the design's "pure assemble()/trace()" requirement.
    """
    from app.schemas.evidence import DecisionTrace  # noqa: PLC0415

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
