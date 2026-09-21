"""Workflow review, dry run, stage SLAs and exceptions — PH4 Wave 2 (O6, O2, O1).

Two routers:

* ``hr_router`` (``/hr``, HR managers): submit a version for review, withdraw
  or reopen it, run and read the dry run, set stage owners and SLAs, read the
  SLA board, and raise / resolve / reassign exceptions.
* ``admin_router`` (``/admin``, the company super admin): the review queue, one
  version in full, approve, request changes. Decision D4-2.

The company always comes from the session. Nothing here changes a candidate's
status: review gates a workflow's configuration, the dry run writes only its own
result, and SLAs and exceptions inform a person.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from shared.intelligence import baseline_profile, compute_profile_id
from sqlalchemy import text

from app.database import DbSessionDep
from app.dependencies import HrCtxDep, SuperAdminCtxDep
from app.stage_sla import (
    StageError,
    eligible_owners,
    list_exceptions,
    raise_exception,
    reassign_exception,
    resolve_exception,
    set_stage_setting,
    sla_board,
    stage_settings,
)
from app.utils.request_ip import extract_client_ip, extract_user_agent
from app.workflow_review import (
    ReviewError,
    approve,
    history,
    pending_for_company,
    reopen,
    request_changes,
    submit,
    withdraw,
)
from app.workflow_simulation import SimulationError, latest_simulation, simulate
from app.workflows import TASK_KINDS, load_criteria, load_rounds, validate

log = structlog.get_logger(__name__)

hr_router = APIRouter(prefix="/hr", tags=["workflow-ops"])
admin_router = APIRouter(prefix="/admin", tags=["workflow-review"])

_LEVELS = ("entry", "mid", "senior")


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class StageIn(BaseModel):
    # None is the final-decision stage.
    round_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    sla_hours: int | None = Field(default=None, ge=1, le=8760)


class ExceptionIn(BaseModel):
    reason: str = Field(min_length=10, max_length=1000)
    owner_user_id: uuid.UUID | None = None


class ResolveIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class ReassignIn(BaseModel):
    owner_user_id: uuid.UUID


def _meta(request: Request) -> dict[str, str | None]:
    return {"ip_address": extract_client_ip(request), "user_agent": extract_user_agent(request)}


def _raise(exc: ReviewError | SimulationError | StageError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


async def _requisition_of(db: DbSessionDep, company_id: uuid.UUID, workflow_id: uuid.UUID) -> Any:
    row = (
        await db.execute(
            text(
                "SELECT w.requisition_id, r.title, r.level FROM workflows w"
                "  JOIN job_requisitions r ON r.id = w.requisition_id"
                " WHERE w.id = :w AND w.company_id = :c AND w.deleted_at IS NULL"
            ),
            {"w": workflow_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return row


async def _competencies(db: DbSessionDep, company_id: uuid.UUID, workflow_id: uuid.UUID) -> list[dict[str, Any]]:
    """The role model's competencies, for the coverage check — as the builder computes them."""
    req = await _requisition_of(db, company_id, workflow_id)
    level = req["level"] if req["level"] in _LEVELS else "mid"
    profile = baseline_profile(
        profile_id=compute_profile_id(job_title=req["title"], seniority=level),
        job_title=req["title"],
        seniority=level,  # type: ignore[arg-type]
    )
    return [{"id": c.id, "name": c.name, "weight": c.weight} for c in profile.competencies]


async def _review_state(db: DbSessionDep, company_id: uuid.UUID, workflow_id: uuid.UUID) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT w.review_status, w.submitted_for_review_at, w.reviewed_at, w.review_note,"
                "       sb.full_name AS submitted_by_name, rb.full_name AS reviewed_by_name"
                "  FROM workflows w"
                "  LEFT JOIN users sb ON sb.id = w.submitted_by_user_id"
                "  LEFT JOIN users rb ON rb.id = w.reviewed_by_user_id"
                " WHERE w.id = :w AND w.company_id = :c AND w.deleted_at IS NULL"
            ),
            {"w": workflow_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return {
        "review_status": row["review_status"],
        "submitted_at": row["submitted_for_review_at"].isoformat()
        if row["submitted_for_review_at"] else None,
        "submitted_by_name": row["submitted_by_name"],
        "reviewed_at": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
        "reviewed_by_name": row["reviewed_by_name"],
        "note": row["review_note"],
        "history": await history(db, company_id=company_id, workflow_id=workflow_id),
    }


# ---------------------------------------------------------------------------
# HR — review (O6)
# ---------------------------------------------------------------------------
@hr_router.get("/workflows/{workflow_id}/review")
async def get_review(workflow_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    _uid, company_id = ctx
    return await _review_state(db, company_id, workflow_id)


@hr_router.post("/workflows/{workflow_id}/submit-review")
async def post_submit_review(
    workflow_id: uuid.UUID, body: NoteIn, request: Request, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    comps = await _competencies(db, company_id, workflow_id)
    try:
        out = await submit(db, company_id=company_id, workflow_id=workflow_id, actor=uid,
                           note=body.note, profile_competencies=comps, **_meta(request))
    except ReviewError as exc:
        await db.rollback()
        raise _raise(exc) from exc
    await db.commit()
    return {**out, "review": await _review_state(db, company_id, workflow_id)}


@hr_router.post("/workflows/{workflow_id}/withdraw-review")
async def post_withdraw_review(
    workflow_id: uuid.UUID, request: Request, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        await withdraw(db, company_id=company_id, workflow_id=workflow_id, actor=uid,
                       **_meta(request))
    except ReviewError as exc:
        await db.rollback()
        raise _raise(exc) from exc
    await db.commit()
    return await _review_state(db, company_id, workflow_id)


@hr_router.post("/workflows/{workflow_id}/reopen")
async def post_reopen(
    workflow_id: uuid.UUID, body: NoteIn, request: Request, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        await reopen(db, company_id=company_id, workflow_id=workflow_id, actor=uid,
                     note=body.note, **_meta(request))
    except ReviewError as exc:
        await db.rollback()
        raise _raise(exc) from exc
    await db.commit()
    return await _review_state(db, company_id, workflow_id)


# ---------------------------------------------------------------------------
# HR — dry run (O2)
# ---------------------------------------------------------------------------
@hr_router.post("/workflows/{workflow_id}/simulate")
async def post_simulate(
    workflow_id: uuid.UUID, request: Request, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """Run the dry run. Writes its own result and an audit row — nothing else."""
    uid, company_id = ctx
    comps = await _competencies(db, company_id, workflow_id)
    try:
        out = await simulate(db, company_id=company_id, workflow_id=workflow_id, actor=uid,
                             profile_competencies=comps, **_meta(request))
    except SimulationError as exc:
        await db.rollback()
        raise _raise(exc) from exc
    await db.commit()
    return out


@hr_router.get("/workflows/{workflow_id}/simulation")
async def get_simulation(
    workflow_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any] | None:
    _uid, company_id = ctx
    await _requisition_of(db, company_id, workflow_id)
    return await latest_simulation(db, company_id=company_id, workflow_id=workflow_id)


# ---------------------------------------------------------------------------
# HR — stage owners and SLAs, the board (O1)
# ---------------------------------------------------------------------------
@hr_router.get("/workflows/{workflow_id}/stages")
async def get_stages(workflow_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    await _requisition_of(db, company_id, workflow_id)
    return await stage_settings(db, company_id=company_id, workflow_id=workflow_id)


@hr_router.put("/workflows/{workflow_id}/stages")
async def put_stage(
    workflow_id: uuid.UUID, body: StageIn, ctx: HrCtxDep, db: DbSessionDep
) -> list[dict[str, Any]]:
    """Set one stage's owner and SLA. Allowed on a live version: nobody is moved by it."""
    uid, company_id = ctx
    try:
        await set_stage_setting(
            db, company_id=company_id, workflow_id=workflow_id, round_id=body.round_id,
            owner_user_id=body.owner_user_id, sla_hours=body.sla_hours, actor=uid,
        )
    except StageError as exc:
        await db.rollback()
        raise _raise(exc) from exc
    await db.commit()
    return await stage_settings(db, company_id=company_id, workflow_id=workflow_id)


@hr_router.get("/stage-owners")
async def get_stage_owners(ctx: HrCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    return await eligible_owners(db, company_id=company_id)


@hr_router.get("/stage-sla")
async def get_stage_sla(
    ctx: HrCtxDep,
    db: DbSessionDep,
    requisition_id: Annotated[uuid.UUID | None, Query()] = None,
    state: Annotated[str | None, Query(max_length=40)] = None,
) -> list[dict[str, Any]]:
    """Applications against their stage SLA, worst first. ``state`` filters, comma-separated."""
    _uid, company_id = ctx
    states = {s for s in (state or "").split(",") if s} or None
    if states and not states <= {"overdue", "due_soon", "on_track"}:
        raise HTTPException(status_code=422, detail="state is overdue, due_soon or on_track.")
    return await sla_board(db, company_id=company_id, requisition_id=requisition_id,
                           states=states)


# ---------------------------------------------------------------------------
# HR — exceptions (O1)
# ---------------------------------------------------------------------------
@hr_router.get("/enrolments/{enrolment_id}/exceptions")
async def get_exceptions(
    enrolment_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    return await list_exceptions(db, company_id=company_id, enrolment_id=enrolment_id)


@hr_router.post("/enrolments/{enrolment_id}/exceptions", status_code=201)
async def post_exception(
    enrolment_id: uuid.UUID, body: ExceptionIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        out = await raise_exception(db, company_id=company_id, enrolment_id=enrolment_id,
                                    reason=body.reason, owner_user_id=body.owner_user_id,
                                    actor=uid)
    except StageError as exc:
        await db.rollback()
        raise _raise(exc) from exc
    await db.commit()
    return out


@hr_router.post("/exceptions/{exception_id}/resolve")
async def post_resolve_exception(
    exception_id: uuid.UUID, body: ResolveIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        out = await resolve_exception(db, company_id=company_id, exception_id=exception_id,
                                      note=body.note, actor=uid)
    except StageError as exc:
        await db.rollback()
        raise _raise(exc) from exc
    await db.commit()
    return out


@hr_router.patch("/exceptions/{exception_id}")
async def patch_exception(
    exception_id: uuid.UUID, body: ReassignIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        out = await reassign_exception(db, company_id=company_id, exception_id=exception_id,
                                       owner_user_id=body.owner_user_id, actor=uid)
    except StageError as exc:
        await db.rollback()
        raise _raise(exc) from exc
    await db.commit()
    return out


# ---------------------------------------------------------------------------
# Super admin — the review queue (O6, D4-2)
# ---------------------------------------------------------------------------
@admin_router.get("/workflow-reviews")
async def get_review_queue(ctx: SuperAdminCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    return await pending_for_company(db, company_id=company_id)


@admin_router.get("/workflow-reviews/{workflow_id}")
async def get_review_detail(
    workflow_id: uuid.UUID, ctx: SuperAdminCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """One version as the reviewer sees it: rounds, branches, criteria, checks, dry run."""
    _uid, company_id = ctx
    req = await _requisition_of(db, company_id, workflow_id)
    wf = (
        await db.execute(
            text(
                "SELECT version, status, name, auto_score_on_apply, auto_assign_first_round,"
                "       auto_advance_rounds, reminders_enabled, shortlist_ats_threshold,"
                "       hold_band FROM workflows WHERE id = :w"
            ),
            {"w": workflow_id},
        )
    ).mappings().first()
    rounds = await load_rounds(db, workflow_id)
    criteria = await load_criteria(db, [r["id"] for r in rounds])
    report = await validate(db, workflow_id, await _competencies(db, company_id, workflow_id))
    titles = {str(r["id"]): r["title"] for r in rounds}

    def title_of(rid: Any) -> str | None:
        return titles.get(str(rid)) if rid else None

    # PH4-D4: the approver sees a task round's own brief and items — what
    # goes live is what they approved, the same reason exam content is
    # already frozen for review.
    task_round_ids = [r["id"] for r in rounds if r["kind"] in TASK_KINDS]
    tasks: dict[str, Any] = {}
    if task_round_ids:
        tasks = {
            str(t["round_id"]): dict(t)
            for t in (
                await db.execute(
                    text(
                        "SELECT round_id, kind, brief, brief_translations, items,"
                        " min_artifacts, max_artifacts, allow_files, allow_links,"
                        " allowed_link_domains FROM round_tasks WHERE round_id = ANY(:ids)"
                    ),
                    {"ids": task_round_ids},
                )
            ).mappings().all()
        }

    return {
        "workflow_id": str(workflow_id),
        "requisition_id": str(req["requisition_id"]),
        "opening_title": req["title"],
        "version": wf["version"] if wf else None,
        "status": wf["status"] if wf else None,
        "settings": {k: wf[k] for k in wf if k not in ("version", "status", "name")}
        if wf else {},
        "rounds": [
            {
                "round_id": str(r["id"]),
                "position": r["position"],
                "title": r["title"],
                "kind": r["kind"],
                "pass_threshold": float(r["pass_threshold"])
                if r["pass_threshold"] is not None else None,
                "deadline_days": r["deadline_days"],
                "on_pass": title_of(r["on_pass_next_round_id"]),
                "on_fail": title_of(r["on_fail_next_round_id"]),
                "fast_track_min_percent": float(r["fast_track_min_percent"])
                if r["fast_track_min_percent"] is not None else None,
                "on_fast_track": title_of(r["on_fast_track_next_round_id"]),
                "criteria": [
                    {"name": c["competency_name"], "weight": c["weight"]}
                    for c in criteria.get(str(r["id"]), [])
                ],
                "task": (
                    {k: v for k, v in tasks[str(r["id"])].items() if k != "round_id"}
                    if str(r["id"]) in tasks else None
                ),
            }
            for r in rounds
        ],
        "validation": report.as_dict(),
        "simulation": await latest_simulation(db, company_id=company_id, workflow_id=workflow_id),
        "stages": await stage_settings(db, company_id=company_id, workflow_id=workflow_id),
        "review": await _review_state(db, company_id, workflow_id),
    }


@admin_router.post("/workflow-reviews/{workflow_id}/approve")
async def post_approve(
    workflow_id: uuid.UUID, body: NoteIn, request: Request, ctx: SuperAdminCtxDep,
    db: DbSessionDep,
) -> dict[str, Any]:
    uid, company_id = ctx
    comps = await _competencies(db, company_id, workflow_id)
    try:
        await approve(db, company_id=company_id, workflow_id=workflow_id, reviewer=uid,
                      note=body.note, profile_competencies=comps, **_meta(request))
    except ReviewError as exc:
        await db.rollback()
        raise _raise(exc) from exc
    await db.commit()
    return await _review_state(db, company_id, workflow_id)


@admin_router.post("/workflow-reviews/{workflow_id}/request-changes")
async def post_request_changes(
    workflow_id: uuid.UUID, body: NoteIn, request: Request, ctx: SuperAdminCtxDep,
    db: DbSessionDep,
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        await request_changes(db, company_id=company_id, workflow_id=workflow_id, reviewer=uid,
                              note=body.note, **_meta(request))
    except ReviewError as exc:
        await db.rollback()
        raise _raise(exc) from exc
    await db.commit()
    return await _review_state(db, company_id, workflow_id)
