"""HR's side of human interview scorecards — PH4-A1.

Assign interviewers, withdraw an assignment, and read the evidence. Company
scoped through ``HrCtxDep``; the company always comes from the session.

HR reads scorecards here and decides elsewhere: ``round-review`` for pass/hold
and ``/decision`` for hire/reject. Nothing in this router changes a candidate's
status, which is what keeps "scorecards inform the decision" from quietly
becoming "scorecards make it".
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.database import DbSessionDep
from app.dependencies import HrCtxDep
from app.interview_kits import KitCriterionIn, get_kit, update_kit
from app.interviewer_scorecards import (
    RequestMeta,
    ScorecardError,
    assign,
    list_assignable_interviewers,
    scorecards_for_enrolment,
    withdraw,
)
from app.utils.request_ip import extract_client_ip, extract_user_agent

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-scorecards"])


def _meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip_address=extract_client_ip(request), user_agent=extract_user_agent(request)
    )


class AssignIn(BaseModel):
    round_id: uuid.UUID
    interviewer_user_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    due_at: datetime | None = None


class WithdrawIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


@router.get("/interviewers")
async def get_interviewers(ctx: HrCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    """Who in this company can be assigned to interview."""
    _uid, company_id = ctx
    return await list_assignable_interviewers(db, company_id=company_id)


@router.get("/enrolments/{enrolment_id}/scorecards")
async def get_enrolment_scorecards(
    enrolment_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        return await scorecards_for_enrolment(
            db, company_id=company_id, enrolment_id=enrolment_id, viewer_user_id=uid
        )
    except ScorecardError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/enrolments/{enrolment_id}/scorecards", status_code=201)
async def assign_interviewers(
    enrolment_id: uuid.UUID,
    body: AssignIn,
    request: Request,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        out = await assign(
            db, company_id=company_id, enrolment_id=enrolment_id, round_id=body.round_id,
            interviewer_user_ids=body.interviewer_user_ids, assigned_by=uid,
            due_at=body.due_at, meta=_meta(request),
        )
    except ScorecardError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await db.commit()
    return out


@router.post(
    "/scorecards/{scorecard_id}/withdraw",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def withdraw_assignment(
    scorecard_id: uuid.UUID,
    body: WithdrawIn,
    request: Request,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> Response:
    uid, company_id = ctx
    try:
        await withdraw(
            db, company_id=company_id, scorecard_id=scorecard_id, actor=uid,
            reason=body.reason, meta=_meta(request),
        )
    except ScorecardError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class KitCriterionBody(BaseModel):
    competency_id: str = Field(min_length=1, max_length=200)
    what_to_evaluate: list[str] = Field(default_factory=list, max_length=10)
    look_for: list[str] = Field(default_factory=list, max_length=10)
    probes: list[str] = Field(default_factory=list, max_length=10)


class KitIn(BaseModel):
    instructions: str | None = Field(default=None, max_length=8000)
    interviewer_notes_from_hr: str | None = Field(default=None, max_length=8000)
    criteria: list[KitCriterionBody] = Field(default_factory=list, max_length=50)


@router.get("/rounds/{round_id}/kit")
async def get_round_kit(round_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    """A human interview round's kit (PH4-A5). Returns criteria even with no kit."""
    _uid, company_id = ctx
    try:
        return await get_kit(db, company_id=company_id, round_id=round_id)
    except ScorecardError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.put("/rounds/{round_id}/kit")
async def put_round_kit(
    round_id: uuid.UUID,
    body: KitIn,
    request: Request,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> dict[str, Any]:
    """Replace a round's kit. Guidance only — criteria are frozen and not editable here."""
    uid, company_id = ctx
    try:
        out = await update_kit(
            db, company_id=company_id, round_id=round_id, actor=uid,
            instructions=body.instructions,
            interviewer_notes=body.interviewer_notes_from_hr,
            criteria=[
                KitCriterionIn(
                    competency_id=c.competency_id, what_to_evaluate=c.what_to_evaluate,
                    look_for=c.look_for, probes=c.probes,
                )
                for c in body.criteria
            ],
            meta=_meta(request),
        )
    except ScorecardError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await db.commit()
    return out
