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
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

# The four round types Phase 2 ships (D-03). Portfolio / file-submission is
# deliberately absent: it needs a new grader and introduces a prompt-injection
# surface on candidate-authored content, so it is a Phase 3 addition.
ROUND_KINDS: frozenset[str] = frozenset({"mcq", "coding", "ai_interview", "human_review"})

# Rounds whose content comes from the existing exam machinery, which already has
# authoring, AI generation, CSV import and graders.
EXAM_BACKED_KINDS: frozenset[str] = frozenset({"mcq", "coding"})

# Rounds graded by a model rather than by an answer key or a person.
AI_GRADED_KINDS: frozenset[str] = frozenset({"ai_interview"})

WORKFLOW_STATUSES: frozenset[str] = frozenset({"draft", "published", "archived"})

MAX_ROUNDS = 12

# Offset used to park positions during a reorder or a removal, so the partial
# unique index on (workflow_id, position) does not reject the intermediate
# states. Comfortably above MAX_ROUNDS and still non-negative.
_POSITION_PARK = 1000


class WorkflowError(Exception):
    """Refused for a reason the caller should show the user verbatim."""


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "publishable": self.publishable,
            "errors": self.errors,
            "warnings": self.warnings,
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


def validate_chain(rounds: list[dict[str, Any]]) -> list[str]:
    """Structural checks on the round chain. Returns blocking errors.

    The cycle check matters more than it looks. Execution is linear today, but
    the next-round pointer is a real edge, and a cycle would put the runner in
    an infinite loop moving one candidate between two rounds forever.
    """
    errors: list[str] = []
    if not rounds:
        return ["A workflow needs at least one round."]
    if len(rounds) > MAX_ROUNDS:
        return [f"A workflow cannot have more than {MAX_ROUNDS} rounds."]

    by_id = {str(r["id"]): r for r in rounds}

    for r in rounds:
        title = r.get("title") or "(untitled)"
        if r["kind"] not in ROUND_KINDS:
            errors.append(f"{title}: unknown round type {r['kind']!r}.")
        if r["kind"] in EXAM_BACKED_KINDS and not r.get("exam_round_id"):
            errors.append(f"{title}: a {r['kind']} round needs questions before publishing.")
        if r["kind"] != "human_review" and r.get("pass_threshold") is None:
            errors.append(f"{title}: needs an advance threshold.")
        nxt = r.get("on_pass_next_round_id")
        if nxt and str(nxt) not in by_id:
            errors.append(f"{title}: points at a round that is not in this workflow.")

    # Every round must be reachable from the first, and the walk must terminate.
    ordered = sorted(rounds, key=lambda r: r["position"])
    seen: set[str] = set()
    cur: str | None = str(ordered[0]["id"])
    while cur is not None:
        if cur in seen:
            errors.append("The rounds form a loop — a candidate would never finish.")
            break
        seen.add(cur)
        nxt = by_id.get(cur, {}).get("on_pass_next_round_id")
        cur = str(nxt) if nxt else None

    unreachable = [by_id[i]["title"] for i in by_id if i not in seen]
    if unreachable and not errors:
        errors.append(
            "Unreachable from the first round: " + ", ".join(sorted(unreachable))
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
                "       deadline_days, on_pass_next_round_id, exam_round_id"
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
    rewire it.
    """
    await _assert_draft(db, workflow_id)
    allowed = {"title", "kind", "pass_threshold", "time_limit_seconds",
               "deadline_days", "exam_round_id"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if "kind" in updates and updates["kind"] not in ROUND_KINDS:
        raise WorkflowError(f"Unknown round type {updates['kind']!r}.")
    sets = ", ".join(f"{k} = :{k}" for k in updates)
    await db.execute(
        text(
            f"UPDATE workflow_rounds SET {sets}, updated_at = :n"
            " WHERE id = :r AND workflow_id = :w AND deleted_at IS NULL"
        ),
        {**updates, "r": round_id, "w": workflow_id, "n": datetime.now(tz=UTC)},
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
    # The unique index on (workflow_id, position) is partial on deleted_at, so
    # the removed row leaves it as soon as it is soft-deleted and its position
    # needs no adjustment.
    await db.execute(
        text(
            "UPDATE workflow_rounds SET deleted_at = :n, on_pass_next_round_id = NULL,"
            " updated_at = :n WHERE id = :r"
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
    report = ValidationReport(errors=errors)

    criteria = await load_criteria(db, [r["id"] for r in rounds])
    titles = {str(r["id"]): r["title"] for r in rounds}

    for r in rounds:
        if r["kind"] in AI_GRADED_KINDS and not criteria.get(str(r["id"])):
            report.errors.append(
                f"{r['title']}: an AI interview round must assess at least one competency."
            )

    if profile_competencies:
        report.coverage, warnings = build_coverage(profile_competencies, criteria, titles)
        report.warnings.extend(warnings)
    return report


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
                "SELECT status, requisition_id FROM workflows"
                " WHERE id = :i AND company_id = :c AND deleted_at IS NULL"
            ),
            {"i": workflow_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise WorkflowError("Workflow not found.")
    if row["status"] != "draft":
        raise WorkflowError("Only a draft can be published.")

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
        nxt = r["on_pass_next_round_id"]
        if nxt:
            await db.execute(
                text("UPDATE workflow_rounds SET on_pass_next_round_id = :nx WHERE id = :i"),
                {"nx": id_map[str(nxt)], "i": id_map[str(r["id"])]},
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
    log.info("workflow.cloned", source=str(workflow_id), draft=str(new_id))
    return new_id


async def _assert_draft(db: AsyncSession, workflow_id: uuid.UUID) -> None:
    """Refuse structural edits to anything but a draft.

    The guard is here rather than in the router because every caller — HTTP,
    the copilot's commit path, a future import — must obey it. A published
    workflow that could be edited in place would silently re-grade the people
    running it.
    """
    st = await db.scalar(
        text("SELECT status FROM workflows WHERE id = :i AND deleted_at IS NULL"),
        {"i": workflow_id},
    )
    if st is None:
        raise WorkflowError("Workflow not found.")
    if st != "draft":
        raise WorkflowError(
            f"This workflow is {st}. Create a new version to change it — "
            "candidates already enrolled must finish on the version they started."
        )
