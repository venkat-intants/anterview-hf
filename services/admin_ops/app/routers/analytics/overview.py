"""GET /admin/overview — the KPI tiles.

One endpoint, one query. It sits alone because it is the only endpoint that
answers the whole dashboard header in a single round-trip, and because keeping
it out of ``interviews`` keeps that module about one table.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, status
from sqlalchemy import text as sa_text

from app.admin_auth import AdminDep
from app.routers.analytics._common import (
    DbSessionDep,
    OverviewResponse,
    _local_day_start,
    _reporting_tz,
    _round1,
    _round2,
    log,
)

router = APIRouter(prefix="/admin", tags=["analytics"])


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
        "'Today' starts at midnight in the reporting timezone (Asia/Kolkata by "
        "default). Soft-deleted users and sessions are excluded."
    ),
)
async def get_overview(
    admin_sub: AdminDep,
    db: DbSessionDep,
) -> OverviewResponse:
    """Single aggregate query returning all KPI tiles."""
    _, tz = _reporting_tz()
    now_local = datetime.now(tz)
    today_start = _local_day_start(now_local.date(), tz)
    # Rolling windows, not calendar buckets — a relative offset is the same
    # instant in every timezone, so these need no conversion.
    last_7d = now_local - timedelta(days=7)
    last_30d = now_local - timedelta(days=30)

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
