"""Workflow authoring — Group C, features C1 to C5.

What a workflow is
------------------
A versioned hiring process attached to one requisition: ordered rounds, each
with a type, an advance threshold, and the competencies it assesses. Candidates
who apply to the role are enrolled into the published version and the runner
walks them through it.

Three rules this module exists to enforce
-----------------------------------------
**Published workflows are immutable.** Editing a published workflow does not
mutate it — :func:`clone_for_edit` produces version *n+1* as a draft. Candidates
already enrolled keep running the version they started, because a threshold
changed halfway through a cohort would otherwise re-grade people who had already
sat the round.

**Criteria are frozen, not referenced.** ``RoleProfile`` is derived at runtime by
``shared/intelligence`` and never persisted. Copying id, name, kind and weight
into ``round_criteria`` at authoring time is what makes the immutability promise
real: a re-derived profile cannot retroactively change what a published workflow
measures.

**A workflow cannot end a candidacy.** There is no round kind, threshold or
setting here that rejects anyone. ``pass_threshold`` decides *advancement*; a
candidate below it is held, which is neutral and non-terminal. That is D-05, and
it is enforced by the absence of any mechanism rather than by a default.

Branches (PH4-O3)
-----------------
A round has up to three exits: the pass branch (``on_pass_next_round_id``, the
chain add/remove/reorder maintain), a fast-track branch for a score at or above
a higher bar, and a fail branch that routes a candidate below the threshold to
another round instead of holding them. Every exit ends at another round, a hold,
or the final human decision — there is no "rejected" destination to point at.
:func:`route_after_result` is the ONE place a result becomes a destination; the
runner and the O2 simulation both call it, so a dry run tests the real logic.

Review (PH4-O6)
---------------
A version is also reviewed: ``review_status`` goes draft → in_review → approved
(or changes_requested). Only an approved version is published, and a version
under review or approved cannot be edited — see ``app/workflow_review.py`` and
migration a2b4c6d8e0f1, which holds both rules at the database too.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

# Job simulations and portfolio rounds (PH4-D4) answer the two deferral
# reasons Phase 2 gave for leaving them out: there is no grader, because a
# named person scores a submission against the round's own FROZEN
# round_criteria (interviewer_scorecards, widened to these two kinds — never
# a threshold, never a model); and the prompt-injection surface a candidate's
# own writing would be to an LLM is closed by construction, because no AI
# module (shared/agents/*, app/agents/*) may reference round_tasks,
# task_submissions or task_responses (AST-tested).
ROUND_KINDS: frozenset[str] = frozenset(
    {"mcq", "coding", "ai_interview", "human_review", "job_simulation", "portfolio"}
)

# Rounds whose content comes from the existing exam machinery, which already has
# authoring, AI generation, CSV import and graders.
EXAM_BACKED_KINDS: frozenset[str] = frozenset({"mcq", "coding"})

# Rounds graded by a model rather than by an answer key or a person.
AI_GRADED_KINDS: frozenset[str] = frozenset({"ai_interview"})

# Task-configured rounds (PH4-D4): a candidate submits work — text, a file or
# a link — that a named person evaluates. app/job_tasks.py owns their config
# and lifecycle.
TASK_KINDS: frozenset[str] = frozenset({"job_simulation", "portfolio"})

# Every round kind a threshold cannot decide: a person always records the
# outcome, never a score against a pass mark. interviewer_scorecards.py's
# SCORABLE_ROUND_KINDS is exactly this set, and the decision queue, the
# requisition dashboard and enrolment_awaits_human all key on it (or its SQL
# equivalent) rather than repeating 'human_review' three different ways.
HUMAN_EVALUATED_KINDS: frozenset[str] = frozenset({"human_review"}) | TASK_KINDS

WORKFLOW_STATUSES: frozenset[str] = frozenset({"draft", "published", "archived"})

MAX_ROUNDS = 12

# Offset used to park positions during a reorder or a removal, so the partial
# unique index on (workflow_id, position) does not reject the intermediate
# states. Comfortably above MAX_ROUNDS and still non-negative.
_POSITION_PARK = 1000


class WorkflowError(Exception):
    """Refused for a reason the caller should show the user verbatim."""


#: Review states in which a version's content is frozen for its reviewer.
REVIEW_LOCKED: frozenset[str] = frozenset({"in_review", "approved"})

#: The fields that make up a round's routing, as the builder edits them.
BRANCH_FIELDS: tuple[str, ...] = (
    "on_fail_next_round_id", "fast_track_min_percent", "on_fast_track_next_round_id",
)


# ---------------------------------------------------------------------------
# Routing (PH4-O3) — the one place a round result becomes a destination
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Route:
    """Where a result sends a candidate.

    ``kind`` is ``advance`` (to ``next_round_id``), ``complete`` (the workflow
    is finished: the final human decision) or ``hold`` (stopped for a person).
    ``branch`` says which exit was taken: ``pass``, ``fast_track`` or ``fail``.
    Note what ``kind`` can never be.
    """

    kind: str
    branch: str
    next_round_id: str | None = None


def route_after_result(round_: dict[str, Any], *, passed: bool, percent: float | None) -> Route:
    """The destination of a result on ``round_``. Pure — no database.

    Called by the runner for real candidates and by the simulation for
    synthetic ones, so a dry run exercises exactly the logic that will run.
    """
    if passed:
        fast_to = round_.get("on_fast_track_next_round_id")
        fast_min = round_.get("fast_track_min_percent")
        if fast_to and fast_min is not None and percent is not None and percent >= float(fast_min):
            return Route("advance", "fast_track", str(fast_to))
        nxt = round_.get("on_pass_next_round_id")
        return Route("advance", "pass", str(nxt)) if nxt else Route("complete", "pass")
    fail_to = round_.get("on_fail_next_round_id")
    return Route("advance", "fail", str(fail_to)) if fail_to else Route("hold", "fail")


def round_edges(r: dict[str, Any]) -> list[tuple[str, str]]:
    """Every (branch, target round id) leaving a round."""
    out: list[tuple[str, str]] = []
    for branch, key in (("pass", "on_pass_next_round_id"),
                        ("fast_track", "on_fast_track_next_round_id"),
                        ("fail", "on_fail_next_round_id")):
        if r.get(key):
            out.append((branch, str(r[key])))
    return out


_BRANCH_WORDS = {"pass": "pass", "fast_track": "fast-track", "fail": "below-threshold"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
@dataclass
class CoverageRow:
    competency_id: str
    competency_name: str
    profile_weight: float
    assessed_in: list[str] = field(default_factory=list)

    @property
    def times_assessed(self) -> int:
        return len(self.assessed_in)


@dataclass
class ValidationReport:
    """What is wrong, and what merely deserves a look.

    ``errors`` block publication. ``warnings`` do not — a coverage gap can be a
    deliberate choice, and refusing to publish over one would make the checker
    something HR learns to route around rather than read.
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    coverage: list[CoverageRow] = field(default_factory=list)

    @property
    def publishable(self) -> bool:
        return not self.errors

    @property
    def weighted_coverage(self) -> float | None:
        """Share of the role's competency WEIGHT that at least one round assesses.

        The per-competency rows say what is missing; this says how much it
        matters. Missing a 0.05 competency and missing a 0.30 one are both "one
        gap", and only the weight tells them apart. None with no role model.
        """
        total = sum(c.profile_weight for c in self.coverage)
        if total <= 0:
            return None
        covered = sum(c.profile_weight for c in self.coverage if c.times_assessed > 0)
        return round(covered / total, 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "publishable": self.publishable,
            "errors": self.errors,
            "warnings": self.warnings,
            "weighted_coverage": self.weighted_coverage,
            "coverage": [
                {
                    "competency_id": c.competency_id,
                    "competency_name": c.competency_name,
                    "profile_weight": c.profile_weight,
                    "times_assessed": c.times_assessed,
                    "assessed_in": c.assessed_in,
                }
                for c in self.coverage
            ],
        }


def branch_errors(rounds: list[dict[str, Any]]) -> list[str]:
    """What is wrong with each round's branches (PH4-O3). Blocking errors.

    Checked per round, before the graph as a whole: a branch that points
    nowhere makes every graph check below meaningless.
    """
    errors: list[str] = []
    by_id = {str(r["id"]): r for r in rounds}
    for r in rounds:
        title = r.get("title") or "(untitled)"
        for branch, target in round_edges(r):
            if target not in by_id:
                errors.append(
                    f"{title}: the {_BRANCH_WORDS[branch]} branch points at a round that is "
                    "not in this workflow."
                )
            elif target == str(r["id"]):
                errors.append(f"{title}: the {_BRANCH_WORDS[branch]} branch points at itself.")
        fast_min = r.get("fast_track_min_percent")
        fast_to = r.get("on_fast_track_next_round_id")
        if (fast_min is None) != (not fast_to):
            errors.append(f"{title}: a fast-track needs both a score and a destination.")
        if fast_to:
            if r["kind"] in HUMAN_EVALUATED_KINDS:
                errors.append(
                    f"{title}: a round a person evaluates has no score, so it cannot "
                    "fast-track anyone."
                )
            elif r.get("pass_threshold") is not None and fast_min is not None and (
                float(fast_min) <= float(r["pass_threshold"])
            ):
                errors.append(
                    f"{title}: the fast-track score ({float(fast_min):.0f}%) must be above the "
                    f"advance threshold ({float(r['pass_threshold']):.0f}%), or it is just the "
                    "pass branch under another name."
                )
    return errors


def graph_problems(rounds: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Cycles and unreachable rounds across EVERY branch. Returns (cycles, unreachable).

    A cycle would move a candidate between rounds forever, and there is no
    runtime guard that could tell a deliberate retake from a loop — so any
    cycle, through any mix of branches, is refused. Unreachable rounds are those
    no path from the first round can get to: dead configuration that would sit
    in every candidate's workflow and never run.
    """
    if not rounds:
        return [], []
    by_id = {str(r["id"]): r for r in rounds}
    first = str(min(rounds, key=lambda r: r["position"])["id"])

    reached: set[str] = set()
    stack = [first]
    while stack:
        cur = stack.pop()
        if cur in reached or cur not in by_id:
            continue
        reached.add(cur)
        stack.extend(t for _b, t in round_edges(by_id[cur]))
    unreachable = sorted(by_id[i].get("title") or i for i in by_id if i not in reached)

    cycles: list[str] = []
    state: dict[str, int] = {}  # 1 = on the current path, 2 = finished

    def visit(node: str, path: list[str]) -> None:
        state[node] = 1
        for _b, nxt in round_edges(by_id[node]):
            if nxt not in by_id:
                continue
            if state.get(nxt) == 1:
                loop = path[path.index(nxt):] + [nxt] if nxt in path else [node, nxt]
                names = " → ".join(by_id[x].get("title") or x for x in loop)
                if names not in cycles:
                    cycles.append(names)
            elif state.get(nxt) is None:
                visit(nxt, path + [nxt])
        state[node] = 2

    for rid in sorted(by_id, key=lambda i: by_id[i]["position"]):
        if state.get(rid) is None:
            visit(rid, [rid])
    return cycles, unreachable


def validate_chain(rounds: list[dict[str, Any]]) -> list[str]:
    """Structural checks on the rounds and every branch. Returns blocking errors.

    The cycle check matters more than it looks: a cycle would put the runner in
    an infinite loop moving one candidate between two rounds forever. With
    branches (PH4-O3) it has to cover every exit, not just the pass chain.
    """
    errors: list[str] = []
    if not rounds:
        return ["A workflow needs at least one round."]
    if len(rounds) > MAX_ROUNDS:
        return [f"A workflow cannot have more than {MAX_ROUNDS} rounds."]

    for r in rounds:
        title = r.get("title") or "(untitled)"
        if r["kind"] not in ROUND_KINDS:
            errors.append(f"{title}: unknown round type {r['kind']!r}.")
        if r["kind"] in EXAM_BACKED_KINDS and not r.get("exam_round_id"):
            errors.append(f"{title}: a {r['kind']} round needs questions before publishing.")
        if r["kind"] not in HUMAN_EVALUATED_KINDS and r.get("pass_threshold") is None:
            errors.append(f"{title}: needs an advance threshold.")

    edge_errors = branch_errors(rounds)
    errors.extend(edge_errors)
    if edge_errors:
        return errors  # a dangling branch makes the graph checks meaningless

    cycles, unreachable = graph_problems(rounds)
    for loop in cycles:
        errors.append(f"The rounds form a loop — a candidate would never finish: {loop}.")
    if unreachable:  # independent of any loop: report both, fix both in one pass
        errors.append("Unreachable from the first round: " + ", ".join(unreachable))
    return errors


# An exam-backed round is only takeable when its exam round is published and has
# questions: exam_take refuses a link to anything else and the candidate reads
# "This exam link isn't valid". Checked at publish so a workflow cannot go live
# pointing at one, and again by the runner before it mints a link, because an
# exam round can be changed after the workflow is published.
_EXAM_ROUND_READINESS_SQL = """
SELECT er.id, er.exam_id, er.status,
       (SELECT count(*) FROM exam_questions q
          JOIN exam_sections s ON s.id = q.section_id AND s.deleted_at IS NULL
         WHERE s.round_id = er.id AND q.deleted_at IS NULL)
     + (SELECT count(*) FROM coding_questions c
          JOIN exam_sections s ON s.id = c.section_id AND s.deleted_at IS NULL
         WHERE s.round_id = er.id AND c.deleted_at IS NULL) AS questions
  FROM exam_rounds er
 WHERE er.id = ANY(:ids) AND er.deleted_at IS NULL
"""


async def exam_round_readiness(
    db: AsyncSession, exam_round_ids: list[Any]
) -> dict[str, dict[str, Any]]:
    """Status and live question count of each exam round, keyed by id."""
    ids = [uuid.UUID(str(i)) for i in exam_round_ids if i]
    if not ids:
        return {}
    rows = (
        await db.execute(text(_EXAM_ROUND_READINESS_SQL), {"ids": ids})
    ).mappings().all()
    return {
        str(r["id"]): {
            "exam_id": r["exam_id"],
            "status": r["status"],
            "questions": int(r["questions"] or 0),
        }
        for r in rows
    }


def exam_round_problem(readiness: dict[str, Any] | None) -> str | None:
    """Why a candidate could not open this exam round, or None when they can."""
    if readiness is None:
        return "no longer exists"
    if readiness.get("status") != "published":
        return "is still a draft — publish it in the exam editor"
    if not readiness.get("questions"):
        return "has no questions"
    return None


def exam_round_errors(
    rounds: list[dict[str, Any]], readiness: dict[str, dict[str, Any]]
) -> list[str]:
    """Blocking errors for exam-backed rounds a candidate could not open.

    A round with nothing attached is left to validate_chain, which already says
    it needs questions.
    """
    errors: list[str] = []
    for r in rounds:
        if r["kind"] not in EXAM_BACKED_KINDS or not r.get("exam_round_id"):
            continue
        problem = exam_round_problem(readiness.get(str(r["exam_round_id"])))
        if problem is not None:
            title = r.get("title") or "(untitled)"
            errors.append(
                f"{title}: the attached exam round {problem}, "
                "so candidates' exam links would not open."
            )
    return errors


def build_coverage(
    profile_competencies: list[dict[str, Any]],
    criteria_by_round: dict[str, list[dict[str, Any]]],
    round_titles: dict[str, str],
) -> tuple[list[CoverageRow], list[str]]:
    """Which of the role's competencies each round assesses.

    This is the one check in the builder that catches a badly designed hiring
    process before a candidate meets it: a competency the role needs that no
    round measures, or one measured three times by accident.
    """
    rows: list[CoverageRow] = []
    warnings: list[str] = []
    for comp in profile_competencies:
        cid = comp["id"]
        where = [
            round_titles.get(rid, rid)
            for rid, crits in criteria_by_round.items()
            if any(c["competency_id"] == cid for c in crits)
        ]
        rows.append(
            CoverageRow(
                competency_id=cid,
                competency_name=comp.get("name", cid),
                profile_weight=float(comp.get("weight", 0)),
                assessed_in=sorted(where),
            )
        )

    for row in rows:
        if row.times_assessed == 0:
            warnings.append(
                f"'{row.competency_name}' carries weight {row.profile_weight:.2f} in this role "
                "but no round assesses it — add it to a round, or accept that it stays out "
                "of the composite."
            )
        elif row.times_assessed > 2:
            warnings.append(
                f"'{row.competency_name}' is assessed in {row.times_assessed} rounds "
                "(" + ", ".join(row.assessed_in) + ") — deliberate reinforcement is fine, "
                "flagged so it is a choice rather than an accident."
            )
    return rows, warnings


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
async def load_rounds(db: AsyncSession, workflow_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT id, position, title, kind, pass_threshold, time_limit_seconds,"
                "       deadline_days, on_pass_next_round_id, exam_round_id,"
                "       on_fail_next_round_id, fast_track_min_percent,"
                "       on_fast_track_next_round_id"
                "  FROM workflow_rounds"
                " WHERE workflow_id = :w AND deleted_at IS NULL"
                " ORDER BY position"
            ),
            {"w": workflow_id},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def load_criteria(
    db: AsyncSession, round_ids: list[uuid.UUID]
) -> dict[str, list[dict[str, Any]]]:
    if not round_ids:
        return {}
    rows = (
        await db.execute(
            text(
                "SELECT round_id, competency_id, competency_name, competency_kind,"
                "       weight, anchors, probes"
                "  FROM round_criteria WHERE round_id = ANY(:ids)"
                " ORDER BY weight DESC"
            ),
            {"ids": round_ids},
        )
    ).mappings().all()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r["round_id"]), []).append(
            {
                "competency_id": r["competency_id"],
                "competency_name": r["competency_name"],
                "competency_kind": r["competency_kind"],
                "weight": float(r["weight"]),
                "anchors": r["anchors"],
                "probes": r["probes"],
            }
        )
    return out


async def published_workflow(
    db: AsyncSession, requisition_id: uuid.UUID
) -> dict[str, Any] | None:
    """The live workflow for a requisition, or None if nothing is published."""
    row = (
        await db.execute(
            text(
                "SELECT * FROM workflows"
                " WHERE requisition_id = :r AND status = 'published' AND deleted_at IS NULL"
            ),
            {"r": requisition_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def round_rubric_for_generation(
    db: AsyncSession, *, company_id: uuid.UUID, workflow_round_id: uuid.UUID
) -> dict[str, Any] | None:
    """The published criteria of one workflow round, ready for the generator (C3).

    None when the round is not this company's, or carries no criteria — the
    caller then generates from the role model as before, because a round with
    nothing selected has expressed no preference.

    Authoring a round's questions and scoring its interview read the same rows,
    so an exam cannot drift onto competencies the round does not assess.
    """
    row = (
        await db.execute(
            text(
                "SELECT wr.id, wr.title, wr.workflow_id, r.title AS job_title"
                "  FROM workflow_rounds wr"
                "  JOIN workflows w ON w.id = wr.workflow_id"
                "  JOIN job_requisitions r ON r.id = w.requisition_id"
                " WHERE wr.id = :i AND wr.company_id = :c AND wr.deleted_at IS NULL"
            ),
            {"i": workflow_round_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        return None
    criteria = (await load_criteria(db, [workflow_round_id])).get(str(workflow_round_id), [])
    if not criteria:
        return None
    return {
        "criteria": criteria,
        "round_title": row["title"],
        "workflow_id": str(row["workflow_id"]),
        "round_id": str(row["id"]),
        "job_title": row["job_title"],
    }


async def rubric_for_exam(
    db: AsyncSession, *, company_id: uuid.UUID, exam_id: uuid.UUID
) -> dict[str, Any] | None:
    """The rubric of the workflow round this exam is used by — when there is
    exactly one (C3).

    Question authoring happens on the exam, not inside the workflow builder, so
    the screen doing the generating does not know which round it is for. An
    exam used by one round is unambiguous and gets that round's competencies;
    an exam shared by several rounds is not, and guessing would generate a
    technical round's questions for an aptitude round. Same rule the runner
    uses to attribute a hand-assigned exam.
    """
    rounds = (
        await db.execute(
            text(
                "SELECT wr.id FROM workflow_rounds wr"
                "  JOIN exam_rounds er ON er.id = wr.exam_round_id"
                " WHERE er.exam_id = :e AND wr.company_id = :c"
                "   AND wr.deleted_at IS NULL AND er.deleted_at IS NULL"
            ),
            {"e": exam_id, "c": company_id},
        )
    ).all()
    if len(rounds) != 1:
        return None
    return await round_rubric_for_generation(
        db, company_id=company_id, workflow_round_id=uuid.UUID(str(rounds[0][0]))
    )


async def scoring_on_apply_enabled(db: AsyncSession, requisition_id: uuid.UUID) -> bool:
    """Whether resumes for this opening are ATS-scored as they arrive (C9).

    True unless the opening's PUBLISHED workflow says otherwise: an opening
    with no workflow yet still gets its applicants scored, which is what makes
    the applicant list useful before anyone has built a process.
    """
    off = await db.scalar(
        text(
            "SELECT 1 FROM workflows"
            " WHERE requisition_id = :r AND status = 'published' AND deleted_at IS NULL"
            "   AND NOT auto_score_on_apply"
        ),
        {"r": requisition_id},
    )
    return off is None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
async def create_draft(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    requisition_id: uuid.UUID,
    created_by: uuid.UUID | None,
    name: str | None = None,
    profile: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Start a new draft at the next version number. Caller commits."""
    existing_draft = await db.scalar(
        text(
            "SELECT id FROM workflows WHERE requisition_id = :r AND status = 'draft'"
            "   AND deleted_at IS NULL LIMIT 1"
        ),
        {"r": requisition_id},
    )
    if existing_draft is not None:
        raise WorkflowError(
            "This opening already has a draft workflow. Edit or discard it first."
        )

    next_version = int(
        await db.scalar(
            text("SELECT COALESCE(max(version), 0) + 1 FROM workflows WHERE requisition_id = :r"),
            {"r": requisition_id},
        )
        or 1
    )
    wf_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "INSERT INTO workflows (id, company_id, requisition_id, version, status, name,"
            " role_profile_id, domain_family, profile_source, created_by_user_id,"
            " created_at, updated_at)"
            " VALUES (:i,:c,:r,:v,'draft',:n,:pid,:df,:ps,:u,:t,:t)"
        ),
        {"i": wf_id, "c": company_id, "r": requisition_id, "v": next_version, "n": name,
         "pid": (profile or {}).get("profile_id"), "df": (profile or {}).get("domain_family"),
         "ps": (profile or {}).get("source"), "u": created_by, "t": now},
    )
    log.info(
        "workflow.draft.created",
        workflow_id=str(wf_id), requisition_id=str(requisition_id), version=next_version,
    )
    return wf_id


async def add_round(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    workflow_id: uuid.UUID,
    title: str,
    kind: str,
    pass_threshold: float | None = None,
    time_limit_seconds: int | None = None,
    deadline_days: int = 7,
    exam_round_id: uuid.UUID | None = None,
    criteria: list[dict[str, Any]] | None = None,
) -> uuid.UUID:
    """Append a round and link the previous one to it. Caller commits.

    Appending rewires ``on_pass_next_round_id`` on the current last round, so
    the chain stays a chain without the caller having to think about pointers.
    """
    if kind not in ROUND_KINDS:
        raise WorkflowError(f"Unknown round type {kind!r}.")
    await _assert_draft(db, workflow_id)

    existing = await load_rounds(db, workflow_id)
    if len(existing) >= MAX_ROUNDS:
        raise WorkflowError(f"A workflow cannot have more than {MAX_ROUNDS} rounds.")

    position = (max((r["position"] for r in existing), default=-1)) + 1
    round_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
            " pass_threshold, time_limit_seconds, deadline_days, exam_round_id,"
            " created_at, updated_at)"
            " VALUES (:i,:c,:w,:p,:t,:k,:th,:tl,:dd,:er,:n,:n)"
        ),
        {"i": round_id, "c": company_id, "w": workflow_id, "p": position, "t": title,
         "k": kind, "th": pass_threshold, "tl": time_limit_seconds, "dd": deadline_days,
         "er": exam_round_id, "n": now},
    )
    if existing:
        tail = max(existing, key=lambda r: r["position"])
        await db.execute(
            text(
                "UPDATE workflow_rounds SET on_pass_next_round_id = :nxt, updated_at = :n"
                " WHERE id = :i"
            ),
            {"nxt": round_id, "i": tail["id"], "n": now},
        )
    if criteria:
        await set_round_criteria(
            db, company_id=company_id, round_id=round_id, criteria=criteria,
            _skip_draft_check=True,
        )
    return round_id


async def update_round(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    round_id: uuid.UUID,
    fields: dict[str, Any],
) -> None:
    """Edit a draft round's own settings. Caller commits.

    Deliberately cannot change ``workflow_id``, ``position`` or
    ``on_pass_next_round_id`` — the chain is maintained by add/remove/reorder so
    it stays valid by construction rather than by the caller remembering to
    rewire it. The OTHER two branches (PH4-O3) are set here, and a destination
    must be a live round of this same workflow.
    """
    await _assert_draft(db, workflow_id)
    allowed = {"title", "kind", "pass_threshold", "time_limit_seconds",
               "deadline_days", "exam_round_id", *BRANCH_FIELDS}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    for key in ("on_fail_next_round_id", "on_fast_track_next_round_id"):
        target = updates.get(key)
        if target is None:
            continue
        if str(target) == str(round_id):
            raise WorkflowError("A branch cannot point at its own round.")
        ok = await db.scalar(
            text(
                "SELECT 1 FROM workflow_rounds"
                " WHERE id = :t AND workflow_id = :w AND deleted_at IS NULL"
            ),
            {"t": target, "w": workflow_id},
        )
        if not ok:
            raise WorkflowError("A branch can only point at another round of this workflow.")
    # Fast-track travels as a pair. A patch may send one half — a new score for
    # the same destination — so the other half comes from the round as it is;
    # if either half ends up empty, both are cleared.
    fast_keys = ("fast_track_min_percent", "on_fast_track_next_round_id")
    if any(k in updates for k in fast_keys):
        current: dict[str, Any] = {} if all(k in updates for k in fast_keys) else dict(
            (await db.execute(
                text(
                    "SELECT fast_track_min_percent, on_fast_track_next_round_id"
                    "  FROM workflow_rounds WHERE id = :r AND workflow_id = :w"
                ),
                {"r": round_id, "w": workflow_id},
            )).mappings().first() or {}
        )
        fast_min = updates.get("fast_track_min_percent", current.get("fast_track_min_percent"))
        fast_to = updates.get(
            "on_fast_track_next_round_id", current.get("on_fast_track_next_round_id")
        )
        if fast_min is None or not fast_to:
            fast_min, fast_to = None, None
        updates["fast_track_min_percent"] = fast_min
        updates["on_fast_track_next_round_id"] = fast_to
    if "kind" in updates and updates["kind"] not in ROUND_KINDS:
        raise WorkflowError(f"Unknown round type {updates['kind']!r}.")
    old_kind: str | None = None
    if "kind" in updates:
        old_kind = await db.scalar(
            text("SELECT kind FROM workflow_rounds WHERE id = :r AND workflow_id = :w"),
            {"r": round_id, "w": workflow_id},
        )
        # A new type clears what cannot apply to it, rather than leaving a
        # stale value the canvas no longer shows: an MCQ turned into a human
        # review kept its exam, threshold and time limit, invisible in the
        # panel and still sitting on the row. Nothing is INVENTED for the new
        # type — a scored round left without a threshold is flagged by
        # validation, which is where HR is told to set one.
        if updates["kind"] in HUMAN_EVALUATED_KINDS:
            updates["pass_threshold"] = None
            updates["exam_round_id"] = None
            # PH4-D4: job_simulation keeps time_limit_seconds — it is the one
            # timer a candidate sees while working; portfolio (no timer, only
            # a due date) and human_review (no candidate-side clock at all)
            # both clear it.
            if updates["kind"] != "job_simulation":
                updates["time_limit_seconds"] = None
            # No score, so nothing to fast-track on.
            updates["fast_track_min_percent"] = None
            updates["on_fast_track_next_round_id"] = None
        elif updates["kind"] not in EXAM_BACKED_KINDS:
            updates["exam_round_id"] = None
    sets = ", ".join(f"{k} = :{k}" for k in updates)
    await db.execute(
        text(
            f"UPDATE workflow_rounds SET {sets}, updated_at = :n"
            " WHERE id = :r AND workflow_id = :w AND deleted_at IS NULL"
        ),
        {**updates, "r": round_id, "w": workflow_id, "n": datetime.now(tz=UTC)},
    )
    # PH4-D4: moving AWAY from a task kind leaves no config behind for a kind
    # that can no longer read it — round_tasks_no_edit_when_published would
    # refuse this DELETE on a published/in-review workflow, but update_round
    # already required a draft (_assert_draft, above), so it is never reached.
    if (
        old_kind is not None and old_kind in TASK_KINDS
        and updates.get("kind") is not None and updates["kind"] not in TASK_KINDS
    ):
        await db.execute(text("DELETE FROM round_tasks WHERE round_id = :r"), {"r": round_id})
        await db.execute(
            text("DELETE FROM round_task_materials WHERE round_id = :r"), {"r": round_id}
        )


async def remove_round(
    db: AsyncSession, *, workflow_id: uuid.UUID, round_id: uuid.UUID
) -> None:
    """Delete a draft round and heal the chain around it. Caller commits.

    The predecessor is re-pointed at the deleted round's successor rather than
    left dangling. Without that, removing a middle round would strand everything
    after it — the validator would catch it, but only after the author had
    already lost their work.
    """
    await _assert_draft(db, workflow_id)
    rounds = await load_rounds(db, workflow_id)
    target = next((r for r in rounds if str(r["id"]) == str(round_id)), None)
    if target is None:
        raise WorkflowError("Round not found in this workflow.")

    now = datetime.now(tz=UTC)
    successor = target["on_pass_next_round_id"]
    await db.execute(
        text(
            "UPDATE workflow_rounds SET on_pass_next_round_id = :nxt, updated_at = :n"
            " WHERE workflow_id = :w AND on_pass_next_round_id = :r"
        ),
        {"nxt": successor, "r": round_id, "w": workflow_id, "n": now},
    )
    # Other branches pointing at it go back to their defaults (hold; no
    # fast-track) rather than dangling — the author re-points them if they want.
    await db.execute(
        text(
            "UPDATE workflow_rounds SET on_fail_next_round_id = NULL, updated_at = :n"
            " WHERE workflow_id = :w AND on_fail_next_round_id = :r"
        ),
        {"r": round_id, "w": workflow_id, "n": now},
    )
    await db.execute(
        text(
            "UPDATE workflow_rounds SET on_fast_track_next_round_id = NULL,"
            " fast_track_min_percent = NULL, updated_at = :n"
            " WHERE workflow_id = :w AND on_fast_track_next_round_id = :r"
        ),
        {"r": round_id, "w": workflow_id, "n": now},
    )
    # The unique index on (workflow_id, position) is partial on deleted_at, so
    # the removed row leaves it as soon as it is soft-deleted and its position
    # needs no adjustment.
    await db.execute(
        text(
            "UPDATE workflow_rounds SET deleted_at = :n, on_pass_next_round_id = NULL,"
            " on_fail_next_round_id = NULL, on_fast_track_next_round_id = NULL,"
            " fast_track_min_percent = NULL, updated_at = :n WHERE id = :r"
        ),
        {"r": round_id, "n": now},
    )
    # Renumber the survivors. Parked out of range first because the index would
    # otherwise reject the intermediate states of the shift — high rather than
    # negative, since ck_workflow_rounds_position requires position >= 0.
    survivors = [x for x in rounds if str(x["id"]) != str(round_id)]
    for r in survivors:
        await db.execute(
            text("UPDATE workflow_rounds SET position = position + :off WHERE id = :i"),
            {"off": _POSITION_PARK, "i": r["id"]},
        )
    for i, r in enumerate(survivors):
        await db.execute(
            text("UPDATE workflow_rounds SET position = :p, updated_at = :n WHERE id = :i"),
            {"p": i, "i": r["id"], "n": now},
        )


async def reorder_rounds(
    db: AsyncSession, *, workflow_id: uuid.UUID, ordered_ids: list[uuid.UUID]
) -> None:
    """Set a new round order and rebuild the chain to match. Caller commits."""
    await _assert_draft(db, workflow_id)
    rounds = await load_rounds(db, workflow_id)
    known = {str(r["id"]) for r in rounds}
    given = [str(i) for i in ordered_ids]
    if set(given) != known or len(given) != len(known):
        raise WorkflowError("The new order must list every round in this workflow exactly once.")

    now = datetime.now(tz=UTC)
    # Park positions out of the way first: the partial unique index on
    # (workflow_id, position) would otherwise reject the intermediate states of
    # any reordering that is not a pure append. Parked HIGH rather than negative
    # because ck_workflow_rounds_position requires position >= 0 — negating them
    # trades one constraint violation for another.
    await db.execute(
        text(
            "UPDATE workflow_rounds SET position = position + :off"
            " WHERE workflow_id = :w AND deleted_at IS NULL"
        ),
        {"w": workflow_id, "off": _POSITION_PARK},
    )
    for i, rid in enumerate(ordered_ids):
        nxt = ordered_ids[i + 1] if i + 1 < len(ordered_ids) else None
        await db.execute(
            text(
                "UPDATE workflow_rounds SET position = :p, on_pass_next_round_id = :nxt,"
                " updated_at = :n WHERE id = :i"
            ),
            {"p": i, "nxt": nxt, "i": rid, "n": now},
        )


async def update_settings(
    db: AsyncSession, *, workflow_id: uuid.UUID, fields: dict[str, Any]
) -> None:
    """Change a draft's automation settings (C9). Caller commits.

    Note what is not settable: nothing here can cause a rejection. The two human
    gates are absent from the column list entirely, so no request body can
    switch them off (D-05).
    """
    await _assert_draft(db, workflow_id)
    allowed = {"name", "auto_score_on_apply", "auto_assign_first_round",
               "auto_advance_rounds", "reminders_enabled", "shortlist_ats_threshold",
               "hold_band"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    sets = ", ".join(f"{k} = :{k}" for k in updates)
    await db.execute(
        text(f"UPDATE workflows SET {sets}, updated_at = :n WHERE id = :i"),
        {**updates, "i": workflow_id, "n": datetime.now(tz=UTC)},
    )


async def discard_draft(
    db: AsyncSession, *, company_id: uuid.UUID, workflow_id: uuid.UUID
) -> None:
    """Soft-delete a draft. Caller commits. Refuses anything published."""
    await _assert_draft(db, workflow_id)
    await db.execute(
        text(
            "UPDATE workflows SET deleted_at = :n, updated_at = :n"
            " WHERE id = :i AND company_id = :c"
        ),
        {"i": workflow_id, "c": company_id, "n": datetime.now(tz=UTC)},
    )


async def set_round_criteria(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    round_id: uuid.UUID,
    criteria: list[dict[str, Any]],
    _skip_draft_check: bool = False,
) -> int:
    """Freeze the selected competencies onto a round. Caller commits.

    Replaces rather than merges: the criteria list is the round's rubric, and a
    partial update would leave a stale competency assessing something HR
    deselected.

    Copies the whole rubric, not just the labels: id, name, kind, weight AND the
    behavioural anchors and probe stems. Freezing what is measured without
    freezing how it is measured is only half a guarantee — a re-derived profile
    with refined anchors would still move the standard a published round grades
    against. See ``shared.intelligence.frozen``.
    """
    if not _skip_draft_check:
        wf = await db.scalar(
            text("SELECT workflow_id FROM workflow_rounds WHERE id = :r"), {"r": round_id}
        )
        if wf is None:
            raise WorkflowError("Round not found.")
        await _assert_draft(db, wf)

    await db.execute(text("DELETE FROM round_criteria WHERE round_id = :r"), {"r": round_id})
    now = datetime.now(tz=UTC)
    for c in criteria:
        weight = float(c.get("weight", 0))
        if not 0 < weight <= 1:
            raise WorkflowError(
                f"Weight for '{c.get('name', c.get('id'))}' must be between 0 and 1."
            )
        anchors = c.get("anchors")
        probes = c.get("probes")
        await db.execute(
            text(
                "INSERT INTO round_criteria (id, company_id, round_id, competency_id,"
                " competency_name, competency_kind, weight, anchors, probes, created_at)"
                " VALUES (:i,:c,:r,:cid,:cn,:ck,:w,"
                "         CAST(:an AS jsonb), CAST(:pr AS jsonb), :n)"
            ),
            {"i": uuid.uuid4(), "c": company_id, "r": round_id,
             "cid": c["id"], "cn": c.get("name", c["id"]), "ck": c.get("kind"),
             "w": weight,
             "an": json.dumps(anchors) if anchors else None,
             "pr": json.dumps(probes) if probes else None,
             "n": now},
        )
    return len(criteria)


async def validate(
    db: AsyncSession,
    workflow_id: uuid.UUID,
    profile_competencies: list[dict[str, Any]] | None = None,
) -> ValidationReport:
    """Everything wrong with a draft, plus the coverage report."""
    rounds = await load_rounds(db, workflow_id)
    errors = validate_chain(rounds)
    readiness = await exam_round_readiness(
        db, [r["exam_round_id"] for r in rounds if r["kind"] in EXAM_BACKED_KINDS]
    )
    errors.extend(exam_round_errors(rounds, readiness))
    report = ValidationReport(errors=errors)

    criteria = await load_criteria(db, [r["id"] for r in rounds])
    titles = {str(r["id"]): r["title"] for r in rounds}

    for r in rounds:
        if r["kind"] in AI_GRADED_KINDS and not criteria.get(str(r["id"])):
            report.errors.append(
                f"{r['title']}: an AI interview round must assess at least one competency."
            )

    task_round_ids = [r["id"] for r in rounds if r["kind"] in TASK_KINDS]
    if task_round_ids:
        configured = set(
            (
                await db.execute(
                    text("SELECT round_id FROM round_tasks WHERE round_id = ANY(:ids)"),
                    {"ids": task_round_ids},
                )
            ).scalars().all()
        )
        for r in rounds:
            if r["kind"] not in TASK_KINDS:
                continue
            if r["id"] not in configured:
                report.errors.append(f"{r['title']}: needs a brief and its items before publishing.")
            if not criteria.get(str(r["id"])):
                report.errors.append(
                    f"{r['title']}: needs at least one evaluation criterion — a reviewer scores "
                    "a submission against the role's competencies, never a threshold."
                )

    if profile_competencies:
        report.coverage, warnings = build_coverage(profile_competencies, criteria, titles)
        report.warnings.extend(warnings)
    return report


# Candidates who applied while the opening had no live workflow: enrolled
# (C5 never loses an applicant) but with no workflow, so nothing could ever
# start for them. Live, not decided, not already inside a round.
#
# Two whole literals rather than one shared WHERE fragment: the SAST gate
# (bandit B608) fails SQL assembled from strings, and the test holds the two
# predicates together instead.
_WAITING_COUNT_SQL = """
SELECT count(*) FILTER (WHERE status = 'shortlisted') AS shortlisted,
       count(*) FILTER (WHERE status <> 'shortlisted') AS applied
  FROM enrolments
 WHERE requisition_id = :r AND deleted_at IS NULL AND workflow_id IS NULL
   AND current_round_id IS NULL AND status NOT IN ('hired', 'rejected')
"""

_ATTACH_WAITING_SQL = """
UPDATE enrolments SET workflow_id = :w, updated_at = now()
 WHERE requisition_id = :r AND deleted_at IS NULL AND workflow_id IS NULL
   AND current_round_id IS NULL AND status NOT IN ('hired', 'rejected')
RETURNING id, status
"""


async def waiting_candidates(db: AsyncSession, requisition_id: uuid.UUID) -> dict[str, int]:
    """How many candidates are waiting for a workflow to go live (D5).

    ``shortlisted`` is the number a person has already confirmed: publishing
    starts their first round. ``applied`` is everyone else, who join the
    workflow and wait for the shortlist as normal.
    """
    row = (
        await db.execute(text(_WAITING_COUNT_SQL), {"r": requisition_id})
    ).mappings().first()
    return {
        "shortlisted": int(row["shortlisted"] or 0) if row else 0,
        "applied": int(row["applied"] or 0) if row else 0,
    }


async def attach_waiting_candidates(
    db: AsyncSession, *, requisition_id: uuid.UUID, workflow_id: uuid.UUID
) -> list[tuple[uuid.UUID, str]]:
    """Put everyone waiting onto the version just published. Caller commits.

    The runner's own comment promised this — applicants who arrive early "join
    a workflow when one goes live" — and nothing did it: publishing left them
    with no workflow, so shortlisting them afterwards reported "no workflow
    attached" and started nothing, silently. Returns (enrolment id, status) for
    each, so the caller can start the ones a person already shortlisted.
    """
    rows = (
        await db.execute(text(_ATTACH_WAITING_SQL), {"r": requisition_id, "w": workflow_id})
    ).all()
    return [(uuid.UUID(str(r[0])), str(r[1])) for r in rows]


async def publish(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    workflow_id: uuid.UUID,
    profile_competencies: list[dict[str, Any]] | None = None,
) -> ValidationReport:
    """Publish a draft, archiving whatever it replaces. Caller commits.

    Archiving rather than deleting the previous version is what lets an enrolled
    candidate keep running it: their ``workflow_id`` still resolves, and their
    rounds and thresholds are exactly the ones they started under.
    """
    row = (
        await db.execute(
            text(
                "SELECT status, review_status, review_fingerprint, requisition_id"
                "  FROM workflows WHERE id = :i AND company_id = :c AND deleted_at IS NULL"
            ),
            {"i": workflow_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise WorkflowError("Workflow not found.")
    if row["status"] != "draft":
        raise WorkflowError("Only a draft can be published.")
    if row["review_status"] != "approved":
        # PH4-O6. The database refuses it as well; this says so in words.
        raise WorkflowError(
            "This version has not been approved. Submit it for review — a company "
            "super admin approves a workflow before it can go live."
        )
    if await workflow_fingerprint(db, workflow_id) != row["review_fingerprint"]:
        # The version itself is locked once submitted, but an exam round it uses
        # is not: what goes live must be what the reviewer approved.
        raise WorkflowError(
            "Something this version uses — such as an exam round's questions — changed "
            "after it was approved. Reopen it and submit it for review again."
        )

    report = await validate(db, workflow_id, profile_competencies)
    if not report.publishable:
        return report

    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE workflows SET status = 'archived', updated_at = :n"
            " WHERE requisition_id = :r AND status = 'published' AND deleted_at IS NULL"
        ),
        {"r": row["requisition_id"], "n": now},
    )
    await db.execute(
        text(
            "UPDATE workflows SET status = 'published', published_at = :n, updated_at = :n"
            " WHERE id = :i"
        ),
        {"i": workflow_id, "n": now},
    )
    log.info(
        "workflow.published",
        workflow_id=str(workflow_id), requisition_id=str(row["requisition_id"]),
        warnings=len(report.warnings),
    )
    return report


async def clone_for_edit(
    db: AsyncSession, *, company_id: uuid.UUID, workflow_id: uuid.UUID,
    created_by: uuid.UUID | None,
) -> uuid.UUID:
    """Copy a published workflow into a new draft at version n+1. Caller commits.

    This is how a published workflow is 'edited'. The original is untouched, so
    every candidate mid-process keeps the rounds, thresholds and criteria they
    started under.
    """
    src = (
        await db.execute(
            text(
                "SELECT requisition_id, name, role_profile_id, domain_family, profile_source,"
                "       auto_score_on_apply, auto_assign_first_round, auto_advance_rounds,"
                "       reminders_enabled, shortlist_ats_threshold, hold_band"
                "  FROM workflows WHERE id = :i AND company_id = :c AND deleted_at IS NULL"
            ),
            {"i": workflow_id, "c": company_id},
        )
    ).mappings().first()
    if src is None:
        raise WorkflowError("Workflow not found.")

    new_id = await create_draft(
        db, company_id=company_id, requisition_id=src["requisition_id"],
        created_by=created_by, name=src["name"],
        profile={"profile_id": src["role_profile_id"], "domain_family": src["domain_family"],
                 "source": src["profile_source"]},
    )
    await db.execute(
        text(
            "UPDATE workflows SET auto_score_on_apply = :a1, auto_assign_first_round = :a2,"
            " auto_advance_rounds = :a3, reminders_enabled = :a4,"
            " shortlist_ats_threshold = :a5, hold_band = :a6"
            " WHERE id = :i"
        ),
        {"i": new_id, "a1": src["auto_score_on_apply"], "a2": src["auto_assign_first_round"],
         "a3": src["auto_advance_rounds"], "a4": src["reminders_enabled"],
         "a5": src["shortlist_ats_threshold"], "a6": src["hold_band"]},
    )

    # Copy rounds, then rewire the chain using the old-to-new id map.
    old_rounds = await load_rounds(db, workflow_id)
    id_map: dict[str, uuid.UUID] = {}
    now = datetime.now(tz=UTC)
    for r in old_rounds:
        nid = uuid.uuid4()
        id_map[str(r["id"])] = nid
        await db.execute(
            text(
                "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title,"
                " kind, pass_threshold, time_limit_seconds, deadline_days, exam_round_id,"
                " created_at, updated_at)"
                " VALUES (:i,:c,:w,:p,:t,:k,:th,:tl,:dd,:er,:n,:n)"
            ),
            {"i": nid, "c": company_id, "w": new_id, "p": r["position"], "t": r["title"],
             "k": r["kind"], "th": r["pass_threshold"], "tl": r["time_limit_seconds"],
             "dd": r["deadline_days"], "er": r["exam_round_id"], "n": now},
        )
    for r in old_rounds:
        # Every branch, re-pointed at the new version's rounds (PH4-O3).
        edges = {
            key: id_map.get(str(r[key])) if r.get(key) else None
            for key in ("on_pass_next_round_id", "on_fail_next_round_id",
                        "on_fast_track_next_round_id")
        }
        if any(edges.values()):
            await db.execute(
                text(
                    "UPDATE workflow_rounds SET on_pass_next_round_id = :p,"
                    " on_fail_next_round_id = :f, on_fast_track_next_round_id = :ft,"
                    " fast_track_min_percent = :fm WHERE id = :i"
                ),
                {"p": edges["on_pass_next_round_id"], "f": edges["on_fail_next_round_id"],
                 "ft": edges["on_fast_track_next_round_id"],
                 "fm": r["fast_track_min_percent"] if edges["on_fast_track_next_round_id"]
                 else None,
                 "i": id_map[str(r["id"])]},
            )

    # PH4-D4: a task round's config and materials travel with it. Materials
    # SHARE their storage key with the original — the object itself is
    # immutable HR reference material, not candidate content, and duplicating
    # bytes for every clone would multiply storage for no benefit (open
    # decision 21). Deleting one requires checking no other row still names
    # the key, which app/job_tasks.py's material removal does.
    old_task_rounds = [r for r in old_rounds if r["kind"] in TASK_KINDS]
    if old_task_rounds:
        tasks = (
            await db.execute(
                text(
                    "SELECT round_id, kind, brief, brief_translations, items, min_artifacts,"
                    " max_artifacts, allow_files, allow_links, allowed_link_domains"
                    "  FROM round_tasks WHERE round_id = ANY(:ids)"
                ),
                {"ids": [r["id"] for r in old_task_rounds]},
            )
        ).mappings().all()
        for t in tasks:
            new_rid = id_map[str(t["round_id"])]
            await db.execute(
                text(
                    "INSERT INTO round_tasks (id, company_id, round_id, kind, brief,"
                    " brief_translations, items, min_artifacts, max_artifacts, allow_files,"
                    " allow_links, allowed_link_domains, created_at, updated_at)"
                    " VALUES (:i,:c,:r,:k,:b, CAST(:bt AS jsonb), CAST(:it AS jsonb), :mina,"
                    " :maxa, :af, :al, :dom, :n, :n)"
                ),
                {"i": uuid.uuid4(), "c": company_id, "r": new_rid, "k": t["kind"],
                 "b": t["brief"],
                 "bt": json.dumps(t["brief_translations"]) if t["brief_translations"] else None,
                 "it": json.dumps(t["items"]), "mina": t["min_artifacts"],
                 "maxa": t["max_artifacts"], "af": t["allow_files"], "al": t["allow_links"],
                 "dom": t["allowed_link_domains"], "n": now},
            )
        materials = (
            await db.execute(
                text(
                    "SELECT round_id, title, storage_key, original_name, content_type,"
                    " size_bytes, sha256, position FROM round_task_materials"
                    " WHERE round_id = ANY(:ids)"
                ),
                {"ids": [r["id"] for r in old_task_rounds]},
            )
        ).mappings().all()
        for m in materials:
            new_rid = id_map[str(m["round_id"])]
            await db.execute(
                text(
                    "INSERT INTO round_task_materials (id, company_id, round_id, title,"
                    " storage_key, original_name, content_type, size_bytes, sha256, position,"
                    " created_at, updated_at)"
                    " VALUES (:i,:c,:r,:t,:k,:n,:ct,:sz,:sha,:p,:now,:now)"
                ),
                {"i": uuid.uuid4(), "c": company_id, "r": new_rid, "t": m["title"],
                 "k": m["storage_key"], "n": m["original_name"], "ct": m["content_type"],
                 "sz": m["size_bytes"], "sha": m["sha256"], "p": m["position"], "now": now},
            )

    old_criteria = await load_criteria(db, [r["id"] for r in old_rounds])
    for old_rid, crits in old_criteria.items():
        for c in crits:
            await db.execute(
                text(
                    "INSERT INTO round_criteria (id, company_id, round_id, competency_id,"
                    " competency_name, competency_kind, weight, anchors, probes, created_at)"
                    " VALUES (:i,:c,:r,:cid,:cn,:ck,:w,"
                    "         CAST(:an AS jsonb), CAST(:pr AS jsonb), :n)"
                ),
                {"i": uuid.uuid4(), "c": company_id, "r": id_map[old_rid],
                 "cid": c["competency_id"], "cn": c["competency_name"],
                 "ck": c["competency_kind"], "w": c["weight"],
                 "an": json.dumps(c["anchors"]) if c.get("anchors") else None,
                 "pr": json.dumps(c["probes"]) if c.get("probes") else None,
                 "n": now},
            )
    # PH4-A5: kits are keyed by round id, and a new version has new round ids.
    # Without this every kit HR wrote would vanish the first time anyone edited
    # the workflow. Imported here: interview_kits imports this module.
    from app.interview_kits import copy_kits  # noqa: PLC0415

    kits = await copy_kits(db, company_id=company_id, id_map=id_map)
    # PH4-O1: who owns each stage and its SLA carry forward the same way.
    from app.stage_sla import copy_stage_settings  # noqa: PLC0415

    stages = await copy_stage_settings(
        db, company_id=company_id, source_workflow_id=workflow_id, target_workflow_id=new_id,
        id_map=id_map,
    )
    log.info("workflow.cloned", source=str(workflow_id), draft=str(new_id), kits=kits,
             stage_settings=stages)
    return new_id


async def _assert_draft(db: AsyncSession, workflow_id: uuid.UUID) -> None:
    """Refuse structural edits to anything but a draft.

    The guard is here rather than in the router because every caller — HTTP,
    the copilot's commit path, a future import — must obey it. A published
    workflow that could be edited in place would silently re-grade the people
    running it.
    """
    row = (
        await db.execute(
            text(
                "SELECT status, review_status FROM workflows"
                " WHERE id = :i AND deleted_at IS NULL"
            ),
            {"i": workflow_id},
        )
    ).first()
    if row is None:
        raise WorkflowError("Workflow not found.")
    st, review = row[0], row[1]
    if st != "draft":
        raise WorkflowError(
            f"This workflow is {st}. Create a new version to change it — "
            "candidates already enrolled must finish on the version they started."
        )
    # PH4-O6: what the reviewer sees is what goes live.
    if review == "in_review":
        raise WorkflowError(
            "This version is waiting for review, so it cannot be edited. Withdraw it "
            "from review to make changes."
        )
    if review == "approved":
        raise WorkflowError(
            "This version has been approved, so it cannot be edited. Reopen it to make "
            "changes — it will need approving again."
        )


# The content of each exam round a version points at, one digest per row. The
# workflow row locks while under review, but an exam round is its own object and
# stays editable — so without this an approved version could go live on an exam
# its reviewer never saw. Timestamps are left out (they move without the content
# moving); ``deleted_at`` is not, so removing a question changes the digest.
#
# PH4-D1: a question/coding row's three ``source_bank_*`` provenance columns are
# also subtracted. Where a question came from is not what the reviewer approved
# — the content is identical whether it was typed by hand or copied from an
# approved bank question — and leaving them in would change every existing
# workflow's fingerprint the moment D1 shipped, failing every approved-but-
# unpublished version's publish with "changed after it was approved" and
# marking every stored dry run stale, for a change nobody made.
_EXAM_CONTENT_SQL = """
SELECT er.id,
       md5((to_jsonb(er) - 'created_at' - 'updated_at')::text) AS round_digest,
       COALESCE((SELECT string_agg(md5((to_jsonb(s) - 'created_at' - 'updated_at')::text),
                                   ',' ORDER BY s.id)
                   FROM exam_sections s
                  WHERE s.round_id = er.id AND s.deleted_at IS NULL), '') AS sections,
       COALESCE((SELECT string_agg(md5((to_jsonb(q) - 'created_at' - 'updated_at'
                                        - 'source_bank_question_id' - 'source_bank_root_id'
                                        - 'source_bank_version')::text),
                                   ',' ORDER BY q.id)
                   FROM exam_questions q
                   JOIN exam_sections s ON s.id = q.section_id AND s.deleted_at IS NULL
                  WHERE s.round_id = er.id AND q.deleted_at IS NULL), '') AS questions,
       COALESCE((SELECT string_agg(md5((to_jsonb(c) - 'created_at' - 'updated_at'
                                        - 'source_bank_question_id' - 'source_bank_root_id'
                                        - 'source_bank_version')::text),
                                   ',' ORDER BY c.id)
                   FROM coding_questions c
                   JOIN exam_sections s ON s.id = c.section_id AND s.deleted_at IS NULL
                  WHERE s.round_id = er.id AND c.deleted_at IS NULL), '') AS coding
  FROM exam_rounds er
 WHERE er.id = ANY(:ids)
 ORDER BY er.id
"""


async def workflow_fingerprint(db: AsyncSession, workflow_id: uuid.UUID) -> str:
    """A hash of everything a reviewer approves and a simulation tests.

    Settings, every round (with its branches), every criterion, and the content
    of every exam round the version points at, in a stable order. Two versions
    with the same fingerprint behave identically; a result recorded against one
    fingerprint says nothing about another. Interview kits and stage owners are
    excluded: they are operational guidance, editable on a live version, and
    change no candidate's path.
    """
    wf = (
        await db.execute(
            text(
                "SELECT auto_score_on_apply, auto_assign_first_round, auto_advance_rounds,"
                "       reminders_enabled, shortlist_ats_threshold, hold_band"
                "  FROM workflows WHERE id = :i"
            ),
            {"i": workflow_id},
        )
    ).mappings().first()
    rounds = await load_rounds(db, workflow_id)
    criteria = await load_criteria(db, [r["id"] for r in rounds])
    exam_ids = sorted({str(r["exam_round_id"]) for r in rounds if r.get("exam_round_id")})
    exams: list[dict[str, Any]] = []
    if exam_ids:
        exams = [
            {k: str(v) for k, v in row.items()}
            for row in (
                await db.execute(
                    text(_EXAM_CONTENT_SQL), {"ids": [uuid.UUID(i) for i in exam_ids]}
                )
            ).mappings().all()
        ]
    payload: dict[str, Any] = {
        "settings": dict(wf) if wf else {},
        "exams": exams,
        "rounds": [
            {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in r.items()}
            for r in rounds
        ],
        "criteria": {
            rid: sorted(
                ({"id": c["competency_id"], "w": c["weight"], "a": c.get("anchors"),
                  "p": c.get("probes")} for c in crits),
                key=lambda c: str(c["id"]),
            )
            for rid, crits in sorted(criteria.items())
        },
    }
    # PH4-D4: a task round's own configuration, keyed only when at least one
    # exists — every fingerprint recorded before this feature shipped stays
    # byte-for-byte identical (the golden-value unit test pins this).
    task_round_ids = [r["id"] for r in rounds if r["kind"] in TASK_KINDS]
    if task_round_ids:
        task_rows = (
            await db.execute(
                text(
                    "SELECT round_id, kind, brief, brief_translations, items, min_artifacts,"
                    " max_artifacts, allow_files, allow_links, allowed_link_domains"
                    "  FROM round_tasks WHERE round_id = ANY(:ids)"
                ),
                {"ids": task_round_ids},
            )
        ).mappings().all()
        payload["tasks"] = {
            str(t["round_id"]): {k: v for k, v in t.items() if k != "round_id"}
            for t in task_rows
        }
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()
