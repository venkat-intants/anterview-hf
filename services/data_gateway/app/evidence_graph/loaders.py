"""Loaders — one per node kind, plus the erasure/href/stage tables they share.

Every query binds ``company_id`` explicitly, on top of whatever composite FK
already enforces it (defence in depth).

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

AR-5: ``stage_transitions.reason`` (a decision's free-text rationale) is
deliberately NOT redacted at its source on erasure (``erasure_executor.py``
EXCLUDED_TABLES) — that is a defensible choice for HR reading their own
history, but not for a NEW surface that also names the candidate
(``candidate.erased``/``[redacted]``) right next to that prose and ships it to
an LLM (``get_decision_trace``). ``_decision_nodes`` therefore withholds it —
never the source row, only this graph's rendering of it — once the candidate
is erased. See ``_ERASED_REASON_HIDDEN``/``_ERASED_REASON_OMISSION``.

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
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.code_evidence import summary_for_enrolments as code_evidence_summary
from app.interviewer_scorecards import scorecards_for_enrolment
from app.job_tasks import consent_withdrawn as _task_consent_withdrawn
from app.schemas.evidence import (
    ActorRef,
    EvidenceNode,
    EvidenceNodeKind,
    EvidenceStage,
    OmittedItem,
    Provenance,
    SourceRef,
    StageInfo,
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
    """What one node points at, for :func:`app.evidence_graph.assemble.assemble`
    to turn into edges.

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


_DECISION_STATUSES: frozenset[str] = frozenset({"hired", "rejected"})


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


def _latest_hire_at_or_before(decision_nodes: list[EvidenceNode], when: datetime) -> str | None:
    """Pure: which hire (if any) an offer created/recorded at ``when`` follows
    — the most recent hire decision at or before it.

    A reversal (a later reject) does not retroactively un-link an offer from
    the hire it followed; a LATER hire recorded after a reversal is a new
    decision, and only an offer created at or after IT follows that one.
    Split out so the selection itself — not just ``assemble()`` turning an
    already-chosen link into an edge — is directly unit-testable.
    """
    hires_by_time = sorted(
        (n for n in decision_nodes if isinstance(n.content, dict) and n.content.get("outcome") == "hired"),
        key=lambda n: n.occurred_at,
    )
    return next((n.id for n in reversed(hires_by_time) if n.occurred_at <= when), None)


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
        follows = _latest_hire_at_or_before(decision_nodes, r["created_at"])
        links.append(_EdgeLink(node_id=node_id, follows_decision_of=follows))
    return nodes, links


#: AR-5: see the module docstring's AR-5 section.
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
