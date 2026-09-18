"""Interview scheduling, loops, panel workload and calibration — PH4 Wave 3 (A2, O5).

Three routers, one per audience, each taking its scope from the session:

* ``hr_router`` (``/hr``, HR managers): availability for any interviewer,
  loops and their sessions, free slots, sending the itinerary, changes and
  cancellations, the calendar file, workload, capacity, calibration.
* ``interviewer_router`` (``/interviewer``): the caller's own sessions and own
  availability — never anyone else's (O5 #6).
* ``me_router`` (``/users/me``): the candidate's own schedules, the slots they
  may pick from, booking one, and the calendar file. There is no route that
  moves or cancels a booked session (A2 #23).

Nothing here changes a candidate's status.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from shared.auth.base import User
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import DbSessionDep, get_db_session
from app.dependencies import HrCtxDep, InterviewerCtxDep, get_current_user
from app.interview_scheduling import (
    SchedulingError,
    add_availability,
    add_session,
    cancel_loop,
    candidate_book,
    candidate_ics,
    candidate_loops,
    candidate_slots,
    create_loop,
    free_slots,
    get_loop,
    hr_ics,
    list_availability,
    loops_for_enrolment,
    remove_availability,
    reschedule_session,
    send_loop,
    sessions_for_interviewer,
    set_session_outcome,
)
from app.interviewer_scorecards import RequestMeta
from app.panel_workload import PanelError, calibration, set_capacity, workload
from app.utils.request_ip import extract_client_ip, extract_user_agent

hr_router = APIRouter(prefix="/hr", tags=["interview-scheduling"])
interviewer_router = APIRouter(prefix="/interviewer", tags=["interview-scheduling"])
me_router = APIRouter(prefix="/users/me", tags=["interview-scheduling"])

# The AUTH user (shared.auth.base.User: ``user_id`` is a string), exactly what
# get_current_user returns — not the ORM model, which has ``.id``. Security
# review F1: annotating the ORM type hid a 500 on every candidate route.
CurrentUserDep = Annotated[User, Depends(get_current_user)]


def _me(user: User) -> uuid.UUID:
    return uuid.UUID(user.user_id)
CandidateDbDep = Annotated[AsyncSession, Depends(get_db_session)]

_ICS_HEADERS = {"Cache-Control": "no-store"}


def _meta(request: Request) -> RequestMeta:
    return RequestMeta(ip_address=extract_client_ip(request), user_agent=extract_user_agent(request))


def _raise(exc: SchedulingError | PanelError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _window(start: datetime | None, end: datetime | None, *, days: int) -> tuple[datetime, datetime]:
    now = datetime.now(tz=UTC)
    start = start or now
    end = end or start + timedelta(days=days)
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(status_code=422, detail="Times need a timezone offset.")
    if end <= start or end - start > timedelta(days=92):
        raise HTTPException(status_code=422, detail="Choose a period of up to 92 days.")
    return start, end


def _ics(body: str, name: str) -> Response:
    return Response(
        content=body, media_type="text/calendar; charset=utf-8",
        headers={**_ICS_HEADERS, "Content-Disposition": f'attachment; filename="{name}.ics"'},
    )


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------
class WindowIn(BaseModel):
    starts_at: datetime
    ends_at: datetime


class LoopIn(BaseModel):
    title: str = Field(default="Interviews", min_length=1, max_length=200)
    candidate_timezone: str = Field(default="Asia/Kolkata", min_length=1, max_length=64)
    buffer_minutes: int = Field(default=15, ge=0, le=240)
    self_schedule: bool = False


class SessionIn(BaseModel):
    round_id: uuid.UUID
    title: str | None = Field(default=None, max_length=200)
    duration_minutes: int = Field(default=45, ge=15, le=480)
    interviewer_user_ids: list[uuid.UUID] = Field(min_length=1, max_length=6)
    starts_at: datetime | None = None
    location: str | None = Field(default=None, max_length=500)
    allow_outside_availability: bool = False


class RescheduleIn(BaseModel):
    starts_at: datetime
    duration_minutes: int | None = Field(default=None, ge=15, le=480)
    location: str | None = Field(default=None, max_length=500)
    allow_outside_availability: bool = False


class OutcomeIn(BaseModel):
    outcome: str = Field(pattern="^(cancelled|completed|no_show)$")
    reason: str | None = Field(default=None, max_length=500)


class CancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class CapacityIn(BaseModel):
    max_sessions_per_day: int | None = Field(default=None, ge=1, le=24)
    max_sessions_per_week: int | None = Field(default=None, ge=1, le=100)


class BookIn(BaseModel):
    session_id: uuid.UUID
    starts_at: datetime
    timezone: str | None = Field(default=None, max_length=64)


# ---------------------------------------------------------------------------
# HR
# ---------------------------------------------------------------------------
@hr_router.get("/interviewers/{user_id}/availability")
async def hr_get_availability(
    user_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    s, e = _window(start, end, days=28)
    return await list_availability(db, company_id=company_id, user_id=user_id, start=s, end=e)


@hr_router.post("/interviewers/{user_id}/availability", status_code=201)
async def hr_add_availability(
    user_id: uuid.UUID, body: WindowIn, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await add_availability(db, company_id=company_id, user_id=user_id,
                                     starts_at=body.starts_at, ends_at=body.ends_at,
                                     actor=actor, meta=_meta(request))
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.commit()
    return out


@hr_router.delete("/availability/{window_id}", status_code=204)
async def hr_remove_availability(
    window_id: uuid.UUID, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> Response:
    actor, company_id = ctx
    try:
        await remove_availability(db, company_id=company_id, window_id=window_id,
                                  actor=actor, meta=_meta(request))
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.commit()
    return Response(status_code=204)


@hr_router.get("/enrolments/{enrolment_id}/loops")
async def hr_list_loops(enrolment_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    return await loops_for_enrolment(db, company_id=company_id, enrolment_id=enrolment_id)


@hr_router.post("/enrolments/{enrolment_id}/loops", status_code=201)
async def hr_create_loop(
    enrolment_id: uuid.UUID, body: LoopIn, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await create_loop(
            db, company_id=company_id, enrolment_id=enrolment_id, title=body.title,
            candidate_timezone=body.candidate_timezone, buffer_minutes=body.buffer_minutes,
            self_schedule=body.self_schedule, actor=actor, meta=_meta(request),
        )
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.commit()
    return await get_loop(db, company_id=company_id, loop_id=uuid.UUID(out["loop_id"]))


@hr_router.get("/loops/{loop_id}")
async def hr_get_loop(loop_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    _uid, company_id = ctx
    try:
        return await get_loop(db, company_id=company_id, loop_id=loop_id)
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc


@hr_router.post("/loops/{loop_id}/sessions", status_code=201)
async def hr_add_session(
    loop_id: uuid.UUID, body: SessionIn, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        await add_session(
            db, company_id=company_id, loop_id=loop_id, round_id=body.round_id,
            title=body.title, duration_minutes=body.duration_minutes,
            interviewer_ids=body.interviewer_user_ids, starts_at=body.starts_at,
            location=body.location, allow_outside_availability=body.allow_outside_availability,
            actor=actor, meta=_meta(request),
        )
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.commit()
    return await get_loop(db, company_id=company_id, loop_id=loop_id)


@hr_router.post("/loops/{loop_id}/send")
async def hr_send_loop(loop_id: uuid.UUID, request: Request, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await send_loop(db, company_id=company_id, loop_id=loop_id, actor=actor, meta=_meta(request))
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.commit()
    return out


@hr_router.post("/loops/{loop_id}/cancel")
async def hr_cancel_loop(
    loop_id: uuid.UUID, body: CancelIn, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await cancel_loop(db, company_id=company_id, loop_id=loop_id, reason=body.reason,
                                actor=actor, meta=_meta(request))
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.commit()
    return out


@hr_router.get("/loops/{loop_id}/calendar.ics")
async def hr_loop_ics(loop_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> Response:
    _uid, company_id = ctx
    try:
        body = await hr_ics(db, company_id=company_id, loop_id=loop_id)
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.rollback()  # the loop row was read FOR UPDATE; release it
    return _ics(body, f"interviews-{loop_id}")


@hr_router.get("/sessions/{session_id}/slots")
async def hr_session_slots(session_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    _uid, company_id = ctx
    try:
        found = await free_slots(db, company_id=company_id, session_id=session_id)
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    return {"slots": found}


@hr_router.patch("/sessions/{session_id}")
async def hr_reschedule(
    session_id: uuid.UUID, body: RescheduleIn, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await reschedule_session(
            db, company_id=company_id, session_id=session_id, starts_at=body.starts_at,
            duration_minutes=body.duration_minutes, location=body.location,
            allow_outside_availability=body.allow_outside_availability, actor=actor,
            meta=_meta(request),
        )
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.commit()
    return out


@hr_router.post("/sessions/{session_id}/outcome")
async def hr_session_outcome(
    session_id: uuid.UUID, body: OutcomeIn, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await set_session_outcome(db, company_id=company_id, session_id=session_id,
                                        outcome=body.outcome, reason=body.reason, actor=actor,
                                        meta=_meta(request))
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.commit()
    return out


@hr_router.get("/panel/workload")
async def hr_workload(
    ctx: HrCtxDep, db: DbSessionDep,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    _uid, company_id = ctx
    s, e = _window(start, end, days=14)
    try:
        return await workload(db, company_id=company_id, start=s, end=e)
    except PanelError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc


@hr_router.put("/interviewers/{user_id}/capacity")
async def hr_set_capacity(
    user_id: uuid.UUID, body: CapacityIn, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await set_capacity(db, company_id=company_id, user_id=user_id,
                                 per_day=body.max_sessions_per_day,
                                 per_week=body.max_sessions_per_week, actor=actor,
                                 meta=_meta(request))
    except PanelError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.commit()
    return out


@hr_router.get("/panel/calibration")
async def hr_calibration(
    request: Request, ctx: HrCtxDep, db: DbSessionDep,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    requisition_id: Annotated[uuid.UUID | None, Query()] = None,
    round_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict[str, Any]:
    actor, company_id = ctx
    now = datetime.now(tz=UTC)
    s, e = _window(start or now - timedelta(days=90), end or now, days=90)
    try:
        out = await calibration(db, company_id=company_id, start=s, end=e,
                                requisition_id=requisition_id, round_id=round_id,
                                actor=actor, meta=_meta(request))
    except PanelError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.commit()  # the audit row: who looked at the panel's scoring, and when
    return out


# ---------------------------------------------------------------------------
# Interviewer — only their own
# ---------------------------------------------------------------------------
@interviewer_router.get("/sessions")
async def iv_sessions(
    ctx: InterviewerCtxDep, db: DbSessionDep,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> list[dict[str, Any]]:
    user_id, company_id = ctx
    s, e = _window(start or datetime.now(tz=UTC) - timedelta(days=1), end, days=30)
    return await sessions_for_interviewer(db, company_id=company_id, user_id=user_id, start=s, end=e)


@interviewer_router.get("/availability")
async def iv_get_availability(
    ctx: InterviewerCtxDep, db: DbSessionDep,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> list[dict[str, Any]]:
    user_id, company_id = ctx
    s, e = _window(start, end, days=28)
    return await list_availability(db, company_id=company_id, user_id=user_id, start=s, end=e)


@interviewer_router.post("/availability", status_code=201)
async def iv_add_availability(
    body: WindowIn, request: Request, ctx: InterviewerCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    user_id, company_id = ctx
    try:
        out = await add_availability(db, company_id=company_id, user_id=user_id,
                                     starts_at=body.starts_at, ends_at=body.ends_at,
                                     actor=user_id, meta=_meta(request))
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.commit()
    return out


@interviewer_router.delete("/availability/{window_id}", status_code=204)
async def iv_remove_availability(
    window_id: uuid.UUID, request: Request, ctx: InterviewerCtxDep, db: DbSessionDep,
) -> Response:
    user_id, company_id = ctx
    try:
        await remove_availability(db, company_id=company_id, window_id=window_id, actor=user_id,
                                  meta=_meta(request), only_user=user_id)
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Candidate — only their own
# ---------------------------------------------------------------------------
@me_router.get("/interview-loops")
async def me_loops(user: CurrentUserDep, db: CandidateDbDep) -> list[dict[str, Any]]:
    return await candidate_loops(db, user_id=_me(user))


@me_router.get("/interview-loops/{loop_id}/sessions/{session_id}/slots")
async def me_slots(
    loop_id: uuid.UUID, session_id: uuid.UUID, user: CurrentUserDep, db: CandidateDbDep,
) -> dict[str, Any]:
    try:
        found = await candidate_slots(db, user_id=_me(user), loop_id=loop_id, session_id=session_id)
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.rollback()
    return {"slots": found}


@me_router.post("/interview-loops/{loop_id}/book")
async def me_book(
    loop_id: uuid.UUID, body: BookIn, request: Request, user: CurrentUserDep, db: CandidateDbDep,
) -> dict[str, Any]:
    try:
        out = await candidate_book(db, user_id=_me(user), loop_id=loop_id,
                                   session_id=body.session_id, starts_at=body.starts_at,
                                   timezone=body.timezone, meta=_meta(request))
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.commit()
    return out


@me_router.get("/interview-loops/{loop_id}/calendar.ics")
async def me_ics(loop_id: uuid.UUID, user: CurrentUserDep, db: CandidateDbDep) -> Response:
    try:
        body = await candidate_ics(db, user_id=_me(user), loop_id=loop_id)
    except SchedulingError as exc:
        await db.rollback()  # a refused write leaves nothing half-done
        raise _raise(exc) from exc
    await db.rollback()
    return _ics(body, "interviews")
