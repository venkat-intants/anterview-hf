"""Proctoring / integrity event ingestion — Phase B.

The candidate's browser runs gaze/face detection (MediaPipe) and watches
browser events (tab switch, fullscreen exit, copy/paste). It batches lightweight
*events* — never raw video — and POSTs them here. We persist each event, then
recompute a rolling integrity score + summary on the session so it is always
current (no dependency on the realtime worker process).

Contract:
  POST /api/sessions/{session_id}/integrity-events
    body : {"events": [{type, started_at, ended_at?, metadata?}, ...]}
    200  : {"integrity_score": int, "summary": {...}, "stored": int}
    401  : missing/invalid JWT
    403  : session belongs to another user, OR a guest token's session_id
           claim doesn't match the path parameter, OR active recording
           consent is absent
    404  : session not found

  GET /api/sessions/{session_id}/integrity
    200  : {"integrity_score": int|null, "summary": {...}|null,
            "session_started_at": iso|null,
            "events": [{event_type, started_at, ended_at, duration_seconds}, ...]}
    401  : missing/invalid JWT
    403  : session belongs to another user, OR a guest token's session_id
           claim doesn't match the path parameter
    404  : session not found
    Read-back of the candidate's OWN proctoring data (DPDP §11 right to access;
    no consent gate — reading your own stored data is not fresh processing).
    integrity_score null means proctoring never ran for this session.

Guest-session binding (security-audit finding, 2026-08): both routes below
take {session_id} and allow the guest_candidate role, so both use the shared
``GuestBoundUserDep`` (app/dependencies.py) instead of the bare ``CurrentUserDep``.
A guest identity is reused across invites (data_gateway interview_take.py), so
one users row can own several sessions — an ownership check on user_id alone
is not enough to stop a guest token minted for session B from reaching
session A; GuestBoundUserDep rejects that before the handler body runs.

DPDP note: gaze/face proctoring events are biometric-derived data under the
DPDP Act 2023.  Storing them requires an active ``interview_voice_recording``
consent on the ``dpdp_consent_ledger``.  This endpoint is FAIL-CLOSED: if the
consent check fails for any reason (DB error, revoked consent, missing entry)
the batch is rejected with HTTP 403 and NO events are persisted.
"""

from __future__ import annotations

import uuid as _uuid_mod
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.consent_guard import has_active_consent
from app.database import get_db_session
from app.dependencies import GuestBoundUserDep
from app.models import IntegrityEvent
from app.models import Session as InterviewSession
from app.proctoring import (
    KNOWN_EVENT_TYPES,
    _duration_seconds,  # single source of truth for the ranged-event clamp
    compute_integrity,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["integrity"])

DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]

# Guard against an abusive/buggy client flooding a single request.
_MAX_EVENTS_PER_BATCH = 200


class IntegrityEventIn(BaseModel):
    """One flagged event from the client."""

    type: str = Field(..., description="Event type, e.g. 'gaze_away', 'tab_blur'.")
    started_at: datetime = Field(..., description="ISO-8601 UTC start timestamp.")
    ended_at: datetime | None = Field(
        default=None, description="ISO-8601 UTC end timestamp for ranged events."
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Optional detail, e.g. {'confidence': 0.7}."
    )

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        # Accept unknown types too (forward-compatible) but normalise.
        return v.strip()


class IntegrityBatchIn(BaseModel):
    """Body for POST /api/sessions/{id}/integrity-events."""

    events: list[IntegrityEventIn] = Field(default_factory=list)


class IntegrityBatchOut(BaseModel):
    """Response: the rolling score after this batch was stored."""

    integrity_score: int
    summary: dict[str, Any]
    stored: int


@router.post(
    "/sessions/{session_id}/integrity-events",
    response_model=IntegrityBatchOut,
    status_code=status.HTTP_200_OK,
    summary="Ingest proctoring integrity events for a session",
)
async def post_integrity_events(
    current_user: GuestBoundUserDep,
    db: DbSessionDep,
    body: IntegrityBatchIn,
    session_id: Annotated[_uuid_mod.UUID, Path()],
) -> IntegrityBatchOut:
    """Persist a batch of integrity events and recompute the session's score."""
    if len(body.events) > _MAX_EVENTS_PER_BATCH:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Too many events; max {_MAX_EVENTS_PER_BATCH} per request.",
        )

    # ---- Ownership check ----
    sess = (
        await db.execute(
            select(InterviewSession).where(InterviewSession.id == session_id)
        )
    ).scalar_one_or_none()
    if sess is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if str(sess.user_id) != current_user["sub"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this session.",
        )

    # ---- DPDP consent gate (FAIL-CLOSED) ----
    # Gaze/face proctoring events are biometric-derived data under the DPDP Act
    # 2023.  We require an active interview_voice_recording consent entry on the
    # dpdp_consent_ledger before persisting ANY such data.  If the consent check
    # itself raises (DB error, network partition) we reject the batch — fail-closed
    # is the DPDP-correct posture; recording without confirmed consent is a
    # violation, but refusing a batch during a transient outage is recoverable.
    try:
        consent_ok = await has_active_consent(db, current_user["sub"])
    except Exception as _exc:
        log.warning(
            "integrity.consent_check_error",
            session_id=str(session_id),
            user_id=current_user["sub"],
            err=type(_exc).__name__,
        )
        consent_ok = False

    if not consent_ok:
        log.warning(
            "integrity.consent_absent — rejecting biometric batch",
            session_id=str(session_id),
            user_id=current_user["sub"],
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Active recording consent is required to persist proctoring events. "
                "Please renew your consent or contact support."
            ),
        )

    now = datetime.now(tz=UTC)

    # ---- Insert events (ignore obviously-empty types) ----
    stored = 0
    for ev in body.events:
        etype = ev.type
        if not etype:
            continue
        db.add(
            IntegrityEvent(
                session_id=session_id,
                event_type=etype,
                started_at=ev.started_at,
                ended_at=ev.ended_at,
                event_metadata=ev.metadata,
                created_at=now,
            )
        )
        stored += 1

    # ---- Recompute rolling score from ALL events for this session ----
    # Flush first so the just-added rows are included in the re-query.
    await db.flush()
    rows = (
        await db.execute(
            select(
                IntegrityEvent.event_type,
                IntegrityEvent.started_at,
                IntegrityEvent.ended_at,
            ).where(IntegrityEvent.session_id == session_id)
        )
    ).all()

    score, summary = compute_integrity(
        [{"event_type": t, "started_at": s, "ended_at": e} for t, s, e in rows]
    )

    await db.execute(
        update(InterviewSession)
        .where(InterviewSession.id == session_id)
        .values(integrity_score=score, proctoring_summary=summary)
    )
    await db.commit()

    log.info(
        "integrity.batch",
        session_id=str(session_id),
        stored=stored,
        score=score,
        total_events=summary.get("total_events"),
        # NEVER log event metadata or any frame data — PII/biometric.
    )

    # Surface unknown types once for observability (helps catch client typos).
    unknown = {e.type for e in body.events if e.type and e.type not in KNOWN_EVENT_TYPES}
    if unknown:
        log.warning("integrity.unknown_event_types", session_id=str(session_id), types=sorted(unknown))

    return IntegrityBatchOut(integrity_score=score, summary=summary, stored=stored)


# ---------------------------------------------------------------------------
# Read-back — the candidate's own integrity report for the scorecard page
# ---------------------------------------------------------------------------


class IntegrityEventEntry(BaseModel):
    """One stored proctoring event, time-ordered for the timeline view."""

    event_type: str
    started_at: datetime
    ended_at: datetime | None
    # Seconds for ranged events (gaze_away, face_absent, ...); null for
    # instantaneous events (tab_blur, copy, ...). Uses the same clamp as the
    # score computation so the timeline and the score always agree.
    duration_seconds: float | None


class IntegrityReportOut(BaseModel):
    """Response for GET /api/sessions/{id}/integrity.

    integrity_score is null when proctoring never ran for this session —
    the UI must distinguish that from a clean 100.
    """

    integrity_score: int | None
    summary: dict[str, Any] | None
    # Session start — lets the UI render events as mm:ss offsets into the
    # interview instead of raw wall-clock times.
    session_started_at: datetime | None
    events: list[IntegrityEventEntry]


@router.get(
    "/sessions/{session_id}/integrity",
    response_model=IntegrityReportOut,
    status_code=status.HTTP_200_OK,
    summary="Read the session's integrity score, summary, and event timeline",
)
async def get_integrity_report(
    current_user: GuestBoundUserDep,
    db: DbSessionDep,
    session_id: Annotated[_uuid_mod.UUID, Path()],
) -> IntegrityReportOut:
    """Return the caller's own proctoring report for one session.

    Owner-only: candidates can read the integrity data of THEIR sessions
    (DPDP right to access). HR/admin views go through admin_ops instead.
    A guest token is additionally bound to its own session_id claim by
    ``GuestBoundUserDep`` — see the module docstring.
    """
    sess = (
        await db.execute(
            select(InterviewSession).where(InterviewSession.id == session_id)
        )
    ).scalar_one_or_none()
    if sess is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if str(sess.user_id) != current_user["sub"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this session.",
        )

    rows = (
        await db.execute(
            select(
                IntegrityEvent.event_type,
                IntegrityEvent.started_at,
                IntegrityEvent.ended_at,
            )
            .where(IntegrityEvent.session_id == session_id)
            .order_by(IntegrityEvent.started_at)
        )
    ).all()

    events = [
        IntegrityEventEntry(
            event_type=etype,
            started_at=started,
            ended_at=ended,
            duration_seconds=(
                round(_duration_seconds(started, ended), 1) if ended is not None else None
            ),
        )
        for etype, started, ended in rows
    ]

    return IntegrityReportOut(
        integrity_score=sess.integrity_score,
        summary=sess.proctoring_summary,
        session_started_at=sess.started_at,
        events=events,
    )
