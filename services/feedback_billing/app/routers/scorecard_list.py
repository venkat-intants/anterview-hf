"""GET /api/scorecards — paginated scorecard history list (UI redesign v2).

Auth: JWT required.
Authz approach: scorecards are keyed by session_id.  There is no user_id column
on the scorecards table.  The cleanest correct approach without a schema change
to scorecards is to:
  1. Resolve ALL session_ids for the caller from the sessions table.
  2. Filter scorecards to those whose session_id is in that set.

This is a single IN-subquery in SQL which Postgres executes efficiently with the
idx_scorecards_session index.  We avoid denormalising user_id onto scorecards
because that would couple feedback_billing to the users table migration cycle and
introduce a redundant FK across service boundaries.  The session-ownership proof
is already authoritative: sessions.user_id == caller guarantees the scorecard
belongs to the caller.

The job_title is resolved by joining sessions → jobs.  If a session row was
soft-deleted (deleted_at IS NOT NULL) the scorecard still appears because the
candidate has a right to see their historical scores; the title falls back to
"(deleted role)".
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from pydantic import BaseModel, Field
from shared.auth.jwt import verify_access_token
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as _app_settings
from app.database import get_db_session

log = structlog.get_logger(__name__)

router = APIRouter(tags=["scorecards"])

_bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing access token",
    headers={"WWW-Authenticate": "Bearer"},
)

# Truncate summary to this many characters in the list view.
_SUMMARY_TRUNCATE_LEN = 200


# ---------------------------------------------------------------------------
# Auth dependency (shared pattern with scorecard.py / score.py)
# ---------------------------------------------------------------------------


async def _require_jwt(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
) -> dict[str, Any]:
    """Verify Bearer JWT; return decoded payload."""
    if credentials is None:
        raise _UNAUTHORIZED

    try:
        payload = verify_access_token(
            credentials.credentials,
            secret=_app_settings.jwt_secret,
            algorithm=_app_settings.jwt_algorithm,
            expected_issuer=_app_settings.jwt_issuer,
            expected_audience=_app_settings.jwt_audience,
        )
    except JWTError as exc:
        log.warning("scorecard_list.auth.jwt_failed", error_type=type(exc).__name__)
        raise _UNAUTHORIZED from exc

    result: dict[str, Any] = dict(payload)
    return result


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ScorecardListItem(BaseModel):
    """A single scorecard row in the paginated list response."""

    scorecard_id: str
    session_id: str
    composite_score: float | None = None
    created_at: str = Field(..., description="ISO-8601 timestamp.")
    summary: str = Field(..., description="First 200 chars of the summary.")
    job_title: str | None = Field(
        default=None,
        description="Job title from the linked session, if resolvable.",
    )


class ScorecardListResponse(BaseModel):
    """Paginated response from GET /api/scorecards."""

    items: list[ScorecardListItem]
    total: int
    page: int
    per_page: int


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/scorecards",
    response_model=ScorecardListResponse,
    status_code=status.HTTP_200_OK,
    summary="List the caller's scorecards",
    description=(
        "Returns the authenticated user's scorecards, newest-first. "
        "Authz: only scorecards whose linked session belongs to the JWT sub "
        "are returned (resolved via sessions.user_id = caller). "
        "``summary`` is truncated to 200 characters. "
        "``job_title`` is resolved from the linked session's job row; "
        "null if the session or job has been deleted."
    ),
)
async def list_scorecards(
    jwt_payload: Annotated[dict[str, Any], Depends(_require_jwt)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    page: int = Query(default=1, ge=1, description="Page number, 1-indexed."),
    per_page: int = Query(default=20, ge=1, le=100, description="Items per page (max 100)."),
) -> ScorecardListResponse:
    """Return the caller's scorecards, newest-first, paginated.

    Authz is enforced by the subquery ``session_id IN (
        SELECT id FROM sessions WHERE user_id = :user_id AND deleted_at IS NULL
    )``.  This is a single query — no N+1 per scorecard.
    """
    user_id: str = str(jwt_payload.get("sub", ""))
    if not user_id:
        raise _UNAUTHORIZED

    offset = (page - 1) * per_page

    # ------------------------------------------------------------------
    # Count query
    # ------------------------------------------------------------------
    count_sql = sa_text(
        """
        SELECT COUNT(*) AS cnt
        FROM scorecards sc
        WHERE sc.session_id IN (
            SELECT id FROM sessions
            WHERE user_id = :user_id
        )
        """
    )
    count_row = (await db.execute(count_sql, {"user_id": user_id})).mappings().first()
    total: int = int(count_row["cnt"]) if count_row else 0

    # ------------------------------------------------------------------
    # Data query: scorecard + job_title resolved via sessions JOIN jobs
    # LEFT JOINs so soft-deleted or job-deleted sessions still appear.
    # ------------------------------------------------------------------
    data_sql = sa_text(
        """
        SELECT
            sc.scorecard_id::text          AS scorecard_id,
            sc.session_id::text            AS session_id,
            sc.composite_score::float      AS composite_score,
            sc.created_at                  AS created_at,
            sc.summary                     AS summary,
            j.title                        AS job_title
        FROM scorecards sc
        LEFT JOIN sessions s ON s.id = sc.session_id
        LEFT JOIN jobs j     ON j.id = s.job_id
        WHERE sc.session_id IN (
            SELECT id FROM sessions
            WHERE user_id = :user_id
        )
        ORDER BY sc.created_at DESC
        LIMIT :limit OFFSET :offset
        """
    )
    rows = (
        await db.execute(data_sql, {"user_id": user_id, "limit": per_page, "offset": offset})
    ).mappings().all()

    items: list[ScorecardListItem] = []
    for row in rows:
        raw_summary: str = str(row["summary"] or "")
        truncated = raw_summary[:_SUMMARY_TRUNCATE_LEN]
        if len(raw_summary) > _SUMMARY_TRUNCATE_LEN:
            truncated += "…"

        items.append(
            ScorecardListItem(
                scorecard_id=str(row["scorecard_id"]),
                session_id=str(row["session_id"]),
                composite_score=float(row["composite_score"]) if row["composite_score"] is not None else None,
                created_at=row["created_at"].isoformat() if row["created_at"] is not None else "",
                summary=truncated,
                job_title=str(row["job_title"]) if row["job_title"] is not None else None,
            )
        )

    log.info(
        "scorecard_list.fetched",
        user_id=user_id,
        total=total,
        page=page,
        per_page=per_page,
    )

    return ScorecardListResponse(items=items, total=total, page=page, per_page=per_page)
