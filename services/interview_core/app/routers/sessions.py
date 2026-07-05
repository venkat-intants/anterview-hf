"""Session management endpoints for interview_core — S2-007 + UI redesign v2.

POST /api/sessions  — create a new interview session (JWT-protected).
GET  /api/sessions  — list the caller's sessions, newest-first, paginated.

NOTE (2026-05-31): the D-ID presenter catalog + GET /api/presenters picker were
removed with the avatar layer. Avatar/voice selection will be reintroduced here
when the real-time (LiveKit/Pipecat) avatar layer is rebuilt. The session.presenter_id
DB column is retained (nullable) and currently left unset.

The frontend calls POST before opening the WebSocket so it has a session_id
to embed in the WS URL path.  The WS handler then loads the session from DB,
verifies ownership, and runs the LangGraph loop.

Session ownership model:
    session.user_id  is set from the JWT sub claim at creation time.
    The WS handler enforces that the connecting user matches session.user_id
    (close code 4003 = Forbidden if they differ).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.avatars import resolve_avatar, valid_avatar_ids
from app.consent_guard import has_active_consent
from app.database import get_db_session
from app.dependencies import NonGuestUserDep
from app.models import Job
from app.models import Session as InterviewSession

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["sessions"])

# ---------------------------------------------------------------------------
# Allowed languages (Day-1 set; expand as new Bhashini models land)
# ---------------------------------------------------------------------------
_ALLOWED_LANGUAGES: frozenset[str] = frozenset({"en", "hi", "te"})

Language = Literal["en", "hi", "te"]


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    """Body for POST /api/sessions."""

    job_id: uuid.UUID = Field(..., description="UUID of the job role to interview for.")
    language: Language = Field(
        default="en",
        description="Interview language. Supported: en, hi, te.",
    )
    avatar_id: str | None = Field(
        default=None,
        description=(
            "Optional avatar id from GET /api/avatars. "
            "If omitted or unknown, defaults to 'anna'. "
            "Valid values: 'lucas', 'anna', 'gloria'."
        ),
    )


class CreateSessionResponse(BaseModel):
    """Response from POST /api/sessions."""

    session_id: uuid.UUID = Field(..., description="UUID of the newly created session.")
    job_title: str = Field(..., description="Human-readable job title for the role.")
    language: Language = Field(..., description="Language the session will be conducted in.")


class SessionListItem(BaseModel):
    """A single session row in the paginated list response."""

    session_id: uuid.UUID
    job_title: str
    language: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    duration_seconds: int | None = None
    created_at: datetime
    scorecard_id: str | None = Field(
        default=None,
        description="scorecard_id if a scorecard exists for this session, else null.",
    )


class SessionListResponse(BaseModel):
    """Paginated response from GET /api/sessions."""

    items: list[SessionListItem]
    total: int
    page: int
    per_page: int


# ---------------------------------------------------------------------------
# Endpoint: GET /api/sessions (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    status_code=status.HTTP_200_OK,
    summary="List the caller's interview sessions",
    description=(
        "Returns the authenticated user's sessions, newest-first. "
        "Supports pagination via ``page`` / ``per_page`` query parameters "
        "and optional ``status`` filter. "
        "Each item includes a ``scorecard_id`` field if a scorecard exists for "
        "that session (resolved via a LEFT JOIN on the scorecards table). "
        "Only the caller's own sessions are returned — AUTHZ enforced."
    ),
)
async def list_sessions(
    current_user: NonGuestUserDep,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    page: int = Query(default=1, ge=1, description="Page number, 1-indexed."),
    per_page: int = Query(default=20, ge=1, le=100, description="Items per page (max 100)."),
    status: str | None = Query(
        default=None,
        description=(
            "Optional status filter. "
            "Values: created, in_progress, completed, abandoned, failed."
        ),
    ),
) -> SessionListResponse:
    """List the authenticated user's interview sessions, newest-first.

    Authz: ``session.user_id`` must equal the JWT ``sub`` — enforced by the
    WHERE clause; no side-channel that reveals other users' sessions.
    """
    user_id = uuid.UUID(current_user["sub"])

    # ------------------------------------------------------------------
    # Build the base filter
    # ------------------------------------------------------------------
    # Note: interview_core's Session model does not map deleted_at (that column
    # is added by the data_gateway migration; interview_core mirrors the same DB
    # but does not ORM-map every column).  We filter at the SQL level only on
    # user_id and status — soft-deleted sessions still appear in history (the
    # DPDP erasure path is handled by data_gateway).
    filters = [
        InterviewSession.user_id == user_id,
    ]
    if status is not None:
        filters.append(InterviewSession.status == status)

    # ------------------------------------------------------------------
    # Count query (scalar — avoids loading rows for the count)
    # ------------------------------------------------------------------
    count_stmt = select(func.count()).select_from(InterviewSession).where(*filters)
    count_result = await db.execute(count_stmt)
    total: int = count_result.scalar_one()

    # ------------------------------------------------------------------
    # Data query — join Job for title, LEFT JOIN scorecards for scorecard_id
    # We use raw SQL via text() for the LEFT JOIN on scorecards because
    # the Scorecard model lives in the same DB but the ORM relationship is
    # not mapped here (scorecards has no FK in the DDL — cross-service rule).
    # Instead we do an ORM query for sessions+jobs and a separate scalar
    # subquery for scorecard_id, which avoids raw SQL while keeping it clean.
    # ------------------------------------------------------------------
    from sqlalchemy import text as sa_text  # local import to avoid pollution

    offset = (page - 1) * per_page

    data_stmt = (
        select(
            InterviewSession.id,
            Job.title.label("job_title"),
            InterviewSession.language,
            InterviewSession.status,
            InterviewSession.started_at,
            InterviewSession.completed_at,
            InterviewSession.duration_seconds,
            InterviewSession.created_at,
        )
        .join(Job, InterviewSession.job_id == Job.id)
        .where(*filters)
        .order_by(InterviewSession.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )

    rows = (await db.execute(data_stmt)).mappings().all()

    # Resolve scorecard_id per session in a single IN query to keep N+1 away.
    # asyncpg (the underlying driver) does not accept a bare Python list bound to
    # ANY(:ids) — it has no way to infer the element type at bind time.
    # We use SQLAlchemy's expanding bindparam which rewrites  "IN (:ids)"  to
    # "IN (:ids_0, :ids_1, ...)" — one scalar parameter per element.  Each is
    # cast to ::text and compared against session_id::text so the driver never
    # has to deal with a UUID-array type.  This is safe and driver-agnostic.
    from sqlalchemy import bindparam  # local import to keep top-level clean

    session_ids = [row["id"] for row in rows]
    scorecard_map: dict[uuid.UUID, str] = {}
    if session_ids:
        sc_rows = (
            await db.execute(
                sa_text(
                    "SELECT scorecard_id::text, session_id::text "
                    "FROM scorecards "
                    "WHERE session_id::text IN :ids"
                ).bindparams(
                    bindparam("ids", value=[str(s) for s in session_ids], expanding=True)
                ),
            )
        ).mappings().all()
        for sc_row in sc_rows:
            # session_id is returned as text (::text cast above); convert back to
            # UUID for map key lookup.  Guard against the value already being a UUID
            # (e.g. in unit tests that return UUID objects from mock DB rows).
            raw_sid = sc_row["session_id"]
            sid_uuid = raw_sid if isinstance(raw_sid, uuid.UUID) else uuid.UUID(str(raw_sid))
            scorecard_map[sid_uuid] = sc_row["scorecard_id"]

    items = [
        SessionListItem(
            session_id=row["id"],
            job_title=str(row["job_title"]),
            language=str(row["language"]),
            status=str(row["status"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            duration_seconds=row["duration_seconds"],
            created_at=row["created_at"],
            scorecard_id=scorecard_map.get(row["id"]),
        )
        for row in rows
    ]

    log.info(
        "sessions.list",
        user_id=str(user_id),
        total=total,
        page=page,
        per_page=per_page,
    )

    return SessionListResponse(items=items, total=total, page=page, per_page=per_page)


# ---------------------------------------------------------------------------
# Endpoint: POST /api/sessions
# ---------------------------------------------------------------------------


@router.post(
    "/sessions",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new interview session",
    description=(
        "Creates a session row linked to the calling user and the specified job. "
        "Returns the session_id to embed in the WebSocket URL. "
        "The session starts in status='created' and transitions to 'in_progress' "
        "when the interview transport connects."
    ),
)
async def create_session(
    body: CreateSessionRequest,
    current_user: NonGuestUserDep,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CreateSessionResponse:
    """Create a new interview session for the authenticated user.

    Validates:
    - ``job_id`` exists and ``is_active=True`` (404 if not found, 400 if inactive).
    - ``language`` is in the allowed set (422 via Pydantic Literal).
    - Active DPDP consent exists (403 if missing).

    Returns the session_id, job_title, and confirmed language.
    """
    user_id_str: str = current_user["sub"]

    # ------------------------------------------------------------------
    # Validate job exists and is active
    # ------------------------------------------------------------------
    result = await db.execute(select(Job).where(Job.id == body.job_id))
    job: Job | None = result.scalar_one_or_none()

    if job is None:
        log.info(
            "sessions.create.job_not_found",
            user_id=user_id_str,
            job_id=str(body.job_id),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {body.job_id} not found.",
        )

    if not job.is_active:
        log.info(
            "sessions.create.job_inactive",
            user_id=user_id_str,
            job_id=str(body.job_id),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job {body.job_id} is not currently active.",
        )

    # ------------------------------------------------------------------
    # Validate avatar_id if provided (422 on unknown id — not a silent fallback;
    # the client passed an explicit id that does not exist in the catalog).
    # ------------------------------------------------------------------
    if body.avatar_id is not None and body.avatar_id not in valid_avatar_ids():
        log.info(
            "sessions.create.unknown_avatar_id",
            user_id=user_id_str,
            avatar_id=body.avatar_id,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown avatar_id {body.avatar_id!r}. "
                f"Valid values: {sorted(valid_avatar_ids())}."
            ),
        )

    # Resolve: if avatar_id is None, resolve_avatar returns the default ("anna").
    # presenter_id is ALWAYS set to a valid catalog id after this point.
    resolved_avatar = resolve_avatar(body.avatar_id)

    # ------------------------------------------------------------------
    # DPDP §6/§7 gate — server-side enforcement of S3-011.
    # Without this, a candidate could bypass the React consent modal by
    # calling the API directly (e.g. via curl or by deleting the modal
    # in DevTools). security-auditor flagged this as CRITICAL.
    # ------------------------------------------------------------------
    if not await has_active_consent(db, user_id_str):
        log.info(
            "sessions.create.consent_required",
            user_id=user_id_str,
            job_id=str(body.job_id),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "DPDP consent required before starting an interview session. "
                "Call POST /consent (data_gateway) first."
            ),
        )

    # ------------------------------------------------------------------
    # Create session row
    # ------------------------------------------------------------------
    now = datetime.now(tz=UTC)
    session_id = uuid.uuid4()

    new_session = InterviewSession(
        id=session_id,
        user_id=uuid.UUID(user_id_str),
        job_id=body.job_id,
        language=body.language,
        status="created",
        # started_at is NOT NULL in the DB (server_default=now()). We set it
        # explicitly here to the current time; the WS handler may update it
        # to the exact socket-accept time when the candidate actually connects.
        started_at=now,
        completed_at=None,
        duration_seconds=None,
        session_metadata={},
        created_at=now,
        updated_at=now,
        # presenter_id stores the resolved avatar id (always a valid catalog id).
        # resolve_avatar() ensures this is never None — defaults to "anna".
        presenter_id=resolved_avatar.id,
    )
    db.add(new_session)
    await db.commit()

    log.info(
        "sessions.created",
        session_id=str(session_id),
        user_id=user_id_str,
        job_id=str(body.job_id),
        language=body.language,
        avatar_id=resolved_avatar.id,
    )

    return CreateSessionResponse(
        session_id=session_id,
        job_title=job.title,
        language=body.language,
    )
