"""LiveKit room token endpoint (real-time transport — docs/ARCH §4).

POST /api/rooms/{session_id}/token

Mints a short-TTL LiveKit join token for ONE interview session, AFTER the same
gates the deleted WebSocket handler enforced:
  - valid Bearer JWT            (CurrentUserDep -> 401)
  - guest token session binding (GuestBoundUserDep -> 403) — a guest token's
    session_id claim must match the path parameter (shared dependency, also
    applied to the integrity router)
  - session exists               (404)
  - session belongs to caller    (403)  — mirrors old WS close 4003
  - session is not already finished (409) — a completed/failed session, or
    one with a scorecard already on file, can never be re-entered
  - active DPDP consent          (403)  — server-side, can't be bypassed via curl

The browser never holds LiveKit API credentials; it calls this endpoint and
gets a scoped token that lets it join exactly its own room. The interview agent
(worker tier) joins the same room separately. Self-hosting LiveKit in Mumbai
for the bid changes only LIVEKIT_URL — this code is unchanged.

POST /api/rooms/{session_id}/redispatch

Idempotent "my interviewer never showed up" path. The worker runs under
EXPLICIT dispatch (see ``app/agent/launcher.py``): the join token carries the
first dispatch, and this endpoint creates another if no agent has joined. The
frontend calls it when no interviewer appears within a few seconds of joining.

CAPACITY. Admission is an atomic reservation in the Redis slot ledger
(``app/worker_capacity.py``) taken by the token endpoint, so N concurrent
candidates cannot all pass a cap that only counted jobs the worker had already
accepted. A full ledger is a clear 503 BEFORE any token is issued.

PII: never log the token or candidate text. Log only event + ids.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Literal

import aiohttp
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from livekit import api
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.launcher import DispatchOutcome, ensure_interviewer, room_configuration
from app.config import settings
from app.consent_guard import has_active_consent
from app.database import get_db_session
from app.dependencies import GuestBoundUserDep
from app.models import Session as InterviewSession
from app.rate_limit import enforce_user_rate_limit
from app.redis_client import get_redis
from app.worker_capacity import SlotResult, note_redispatch, reserve_slot, reset_redispatch

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


async def _scorecard_exists(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """True iff a scorecard row exists for this session.

    Scorecards are owned by feedback_billing; interview_core does not ORM-map
    that table (same cross-service-boundary rule as ``app/consent_guard.py``
    for ``dpdp_consent_ledger`` — see that module's docstring). A raw,
    parameterized ``SELECT 1 ... LIMIT 1`` is the cheap, dependency-free way
    to check. Mirrors data_gateway's ``interview_take.py::_scorecard_exists``,
    which uses the same query as a durable backstop against re-entering a
    session whose status write raced or whose scoring later failed.
    """
    result = await db.execute(
        text("SELECT 1 FROM scorecards WHERE session_id = :sid LIMIT 1"),
        {"sid": session_id},
    )
    return result.scalar_one_or_none() is not None


async def _has_other_active_session(
    db: AsyncSession, user_id: str, session_id: uuid.UUID
) -> bool:
    """True iff this user has a DIFFERENT session currently running.

    'Running' = status 'in_progress' and touched within
    ``active_session_window_minutes``. The window is what stops a row stranded by
    a crashed worker from locking the candidate out for good.
    """
    cap = settings.max_active_sessions_per_user
    if cap <= 0:
        return False
    result = await db.execute(
        text(
            "SELECT count(*) AS active_session_probe FROM sessions"
            " WHERE user_id = :uid AND id <> :sid AND status = 'in_progress'"
            "   AND deleted_at IS NULL"
            "   AND updated_at > now() - make_interval(mins => :mins)"
        ),
        {
            "uid": uuid.UUID(user_id),
            "sid": session_id,
            "mins": settings.active_session_window_minutes,
        },
    )
    running = result.scalar_one_or_none() or 0
    return int(running) >= cap


async def _load_open_session(
    db: AsyncSession, session_id: uuid.UUID, user_id_str: str
) -> InterviewSession:
    """Load the session and enforce existence, ownership and not-finished.

    Shared by the token and re-dispatch endpoints so the two can never drift:
    a gate added to one and not the other would let ``/redispatch`` start an
    interviewer for a session ``/token`` refuses.
    """
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

    # 3. A finished session is never re-enterable (security-audit finding,
    #    2026-08). data_gateway's magic-link redeem path already refuses to
    #    re-enter a finished interview (interview_take.py — "A finished
    #    interview is never re-enterable"), but that control lived ONLY on
    #    the redeem/resume path. Without this check here, a candidate's
    #    still-valid 15-minute access token could re-POST for an already
    #    COMPLETED (and possibly already-scored) session, get a fresh LiveKit
    #    token, and the worker would run a SECOND full interview on it —
    #    flipping status back to 'in_progress' and overwriting completed_at /
    #    duration_seconds. 'failed' is included too (a session that errored
    #    out mid-interview is also not resumable via this endpoint); a
    #    scorecard row is checked as a durable backstop in case the status
    #    write raced or never landed. 'abandoned' / 'in_progress' /
    #    'created' are deliberately NOT blocked — those must stay
    #    re-enterable within the token's TTL.
    if session.status in ("completed", "failed") or await _scorecard_exists(db, session_id):
        log.info(
            "rooms.token.session_finished",
            session_id=str(session_id),
            status=session.status,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This interview session has already finished and cannot be re-entered.",
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

    # 5. One RUNNING interview per user (M3). Another of this user's sessions in
    #    progress refuses this one -- unless this request IS that session, which
    #    is a reload / rejoin (the probe excludes `session_id`). A database check,
    #    so it holds even while every Redis control fails open. It lives in this
    #    shared gate, not in /token alone: /redispatch skipped it once (security
    #    review H1) and so could start workers past the rule.
    if await _has_other_active_session(db, user_id_str, session_id):
        log.warning(
            "rooms.token.other_session_active",
            session_id=str(session_id),
            user_id=user_id_str,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "You already have an interview in progress. Finish it, or wait a "
                "few minutes for it to close, before starting another."
            ),
        )
    return session


@router.post(
    "/{session_id}/token",
    response_model=RoomTokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Mint a LiveKit join token for an interview session",
)
async def create_room_token(
    session_id: uuid.UUID,
    current_user: GuestBoundUserDep,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> RoomTokenResponse:
    """Issue a LiveKit join token after auth + ownership + consent checks.

    Guest-session binding (Phase 3, B7 — a magic-link guest token carries a
    session_id claim and may join ONLY that one session) is enforced by the
    ``GuestBoundUserDep`` dependency before this function body runs.
    """
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        # Misconfiguration — fail loud server-side, generic detail to client.
        log.error("rooms.token.livekit_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Real-time transport is not configured.",
        )

    user_id_str: str = current_user["sub"]

    # 0. Per-user cap on token requests (M3). Each successful call can dispatch a
    #    worker. 20/min is far above a reload or a flaky-network retry; a script
    #    hits it at once. Before any DB work so a flood is cheap to refuse.
    await enforce_user_rate_limit("room_token", user_id_str, settings.room_token_per_minute)

    await _load_open_session(db, session_id, user_id_str)

    # The gates above (and consent / one-running-interview, now in the shared
    # gate) are all database reads. Return the connection to the pool BEFORE the
    # Redis round trip and the rest: nothing below touches the DB, and a pooled
    # connection is a scarce slot (security review M3).
    await db.close()

    # 6. Atomically RESERVE an interview slot - after every other gate, so a
    #    refusal above never leaves a reservation behind, and BEFORE the token,
    #    so a full worker is a clear 503 instead of a dead room.
    #    The old check read a counter that only rose once the worker had
    #    ACCEPTED a job: N concurrent requests all saw "0 active" and all
    #    passed the cap. The ledger reservation is a single Lua script, so
    #    concurrent requests are serialised by Redis. Re-requesting for the SAME
    #    session (reload, retry) refreshes its slot and never takes a second.
    #    Fails OPEN on a Redis error: blocking every candidate because the
    #    ledger is unreachable is worse than over-admitting briefly; the worker's
    #    own load/memory gates still bound the real load and /redispatch
    #    recovers anyone who is over-admitted.
    room_name = str(session_id)
    cap = settings.worker_max_concurrent_jobs
    if cap > 0:
        try:
            slot, occupied = await reserve_slot(
                get_redis(),
                room_name,
                cap=cap,
                ttl_seconds=settings.slot_reservation_seconds,
                user_id=user_id_str,
            )
        except Exception as exc:  # noqa: BLE001 - fail OPEN, see above
            log.warning(
                "rooms.token.capacity_check_failed_open",
                session_id=room_name,
                err=type(exc).__name__,
            )
        else:
            if slot is SlotResult.USER_BUSY:
                # One reservation per user: stops one account holding slots it
                # never uses (security review H2).
                log.warning("rooms.token.user_slot_busy", session_id=room_name)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "You already have another interview waiting to start. "
                        "Join it, or wait a few minutes for it to expire."
                    ),
                )
            if slot is SlotResult.FULL:
                log.warning(
                    "rooms.token.worker_full",
                    session_id=room_name,
                    active_jobs=occupied,
                    cap=cap,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "All interview slots are currently occupied. "
                        "Please wait a few minutes and try again."
                    ),
                    headers={"Retry-After": "30"},
                )

    # The interviewer is dispatched by the join token itself (explicit
    # dispatch, see 7) - there is no separate launch call to forget.

    # 7. Mint the LiveKit join token, scoped to this one room.
    access = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(f"candidate-{user_id_str}")
        .with_name("Candidate")
        .with_ttl(_TOKEN_TTL)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,     # mic
                can_subscribe=True,   # interviewer audio + avatar video
                # can_publish_data deliberately NOT granted. The viseme stream
                # is agent -> client, so the candidate never needs to publish
                # data; granting it let them write to the `lk.chat` text
                # stream, which livekit-agents feeds in as a spoken answer.
                # Defence in depth for the same bypass the worker now closes
                # with text_input=False: a candidate typing answers would skip
                # the microphone, the STT and every proctoring signal.
                # Re-enable ONLY if a client->agent data channel actually
                # ships, and disable text_input at the session regardless.
                can_publish_data=False,
            )
        )
    )
    # EXPLICIT dispatch: the join token itself asks LiveKit to dispatch the
    # interviewer when the candidate's join creates the room. Under the legacy
    # automatic mode (INTERVIEW_AGENT_NAME="") there is nothing to add.
    room_config = room_configuration()
    if room_config is not None:
        access = access.with_room_config(room_config)
    token = access.to_jwt()
    # A fresh join starts a fresh re-dispatch budget, so a candidate who used it
    # up on an earlier attempt is not locked out of recovery for an hour.
    try:
        await reset_redispatch(get_redis(), room_name)
    except Exception as exc:  # noqa: BLE001 - best effort
        log.warning("rooms.token.redispatch_reset_failed", err=type(exc).__name__)

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


class RedispatchResponse(BaseModel):
    """Result of an interviewer re-dispatch attempt."""

    status: Literal["already_connected", "starting", "dispatched", "automatic"] = Field(
        ...,
        description=(
            "already_connected: an interviewer is in the room. starting: a "
            "dispatch is live (or being created), the interviewer is about to "
            "join. dispatched: a fresh dispatch was created. automatic: explicit "
            "dispatch is switched off, nothing to do."
        ),
    )
    attempt: int = Field(..., ge=1, description="Re-dispatch calls made for this session.")
    max_attempts: int = Field(..., ge=1, description="Ceiling before the server refuses (429).")


@router.post(
    "/{session_id}/redispatch",
    response_model=RedispatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Ask for an interviewer if none has joined the room (idempotent)",
)
async def redispatch_interviewer(
    session_id: uuid.UUID,
    current_user: GuestBoundUserDep,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> RedispatchResponse:
    """Re-create the interviewer dispatch for a room that has no interviewer.

    A RECOVERY path for a candidate who has already joined, never an entry path:
    it refuses (409) unless the room exists and contains this user's own
    ``candidate-<id>`` participant, because LiveKit would otherwise create the
    room as a side effect of the dispatch and a paid interviewer would run into
    an empty room (security review H1).

    IDEMPOTENT by construction (this is the mutation-idempotency contract for
    this endpoint, in place of a client-supplied key): while an agent is in the
    room, or a dispatch is live or being created, it changes nothing and returns
    ``already_connected`` / ``starting``. Only a room with NO interviewer and no
    live dispatch gets a new one, after dead dispatches are removed.

    Gates, in order: per-user rate limit; the SAME database gates as ``/token``
    (ownership, not finished, consent, one running interview per user - shared in
    ``_load_open_session`` so they cannot drift); the per-session ceiling, which
    FAILS CLOSED on a Redis error; the participant check and dispatch lock (also
    fail closed); and only then the slot reservation, which is non-extending and
    fails open. The ceiling and participant check come BEFORE reserving, so a
    refused call never touches the ledger.

    The DB connection is released before any LiveKit call (M3).
    """
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        log.error("rooms.redispatch.livekit_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Real-time transport is not configured.",
        )

    user_id_str: str = current_user["sub"]
    await enforce_user_rate_limit(
        "room_redispatch", user_id_str, settings.room_redispatch_per_minute
    )
    await _load_open_session(db, session_id, user_id_str)
    await db.close()  # nothing below touches the DB; LiveKit calls can be slow

    room_name = str(session_id)
    max_attempts = settings.room_redispatch_max_per_session
    cap = settings.worker_max_concurrent_jobs
    redis = get_redis()

    try:
        attempt = await note_redispatch(redis, room_name)
    except Exception as exc:  # noqa: BLE001 - fail CLOSED, see docstring
        log.error(
            "rooms.redispatch.ceiling_unavailable",
            session_id=room_name,
            err=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Please try again in a moment.",
        ) from exc
    if attempt > max_attempts:
        log.warning("rooms.redispatch.ceiling", session_id=room_name, attempt=attempt)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="We could not start your interviewer. Please try again later.",
        )

    async def reserve() -> None:
        """Take (never extend) this session's slot, right before dispatching."""
        if cap <= 0:
            return
        try:
            slot, occupied = await reserve_slot(
                redis,
                room_name,
                cap=cap,
                ttl_seconds=settings.slot_reservation_seconds,
                user_id=user_id_str,
            )
        except Exception as exc:  # noqa: BLE001 - the ledger fails OPEN
            log.warning(
                "rooms.redispatch.ledger_failed_open",
                session_id=room_name,
                err=type(exc).__name__,
            )
            return
        if slot is SlotResult.FULL:
            log.warning(
                "rooms.redispatch.worker_full",
                session_id=room_name,
                active_jobs=occupied,
                cap=cap,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "All interview slots are currently occupied. "
                    "Please wait a few minutes and try again."
                ),
                headers={"Retry-After": "30"},
            )
        if slot is SlotResult.USER_BUSY:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You already have another interview waiting to start.",
            )

    try:
        outcome = await ensure_interviewer(
            room_name, user_id_str, redis_getter=get_redis, before_create=reserve
        )
    except (api.TwirpError, aiohttp.ClientError, TimeoutError) as exc:
        log.error(
            "rooms.redispatch.livekit_error",
            session_id=room_name,
            err=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The interview service is temporarily unavailable. Please try again.",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - the dispatch lock fails CLOSED
        log.error(
            "rooms.redispatch.lock_unavailable",
            session_id=room_name,
            err=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Please try again in a moment.",
        ) from exc

    if outcome is DispatchOutcome.NOT_JOINED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Join the interview room first; there is nothing to restart yet.",
        )

    log.info(
        "rooms.redispatch.done",
        session_id=room_name,
        outcome=outcome.value,
        attempt=attempt,
    )
    return RedispatchResponse(
        status=outcome.value,  # type: ignore[arg-type]
        attempt=attempt,
        max_attempts=max_attempts,
    )
