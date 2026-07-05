"""LiveKit room token endpoint (real-time transport — docs/ARCH §4).

POST /api/rooms/{session_id}/token

Mints a short-TTL LiveKit join token for ONE interview session, AFTER the same
gates the deleted WebSocket handler enforced:
  - valid Bearer JWT            (CurrentUserDep -> 401)
  - session exists              (404)
  - session belongs to caller   (403)  — mirrors old WS close 4003
  - active DPDP consent         (403)  — server-side, can't be bypassed via curl

The browser never holds LiveKit API credentials; it calls this endpoint and
gets a scoped token that lets it join exactly its own room. The interview agent
(worker tier) joins the same room separately. Self-hosting LiveKit in Mumbai
for the bid changes only LIVEKIT_URL — this code is unchanged.

PII: never log the token or candidate text. Log only event + ids.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from livekit import api
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.launcher import dispatch_interview_agent
from app.config import settings
from app.consent_guard import has_active_consent
from app.database import get_db_session
from app.dependencies import CurrentUserDep
from app.graph.state import Language as InterviewLanguage
from app.models import Job
from app.models import Session as InterviewSession
from app.redis_client import get_redis
from app.worker_capacity import read_active_jobs

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/rooms", tags=["rooms"])

# Join token lifetime. The candidate must connect within this window; the media
# session itself can run longer once joined. 10 min covers click-to-connect.
_TOKEN_TTL = timedelta(minutes=10)


class RoomTokenResponse(BaseModel):
    """Everything the browser needs to join the LiveKit room."""

    url: str = Field(..., description="LiveKit server wss:// URL.")
    token: str = Field(..., description="Short-TTL join JWT scoped to this room.")
    room_name: str = Field(..., description="Room name (== session_id).")


@router.post(
    "/{session_id}/token",
    response_model=RoomTokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Mint a LiveKit join token for an interview session",
)
async def create_room_token(
    session_id: uuid.UUID,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> RoomTokenResponse:
    """Issue a LiveKit join token after auth + ownership + consent checks."""
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        # Misconfiguration — fail loud server-side, generic detail to client.
        log.error("rooms.token.livekit_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Real-time transport is not configured.",
        )

    user_id_str: str = current_user["sub"]

    # 0. Guest binding (Phase 3, B7): a magic-link guest token carries a
    #    session_id claim and may join ONLY that one session — even though the
    #    ownership check below would also pass (the guest IS the session's user),
    #    this binds the token at the transport layer so a guest token can never be
    #    pointed at another session id.
    roles = current_user.get("roles") or []
    if "guest_candidate" in roles and current_user.get("session_id") != str(session_id):
        log.info("rooms.token.guest_session_mismatch", session_id=str(session_id))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This guest token is not valid for this session.",
        )

    # 1. Session must exist.
    result = await db.execute(
        select(InterviewSession).where(InterviewSession.id == session_id)
    )
    session: InterviewSession | None = result.scalar_one_or_none()
    if session is None:
        log.info("rooms.token.session_not_found", session_id=str(session_id))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )

    # 2. Session must belong to the caller (old WS close 4003).
    if str(session.user_id) != user_id_str:
        log.info(
            "rooms.token.ownership_mismatch",
            session_id=str(session_id),
            user_id=user_id_str,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this interview session.",
        )

    # 3. Worker capacity check — BEFORE issuing a token.
    #    The worker publishes its active-job count to Redis on every admission
    #    change.  We read it here so a full worker returns HTTP 503 with a clear
    #    human-readable message BEFORE the candidate enters a dead LiveKit room.
    #    Fails open: if Redis is unavailable we issue the token and let the
    #    worker's in-process gate handle overload (silent dead-room risk is
    #    preferable to blocking ALL token issuance on a Redis outage).
    cap = settings.worker_max_concurrent_jobs
    if cap > 0:
        try:
            active = await read_active_jobs(get_redis())
        except Exception:  # noqa: BLE001
            active = None
        if active is not None and active >= cap:
            log.warning(
                "rooms.token.worker_full",
                session_id=str(session_id),
                active_jobs=active,
                cap=cap,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "All interview slots are currently occupied. "
                    "Please wait a few minutes and try again."
                ),
            )

    # 4. DPDP consent must be present (server-side; can't bypass via curl).
    if not await has_active_consent(db, user_id_str):
        log.info("rooms.token.consent_required", session_id=str(session_id))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "DPDP consent required before starting an interview session. "
                "Call POST /consent (data_gateway) first."
            ),
        )

    # 5. Dispatch the interview WORKER into this room with session metadata.
    #    The worker (app/worker/interview_worker.py) must be running separately;
    #    it drives the Simli avatar + Sarvam voice. Idempotent per room.
    room_name = str(session_id)
    job_result = await db.execute(select(Job).where(Job.id == session.job_id))
    job: Job | None = job_result.scalar_one_or_none()
    job_title = job.title if job is not None else "the role"
    raw_lang = (session.language or "en").lower()
    language: InterviewLanguage = raw_lang if raw_lang in ("en", "hi", "te") else "en"  # type: ignore[assignment]
    dispatched = await dispatch_interview_agent(
        room_name=room_name,
        session_id=str(session_id),
        job_id=str(session.job_id),
        job_title=job_title,
        language=language,
    )
    log.info("rooms.token.agent_dispatch", session_id=str(session_id), dispatched=dispatched)

    # 6. Mint the LiveKit join token, scoped to this one room.
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(f"candidate-{user_id_str}")
        .with_name("Candidate")
        .with_ttl(_TOKEN_TTL)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,       # mic
                can_subscribe=True,     # interviewer audio + avatar video
                can_publish_data=True,  # client-viseme channel (bid path)
            )
        )
        .to_jwt()
    )

    log.info(
        "rooms.token.issued",
        session_id=str(session_id),
        user_id=user_id_str,
        room=room_name,
    )
    return RoomTokenResponse(
        url=settings.livekit_url,
        token=token,
        room_name=room_name,
    )
