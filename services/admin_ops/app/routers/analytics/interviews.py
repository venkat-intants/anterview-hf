"""Interview-level endpoints: list, CSV export, drill-in detail, transcript.

All four read the same sessions⋈users⋈jobs⋈scorecards join, and the list and
the export MUST share one filter builder — a divergence there would mean the
CSV silently exports a different row set than the screen it was launched from.
That shared SQL is why these four live together rather than one module each.

Route ordering inside this module is load-bearing: ``/interviews/export.csv``
is registered before ``/interviews/{session_id}`` so FastAPI does not try to
parse "export.csv" as a UUID.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text as sa_text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_auth import AdminDep
from app.models import AuditLog
from app.routers.analytics._common import (
    _AXES,
    _CSV_COLUMNS,
    DbSessionDep,
    IntegrityEventItem,
    InterviewDetailResponse,
    InterviewListItem,
    InterviewListResponse,
    ScorecardDetail,
    SessionFactoryDep,
    _iso,
    _round2,
    log,
)

router = APIRouter(prefix="/admin", tags=["analytics"])


# _write_audit lives here rather than in _common because all three of its call
# sites — the export, the drill-in and the transcript — are in this module.
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


# Characters that make a spreadsheet treat a cell as a formula rather than as
# text. Tab and CR are included because Excel strips them and then re-reads the
# first surviving character.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Neutralise a spreadsheet formula prefix on a CSV cell.

    csv.DictWriter quotes correctly for CSV *parsing*, but quoting does not stop
    Excel or LibreOffice evaluating a cell that begins with '='. That matters
    here because ``candidate_name`` is ``users.full_name``, which a candidate
    sets themselves at open self-registration with no character restriction
    (data_gateway auth.py: ``full_name: str = Field(min_length=1)``), and this
    export is opened on the workstation of the highest-privilege operator on the
    platform — while the same file carries every tenant's candidate PII, which
    is what a HYPERLINK/WEBSERVICE payload would exfiltrate.

    Prefixing with an apostrophe is the standard neutralisation: spreadsheets
    treat the cell as text and do not display the quote.
    """
    if value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def _csv_line(row: Any) -> str:
    """Format one DB row mapping as a single CSV data line string.

    Every free-text cell goes through ``_csv_safe`` — see CWE-1236.

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
            "candidate_email": _csv_safe(str(row["candidate_email"])),
            "candidate_name": _csv_safe(str(row["candidate_name"] or "")),
            "job_title": _csv_safe(str(row["job_title"] or "")),
            "status": _csv_safe(str(row["status"])),
            "language": _csv_safe(str(row["language"])),
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
    session_factory: SessionFactoryDep,
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
    The stream opens its own session (see ``_generate``); the request-scoped
    ``db`` is used only for the audit row, which is written BEFORE the stream
    begins so a client disconnect cannot cause it to be skipped.
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

        # The request-scoped `db` is unusable here: FastAPI (>=0.106) unwinds
        # the dependency exit stack before Starlette sends the response body, so
        # `db` is already closed by the time this generator runs — streaming
        # from it silently leaks a connection per export.  The stream therefore
        # owns a session for exactly its own lifetime.
        #
        # Server-side streaming: .stream() is an async function returning an
        # AsyncResult; iterating its .mappings() yields rows one at a time
        # without loading the full result set into memory.
        async with session_factory() as stream_session:
            stream_result = await stream_session.stream(data_sql, params)
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
            # Only the four canonical axes hold prose.  The scorer also parks
            # nested structures in this same JSONB column (axis_feedback,
            # _competencies, _role_profile_id, …); str()-ing those would ship
            # Python dict reprs — kilobytes of unparseable, transcript-derived
            # text — to a client that only ever indexes the axes.
            rationale={
                axis: str(rationale_raw[axis])
                for axis in _AXES
                if rationale_raw.get(axis) is not None
            },
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
    summary="Interview transcript (ordered conversation turns, audit-logged)",
    description=(
        "Every access is written to audit_log (action "
        "'admin.interview.transcript.view'). Returns every word the "
        "candidate spoke in the session."
    ),
)
async def get_interview_transcript(
    session_id: uuid.UUID,
    admin_sub: AdminDep,
    db: DbSessionDep,
) -> TranscriptResponse:
    """Ordered conversation turns for a session (admin drill-in). Audit-logs PII access."""
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

    # This is the most sensitive artefact in the system — every word the
    # candidate spoke — so it gets the same audit trail as the drill-in detail
    # and CSV export endpoints, not just a structlog line.
    await _write_audit(
        db=db,
        actor_id=admin_sub,
        action="admin.interview.transcript.view",
        resource_type="session",
        resource_id=session_id,
        details=None,
    )

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
