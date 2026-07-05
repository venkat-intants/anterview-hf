"""Admin analytics endpoints — read-only aggregate queries over the shared DB.

All endpoints sit behind the shared verify_admin_role dependency (HTTP 401/403
on missing or non-admin JWT).  All queries exclude soft-deleted rows via
``users.deleted_at IS NULL`` and ``sessions.deleted_at IS NULL`` predicates.

Endpoints
---------
GET /admin/overview                    — KPI tiles
GET /admin/interviews                  — paginated interview list with filters
GET /admin/interviews/export.csv       — streaming CSV export (same filters)
GET /admin/interviews/{session_id}     — drill-in detail (audit-logged)
GET /admin/analytics/by-role           — grouped by job title
GET /admin/analytics/by-language       — grouped by language
GET /admin/analytics/score-distribution — histogram + per-axis averages
GET /admin/analytics/trends            — daily series (date_trunc)

PII note
--------
- Candidate email and full_name are returned in paginated lists and CSV
  exports.  These are admin-only endpoints (JWT role check enforced at the
  prefix level in main.py AND individually on each endpoint via AdminDep).
- The drill-in endpoint (GET /admin/interviews/{session_id}) writes an
  audit_log entry for every access: action "admin.interview.view",
  resource_type "session", resource_id = session_id.
- The CSV export endpoint writes an audit_log entry: action
  "admin.interviews.export".
- Candidate PII is NEVER written to structlog.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_auth import AdminDep
from app.database import get_db_session
from app.models import AuditLog

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["analytics"])

DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]

# ---------------------------------------------------------------------------
# Score axes — 4 NOS-aligned axes defined in LLD §10 / scorer.py _WEIGHTS
# ---------------------------------------------------------------------------

_AXES: list[str] = ["communication", "technical", "problem_solving", "confidence"]

# CSV export column order (mirrors InterviewListItem fields)
_CSV_COLUMNS: list[str] = [
    "session_id",
    "candidate_email",
    "candidate_name",
    "job_title",
    "status",
    "language",
    "composite_score",
    "created_at",
    "completed_at",
    "duration_seconds",
]

# Fixed score histogram bucket labels (inclusive lower, exclusive upper)
_SCORE_BUCKETS: list[str] = ["0-2", "2-4", "4-6", "6-8", "8-10"]

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class OverviewResponse(BaseModel):
    """KPI tile data returned by GET /admin/overview."""

    total_candidates: int = Field(..., description="Non-deleted users.")
    total_interviews: int = Field(..., description="Non-deleted sessions.")
    completed_interviews: int
    completion_rate: float = Field(..., description="Fraction 0.0–1.0; 0 when no interviews.")
    avg_composite_score: float | None = Field(None, description="Rounded to 2 dp; null if none.")
    avg_duration_seconds: float | None = Field(None, description="Rounded to 1 dp; null if none.")
    interviews_today: int
    interviews_last_7d: int
    interviews_last_30d: int


class InterviewListItem(BaseModel):
    """One row in the paginated interview list."""

    session_id: str
    candidate_email: str
    candidate_name: str | None
    job_title: str | None
    status: str
    language: str
    composite_score: float | None = Field(None, description="Rounded to 2 dp.")
    created_at: str = Field(..., description="ISO-8601 UTC timestamp.")
    completed_at: str | None
    duration_seconds: int | None


class InterviewListResponse(BaseModel):
    """Paginated response from GET /admin/interviews."""

    items: list[InterviewListItem]
    total: int
    page: int
    per_page: int


class ScorecardDetail(BaseModel):
    """Full scorecard embedded in the drill-in detail response."""

    scorecard_id: str
    composite_score: float | None
    communication: float | None
    technical: float | None
    problem_solving: float | None
    confidence: float | None
    # Per-axis "why this score" explanation, keyed by axis. Empty dict for
    # scorecards generated before the rationale feature.
    rationale: dict[str, str] = {}
    strengths: list[Any] | None
    improvements: list[Any] | None
    summary: str | None


class IntegrityEventItem(BaseModel):
    """One flagged proctoring event in the drill-in timeline."""

    event_type: str
    started_at: str
    ended_at: str | None = None
    duration_seconds: float | None = None


class InterviewDetailResponse(BaseModel):
    """Drill-in detail returned by GET /admin/interviews/{session_id}."""

    session_id: str
    candidate_email: str
    candidate_name: str | None
    candidate_preferred_language: str | None
    job_title: str | None
    status: str
    language: str
    started_at: str | None
    completed_at: str | None
    duration_seconds: int | None
    scorecard: ScorecardDetail | None = Field(
        None, description="null when the session has not been scored yet."
    )
    # Phase B proctoring. null when proctoring was off for this session.
    integrity_score: int | None = Field(
        None, description="0-100 integrity score, higher = cleaner. null if no proctoring."
    )
    proctoring_summary: dict[str, Any] | None = Field(
        None, description="Per-type event counts + flagged seconds. null if no proctoring."
    )
    integrity_events: list[IntegrityEventItem] = Field(
        default_factory=list,
        description="Time-ordered flagged proctoring events (most recent first, capped).",
    )


class ByRoleItem(BaseModel):
    """One job-role group from GET /admin/analytics/by-role."""

    job_id: str
    job_title: str
    interview_count: int
    avg_composite: float | None = Field(None, description="Rounded to 2 dp.")
    avg_communication: float | None = Field(None, description="Rounded to 2 dp.")
    avg_technical: float | None = Field(None, description="Rounded to 2 dp.")
    avg_problem_solving: float | None = Field(None, description="Rounded to 2 dp.")
    avg_confidence: float | None = Field(None, description="Rounded to 2 dp.")


class ByLanguageItem(BaseModel):
    """One language group from GET /admin/analytics/by-language."""

    language: str
    interview_count: int
    avg_composite: float | None = Field(None, description="Rounded to 2 dp.")


class ScoreBucket(BaseModel):
    """One histogram bucket."""

    label: str = Field(..., description="e.g. '0-2', '2-4', …")
    count: int


class ScoreDistributionResponse(BaseModel):
    """Response from GET /admin/analytics/score-distribution."""

    buckets: list[ScoreBucket]
    avg_communication: float | None
    avg_technical: float | None
    avg_problem_solving: float | None
    avg_confidence: float | None


class TrendItem(BaseModel):
    """One day in the trend series."""

    date: str = Field(..., description="ISO-8601 date string, e.g. '2026-05-01'.")
    interview_count: int
    avg_composite: float | None = Field(None, description="Rounded to 2 dp.")


class TrendsResponse(BaseModel):
    """Response from GET /admin/analytics/trends."""

    items: list[TrendItem]
    date_from: str
    date_to: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _round2(value: Any) -> float | None:
    """Return float rounded to 2 dp, or None if value is None."""
    if value is None:
        return None
    return round(float(value), 2)


def _round1(value: Any) -> float | None:
    """Return float rounded to 1 dp, or None if value is None."""
    if value is None:
        return None
    return round(float(value), 1)


def _iso(value: Any) -> str | None:
    """Return ISO-8601 string from a datetime-like, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def _write_audit(
    *,
    db: AsyncSession,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    details: dict[str, Any] | None = None,
) -> None:
    """Insert one audit_log row.  Commits separately so the main transaction is
    unaffected by audit failures (we log the error and continue)."""
    try:
        row = AuditLog(
            actor_id=uuid.UUID(actor_id) if actor_id else None,
            actor_type="admin",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=None,
            user_agent=None,
            event_ts=datetime.now(UTC),
        )
        db.add(row)
        await db.commit()
    except (SQLAlchemyError, ValueError) as exc:
        log.error(
            "analytics.audit_write_failed",
            action=action,
            resource_id=str(resource_id),
            exc_type=type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# 1. GET /admin/overview
# ---------------------------------------------------------------------------


@router.get(
    "/overview",
    response_model=OverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Admin KPI overview tiles",
    description=(
        "Returns platform-wide KPI tiles in one round-trip: total candidates, "
        "total/completed interviews, completion rate, avg composite score, "
        "avg duration, and interview counts for today / last 7 / last 30 days. "
        "Soft-deleted users and sessions are excluded."
    ),
)
async def get_overview(
    admin_sub: AdminDep,
    db: DbSessionDep,
) -> OverviewResponse:
    """Single aggregate query returning all KPI tiles."""
    now_utc = datetime.now(UTC)
    today_start = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=UTC)
    last_7d = now_utc - timedelta(days=7)
    last_30d = now_utc - timedelta(days=30)

    sql = sa_text(
        """
        SELECT
            (SELECT COUNT(*) FROM users WHERE deleted_at IS NULL)
                AS total_candidates,
            COUNT(s.id)
                AS total_interviews,
            COUNT(s.id) FILTER (WHERE s.status = 'completed')
                AS completed_interviews,
            AVG(sc.composite_score)
                AS avg_composite_score,
            AVG(s.duration_seconds)
                AS avg_duration_seconds,
            COUNT(s.id) FILTER (WHERE s.created_at >= :today_start)
                AS interviews_today,
            COUNT(s.id) FILTER (WHERE s.created_at >= :last_7d)
                AS interviews_last_7d,
            COUNT(s.id) FILTER (WHERE s.created_at >= :last_30d)
                AS interviews_last_30d
        FROM sessions s
        LEFT JOIN scorecards sc ON sc.session_id = s.id
        WHERE s.deleted_at IS NULL
        """
    )
    row = (
        await db.execute(
            sql,
            {
                "today_start": today_start,
                "last_7d": last_7d,
                "last_30d": last_30d,
            },
        )
    ).mappings().first()

    if row is None:
        return OverviewResponse(
            total_candidates=0,
            total_interviews=0,
            completed_interviews=0,
            completion_rate=0.0,
            avg_composite_score=None,
            avg_duration_seconds=None,
            interviews_today=0,
            interviews_last_7d=0,
            interviews_last_30d=0,
        )

    total = int(row["total_interviews"] or 0)
    completed = int(row["completed_interviews"] or 0)
    completion_rate = round(completed / total, 4) if total > 0 else 0.0

    log.info("analytics.overview.fetched", actor=admin_sub, total_interviews=total)

    return OverviewResponse(
        total_candidates=int(row["total_candidates"] or 0),
        total_interviews=total,
        completed_interviews=completed,
        completion_rate=completion_rate,
        avg_composite_score=_round2(row["avg_composite_score"]),
        avg_duration_seconds=_round1(row["avg_duration_seconds"]),
        interviews_today=int(row["interviews_today"] or 0),
        interviews_last_7d=int(row["interviews_last_7d"] or 0),
        interviews_last_30d=int(row["interviews_last_30d"] or 0),
    )


# ---------------------------------------------------------------------------
# Shared filter SQL builder (used by list endpoint AND CSV export)
# ---------------------------------------------------------------------------


# Explicit sort-column whitelist — defence-in-depth in addition to the
# endpoint pattern= validator.  Any unrecognised value falls back to created_at.
_SORT_WHITELIST: dict[str, str] = {
    "created_at": "s.created_at",
    "composite_score": "sc.composite_score",
}


def _build_interview_filter_sql(
    *,
    date_from: datetime | None,
    date_to: datetime | None,
    status_filter: str | None,
    job_id: uuid.UUID | None,
    language: str | None,
    min_score: float | None,
    max_score: float | None,
    q: str | None,
    sort_by: str,
    sort_desc: bool,
) -> tuple[str, str, dict[str, Any]]:
    """Return (where_clause, order_clause, params) as separate strings.

    The caller prepends SELECT … FROM sessions … and appends LIMIT/OFFSET.
    All filters are AND-combined.  Returns only non-deleted sessions/users.
    Splitting where and order allows the count query to skip ORDER BY without
    fragile string splitting.
    """
    conditions: list[str] = ["s.deleted_at IS NULL", "u.deleted_at IS NULL"]
    params: dict[str, Any] = {}

    if date_from is not None:
        conditions.append("s.created_at >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        conditions.append("s.created_at <= :date_to")
        params["date_to"] = date_to
    if status_filter is not None:
        conditions.append("s.status = :status")
        params["status"] = status_filter
    if job_id is not None:
        conditions.append("s.job_id = :job_id")
        params["job_id"] = job_id
    if language is not None:
        conditions.append("s.language = :language")
        params["language"] = language
    if min_score is not None:
        conditions.append("sc.composite_score >= :min_score")
        params["min_score"] = min_score
    if max_score is not None:
        conditions.append("sc.composite_score <= :max_score")
        params["max_score"] = max_score
    if q is not None:
        conditions.append("(u.email ILIKE :q OR u.full_name ILIKE :q)")
        params["q"] = f"%{q}%"

    where = "WHERE " + " AND ".join(conditions)

    # Whitelist lookup — prevents SQL injection even if pattern= validator is bypassed.
    sort_col = _SORT_WHITELIST.get(sort_by, "s.created_at")
    order_dir = "DESC" if sort_desc else "ASC"
    order = f"ORDER BY {sort_col} {order_dir} NULLS LAST"

    return where, order, params


_INTERVIEW_SELECT = """
    SELECT
        s.id::text                          AS session_id,
        u.email                             AS candidate_email,
        u.full_name                         AS candidate_name,
        j.title                             AS job_title,
        s.status                            AS status,
        s.language                          AS language,
        sc.composite_score::float           AS composite_score,
        s.created_at                        AS created_at,
        s.completed_at                      AS completed_at,
        s.duration_seconds                  AS duration_seconds
    FROM sessions s
    JOIN users u ON u.id = s.user_id
    LEFT JOIN jobs j ON j.id = s.job_id
    LEFT JOIN scorecards sc ON sc.session_id = s.id
"""


def _csv_line(row: Any) -> str:
    """Format one DB row mapping as a single CSV data line string.

    S4 fix: composite_score == 0.0 renders as '0.0' (not empty string).
    Uses explicit ``is None`` check instead of falsy ``or ""``.
    Same fix applied to duration_seconds == 0.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, lineterminator="\n")
    # Explicit None-check: 0.0 must not be treated as falsy.
    score_val = _round2(row["composite_score"])
    composite_cell = "" if score_val is None else str(score_val)
    dur = row["duration_seconds"]
    duration_cell = "" if dur is None else str(int(dur))
    writer.writerow(
        {
            "session_id": str(row["session_id"]),
            "candidate_email": str(row["candidate_email"]),
            "candidate_name": str(row["candidate_name"] or ""),
            "job_title": str(row["job_title"] or ""),
            "status": str(row["status"]),
            "language": str(row["language"]),
            "composite_score": composite_cell,
            "created_at": _iso(row["created_at"]) or "",
            "completed_at": _iso(row["completed_at"]) or "",
            "duration_seconds": duration_cell,
        }
    )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 2. GET /admin/interviews — paginated list
# ---------------------------------------------------------------------------


@router.get(
    "/interviews",
    response_model=InterviewListResponse,
    status_code=status.HTTP_200_OK,
    summary="Paginated admin interview list with filters",
    description=(
        "Paginated list of interview sessions. "
        "All filters are optional and AND-combined. "
        "Sortable by created_at (default desc) or composite_score. "
        "Soft-deleted sessions and users are excluded."
    ),
)
async def list_interviews(
    admin_sub: AdminDep,
    db: DbSessionDep,
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)."),
    per_page: int = Query(default=20, ge=1, le=200, description="Rows per page (max 200)."),
    date_from: datetime | None = Query(default=None, description="Filter sessions created >= this UTC datetime."),
    date_to: datetime | None = Query(default=None, description="Filter sessions created <= this UTC datetime."),
    status_filter: str | None = Query(default=None, alias="status", description="Filter by session status."),
    job_id: uuid.UUID | None = Query(default=None, description="Filter by job UUID."),
    language: str | None = Query(default=None, description="Filter by session language code."),
    min_score: float | None = Query(default=None, ge=0.0, le=10.0, description="Min composite_score (inclusive)."),
    max_score: float | None = Query(default=None, ge=0.0, le=10.0, description="Max composite_score (inclusive)."),
    q: str | None = Query(default=None, description="ILIKE search on candidate email or full_name."),
    sort_by: str = Query(default="created_at", pattern="^(created_at|composite_score)$"),
    sort_desc: bool = Query(default=True, description="Descending sort when true."),
) -> InterviewListResponse:
    """Return paginated interview sessions matching the supplied filters."""
    where_clause, order_clause, params = _build_interview_filter_sql(
        date_from=date_from,
        date_to=date_to,
        status_filter=status_filter,
        job_id=job_id,
        language=language,
        min_score=min_score,
        max_score=max_score,
        q=q,
        sort_by=sort_by,
        sort_desc=sort_desc,
    )

    # COUNT query — use only where_clause (no ORDER BY needed for counting).
    count_sql = sa_text(
        f"""
        SELECT COUNT(*) AS cnt
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        LEFT JOIN jobs j ON j.id = s.job_id
        LEFT JOIN scorecards sc ON sc.session_id = s.id
        {where_clause}
        """
    )
    count_row = (await db.execute(count_sql, params)).mappings().first()
    total = int(count_row["cnt"]) if count_row else 0

    offset = (page - 1) * per_page
    data_sql = sa_text(
        f"""
        {_INTERVIEW_SELECT}
        {where_clause}
        {order_clause}
        LIMIT :limit OFFSET :offset
        """
    )
    rows = (
        await db.execute(data_sql, {**params, "limit": per_page, "offset": offset})
    ).mappings().all()

    items = [
        InterviewListItem(
            session_id=str(row["session_id"]),
            candidate_email=str(row["candidate_email"]),
            candidate_name=str(row["candidate_name"]) if row["candidate_name"] else None,
            job_title=str(row["job_title"]) if row["job_title"] else None,
            status=str(row["status"]),
            language=str(row["language"]),
            composite_score=_round2(row["composite_score"]),
            created_at=_iso(row["created_at"]) or "",
            completed_at=_iso(row["completed_at"]),
            duration_seconds=int(row["duration_seconds"]) if row["duration_seconds"] is not None else None,
        )
        for row in rows
    ]

    log.info("analytics.interviews.list", actor=admin_sub, total=total, page=page)
    return InterviewListResponse(items=items, total=total, page=page, per_page=per_page)


# ---------------------------------------------------------------------------
# 8. GET /admin/interviews/export.csv — streaming CSV
# NOTE: this route MUST be registered before the {session_id} route so that
# FastAPI does not try to parse "export.csv" as a UUID path parameter.
# ---------------------------------------------------------------------------


@router.get(
    "/interviews/export.csv",
    status_code=status.HTTP_200_OK,
    summary="Stream all matching interviews as a CSV download",
    description=(
        "Applies the same filters as GET /admin/interviews (no pagination). "
        "Returns a streaming CSV attachment. "
        "Each export is audit-logged (action 'admin.interviews.export')."
    ),
    response_class=StreamingResponse,
)
async def export_interviews_csv(
    admin_sub: AdminDep,
    db: DbSessionDep,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    job_id: uuid.UUID | None = Query(default=None),
    language: str | None = Query(default=None),
    min_score: float | None = Query(default=None, ge=0.0, le=10.0),
    max_score: float | None = Query(default=None, ge=0.0, le=10.0),
    q: str | None = Query(default=None),
    sort_by: str = Query(default="created_at", pattern="^(created_at|composite_score)$"),
    sort_desc: bool = Query(default=True),
) -> StreamingResponse:
    """Stream matching interviews as CSV, one row per session.

    Uses SQLAlchemy async server-side streaming so the full result set is
    never loaded into memory — safe at govt scale.
    Audit log is written BEFORE the stream begins so a client disconnect
    cannot cause it to be skipped.
    """
    where_clause, order_clause, params = _build_interview_filter_sql(
        date_from=date_from,
        date_to=date_to,
        status_filter=status_filter,
        job_id=job_id,
        language=language,
        min_score=min_score,
        max_score=max_score,
        q=q,
        sort_by=sort_by,
        sort_desc=sort_desc,
    )

    data_sql = sa_text(
        f"""
        {_INTERVIEW_SELECT}
        {where_clause}
        {order_clause}
        """
    )

    # Audit log the export BEFORE streaming begins (client disconnect cannot skip it).
    await _write_audit(
        db=db,
        actor_id=admin_sub,
        action="admin.interviews.export",
        resource_type="interview_list",
        resource_id=uuid.uuid4(),  # synthetic resource id for the export event
        details=None,
    )

    log.info("analytics.interviews.export", actor=admin_sub)

    async def _generate() -> AsyncGenerator[str, None]:
        # Yield header first.
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        yield buf.getvalue()

        # Server-side streaming: db.stream() is an async function that returns
        # AsyncResult; iterate its .mappings() to yield rows one at a time
        # without loading the full result set into memory.
        stream_result = await db.stream(data_sql, params)
        async for row in stream_result.mappings():
            yield _csv_line(row)

    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=interviews.csv"},
    )


# ---------------------------------------------------------------------------
# 3. GET /admin/interviews/{session_id} — drill-in detail
# NOTE: registered after export.csv to avoid route collision.
# ---------------------------------------------------------------------------


@router.get(
    "/interviews/{session_id}",
    response_model=InterviewDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Admin interview drill-in detail (PII-access audit-logged)",
    description=(
        "Returns full session detail including the scorecard if present. "
        "Every access is written to audit_log (action 'admin.interview.view'). "
        "404 if the session is missing or soft-deleted."
    ),
)
async def get_interview_detail(
    session_id: uuid.UUID,
    admin_sub: AdminDep,
    db: DbSessionDep,
) -> InterviewDetailResponse:
    """Drill-in detail for one interview session.  Audit-logs PII access."""
    sql = sa_text(
        """
        SELECT
            s.id::text                          AS session_id,
            u.email                             AS candidate_email,
            u.full_name                         AS candidate_name,
            u.preferred_language                AS candidate_preferred_language,
            j.title                             AS job_title,
            s.status                            AS status,
            s.language                          AS language,
            s.started_at                        AS started_at,
            s.completed_at                      AS completed_at,
            s.duration_seconds                  AS duration_seconds,
            s.integrity_score                   AS integrity_score,
            s.proctoring_summary                AS proctoring_summary,
            sc.scorecard_id::text               AS scorecard_id,
            sc.composite_score::float           AS composite_score,
            sc.scores                           AS scores,
            sc.rationale                        AS rationale,
            sc.strengths                        AS strengths,
            sc.improvements                     AS improvements,
            sc.summary                          AS summary
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        LEFT JOIN jobs j ON j.id = s.job_id
        LEFT JOIN scorecards sc ON sc.session_id = s.id
        WHERE s.id = :session_id
          AND s.deleted_at IS NULL
          AND u.deleted_at IS NULL
        LIMIT 1
        """
    )
    row = (await db.execute(sql, {"session_id": session_id})).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )

    # Proctoring event timeline (most-recent-first, capped). Separate query so a
    # session with no proctoring simply yields an empty list.
    event_rows = (
        await db.execute(
            sa_text(
                """
                SELECT event_type, started_at, ended_at
                FROM integrity_events
                WHERE session_id = :session_id
                ORDER BY started_at DESC
                LIMIT 200
                """
            ),
            {"session_id": session_id},
        )
    ).mappings().all()

    # Audit-log PII access — write in background so a commit failure here does
    # not prevent the response from being returned.
    await _write_audit(
        db=db,
        actor_id=admin_sub,
        action="admin.interview.view",
        resource_type="session",
        resource_id=session_id,
        details=None,
    )

    log.info("analytics.interview.detail", actor=admin_sub, session_id=str(session_id))

    # Parse JSONB scores dict to per-axis floats
    scores_raw: dict[str, Any] = row["scores"] or {}
    rationale_raw: dict[str, Any] = row["rationale"] or {}
    scorecard: ScorecardDetail | None = None
    if row["scorecard_id"] is not None:
        scorecard = ScorecardDetail(
            scorecard_id=str(row["scorecard_id"]),
            composite_score=_round2(row["composite_score"]),
            communication=_round2(scores_raw.get("communication")),
            technical=_round2(scores_raw.get("technical")),
            problem_solving=_round2(scores_raw.get("problem_solving")),
            confidence=_round2(scores_raw.get("confidence")),
            rationale={k: str(v) for k, v in rationale_raw.items()},
            strengths=list(row["strengths"]) if row["strengths"] else None,
            improvements=list(row["improvements"]) if row["improvements"] else None,
            summary=str(row["summary"]) if row["summary"] else None,
        )

    integrity_events: list[IntegrityEventItem] = []
    for ev in event_rows:
        started = ev["started_at"]
        ended = ev["ended_at"]
        dur: float | None = None
        if started is not None and ended is not None:
            dur = round((ended - started).total_seconds(), 1)
        integrity_events.append(
            IntegrityEventItem(
                event_type=str(ev["event_type"]),
                started_at=_iso(started) or "",
                ended_at=_iso(ended),
                duration_seconds=dur,
            )
        )

    return InterviewDetailResponse(
        session_id=str(row["session_id"]),
        candidate_email=str(row["candidate_email"]),
        candidate_name=str(row["candidate_name"]) if row["candidate_name"] else None,
        candidate_preferred_language=(
            str(row["candidate_preferred_language"])
            if row["candidate_preferred_language"]
            else None
        ),
        job_title=str(row["job_title"]) if row["job_title"] else None,
        status=str(row["status"]),
        language=str(row["language"]),
        started_at=_iso(row["started_at"]),
        completed_at=_iso(row["completed_at"]),
        duration_seconds=(
            int(row["duration_seconds"]) if row["duration_seconds"] is not None else None
        ),
        scorecard=scorecard,
        integrity_score=(
            int(row["integrity_score"]) if row["integrity_score"] is not None else None
        ),
        proctoring_summary=row["proctoring_summary"] or None,
        integrity_events=integrity_events,
    )


# ---------------------------------------------------------------------------
# 3b. GET /admin/interviews/{session_id}/transcript — conversation turns
# ---------------------------------------------------------------------------


class TranscriptTurn(BaseModel):
    turn_number: int
    speaker: str  # interviewer | candidate
    text: str | None
    created_at: str | None


class TranscriptResponse(BaseModel):
    session_id: str
    turns: list[TranscriptTurn]


@router.get(
    "/interviews/{session_id}/transcript",
    response_model=TranscriptResponse,
    summary="Interview transcript (ordered conversation turns)",
)
async def get_interview_transcript(
    session_id: uuid.UUID,
    admin_sub: AdminDep,
    db: DbSessionDep,
) -> TranscriptResponse:
    """Ordered conversation turns for a session (admin drill-in)."""
    rows = (
        await db.execute(
            sa_text(
                """
                SELECT t.turn_number, t.speaker, t.text_content, t.created_at
                FROM turns t
                JOIN sessions s ON s.id = t.session_id
                WHERE t.session_id = :session_id AND s.deleted_at IS NULL
                ORDER BY t.turn_number ASC
                """
            ),
            {"session_id": session_id},
        )
    ).mappings().all()
    log.info("analytics.interview.transcript", actor=admin_sub, session_id=str(session_id))
    return TranscriptResponse(
        session_id=str(session_id),
        turns=[
            TranscriptTurn(
                turn_number=int(r["turn_number"]),
                speaker=str(r["speaker"]),
                text=str(r["text_content"]) if r["text_content"] else None,
                created_at=_iso(r["created_at"]),
            )
            for r in rows
        ],
    )


# ---------------------------------------------------------------------------
# 4. GET /admin/analytics/by-role
# ---------------------------------------------------------------------------


@router.get(
    "/analytics/by-role",
    response_model=list[ByRoleItem],
    status_code=status.HTTP_200_OK,
    summary="Interview counts and score averages grouped by job role",
    description=(
        "Groups non-deleted sessions by job_id / job title. "
        "Score averages exclude sessions without a scorecard "
        "(they still contribute to interview_count)."
    ),
)
async def analytics_by_role(
    admin_sub: AdminDep,
    db: DbSessionDep,
) -> list[ByRoleItem]:
    """Single GROUP BY query — no N+1."""
    sql = sa_text(
        """
        SELECT
            s.job_id::text                          AS job_id,
            COALESCE(j.title, '(unknown role)')     AS job_title,
            COUNT(s.id)                             AS interview_count,
            AVG(sc.composite_score)                 AS avg_composite,
            AVG((sc.scores->>'communication')::float)   AS avg_communication,
            AVG((sc.scores->>'technical')::float)       AS avg_technical,
            AVG((sc.scores->>'problem_solving')::float) AS avg_problem_solving,
            AVG((sc.scores->>'confidence')::float)      AS avg_confidence
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        LEFT JOIN jobs j ON j.id = s.job_id
        LEFT JOIN scorecards sc ON sc.session_id = s.id
        WHERE s.deleted_at IS NULL
          AND u.deleted_at IS NULL
        GROUP BY s.job_id, j.title
        ORDER BY interview_count DESC
        """
    )
    rows = (await db.execute(sql)).mappings().all()

    log.info("analytics.by_role.fetched", actor=admin_sub, groups=len(rows))

    return [
        ByRoleItem(
            job_id=str(row["job_id"]),
            job_title=str(row["job_title"]),
            interview_count=int(row["interview_count"]),
            avg_composite=_round2(row["avg_composite"]),
            avg_communication=_round2(row["avg_communication"]),
            avg_technical=_round2(row["avg_technical"]),
            avg_problem_solving=_round2(row["avg_problem_solving"]),
            avg_confidence=_round2(row["avg_confidence"]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 5. GET /admin/analytics/by-language
# ---------------------------------------------------------------------------


@router.get(
    "/analytics/by-language",
    response_model=list[ByLanguageItem],
    status_code=status.HTTP_200_OK,
    summary="Interview counts and score averages grouped by language",
)
async def analytics_by_language(
    admin_sub: AdminDep,
    db: DbSessionDep,
) -> list[ByLanguageItem]:
    """Single GROUP BY query over sessions.language."""
    sql = sa_text(
        """
        SELECT
            s.language                      AS language,
            COUNT(s.id)                     AS interview_count,
            AVG(sc.composite_score)         AS avg_composite
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        LEFT JOIN scorecards sc ON sc.session_id = s.id
        WHERE s.deleted_at IS NULL
          AND u.deleted_at IS NULL
        GROUP BY s.language
        ORDER BY interview_count DESC
        """
    )
    rows = (await db.execute(sql)).mappings().all()

    log.info("analytics.by_language.fetched", actor=admin_sub, groups=len(rows))

    return [
        ByLanguageItem(
            language=str(row["language"]),
            interview_count=int(row["interview_count"]),
            avg_composite=_round2(row["avg_composite"]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 6. GET /admin/analytics/score-distribution
# ---------------------------------------------------------------------------


@router.get(
    "/analytics/score-distribution",
    response_model=ScoreDistributionResponse,
    status_code=status.HTTP_200_OK,
    summary="Composite score histogram (fixed buckets) + per-axis averages",
    description=(
        "Composite score histogram in five fixed buckets (0-2, 2-4, 4-6, 6-8, 8-10) "
        "plus overall averages for each of the four NOS axes."
    ),
)
async def analytics_score_distribution(
    admin_sub: AdminDep,
    db: DbSessionDep,
) -> ScoreDistributionResponse:
    """Two queries: bucket counts + axis averages."""
    # C1: No ORDER BY label — Python fill loop over _SCORE_BUCKETS enforces order.
    # C2: AND sc.composite_score IS NOT NULL — prevents NULL rows from falling
    #     into the ELSE branch and inflating the '8-10' bucket.
    bucket_sql = sa_text(
        """
        SELECT
            CASE
                WHEN sc.composite_score < 2  THEN '0-2'
                WHEN sc.composite_score < 4  THEN '2-4'
                WHEN sc.composite_score < 6  THEN '4-6'
                WHEN sc.composite_score < 8  THEN '6-8'
                ELSE '8-10'
            END                             AS label,
            COUNT(*)                        AS cnt
        FROM scorecards sc
        JOIN sessions s ON s.id = sc.session_id
        WHERE s.deleted_at IS NULL
          AND sc.composite_score IS NOT NULL
        GROUP BY label
        """
    )

    # AVG already ignores NULLs natively; JOIN ensures only non-deleted sessions
    # contribute (soft-deleted sessions are excluded via s.deleted_at IS NULL).
    axis_sql = sa_text(
        """
        SELECT
            AVG((sc.scores->>'communication')::float)   AS avg_communication,
            AVG((sc.scores->>'technical')::float)       AS avg_technical,
            AVG((sc.scores->>'problem_solving')::float) AS avg_problem_solving,
            AVG((sc.scores->>'confidence')::float)      AS avg_confidence
        FROM scorecards sc
        JOIN sessions s ON s.id = sc.session_id
        WHERE s.deleted_at IS NULL
        """
    )

    bucket_rows = (await db.execute(bucket_sql)).mappings().all()
    axis_row = (await db.execute(axis_sql)).mappings().first()

    # Ensure all 5 fixed buckets are present, even if count = 0
    bucket_map = {str(r["label"]): int(r["cnt"]) for r in bucket_rows}
    buckets = [
        ScoreBucket(label=label, count=bucket_map.get(label, 0))
        for label in _SCORE_BUCKETS
    ]

    log.info("analytics.score_distribution.fetched", actor=admin_sub)

    return ScoreDistributionResponse(
        buckets=buckets,
        avg_communication=_round2(axis_row["avg_communication"]) if axis_row else None,
        avg_technical=_round2(axis_row["avg_technical"]) if axis_row else None,
        avg_problem_solving=_round2(axis_row["avg_problem_solving"]) if axis_row else None,
        avg_confidence=_round2(axis_row["avg_confidence"]) if axis_row else None,
    )


# ---------------------------------------------------------------------------
# 7. GET /admin/analytics/trends
# ---------------------------------------------------------------------------

_DEFAULT_TREND_DAYS = 30


@router.get(
    "/analytics/trends",
    response_model=TrendsResponse,
    status_code=status.HTTP_200_OK,
    summary="Daily interview count and avg composite score trend series",
    description=(
        "Returns a daily series (date_trunc day) of interview_count and "
        "avg_composite. Defaults to the last 30 days. "
        "Empty days (no interviews) are omitted from the series."
    ),
)
async def analytics_trends(
    admin_sub: AdminDep,
    db: DbSessionDep,
    date_from: date | None = Query(
        default=None,
        description="Start date (inclusive). Defaults to 30 days ago.",
    ),
    date_to: date | None = Query(
        default=None,
        description="End date (inclusive). Defaults to today.",
    ),
) -> TrendsResponse:
    """date_trunc('day') GROUP BY over the selected window."""
    now_utc = datetime.now(UTC)
    resolved_to = date_to or now_utc.date()
    resolved_from = date_from or (now_utc - timedelta(days=_DEFAULT_TREND_DAYS)).date()

    # Convert to UTC datetimes for the timestamp comparison
    from_dt = datetime(resolved_from.year, resolved_from.month, resolved_from.day, tzinfo=UTC)
    to_dt = datetime(
        resolved_to.year, resolved_to.month, resolved_to.day, 23, 59, 59, tzinfo=UTC
    )

    sql = sa_text(
        """
        SELECT
            date_trunc('day', s.created_at)::date   AS day,
            COUNT(s.id)                             AS interview_count,
            AVG(sc.composite_score)                 AS avg_composite
        FROM sessions s
        LEFT JOIN scorecards sc ON sc.session_id = s.id
        WHERE s.deleted_at IS NULL
          AND s.created_at >= :from_dt
          AND s.created_at <= :to_dt
        GROUP BY day
        ORDER BY day ASC
        """
    )
    rows = (await db.execute(sql, {"from_dt": from_dt, "to_dt": to_dt})).mappings().all()

    log.info("analytics.trends.fetched", actor=admin_sub, rows=len(rows))

    return TrendsResponse(
        items=[
            TrendItem(
                date=str(row["day"]),
                interview_count=int(row["interview_count"]),
                avg_composite=_round2(row["avg_composite"]),
            )
            for row in rows
        ],
        date_from=str(resolved_from),
        date_to=str(resolved_to),
    )
