"""The interviewer console — PH4-A1 (scorecards) and PH4-A5 (kits).

Everything here is scoped twice: the role gate (``InterviewerCtxDep`` admits
interviewers and HR managers) and, for every scorecard, an ownership filter in
the query itself. There is no endpoint here that reads another person's
scorecard, and none that lets a candidate or the AI reach one.

WHY THE AI CANNOT WRITE THESE
Not by a check in this file. Agents cannot hold a write tool at all
(``shared/agents/schema.py``: ``ToolEffect`` has only ``read`` and ``draft``),
and these routes require an authenticated human session carrying the
interviewer or hr_manager role. An agent proposal that tried to submit a
scorecard would have to be fired by a person, with their own credentials, as
their own submission — which is the point.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.database import DbSessionDep
from app.dependencies import InterviewerCtxDep
from app.interviewer_scorecards import (
    RequestMeta,
    ScorecardError,
    get_for_interviewer,
    list_for_interviewer,
    open_correction,
    save_draft,
    submit,
)
from app.utils.request_ip import extract_client_ip, extract_user_agent

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/interviewer", tags=["interviewer"])


def _meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip_address=extract_client_ip(request), user_agent=extract_user_agent(request)
    )


class ScoreIn(BaseModel):
    competency_id: str = Field(min_length=1, max_length=200)
    score: int | None = None
    not_assessed: bool = False
    evidence: str | None = Field(default=None, max_length=4000)


class ScorecardIn(BaseModel):
    scores: list[ScoreIn] = Field(default_factory=list, max_length=50)
    summary: str | None = Field(default=None, max_length=4000)


class CorrectionIn(BaseModel):
    reason: str = Field(min_length=10, max_length=1000)


@router.get("/assignments")
async def my_assignments(ctx: InterviewerCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    """The caller's own interviews — late first, then due soonest."""
    uid, company_id = ctx
    return await list_for_interviewer(db, interviewer_user_id=uid, company_id=company_id)


@router.get("/scorecards/{scorecard_id}")
async def get_scorecard(
    scorecard_id: uuid.UUID, ctx: InterviewerCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        return await get_for_interviewer(
            db, scorecard_id=scorecard_id, interviewer_user_id=uid, company_id=company_id
        )
    except ScorecardError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.put("/scorecards/{scorecard_id}")
async def save_scorecard_draft(
    scorecard_id: uuid.UUID,
    body: ScorecardIn,
    request: Request,
    ctx: InterviewerCtxDep,
    db: DbSessionDep,
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        out = await save_draft(
            db, scorecard_id=scorecard_id, interviewer_user_id=uid, company_id=company_id,
            scores=[s.model_dump() for s in body.scores], summary=body.summary,
            meta=_meta(request),
        )
    except ScorecardError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await db.commit()
    return out


@router.post("/scorecards/{scorecard_id}/submit")
async def submit_scorecard(
    scorecard_id: uuid.UUID,
    body: ScorecardIn,
    request: Request,
    ctx: InterviewerCtxDep,
    db: DbSessionDep,
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        out = await submit(
            db, scorecard_id=scorecard_id, interviewer_user_id=uid, company_id=company_id,
            scores=[s.model_dump() for s in body.scores], summary=body.summary,
            meta=_meta(request),
        )
    except ScorecardError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await db.commit()
    return out


@router.post("/scorecards/{scorecard_id}/correction")
async def correct_scorecard(
    scorecard_id: uuid.UUID,
    body: CorrectionIn,
    request: Request,
    ctx: InterviewerCtxDep,
    db: DbSessionDep,
) -> dict[str, Any]:
    """Open an audited correction. The original stays on record."""
    uid, company_id = ctx
    try:
        out = await open_correction(
            db, scorecard_id=scorecard_id, interviewer_user_id=uid, company_id=company_id,
            reason=body.reason, meta=_meta(request),
        )
    except ScorecardError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await db.commit()
    return out
