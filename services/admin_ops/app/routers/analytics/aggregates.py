"""Grouped analytics: by-role, by-language, score distribution, trends.

Four endpoints, four GROUP BY queries, no shared SQL — they are together
because they are the same shape of thing (one aggregate query in, one chart
out) and a reader looking for "where do the dashboard charts come from" should
find all of them at once.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Query, status
from sqlalchemy import text as sa_text

from app.admin_auth import AdminDep
from app.routers.analytics._common import (
    _SCORE_BUCKETS,
    ByLanguageItem,
    ByRoleItem,
    DbSessionDep,
    ScoreBucket,
    ScoreDistributionResponse,
    TrendItem,
    TrendsResponse,
    _local_day_start,
    _reporting_tz,
    _round2,
    log,
)

router = APIRouter(prefix="/admin", tags=["analytics"])


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
        "avg_composite. Days are calendar days in the reporting timezone "
        "(Asia/Kolkata by default). Defaults to the last 30 days. "
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
    tz_name, tz = _reporting_tz()
    now_local = datetime.now(tz)
    resolved_to = date_to or now_local.date()
    resolved_from = date_from or (now_local - timedelta(days=_DEFAULT_TREND_DAYS)).date()

    # Local midnight bounds. The upper bound is the start of the day AFTER
    # resolved_to and compared exclusively — an inclusive 23:59:59 bound drops
    # every session created in the final second of the range.
    from_dt = _local_day_start(resolved_from, tz)
    to_dt_exclusive = _local_day_start(resolved_to + timedelta(days=1), tz)

    # AT TIME ZONE shifts the timestamptz into reporting-local time before the
    # truncation, so each bar is one Indian working day rather than a UTC day
    # split across two of them.
    sql = sa_text(
        """
        SELECT
            date_trunc('day', s.created_at AT TIME ZONE :report_tz)::date AS day,
            COUNT(s.id)                             AS interview_count,
            AVG(sc.composite_score)                 AS avg_composite
        FROM sessions s
        LEFT JOIN scorecards sc ON sc.session_id = s.id
        WHERE s.deleted_at IS NULL
          AND s.created_at >= :from_dt
          AND s.created_at < :to_dt_exclusive
        GROUP BY day
        ORDER BY day ASC
        """
    )
    rows = (
        await db.execute(
            sql,
            {
                "report_tz": tz_name,
                "from_dt": from_dt,
                "to_dt_exclusive": to_dt_exclusive,
            },
        )
    ).mappings().all()

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
