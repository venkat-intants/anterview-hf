"""Workflow-authoring tools for the builder copilot — D6.

Registered onto the same ``registry`` as every other tool, so they inherit the
guarantees that matter: no write effect, role gating at construction, and a
``ToolContext`` the model cannot influence.

WHICH OPENING IS BEING DESIGNED comes from ``ctx.resources["requisition_id"]``,
which the router set after checking that the opening belongs to the caller's
company. It is deliberately NOT a tool parameter. That keeps the existing
invariant — scope comes from ctx, never from args — and makes "design a
workflow for opening X" unaskable: the copilot has no way to name a different
opening, whether it was asked to or talked into it.

THE RUBRIC IS NEVER MODEL-AUTHORED. ``draft_round_criteria`` takes competency
IDs and resolves every one against the role model, dropping anything it does
not recognise. Anchors and probes come from the taxonomy, not from the model's
output. This is the sharpest edge in the file: a workflow freezes its rubric at
publication and every candidate is then assessed against exactly that text, so
a hallucinated anchor would not be a wrong sentence in a chat window — it would
become the employer's stated standard, applied to real people, months later,
with nothing in the system marking it as invented.

Nothing here creates a workflow. Every draft_* tool returns a ``Proposal``
carrying the request a human would fire; and even once fired, the result is a
DRAFT the user must separately publish. An agent-shaped process therefore
reaches a candidate only after two deliberate human acts.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from shared.agents import (
    Citation,
    CommitSpec,
    Proposal,
    ToolContext,
    ToolOutput,
)
from shared.intelligence import baseline_profile, compute_profile_id
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import registry
from app.workflows import (
    EXAM_BACKED_KINDS,
    MAX_ROUNDS,
    ROUND_KINDS,
    load_criteria,
    load_rounds,
    validate,
)

log = structlog.get_logger(__name__)

# Mirrors the server cap in hr_workflows.put_criteria. A round that assesses
# everything assesses nothing in particular.
MAX_CRITERIA_PER_ROUND: int = 8

# Only the HR console. `company_scoped` also permits super_admin, but the REST
# endpoints these proposals commit to are gated on hr_manager by
# get_hr_company — so offering the tool to a super admin would draft a button
# that 403s on click.
WORKFLOW_ROLES: tuple[str, ...] = ("hr_manager",)

# These tools belong to the builder screen and nowhere else. Declaring the
# surface keeps them out of the general HR copilot's prompt, where they would
# be six unusable descriptions inviting a call that can only fail — the
# opening under design is injected per-request and does not exist elsewhere.
BUILDER_SURFACE: tuple[str, ...] = ("workflow_builder",)

_LEVELS = ("entry", "mid", "senior")


def _db(ctx: ToolContext) -> AsyncSession:
    return ctx.require("db")  # type: ignore[no-any-return]


def _requisition_id(ctx: ToolContext) -> str:
    """The opening under design, from the router — never from the model."""
    return str(ctx.require("requisition_id"))


async def _requisition(ctx: ToolContext) -> dict[str, Any]:
    row = (
        await _db(ctx).execute(
            text(
                "SELECT id, title, level, jd_text, target_hires, status"
                "  FROM job_requisitions"
                " WHERE id = CAST(:r AS uuid) AND company_id = CAST(:c AS uuid)"
                "   AND deleted_at IS NULL"
            ),
            {"r": _requisition_id(ctx), "c": ctx.company_id},
        )
    ).mappings().first()
    if row is None:
        # Two ways here: the opening was deleted between the router's check and
        # this call, or the injected id belongs to another tenant and the
        # company filter dropped it. Worded for both, because from this
        # caller's side they are the same fact — and raising lets the registry
        # return a readable tool error rather than the handler dereferencing
        # None.
        raise LookupError("that opening is not available")
    return dict(row)


def _profile_for(req: dict[str, Any]) -> Any:
    level = req["level"] if req["level"] in _LEVELS else "mid"
    return baseline_profile(
        profile_id=compute_profile_id(job_title=req["title"], seniority=level),
        job_title=req["title"],
        seniority=level,
    )


def _requisition_citation(req: dict[str, Any]) -> Citation:
    return Citation(
        kind="job",
        id=str(req["id"]),
        label=f"Opening — {req['title']}",
        href=f"/hr/requisitions/{req['id']}/workflow",
    )


async def _current_workflow(ctx: ToolContext) -> dict[str, Any] | None:
    """The draft if there is one, otherwise the live version, otherwise None.

    Same precedence the builder page uses, so the copilot and the canvas are
    always talking about the same object.

    Filters on ``ctx.company_id`` as well as the requisition, even though the
    router already checked that the requisition belongs to this company. The
    duplication is the point: the router's check is one line in a different
    file, and if a future caller injects ``requisition_id`` without it, an
    unscoped query here would read another tenant's workflow rather than
    returning nothing.
    """
    row = (
        await _db(ctx).execute(
            text(
                "SELECT id, version, status, name FROM workflows"
                " WHERE requisition_id = CAST(:r AS uuid)"
                "   AND company_id = CAST(:c AS uuid) AND deleted_at IS NULL"
                "   AND status IN ('draft','published')"
                " ORDER BY (status = 'draft') DESC, version DESC LIMIT 1"
            ),
            {"r": _requisition_id(ctx), "c": ctx.company_id},
        )
    ).mappings().first()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@registry.tool(
    name="get_opening_under_design",
    description=(
        "The job opening whose hiring workflow is being designed, together with "
        "its role model: the competencies that matter for this role, their "
        "weights, and what weak/adequate/strong evidence looks like for each. "
        "Call this FIRST — every design decision should refer to it."
    ),
    parameters={"type": "object", "properties": {}},
    data_class="company_scoped",
    allowed_roles=WORKFLOW_ROLES,
    surfaces=BUILDER_SURFACE,
)
async def _get_opening_under_design(_args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
    req = await _requisition(ctx)
    profile = _profile_for(req)
    jd = (req["jd_text"] or "").strip()
    return ToolOutput(
        data={
            "opening": {
                "title": req["title"],
                "level": req["level"],
                "status": req["status"],
                "target_hires": req["target_hires"],
                # Truncated: a full JD can be 40k characters and would crowd
                # the actual question out of the model's attention.
                "job_description_excerpt": jd[:1500] or None,
            },
            "occupational_family": profile.domain_label,
            "competencies": [
                {
                    "id": c.id,
                    "name": c.name,
                    "kind": c.kind,
                    "weight_in_role": round(c.weight, 3),
                    "weak": c.anchors.low,
                    "adequate": c.anchors.mid,
                    "strong": c.anchors.high,
                }
                for c in profile.competencies
            ],
            "note": (
                "Use these competency ids verbatim when drafting. The rating "
                "scale for each is fixed by the platform and copied onto a "
                "round automatically — do not write your own."
            ),
        },
        citations=[
            _requisition_citation(req),
            Citation(
                kind="role_profile",
                id=profile.profile_id,
                label=f"Role model — {profile.job_title}",
            ),
        ],
    )


@registry.tool(
    name="get_current_workflow",
    description=(
        "The hiring workflow this opening has right now: its rounds in order, "
        "what each assesses, its thresholds, and what still blocks publishing. "
        "Returns exists=false when the opening has no workflow yet. Call before "
        "proposing changes, so you build on what is there instead of "
        "duplicating it."
    ),
    parameters={"type": "object", "properties": {}},
    data_class="company_scoped",
    allowed_roles=WORKFLOW_ROLES,
    surfaces=BUILDER_SURFACE,
)
async def _get_current_workflow(_args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
    req = await _requisition(ctx)
    wf = await _current_workflow(ctx)
    if wf is None:
        return ToolOutput(
            data={
                "exists": False,
                "note": "This opening has no workflow. Use draft_hiring_workflow.",
            },
            citations=[_requisition_citation(req)],
        )

    rounds = await load_rounds(_db(ctx), wf["id"])
    criteria = await load_criteria(_db(ctx), [r["id"] for r in rounds])
    profile = _profile_for(req)
    report = await validate(
        _db(ctx),
        wf["id"],
        [{"id": c.id, "name": c.name, "weight": c.weight} for c in profile.competencies],
    )

    return ToolOutput(
        data={
            "exists": True,
            "workflow_id": str(wf["id"]),
            "version": wf["version"],
            "status": wf["status"],
            # The single most important fact for the copilot's next move: a
            # published workflow cannot be changed, only cloned to a new
            # version, so proposing an edit to one would be proposing a button
            # that is refused.
            "editable": wf["status"] == "draft",
            "rounds": [
                {
                    "round_id": str(r["id"]),
                    "position": r["position"],
                    "title": r["title"],
                    "kind": r["kind"],
                    "pass_threshold_percent": (
                        float(r["pass_threshold"]) if r["pass_threshold"] is not None else None
                    ),
                    "has_questions_attached": bool(r["exam_round_id"]),
                    "assesses": [
                        c["competency_name"] for c in criteria.get(str(r["id"]), [])
                    ],
                }
                for r in rounds
            ],
            "blocking_errors": report.errors,
            "warnings": report.warnings,
            "competencies_no_round_assesses": [
                c.competency_name for c in report.coverage if c.times_assessed == 0
            ],
        },
        citations=[_requisition_citation(req)],
    )


@registry.tool(
    name="list_available_exams",
    description=(
        "The company's exams and the rounds inside them, for attaching "
        "questions to an MCQ or coding round. An exam-backed round cannot be "
        "published until one is attached, so mention when nothing suitable "
        "exists rather than proposing a round that cannot go live."
    ),
    parameters={"type": "object", "properties": {}},
    data_class="company_scoped",
    allowed_roles=WORKFLOW_ROLES,
    surfaces=BUILDER_SURFACE,
)
async def _list_available_exams(_args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
    rows = (
        await _db(ctx).execute(
            text(
                "SELECT e.id AS exam_id, e.title AS exam_title, e.kind,"
                "       r.id AS round_id, r.title AS round_title,"
                "       (SELECT count(*) FROM exam_questions q"
                "         WHERE q.exam_id = e.id) AS questions"
                "  FROM exams e"
                "  LEFT JOIN exam_rounds r ON r.exam_id = e.id AND r.deleted_at IS NULL"
                " WHERE e.company_id = CAST(:c AS uuid) AND e.deleted_at IS NULL"
                " ORDER BY e.created_at DESC LIMIT 40"
            ),
            {"c": ctx.company_id},
        )
    ).mappings().all()
    return ToolOutput(
        data={
            "exam_rounds": [
                {
                    "exam": r["exam_title"],
                    "kind": r["kind"],
                    "round": r["round_title"],
                    "exam_round_id": str(r["round_id"]),
                    "questions_in_exam": int(r["questions"] or 0),
                }
                for r in rows
                if r["round_id"]
            ],
            "note": (
                "Attach by exam_round_id. If this list is empty the user has "
                "no exams yet and must author one before an MCQ or coding "
                "round can be published."
            ),
        },
        citations=[
            Citation(kind="exam", id=str(r["exam_id"]), label=str(r["exam_title"]),
                     href=f"/hr/exams/{r['exam_id']}")
            for r in rows
            if r["round_id"]
        ][:10],
    )


# ---------------------------------------------------------------------------
# Resolving criteria — where a hallucinated rubric would otherwise get in
# ---------------------------------------------------------------------------


def _resolve_criteria(
    profile: Any, competency_ids: Any
) -> tuple[list[dict[str, Any]], list[str]]:
    """Turn model-supplied ids into real, taxonomy-backed criteria.

    Returns (criteria, unknown_ids). Everything the round will freeze — name,
    kind, weight, anchors, probes — is read off the role model here. The model
    contributes the SELECTION and nothing else, which is the whole of what it
    should be trusted with: choosing what to assess is a judgement, writing the
    scale a stranger is measured against is not.
    """
    if not isinstance(competency_ids, list):
        return [], []
    by_id = {c.id: c for c in profile.competencies}
    criteria: list[dict[str, Any]] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for raw in competency_ids[:MAX_CRITERIA_PER_ROUND]:
        cid = str(raw).strip()
        if cid in seen:
            continue
        seen.add(cid)
        comp = by_id.get(cid)
        if comp is None:
            unknown.append(cid)
            continue
        criteria.append(
            {
                "id": comp.id,
                "name": comp.name,
                "kind": comp.kind,
                "weight": comp.weight,
                "anchors": {
                    "low": comp.anchors.low,
                    "mid": comp.anchors.mid,
                    "high": comp.anchors.high,
                },
                "probes": list(comp.probes),
            }
        )
    return criteria, unknown


def _clean_threshold(value: Any, kind: str) -> float | None:
    """A percentage in 0–100, or None for a round that scores nothing.

    Thresholds are ALWAYS percentages here, whatever the round kind — the
    runner converts an interview's 0–10 composite at the edge. A model that
    offered 7.5 for an interview would be writing 7.5%, which held candidates
    who had comfortably passed the last time these two units shared a column.
    """
    if kind == "human_review":
        return None
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return 60.0
    return min(100.0, max(0.0, round(pct, 2)))


def _normalise_rounds(
    raw: Any, profile: Any
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate a model-proposed round list against what the server accepts.

    Rejecting here rather than at commit time is the point: a proposal that
    409s when the user clicks it is worse than one that was never offered, and
    the user has no way to tell which of the two they are looking at.
    """
    notes: list[str] = []
    if not isinstance(raw, list) or not raw:
        return [], ["rounds must be a non-empty list"]

    rounds: list[dict[str, Any]] = []
    for item in raw[:MAX_ROUNDS]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip()
        if kind not in ROUND_KINDS:
            notes.append(f"dropped a round with unknown type {kind!r}")
            continue
        title = str(item.get("title", "")).strip()[:200]
        if not title:
            title = kind.replace("_", " ").title()

        criteria, unknown = _resolve_criteria(profile, item.get("competency_ids"))
        if unknown:
            notes.append(
                f"{title}: ignored competency id(s) not in this role model: "
                + ", ".join(sorted(unknown))
            )

        deadline = item.get("deadline_days", 7)
        try:
            deadline = min(365, max(1, int(deadline)))
        except (TypeError, ValueError):
            deadline = 7

        rounds.append(
            {
                "title": title,
                "kind": kind,
                "pass_threshold": _clean_threshold(item.get("pass_threshold"), kind),
                "deadline_days": deadline,
                "criteria": criteria,
            }
        )

    if len(raw) > MAX_ROUNDS:
        notes.append(f"kept the first {MAX_ROUNDS} rounds — that is the maximum")
    return rounds, notes


def _round_summary(rounds: list[dict[str, Any]]) -> str:
    parts = []
    for r in rounds:
        bit = r["title"]
        if r["pass_threshold"] is not None:
            bit += f" ({r['pass_threshold']:g}%)"
        parts.append(bit)
    return " → ".join(parts)


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


@registry.tool(
    name="draft_hiring_workflow",
    description=(
        "Propose a complete hiring workflow for this opening: an ordered list "
        "of rounds with thresholds and the competencies each assesses. Produces "
        "a preview on the user's canvas — it does NOT create anything. Use when "
        "the opening has no workflow yet, or when redesigning from scratch."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "A short name for this process."},
            "rounds": {
                "type": "array",
                "maxItems": MAX_ROUNDS,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": ["mcq", "coding", "ai_interview", "human_review"],
                        },
                        "pass_threshold": {
                            "type": "number",
                            "description": (
                                "Percentage 0-100 to advance. Omit for "
                                "human_review, which scores nothing. Below this "
                                "sends the candidate to the decision queue; it "
                                "never rejects them."
                            ),
                        },
                        "deadline_days": {"type": "integer"},
                        "competency_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Ids from get_opening_under_design. The rating "
                                "scale is attached automatically — do not "
                                "invent competencies or write anchor text."
                            ),
                        },
                    },
                    "required": ["title", "kind"],
                },
            },
            "rationale": {
                "type": "string",
                "description": "Why this shape. Shown to the user beside the preview.",
            },
        },
        "required": ["rounds"],
    },
    effect="draft",
    data_class="company_scoped",
    allowed_roles=WORKFLOW_ROLES,
    surfaces=BUILDER_SURFACE,
)
async def _draft_hiring_workflow(args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
    req = await _requisition(ctx)
    profile = _profile_for(req)
    rounds, notes = _normalise_rounds(args.get("rounds"), profile)
    if not rounds:
        return ToolOutput(data={"error": "no usable rounds", "notes": notes})

    existing = await _current_workflow(ctx)
    if existing is not None and existing["status"] == "draft":
        # create_draft refuses a second draft, so this proposal would 409. Say
        # so here instead, where the model can still change course.
        return ToolOutput(
            data={
                "error": (
                    "This opening already has a draft. Add to it with "
                    "draft_workflow_round, or tell the user to discard the "
                    "draft first if they want to start over."
                ),
                "existing_draft_version": existing["version"],
            }
        )

    covered = {c["id"] for r in rounds for c in r["criteria"]}
    uncovered = [c.name for c in profile.competencies if c.id not in covered]

    proposal = Proposal(
        kind="workflow",
        title=f"Hiring process — {req['title']}",
        summary=f"{len(rounds)} rounds · {_round_summary(rounds)}",
        commit=CommitSpec(
            method="POST",
            path=f"/hr/requisitions/{req['id']}/workflows/apply",
            body={
                "name": str(args.get("name", "")).strip()[:200] or None,
                "rounds": [
                    {
                        "title": r["title"],
                        "kind": r["kind"],
                        "pass_threshold": r["pass_threshold"],
                        "deadline_days": r["deadline_days"],
                        "criteria": r["criteria"],
                    }
                    for r in rounds
                ],
            },
            label="Add to canvas",
        ),
        rationale=str(args.get("rationale", "")).strip(),
        # Not a risk_note about email: committing this creates a DRAFT, which
        # no candidate can see. Saying otherwise would be crying wolf on the
        # one field the review panel renders loudly.
        risk_note=None,
        citations=[_requisition_citation(req)],
    )

    return ToolOutput(
        data={
            "drafted_rounds": len(rounds),
            "competencies_left_unmeasured": uncovered,
            "adjustments": notes,
            "note": (
                "Nothing was created. This is a preview on the user's canvas. "
                "Even after they accept it, the workflow is a draft that they "
                "must publish separately before any candidate sees it."
            ),
        },
        proposals=[proposal],
        citations=[_requisition_citation(req)],
    )


@registry.tool(
    name="draft_workflow_round",
    description=(
        "Propose ONE extra round on the end of the existing draft. Produces a "
        "preview; creates nothing. Use when a draft already exists and the user "
        "wants to add to it rather than start over."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": ["mcq", "coding", "ai_interview", "human_review"],
            },
            "pass_threshold": {"type": "number", "description": "Percentage 0-100."},
            "deadline_days": {"type": "integer"},
            "competency_ids": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
        },
        "required": ["title", "kind"],
    },
    effect="draft",
    data_class="company_scoped",
    allowed_roles=WORKFLOW_ROLES,
    surfaces=BUILDER_SURFACE,
)
async def _draft_workflow_round(args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
    req = await _requisition(ctx)
    wf = await _current_workflow(ctx)
    if wf is None:
        return ToolOutput(
            data={"error": "no workflow yet — use draft_hiring_workflow instead"}
        )
    if wf["status"] != "draft":
        return ToolOutput(
            data={
                "error": (
                    f"version {wf['version']} is published and cannot be "
                    "changed. The user must open it as a new version first — "
                    "the 'Edit as new version' button on the canvas."
                )
            }
        )

    profile = _profile_for(req)
    rounds, notes = _normalise_rounds([args], profile)
    if not rounds:
        return ToolOutput(data={"error": "that round is not usable", "notes": notes})
    r = rounds[0]

    existing = await load_rounds(_db(ctx), wf["id"])
    if len(existing) >= MAX_ROUNDS:
        return ToolOutput(
            data={"error": f"this workflow already has the maximum {MAX_ROUNDS} rounds"}
        )

    needs_exam = r["kind"] in EXAM_BACKED_KINDS
    proposal = Proposal(
        kind="workflow_round",
        title=f"Add round — {r['title']}",
        summary=(
            f"{r['kind'].replace('_', ' ')}"
            + (f" · advance at {r['pass_threshold']:g}%" if r["pass_threshold"] else "")
            + (f" · assesses {len(r['criteria'])}" if r["criteria"] else "")
        ),
        commit=CommitSpec(
            method="POST",
            path=f"/hr/workflows/{wf['id']}/rounds",
            body={
                "title": r["title"],
                "kind": r["kind"],
                "pass_threshold": r["pass_threshold"],
                "deadline_days": r["deadline_days"],
                "criteria": r["criteria"],
            },
            label="Add round",
        ),
        rationale=str(args.get("rationale", "")).strip(),
        risk_note=None,
        citations=[_requisition_citation(req)],
    )
    return ToolOutput(
        data={
            "drafted": r["title"],
            "position": len(existing) + 1,
            "still_needs_questions_attached": needs_exam,
            "adjustments": notes,
            "note": "Nothing was added. This is a preview for the user to accept.",
        },
        proposals=[proposal],
    )


@registry.tool(
    name="draft_round_criteria",
    description=(
        "Propose what an EXISTING round assesses, replacing its current "
        "competencies. Produces a preview; changes nothing. Give competency ids "
        "from get_opening_under_design — the rating scale for each is attached "
        "automatically."
    ),
    parameters={
        "type": "object",
        "properties": {
            "round_id": {"type": "string", "description": "From get_current_workflow."},
            "competency_ids": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
        },
        "required": ["round_id", "competency_ids"],
    },
    effect="draft",
    data_class="company_scoped",
    allowed_roles=WORKFLOW_ROLES,
    surfaces=BUILDER_SURFACE,
)
async def _draft_round_criteria(args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
    req = await _requisition(ctx)
    wf = await _current_workflow(ctx)
    if wf is None or wf["status"] != "draft":
        return ToolOutput(data={"error": "there is no editable draft to change"})

    try:
        round_id = uuid.UUID(str(args.get("round_id", "")).strip())
    except ValueError:
        return ToolOutput(data={"error": "round_id must be a valid id from get_current_workflow"})

    # The round must belong to THIS opening's draft. Without this check a model
    # given a round id from anywhere would draft a commit against it, and the
    # commit path takes the workflow id from the URL — so the user would be one
    # click from editing a workflow they were not looking at.
    rounds = await load_rounds(_db(ctx), wf["id"])
    target = next((r for r in rounds if str(r["id"]) == str(round_id)), None)
    if target is None:
        return ToolOutput(data={"error": "that round is not part of this opening's draft"})

    profile = _profile_for(req)
    criteria, unknown = _resolve_criteria(profile, args.get("competency_ids"))
    if not criteria:
        return ToolOutput(
            data={
                "error": "none of those competency ids exist in this role model",
                "unknown_ids": unknown,
            }
        )

    proposal = Proposal(
        kind="workflow_round",
        title=f"What “{target['title']}” assesses",
        summary=", ".join(c["name"] for c in criteria),
        commit=CommitSpec(
            method="PUT",
            path=f"/hr/workflows/{wf['id']}/rounds/{round_id}/criteria",
            body={"criteria": criteria},
            label="Set competencies",
        ),
        rationale=str(args.get("rationale", "")).strip(),
        risk_note=None,
        citations=[_requisition_citation(req)],
    )
    return ToolOutput(
        data={
            "round": target["title"],
            "competencies": [c["name"] for c in criteria],
            "ignored_unknown_ids": unknown,
            "note": (
                "Nothing changed. Replacing a round's competencies replaces "
                "the whole set, not just adds to it."
            ),
        },
        proposals=[proposal],
    )


@registry.tool(
    name="draft_workflow_settings",
    description=(
        "Propose the automation settings for the draft: what happens without "
        "anyone pressing a button. Produces a preview; changes nothing. There "
        "is no setting that rejects a candidate — do not offer one."
    ),
    parameters={
        "type": "object",
        "properties": {
            "auto_score_on_apply": {"type": "boolean"},
            "auto_assign_first_round": {"type": "boolean"},
            "auto_advance_rounds": {"type": "boolean"},
            "reminders_enabled": {"type": "boolean"},
            "shortlist_ats_threshold": {
                "type": "integer",
                "description": "0-10 ATS score at or above which an applicant is shortlisted.",
            },
            "hold_band": {
                "type": "integer",
                "description": (
                    "Percentage points below a round threshold that still route "
                    "the candidate to a human rather than stalling."
                ),
            },
            "rationale": {"type": "string"},
        },
    },
    effect="draft",
    data_class="company_scoped",
    allowed_roles=WORKFLOW_ROLES,
    surfaces=BUILDER_SURFACE,
)
async def _draft_workflow_settings(args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
    req = await _requisition(ctx)
    wf = await _current_workflow(ctx)
    if wf is None or wf["status"] != "draft":
        return ToolOutput(data={"error": "there is no editable draft to configure"})

    # An allow-list, not a pass-through of args. The service layer has its own
    # (update_settings ignores anything unrecognised), so this is the second of
    # two — but the first one a reviewer reads, and the place where "the agent
    # cannot introduce a rejection mechanism" is legible rather than implied.
    booleans = (
        "auto_score_on_apply",
        "auto_assign_first_round",
        "auto_advance_rounds",
        "reminders_enabled",
    )
    fields: dict[str, Any] = {}
    for key in booleans:
        if key in args:
            fields[key] = bool(args[key])
    for key, lo, hi in (("shortlist_ats_threshold", 0, 10), ("hold_band", 0, 100)):
        if key in args and args[key] is not None:
            try:
                fields[key] = min(hi, max(lo, int(args[key])))
            except (TypeError, ValueError):
                continue

    if not fields:
        return ToolOutput(data={"error": "no recognised settings were given"})

    labels = {
        "auto_score_on_apply": "score resumes on arrival",
        "auto_assign_first_round": "send the first round automatically",
        "auto_advance_rounds": "advance between rounds automatically",
        "reminders_enabled": "remind candidates",
        "shortlist_ats_threshold": "auto-shortlist at ATS",
        "hold_band": "hold band",
    }
    summary = ", ".join(
        f"{labels[k]}: {'on' if v is True else 'off' if v is False else v}"
        for k, v in fields.items()
    )

    return ToolOutput(
        data={
            "settings": fields,
            "note": (
                "Nothing changed. None of these can reject a candidate — "
                "below-threshold results go to the decision queue for a person."
            ),
        },
        proposals=[
            Proposal(
                kind="workflow_settings",
                title="Automation settings",
                summary=summary,
                commit=CommitSpec(
                    method="PATCH",
                    path=f"/hr/workflows/{wf['id']}",
                    body=fields,
                    label="Apply settings",
                ),
                rationale=str(args.get("rationale", "")).strip(),
                risk_note=None,
                citations=[_requisition_citation(req)],
            )
        ],
    )
