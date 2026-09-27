"""HR's 90-day hire check-in — PH5-D5-2 (C1 quality-of-hire signal).

Record, correct and read the post-hire outcome for one hire, and list the
hires that are due one. Company scoped through ``HrCtxDep``; the company
always comes from the session.

This never changes a candidate's status. ``app.hire_checkins`` never writes
``enrolments`` and never calls a lifecycle or decision writer — this router
does not either.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.database import DbSessionDep
from app.dependencies import HrCtxDep
from app.hire_checkins import (
    CheckinError,
    RequestMeta,
    correct,
    due,
    list_for_enrolment,
    record,
)
from app.utils.request_ip import extract_client_ip, extract_user_agent

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-hire-checkins"])

#: Shown on the record/correct form (design review, point 8) — exact text.
CHECKIN_NOTICE = (
    "Record what your HR system shows; used only in aggregate; not a "
    "performance record."
)


def _meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip_address=extract_client_ip(request), user_agent=extract_user_agent(request)
    )


class CheckinIn(BaseModel):
    """Recording and correcting a check-in take the same shape — no
    correction reason (security review LOW-2: collected with no stated
    purpose, one refactor away from free text in an audit log that is never
    redacted)."""

    employment: str
    left_reason: str | None = Field(default=None, max_length=32)
    performance: str | None = Field(default=None, max_length=32)


class CheckinOut(BaseModel):
    checkin_id: str
    enrolment_id: str
    kind: str
    employment: str
    left_reason: str | None
    performance: str | None
    recorded_by_user_id: str
    # The recorder's display name (full_name, falling back to email — the
    # interviewer_scorecards precedent), company-scoped. Null if the user no
    # longer exists.
    recorded_by_name: str | None
    recorded_at: str
    supersedes_id: str | None
    superseded: bool


class CheckinWindowOut(BaseModel):
    """When a check-in may be recorded for this hire (security review
    MEDIUM-1) — ``start``/``employed_from``/``closes_at`` are ISO timestamps,
    or all null when the start date cannot be determined at all."""

    start: str | None
    employed_from: str | None
    closes_at: str | None
    open: bool


class CheckinListOut(BaseModel):
    checkins: list[CheckinOut]
    window: CheckinWindowOut
    notice: str


class DueItemOut(BaseModel):
    enrolment_id: str
    # So a row can open the shared CandidateDrawer, which loads by applicant id.
    applicant_id: str
    full_name: str
    job_title: str
    employment_start: str
    due_at: str
    days_since_start: int


# Registered ahead of the parameterised /checkins/{checkin_id}/... route below
# so "due" is never parsed as a checkin id.
@router.get("/checkins/due", response_model=list[DueItemOut])
async def get_checkins_due(ctx: HrCtxDep, db: DbSessionDep) -> list[DueItemOut]:
    """Hires past the window with no live check-in, oldest ``employment_start`` first."""
    _uid, company_id = ctx
    items = await due(db, company_id=company_id)
    return [DueItemOut(**i) for i in items]


@router.get("/enrolments/{enrolment_id}/checkins", response_model=CheckinListOut)
async def get_enrolment_checkins(
    enrolment_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> CheckinListOut:
    _uid, company_id = ctx
    try:
        result = await list_for_enrolment(db, company_id=company_id, enrolment_id=enrolment_id)
    except CheckinError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return CheckinListOut(
        checkins=[CheckinOut(**i) for i in result["checkins"]],
        window=CheckinWindowOut(**result["window"]),
        notice=CHECKIN_NOTICE,
    )


@router.post(
    "/enrolments/{enrolment_id}/checkins", status_code=201, response_model=CheckinOut
)
async def post_enrolment_checkin(
    enrolment_id: uuid.UUID,
    body: CheckinIn,
    request: Request,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> CheckinOut:
    uid, company_id = ctx
    try:
        out = await record(
            db, company_id=company_id, enrolment_id=enrolment_id, actor=uid,
            employment=body.employment, left_reason=body.left_reason,
            performance=body.performance, meta=_meta(request),
        )
    except CheckinError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await db.commit()
    return CheckinOut(**out)


@router.post("/checkins/{checkin_id}/correct", response_model=CheckinOut)
async def post_checkin_correct(
    checkin_id: uuid.UUID,
    body: CheckinIn,
    request: Request,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> CheckinOut:
    uid, company_id = ctx
    try:
        out = await correct(
            db, company_id=company_id, checkin_id=checkin_id, actor=uid,
            employment=body.employment, left_reason=body.left_reason,
            performance=body.performance, meta=_meta(request),
        )
    except CheckinError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await db.commit()
    return CheckinOut(**out)
