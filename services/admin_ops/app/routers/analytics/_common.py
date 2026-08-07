"""Shared analytics building blocks — models, helpers, constants, DI aliases.

Everything in here is imported by two or more of the endpoint modules
(``overview``, ``interviews``, ``aggregates``), which is the only reason a
thing lives here rather than next to the endpoint that uses it. Nothing in this
module is an endpoint and nothing in it holds a router, so importing it can
never register a route.

See ``__init__.py`` for why the package is split at all.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time, tzinfo
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import get_db_session, get_session_factory

# One logger for the whole package, named for the package rather than for each
# module, so every analytics log line keeps the logger name it had before the
# split. Splitting a file should not move anybody's log filter.
log = structlog.get_logger("app.routers.analytics")

DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
# Injected as a plain (non-yield) dependency so it is NOT torn down with the
# request exit stack — the CSV export needs it alive while the body streams.
SessionFactoryDep = Annotated[
    async_sessionmaker[AsyncSession], Depends(get_session_factory)
]

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

# Timezone whose midnight starts a reporting "day".  Every operator reads these
# tiles in IST (CLAUDE.md pins India residency and Indian buyers), and UTC
# midnight is 05:30 IST — bucketing on UTC would put a day and a half of
# sessions in "today" and file an 04:00 IST interview under "yesterday".
# Overridable per deployment via the REPORTING_TIMEZONE env var.
_DEFAULT_REPORTING_TZ = "Asia/Kolkata"

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


def _reporting_tz() -> tuple[str, tzinfo]:
    """Return the (IANA name, tzinfo) pair used for all calendar bucketing.

    Both halves come from here so the Python-side day boundaries and the
    Postgres-side ``AT TIME ZONE`` grouping can never drift apart.  Resolved per
    call — ZoneInfo caches instances, so this is cheap, and a deployment can
    change REPORTING_TIMEZONE without a code change.

    Falls back to UTC when the host image ships no tz database: a tile that is
    wrong by 5.5 hours beats a 500 on the whole admin dashboard.
    """
    name = os.getenv("REPORTING_TIMEZONE", _DEFAULT_REPORTING_TZ).strip() or _DEFAULT_REPORTING_TZ
    try:
        return name, ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("analytics.reporting_tz_unavailable", requested=name)
        return "UTC", UTC


def _local_day_start(day: date, tz: tzinfo) -> datetime:
    """Midnight at the start of ``day`` in ``tz``, as an aware datetime.

    Built from the calendar date rather than by arithmetic on a timestamp so a
    DST-observing reporting timezone still lands on the real local midnight.
    """
    return datetime.combine(day, time.min, tzinfo=tz)


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
