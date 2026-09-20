"""Candidate accommodations — PH4-D2.

``hr_router`` only (``/hr``, ``HrCtxDep``): there is no super-admin,
interviewer, candidate or agent route. A candidate cannot see or set their own
adjustment; the only accommodation text an interviewer ever sees is
``adjustments_note`` on their own scorecard (``interviewer_scorecards.py``),
never a route here.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import accommodations as svc
from app.database import DbSessionDep
from app.dependencies import HrCtxDep
from app.interviewer_scorecards import RequestMeta
from app.models import Applicant, ExamRound, WorkflowRound
from app.utils.ownership import get_owned
from app.utils.request_ip import extract_client_ip, extract_user_agent

hr_router = APIRouter(prefix="/hr", tags=["accommodations"])


def _meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip_address=extract_client_ip(request), user_agent=extract_user_agent(request)
    )


async def _fail(exc: svc.AccommodationError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


async def _check_enrolment(
    db: AsyncSession, *, company_id: uuid.UUID, applicant_id: uuid.UUID, enrolment_id: uuid.UUID,
) -> None:
    row = await db.scalar(
        text(
            "SELECT 1 FROM enrolments WHERE id = :e AND company_id = :c AND applicant_id = :a"
            "   AND deleted_at IS NULL"
        ),
        {"e": enrolment_id, "c": company_id, "a": applicant_id},
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Application not found.")


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------
class AccommodationRecordIn(BaseModel):
    enrolment_id: uuid.UUID | None = None
    round_id: uuid.UUID | None = None
    exam_round_id: uuid.UUID | None = None
    extra_time_percent: int | None = Field(default=None, ge=10, le=200)
    deadline_extension_days: int | None = Field(default=None, ge=1, le=30)
    relax_auto_submit: bool = False
    other_adjustment: str | None = Field(default=None, max_length=500)
    interviewer_note: str | None = Field(default=None, max_length=500)
    internal_note: str | None = Field(default=None, max_length=1000)
    basis: str = "hr_initiated"
    requested_on: date | None = None
    effective_from: datetime | None = None
    effective_until: datetime | None = None


class AccommodationReviseIn(BaseModel):
    extra_time_percent: int | None = Field(default=None, ge=10, le=200)
    deadline_extension_days: int | None = Field(default=None, ge=1, le=30)
    relax_auto_submit: bool = False
    other_adjustment: str | None = Field(default=None, max_length=500)
    interviewer_note: str | None = Field(default=None, max_length=500)
    internal_note: str | None = Field(default=None, max_length=1000)
    effective_from: datetime | None = None
    effective_until: datetime | None = None


class AccommodationRevokeIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@hr_router.get("/applicants/{applicant_id}/accommodations")
async def list_accommodations(
    applicant_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    await get_owned(db, Applicant, company_id, applicant_id, noun="Applicant")
    try:
        return await svc.list_for_applicant(db, company_id=company_id, applicant_id=applicant_id)
    except svc.AccommodationError as exc:
        raise await _fail(exc) from exc


@hr_router.post("/applicants/{applicant_id}/accommodations", status_code=201)
async def record_accommodation(
    applicant_id: uuid.UUID, body: AccommodationRecordIn, request: Request,
    ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    uid, company_id = ctx
    await get_owned(db, Applicant, company_id, applicant_id, noun="Applicant")
    if body.enrolment_id is not None:
        await _check_enrolment(
            db, company_id=company_id, applicant_id=applicant_id, enrolment_id=body.enrolment_id
        )
    if body.round_id is not None:
        await get_owned(db, WorkflowRound, company_id, body.round_id, noun="Round")
    if body.exam_round_id is not None:
        await get_owned(db, ExamRound, company_id, body.exam_round_id, noun="Exam round")
    try:
        aid = await svc.record(
            db, company_id=company_id, applicant_id=applicant_id, actor=uid,
            enrolment_id=body.enrolment_id, round_id=body.round_id,
            exam_round_id=body.exam_round_id, extra_time_percent=body.extra_time_percent,
            deadline_extension_days=body.deadline_extension_days,
            relax_auto_submit=body.relax_auto_submit, other_adjustment=body.other_adjustment,
            interviewer_note=body.interviewer_note, internal_note=body.internal_note,
            basis=body.basis, requested_on=body.requested_on, effective_from=body.effective_from,
            effective_until=body.effective_until, meta=_meta(request),
        )
    except svc.AccommodationError as exc:
        await db.rollback()
        raise await _fail(exc) from exc
    # Staged on the SAME transaction as the record, so the email exists iff
    # the accommodation does (the offers/preboarding precedent).
    await svc.notify_recorded(
        db, company_id=company_id, applicant_id=applicant_id,
        extra_time_percent=body.extra_time_percent,
        deadline_extension_days=body.deadline_extension_days,
        relax_auto_submit=body.relax_auto_submit, other_adjustment=body.other_adjustment,
    )
    await db.commit()
    return {"id": str(aid)}


@hr_router.post("/accommodations/{accommodation_id}/revise")
async def revise_accommodation(
    accommodation_id: uuid.UUID, body: AccommodationReviseIn, request: Request,
    ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        new_id = await svc.revise(
            db, company_id=company_id, actor=uid, accommodation_id=accommodation_id,
            extra_time_percent=body.extra_time_percent,
            deadline_extension_days=body.deadline_extension_days,
            relax_auto_submit=body.relax_auto_submit, other_adjustment=body.other_adjustment,
            interviewer_note=body.interviewer_note, internal_note=body.internal_note,
            effective_from=body.effective_from, effective_until=body.effective_until,
            meta=_meta(request),
        )
    except svc.AccommodationError as exc:
        await db.rollback()
        raise await _fail(exc) from exc
    await db.commit()
    return {"id": str(new_id)}


@hr_router.post("/accommodations/{accommodation_id}/revoke")
async def revoke_accommodation(
    accommodation_id: uuid.UUID, body: AccommodationRevokeIn, request: Request,
    ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, str]:
    uid, company_id = ctx
    try:
        await svc.revoke(
            db, company_id=company_id, actor=uid, accommodation_id=accommodation_id,
            reason=body.reason, meta=_meta(request),
        )
    except svc.AccommodationError as exc:
        await db.rollback()
        raise await _fail(exc) from exc
    await db.commit()
    return {"status": "revoked"}


@hr_router.get("/enrolments/{enrolment_id}/accommodations/effective")
async def effective_accommodation(
    enrolment_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep, round_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    _uid, company_id = ctx
    try:
        return await svc.effective_preview(
            db, company_id=company_id, enrolment_id=enrolment_id, round_id=round_id
        )
    except svc.AccommodationError as exc:
        raise await _fail(exc) from exc
