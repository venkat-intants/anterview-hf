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
    403  : session belongs to another user OR active recording consent absent
    404  : session not found

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
from app.dependencies import CurrentUserDep
from app.models import IntegrityEvent
from app.models import Session as InterviewSession
from app.proctoring import KNOWN_EVENT_TYPES, compute_integrity

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
    current_user: CurrentUserDep,
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
