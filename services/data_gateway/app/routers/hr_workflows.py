"""HR workflow authoring endpoints — Group C.

The API surface the visual builder is drawn on.

    GET    /hr/requisitions/{id}/role-model        competencies HR picks from
    GET    /hr/requisitions/{id}/workflows         every version, newest first
    POST   /hr/requisitions/{id}/workflows         start a draft
    POST   /hr/requisitions/{id}/workflows/apply   a whole workflow, atomically
    GET    /hr/workflows/{id}                      full workflow: rounds + criteria
    PATCH  /hr/workflows/{id}                      automation settings (C9)
    DELETE /hr/workflows/{id}                      discard a draft
    POST   /hr/workflows/{id}/rounds               append a round
    PATCH  /hr/workflows/{id}/rounds/{rid}         edit a round
    DELETE /hr/workflows/{id}/rounds/{rid}         remove one, healing the chain
    PUT    /hr/workflows/{id}/rounds/order         reorder
    PUT    /hr/workflows/{id}/rounds/{rid}/criteria  freeze what it assesses
    GET    /hr/workflows/{id}/validate             errors + coverage report
    POST   /hr/workflows/{id}/publish              go live
    POST   /hr/workflows/{id}/clone                edit a published version
    GET    /hr/requisitions/{id}/decision-queue    advanced AND held, together
    POST   /hr/enrolments/{id}/release-hold        a person lets someone continue

Every structural write goes through ``app.workflows``, which refuses to touch a
published workflow. That guard lives in the service layer rather than here on
purpose: the copilot's commit path and any future import will hit it too.

MULTI-TENANT: company scope comes from ``HrCtxDep`` and every workflow is
re-checked against it, so a valid id from another tenant reads as 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from shared.intelligence import baseline_profile, compute_profile_id
from sqlalchemy import text

from app.database import DbSessionDep
from app.dependencies import HrCtxDep
from app.workflow_runner import decision_queue, record_result, release_hold
from app.workflows import (
    EXAM_BACKED_KINDS,
    MAX_ROUNDS,
    ROUND_KINDS,
    WorkflowError,
    add_round,
    clone_for_edit,
    create_draft,
    discard_draft,
    load_criteria,
    load_rounds,
    publish,
    remove_round,
    reorder_rounds,
    set_round_criteria,
    update_round,
    update_settings,
    validate,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/hr", tags=["hr-workflows"])

_LEVELS = ("entry", "mid", "senior")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class CriterionIn(BaseModel):
    """One competency a round assesses — frozen onto the round as given."""

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    kind: str | None = Field(default=None, max_length=40)
    weight: float = Field(gt=0, le=1)
    anchors: dict[str, str] | None = None
    probes: list[str] | None = Field(default=None, max_length=6)


class RoundIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: str
    pass_threshold: float | None = Field(default=None, ge=0, le=100)
    time_limit_seconds: int | None = Field(default=None, gt=0, le=86_400)
    deadline_days: int = Field(default=7, gt=0, le=365)
    exam_round_id: uuid.UUID | None = None
    criteria: list[CriterionIn] = Field(default_factory=list, max_length=8)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        if v not in ROUND_KINDS:
            raise ValueError(f"kind must be one of {sorted(ROUND_KINDS)}")
        return v


class RoundPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pass_threshold: float | None = Field(default=None, ge=0, le=100)
    time_limit_seconds: int | None = Field(default=None, gt=0, le=86_400)
    deadline_days: int | None = Field(default=None, gt=0, le=365)
    exam_round_id: uuid.UUID | None = None


class SettingsPatch(BaseModel):
    """C9. Note the absence of anything that could reject a candidate."""

    name: str | None = Field(default=None, max_length=200)
    auto_score_on_apply: bool | None = None
    auto_assign_first_round: bool | None = None
    auto_advance_rounds: bool | None = None
    reminders_enabled: bool | None = None
    shortlist_ats_threshold: int | None = Field(default=None, ge=0, le=10)
    hold_band: int | None = Field(default=None, ge=0, le=100)


class OrderIn(BaseModel):
    round_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_ROUNDS)


class CriteriaIn(BaseModel):
    """A round's whole rubric.

    An object rather than a bare JSON array, matching every other body in this
    router — and required by ``CommitSpec``, whose body is a dict by design, so
    a copilot proposal can target this endpoint at all.
    """

    criteria: list[CriterionIn] = Field(max_length=8)


class ApplyDraftIn(BaseModel):
    """A whole workflow in one payload — what the copilot proposes.

    Exists because a workflow cannot be assembled from independent requests.
    Round positions come from insert order and a round's criteria need its id,
    which does not exist until it is created, so a chain of separate proposals
    would be order-dependent, half-appliable, and wrong the moment a user
    approved them out of sequence. One payload, one transaction, one click.
    """

    name: str | None = Field(default=None, max_length=200)
    rounds: list[RoundIn] = Field(min_length=1, max_length=MAX_ROUNDS)
    settings: SettingsPatch | None = None


class ReleaseIn(BaseModel):
    to_status: str = "shortlisted"
    reason: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------
async def _owned_workflow(
    db: DbSessionDep, company_id: uuid.UUID, workflow_id: uuid.UUID
) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT * FROM workflows"
                " WHERE id = :i AND company_id = :c AND deleted_at IS NULL"
            ),
            {"i": workflow_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return dict(row)


async def _owned_requisition(
    db: DbSessionDep, company_id: uuid.UUID, requisition_id: uuid.UUID
) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT id, title, level FROM job_requisitions"
                " WHERE id = :i AND company_id = :c AND deleted_at IS NULL"
            ),
            {"i": requisition_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Requisition not found.")
    return dict(row)


def _settings_of(row: dict[str, Any]) -> dict[str, Any]:
    return {
        k: row[k]
        for k in (
            "auto_score_on_apply", "auto_assign_first_round", "auto_advance_rounds",
            "reminders_enabled", "shortlist_ats_threshold", "hold_band",
        )
    }


# ---------------------------------------------------------------------------
# The competencies HR chooses from
# ---------------------------------------------------------------------------
@router.get("/requisitions/{requisition_id}/role-model")
async def role_model(
    requisition_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """The role's competencies, for the builder's criteria picker.

    Taxonomy baseline only — no LLM call. This is fetched every time the builder
    opens, and a multi-second refinement round-trip in front of a form is a poor
    trade for a difference HR is about to override anyway. The refinement still
    happens where it counts: at interview time, for workflows that have not
    frozen their own rubric.
    """
    _hr_uid, company_id = ctx
    req = await _owned_requisition(db, company_id, requisition_id)
    level = req["level"] if req["level"] in _LEVELS else "mid"
    profile = baseline_profile(
        profile_id=compute_profile_id(job_title=req["title"], seniority=level),
        job_title=req["title"],
        seniority=level,  # type: ignore[arg-type]
    )
    return {
        "job_title": profile.job_title,
        "domain_family": profile.domain_family,
        "domain_label": profile.domain_label,
        "source": profile.source,
        "competencies": [
            {
                "id": c.id, "name": c.name, "kind": c.kind, "weight": c.weight,
                # Sent so the builder can freeze the full rubric — not just the
                # labels — when HR selects a competency (C8).
                "anchors": {"low": c.anchors.low, "mid": c.anchors.mid,
                            "high": c.anchors.high},
                "probes": list(c.probes),
            }
            for c in profile.competencies
        ],
    }


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------
@router.get("/requisitions/{requisition_id}/workflows")
async def list_workflows(
    requisition_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> list[dict[str, Any]]:
    _hr_uid, company_id = ctx
    await _owned_requisition(db, company_id, requisition_id)
    rows = (
        await db.execute(
            text(
                # DISTINCT on both counts, not just the enrolments one. Joining
                # rounds AND enrolments to the same workflow multiplies them:
                # 4 rounds x 18 enrolled candidates is 72 rows, and a plain
                # count(r.id) reported "72 rounds" for a four-round workflow.
                # It read correctly right up until the first candidate enrolled.
                "SELECT w.id, w.version, w.status, w.name, w.published_at, w.created_at,"
                "       count(DISTINCT r.id) FILTER (WHERE r.deleted_at IS NULL) AS rounds,"
                "       count(DISTINCT e.id) AS enrolled"
                "  FROM workflows w"
                "  LEFT JOIN workflow_rounds r ON r.workflow_id = w.id"
                "  LEFT JOIN enrolments e ON e.workflow_id = w.id AND e.deleted_at IS NULL"
                " WHERE w.requisition_id = :r AND w.deleted_at IS NULL"
                " GROUP BY w.id ORDER BY w.version DESC"
            ),
            {"r": requisition_id},
        )
    ).mappings().all()
    return [
        {
            "id": str(r["id"]), "version": r["version"], "status": r["status"],
            "name": r["name"], "rounds": int(r["rounds"] or 0),
            # Why an archived version still matters: these candidates are still
            # running it, and it must not be treated as dead.
            "enrolled_candidates": int(r["enrolled"] or 0),
            "published_at": r["published_at"].isoformat() if r["published_at"] else None,
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.post(
    "/requisitions/{requisition_id}/workflows", status_code=status.HTTP_201_CREATED
)
async def start_draft(
    requisition_id: uuid.UUID,
    ctx: HrCtxDep,
    db: DbSessionDep,
    name: Annotated[str | None, Query(max_length=200)] = None,
) -> dict[str, Any]:
    hr_uid, company_id = ctx
    await _owned_requisition(db, company_id, requisition_id)
    try:
        wf_id = await create_draft(
            db, company_id=company_id, requisition_id=requisition_id,
            created_by=hr_uid, name=name,
        )
        await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await get_workflow(wf_id, ctx, db)


@router.post(
    "/requisitions/{requisition_id}/workflows/apply",
    status_code=status.HTTP_201_CREATED,
)
async def apply_draft(
    requisition_id: uuid.UUID, body: ApplyDraftIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """Create a draft and all its rounds in ONE transaction.

    This is the endpoint a copilot proposal commits to. It exists for exactly
    one reason: a workflow is not assemblable from independent requests. Round
    positions come from insert order, and a round’s criteria need its id, which
    does not exist until the round does — so proposing a workflow as five
    separate commits would produce something that is order-dependent and can be
    left half-built by a user who approves three of them and stops.

    Note what this does NOT do: publish. It produces a draft, exactly as if the
    HR manager had dragged the rounds onto the canvas themselves, and they still
    review it and press publish. An agent-authored process reaches a candidate
    only after two human acts, not one.
    """
    hr_uid, company_id = ctx
    await _owned_requisition(db, company_id, requisition_id)
    try:
        wf_id = await create_draft(
            db, company_id=company_id, requisition_id=requisition_id,
            created_by=hr_uid, name=body.name,
        )
        for round_ in body.rounds:
            await add_round(
                db,
                company_id=company_id,
                workflow_id=wf_id,
                title=round_.title,
                kind=round_.kind,
                pass_threshold=round_.pass_threshold,
                time_limit_seconds=round_.time_limit_seconds,
                deadline_days=round_.deadline_days,
                exam_round_id=round_.exam_round_id,
                criteria=[c.model_dump() for c in round_.criteria],
            )
        if body.settings is not None:
            await update_settings(
                db,
                workflow_id=wf_id,
                fields=body.settings.model_dump(exclude_unset=True),
            )
        await db.commit()
    except WorkflowError as exc:
        # One rollback for the whole thing. A partially applied workflow — two
        # of four rounds, in order, looking complete — is worse than none.
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    log.info("hr.workflow.applied", workflow_id=str(wf_id),
             requisition_id=str(requisition_id), rounds=len(body.rounds))
    return await get_workflow(wf_id, ctx, db)


@router.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """The whole workflow: settings, rounds in order, and each round's rubric."""
    _hr_uid, company_id = ctx
    wf = await _owned_workflow(db, company_id, workflow_id)
    rounds = await load_rounds(db, workflow_id)
    criteria = await load_criteria(db, [r["id"] for r in rounds])
    return {
        "id": str(wf["id"]),
        "requisition_id": str(wf["requisition_id"]),
        "version": wf["version"],
        "status": wf["status"],
        "name": wf["name"],
        "editable": wf["status"] == "draft",
        "role_profile_id": wf["role_profile_id"],
        "settings": _settings_of(wf),
        "published_at": wf["published_at"].isoformat() if wf["published_at"] else None,
        "rounds": [
            {
                "id": str(r["id"]),
                "position": r["position"],
                "title": r["title"],
                "kind": r["kind"],
                # Always a percentage, whatever the kind — see
                # workflow_runner.INTERVIEW_SCORE_MAX.
                "pass_threshold": float(r["pass_threshold"])
                if r["pass_threshold"] is not None else None,
                "time_limit_seconds": r["time_limit_seconds"],
                "deadline_days": r["deadline_days"],
                "on_pass_next_round_id": str(r["on_pass_next_round_id"])
                if r["on_pass_next_round_id"] else None,
                "exam_round_id": str(r["exam_round_id"]) if r["exam_round_id"] else None,
                "needs_questions": r["kind"] in EXAM_BACKED_KINDS and not r["exam_round_id"],
                "criteria": criteria.get(str(r["id"]), []),
            }
            for r in rounds
        ],
    }


@router.patch("/workflows/{workflow_id}")
async def patch_settings(
    workflow_id: uuid.UUID, body: SettingsPatch, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    _hr_uid, company_id = ctx
    await _owned_workflow(db, company_id, workflow_id)
    try:
        await update_settings(
            db, workflow_id=workflow_id, fields=body.model_dump(exclude_unset=True)
        )
        await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await get_workflow(workflow_id, ctx, db)


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(
    workflow_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> Response:
    _hr_uid, company_id = ctx
    await _owned_workflow(db, company_id, workflow_id)
    try:
        await discard_draft(db, company_id=company_id, workflow_id=workflow_id)
        await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Rounds
# ---------------------------------------------------------------------------
@router.post("/workflows/{workflow_id}/rounds", status_code=status.HTTP_201_CREATED)
async def create_round(
    workflow_id: uuid.UUID, body: RoundIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    _hr_uid, company_id = ctx
    await _owned_workflow(db, company_id, workflow_id)
    try:
        await add_round(
            db,
            company_id=company_id,
            workflow_id=workflow_id,
            title=body.title,
            kind=body.kind,
            pass_threshold=body.pass_threshold,
            time_limit_seconds=body.time_limit_seconds,
            deadline_days=body.deadline_days,
            exam_round_id=body.exam_round_id,
            criteria=[c.model_dump() for c in body.criteria],
        )
        await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await get_workflow(workflow_id, ctx, db)


@router.patch("/workflows/{workflow_id}/rounds/{round_id}")
async def patch_round(
    workflow_id: uuid.UUID, round_id: uuid.UUID, body: RoundPatch,
    ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    _hr_uid, company_id = ctx
    await _owned_workflow(db, company_id, workflow_id)
    try:
        await update_round(
            db, workflow_id=workflow_id, round_id=round_id,
            fields=body.model_dump(exclude_unset=True),
        )
        await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await get_workflow(workflow_id, ctx, db)


@router.delete("/workflows/{workflow_id}/rounds/{round_id}")
async def delete_round(
    workflow_id: uuid.UUID, round_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    _hr_uid, company_id = ctx
    await _owned_workflow(db, company_id, workflow_id)
    try:
        await remove_round(db, workflow_id=workflow_id, round_id=round_id)
        await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await get_workflow(workflow_id, ctx, db)


@router.put("/workflows/{workflow_id}/rounds/order")
async def put_order(
    workflow_id: uuid.UUID, body: OrderIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    _hr_uid, company_id = ctx
    await _owned_workflow(db, company_id, workflow_id)
    try:
        await reorder_rounds(db, workflow_id=workflow_id, ordered_ids=body.round_ids)
        await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await get_workflow(workflow_id, ctx, db)


@router.put("/workflows/{workflow_id}/rounds/{round_id}/criteria")
async def put_criteria(
    workflow_id: uuid.UUID, round_id: uuid.UUID, body: CriteriaIn,
    ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    """Freeze what a round assesses.

    Replaces the round's whole rubric. The payload carries anchors and probes as
    well as ids, because a rubric without its behavioural bands measures less
    precisely and cannot be reproduced later (C8).
    """
    _hr_uid, company_id = ctx
    await _owned_workflow(db, company_id, workflow_id)
    try:
        await set_round_criteria(
            db, company_id=company_id, round_id=round_id,
            criteria=[c.model_dump() for c in body.criteria],
        )
        await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await get_workflow(workflow_id, ctx, db)


# ---------------------------------------------------------------------------
# Validate / publish / clone
# ---------------------------------------------------------------------------
async def _profile_competencies(
    db: DbSessionDep, company_id: uuid.UUID, requisition_id: uuid.UUID
) -> list[dict[str, Any]]:
    req = await _owned_requisition(db, company_id, requisition_id)
    level = req["level"] if req["level"] in _LEVELS else "mid"
    profile = baseline_profile(
        profile_id=compute_profile_id(job_title=req["title"], seniority=level),
        job_title=req["title"],
        seniority=level,  # type: ignore[arg-type]
    )
    return [{"id": c.id, "name": c.name, "weight": c.weight} for c in profile.competencies]


@router.get("/workflows/{workflow_id}/validate")
async def validate_workflow(
    workflow_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """Blocking errors, plus the live coverage report the builder shows."""
    _hr_uid, company_id = ctx
    wf = await _owned_workflow(db, company_id, workflow_id)
    comps = await _profile_competencies(db, company_id, wf["requisition_id"])
    report = await validate(db, workflow_id, comps)
    return report.as_dict()


@router.post("/workflows/{workflow_id}/publish")
async def publish_workflow(
    workflow_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """Publish a draft, archiving whatever it replaces.

    Returns 422 with the validation report when the draft is not publishable —
    the builder renders those errors inline rather than showing a bare failure.
    """
    _hr_uid, company_id = ctx
    wf = await _owned_workflow(db, company_id, workflow_id)
    comps = await _profile_competencies(db, company_id, wf["requisition_id"])
    try:
        report = await publish(
            db, company_id=company_id, workflow_id=workflow_id, profile_competencies=comps
        )
    except WorkflowError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if not report.publishable:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=report.as_dict()
        )
    await db.commit()
    log.info("hr.workflow.published", workflow_id=str(workflow_id),
             company_id=str(company_id), warnings=len(report.warnings))
    return {**await get_workflow(workflow_id, ctx, db), "validation": report.as_dict()}


@router.post("/workflows/{workflow_id}/clone", status_code=status.HTTP_201_CREATED)
async def clone_workflow(
    workflow_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """Open a published workflow for editing as version n+1.

    The published version is untouched, so every candidate mid-process keeps the
    rounds, thresholds and rubric they started under.
    """
    hr_uid, company_id = ctx
    await _owned_workflow(db, company_id, workflow_id)
    try:
        new_id = await clone_for_edit(
            db, company_id=company_id, workflow_id=workflow_id, created_by=hr_uid
        )
        await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await get_workflow(new_id, ctx, db)


# ---------------------------------------------------------------------------
# The decision queue and holds
# ---------------------------------------------------------------------------
@router.get("/requisitions/{requisition_id}/decision-queue")
async def get_decision_queue(
    requisition_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> list[dict[str, Any]]:
    """Everyone awaiting a decision — advanced and held, in one list.

    Deliberately not two endpoints. Held candidates behind a separate call are
    held candidates nobody opens, which would make "every candidate reaches a
    human decision" true on paper and false in practice (D-05).
    """
    _hr_uid, company_id = ctx
    await _owned_requisition(db, company_id, requisition_id)
    return await decision_queue(db, company_id=company_id, requisition_id=requisition_id)


class RoundReviewIn(BaseModel):
    """A person's verdict on a human_review round."""

    passed: bool
    note: str | None = Field(default=None, max_length=2000)


@router.post("/enrolments/{enrolment_id}/round-review")
async def post_round_review(
    enrolment_id: uuid.UUID, body: RoundReviewIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """Record a reviewer's verdict on the round a candidate is sitting on (C4).

    The runner has always supported this — ``record_result``'s
    ``passed_override`` is exactly it — but nothing outside the tests could
    call it, so a workflow containing a human_review round stalled there: the
    candidate was moved onto the round, the owner was notified, and there was
    no way to say "yes, continue".

    Passing advances to the next round (or completes the workflow, which queues
    the final decision). NOT passing HOLDS — it never rejects, even though a
    person is deciding, because a rejection is a separate, explicit act on the
    decision queue that says so in the ledger (D-05).
    """
    hr_uid, company_id = ctx
    row = (
        await db.execute(
            text(
                "SELECT e.current_round_id, wr.kind, wr.title"
                "  FROM enrolments e"
                "  LEFT JOIN workflow_rounds wr ON wr.id = e.current_round_id"
                "                              AND wr.deleted_at IS NULL"
                " WHERE e.id = :e AND e.company_id = :c AND e.deleted_at IS NULL"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    if row["current_round_id"] is None:
        raise HTTPException(
            status_code=409,
            detail="This candidate is not on a round — record the final decision instead.",
        )
    if row["kind"] != "human_review":
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{row['title']}' is scored by the system, not reviewed. "
                "Its result arrives when the candidate completes it."
            ),
        )

    outcome = await record_result(
        db,
        enrolment_id=enrolment_id,
        round_id=uuid.UUID(str(row["current_round_id"])),
        score=None,
        graded_by="human",
        grader_user_id=hr_uid,
        evidence=body.note,
        passed_override=body.passed,
    )
    await db.commit()
    log.info(
        "hr.round_review.recorded",
        enrolment_id=str(enrolment_id), passed=body.passed, action=outcome.action,
    )
    return outcome.as_dict()


@router.post("/enrolments/{enrolment_id}/release-hold")
async def post_release_hold(
    enrolment_id: uuid.UUID, body: ReleaseIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """Let a held candidate continue. Human-only by construction.

    There is no automated caller for this anywhere in the codebase: deciding
    that a below-threshold candidate should proceed is exactly the judgement
    D-05 reserves for a person.
    """
    hr_uid, company_id = ctx
    owned = await db.scalar(
        text(
            "SELECT 1 FROM enrolments WHERE id = :e AND company_id = :c AND deleted_at IS NULL"
        ),
        {"e": enrolment_id, "c": company_id},
    )
    if owned is None:
        raise HTTPException(status_code=404, detail="Enrolment not found.")
    out = await release_hold(
        db, enrolment_id=enrolment_id, actor_user_id=hr_uid, to_status=body.to_status,
        reason=body.reason,
    )
    await db.commit()
    if out.action == "noop":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=out.reason or "No change.")
    return out.as_dict()
