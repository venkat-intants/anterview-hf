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
GET /admin/interviews/{session_id}/transcript — conversation turns (audit-logged)
GET /admin/analytics/by-role           — grouped by job title
GET /admin/analytics/by-language       — grouped by language
GET /admin/analytics/score-distribution — histogram + per-axis averages
GET /admin/analytics/trends            — daily series (date_trunc)

Timezone note
-------------
Calendar buckets ("interviews today", the daily trend series) break at midnight
in the reporting timezone, not UTC — see ``_reporting_tz``.  Raw timestamps in
responses stay UTC/ISO-8601.

PII note
--------
- Candidate email and full_name are returned in paginated lists and CSV
  exports.  These are admin-only endpoints (JWT role check enforced at the
  prefix level in main.py AND individually on each endpoint via AdminDep).
- The drill-in endpoint (GET /admin/interviews/{session_id}) writes an
  audit_log entry for every access: action "admin.interview.view",
  resource_type "session", resource_id = session_id.
- The transcript endpoint (GET /admin/interviews/{session_id}/transcript)
  writes an audit_log entry for every access: action
  "admin.interview.transcript.view", resource_type "session",
  resource_id = session_id.
- The CSV export endpoint writes an audit_log entry: action
  "admin.interviews.export".
- Candidate PII is NEVER written to structlog.

Package layout (split 2026-08-07 — a pure move, no logic changed)
-----------------------------------------------------------------
This was a single 1,297-line module, four times the next-largest router in the
service, and it was read almost exclusively one endpoint at a time. It is now
split along the section comments that were already in it:

``_common.py``     response models, helpers, constants, DI aliases
``overview.py``    GET /admin/overview
``interviews.py``  the four /admin/interviews* endpoints + their shared SQL
``aggregates.py``  the four /admin/analytics/* grouped queries

This module re-exports the combined router and every name the old module
exposed, so ``from app.routers.analytics import router`` — and the tests that
reach for ``_csv_line``, ``_build_interview_filter_sql`` or ``_SCORE_BUCKETS``
— keep working unchanged. That is the point: the split is only worth doing if
it costs no caller anything.

Sub-router include order matters exactly once: nothing here may register a
route under ``/admin/interviews/`` before ``interviews.router``, whose own
internal ordering puts ``export.csv`` ahead of ``{session_id}``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routers.analytics import aggregates, interviews, overview
from app.routers.analytics._common import (
    _AXES,
    _CSV_COLUMNS,
    _DEFAULT_REPORTING_TZ,
    _SCORE_BUCKETS,
    ByLanguageItem,
    ByRoleItem,
    DbSessionDep,
    IntegrityEventItem,
    InterviewDetailResponse,
    InterviewListItem,
    InterviewListResponse,
    OverviewResponse,
    ScoreBucket,
    ScorecardDetail,
    ScoreDistributionResponse,
    SessionFactoryDep,
    TrendItem,
    TrendsResponse,
    _iso,
    _local_day_start,
    _reporting_tz,
    _round1,
    _round2,
    log,
)
from app.routers.analytics.aggregates import (
    _DEFAULT_TREND_DAYS,
    analytics_by_language,
    analytics_by_role,
    analytics_score_distribution,
    analytics_trends,
)
from app.routers.analytics.interviews import (
    _CSV_FORMULA_PREFIXES,
    _INTERVIEW_SELECT,
    _SORT_WHITELIST,
    TranscriptResponse,
    TranscriptTurn,
    _build_interview_filter_sql,
    _csv_line,
    _csv_safe,
    _write_audit,
    export_interviews_csv,
    get_interview_detail,
    get_interview_transcript,
    list_interviews,
)
from app.routers.analytics.overview import get_overview

router = APIRouter()
router.include_router(overview.router)
router.include_router(interviews.router)
router.include_router(aggregates.router)

# Re-exported deliberately, private names included: these were importable from
# ``app.routers.analytics`` before the split and several tests import them by
# that path. __all__ is what tells ruff they are re-exports and not dead
# imports.
__all__ = [
    "_AXES",
    "_CSV_COLUMNS",
    "_CSV_FORMULA_PREFIXES",
    "_DEFAULT_REPORTING_TZ",
    "_DEFAULT_TREND_DAYS",
    "_INTERVIEW_SELECT",
    "_SCORE_BUCKETS",
    "_SORT_WHITELIST",
    "ByLanguageItem",
    "ByRoleItem",
    "DbSessionDep",
    "IntegrityEventItem",
    "InterviewDetailResponse",
    "InterviewListItem",
    "InterviewListResponse",
    "OverviewResponse",
    "ScoreBucket",
    "ScoreDistributionResponse",
    "ScorecardDetail",
    "SessionFactoryDep",
    "TranscriptResponse",
    "TranscriptTurn",
    "TrendItem",
    "TrendsResponse",
    "_build_interview_filter_sql",
    "_csv_line",
    "_csv_safe",
    "_iso",
    "_local_day_start",
    "_reporting_tz",
    "_round1",
    "_round2",
    "_write_audit",
    "aggregates",
    "analytics_by_language",
    "analytics_by_role",
    "analytics_score_distribution",
    "analytics_trends",
    "export_interviews_csv",
    "get_interview_detail",
    "get_interview_transcript",
    "get_overview",
    "interviews",
    "list_interviews",
    "log",
    "overview",
    "router",
]
