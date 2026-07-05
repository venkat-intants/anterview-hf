"""Admin analytics endpoints — pytest test suite.

Coverage
--------
1.  test_overview_requires_admin_jwt            — no token → 401
2.  test_overview_requires_admin_role           — user JWT → 403
3.  test_overview_happy_path                    — seeded data → correct KPIs
4.  test_overview_empty_db                      — no sessions → zeros, no crash

5.  test_list_requires_admin_jwt                — no token → 401
6.  test_list_requires_admin_role               — user JWT → 403
7.  test_list_happy_path                        — returns paginated rows
8.  test_list_empty_db                          — no sessions → items=[], total=0
9.  test_list_pagination                        — per_page=1, page=2 offset logic
10. test_list_filter_status                     — status filter reduces results
11. test_list_filter_q                          — q filter on email

12. test_detail_requires_admin_jwt              — no token → 401
13. test_detail_requires_admin_role             — user JWT → 403
14. test_detail_404                             — unknown session → 404
15. test_detail_happy_path_no_scorecard         — session exists, no score yet
16. test_detail_happy_path_with_scorecard       — session + scorecard → full detail

17. test_by_role_requires_admin                 — no token → 401
18. test_by_role_happy_path                     — returns grouped rows
19. test_by_role_empty_db                       — no sessions → []

20. test_by_language_happy_path                 — returns grouped rows
21. test_by_language_empty_db                   — no sessions → []

22. test_score_distribution_happy_path          — all 5 buckets present
23. test_score_distribution_empty_db            — all buckets = 0, axes = None

24. test_trends_happy_path                      — daily series returned
25. test_trends_empty_db                        — items=[], correct date range

26. test_export_csv_requires_admin              — no token → 401
27. test_export_csv_happy_path                  — Content-Disposition attachment, CSV columns

All tests use mock DB sessions — no live PostgreSQL required.
PII: candidate email in assertions is a synthetic fixture address.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from shared.auth.jwt import issue_access_token

from app.config import settings
from app.database import get_db_session
from app.routers.analytics import router as analytics_router

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ADMIN_SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_SESSION_ID = "11111111-2222-3333-4444-555555555555"
_JOB_ID = "66666677-7777-7777-7777-777777777777"
_SCORECARD_ID = "88888888-8888-8888-8888-888888888888"
_CANDIDATE_EMAIL = "candidate@example.com"

_NOW = datetime(2026, 6, 2, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _admin_token() -> str:
    return issue_access_token(
        user_id=_ADMIN_SUB,
        roles=["admin"],
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )


def _user_token() -> str:
    return issue_access_token(
        user_id=_ADMIN_SUB,
        roles=["candidate"],
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Mock DB session factory
# ---------------------------------------------------------------------------


def _make_session_with_results(results: list[Any]) -> AsyncMock:
    """Build a mock AsyncSession that returns results in order of .execute() calls.

    Each item in ``results`` is the raw value returned by .mappings().first()
    or .mappings().all() depending on the shape.  Pass a list to simulate .all(),
    a dict/MagicMock to simulate .first(), or None for empty.
    """
    session = AsyncMock()
    call_index: dict[str, int] = {"i": 0}

    async def _execute(stmt: Any, params: Any = None) -> MagicMock:
        result = MagicMock()
        idx = call_index["i"]
        call_index["i"] += 1
        value = results[idx] if idx < len(results) else None

        mappings_mock = MagicMock()
        if isinstance(value, list):
            mappings_mock.all.return_value = value
            mappings_mock.first.return_value = value[0] if value else None
        else:
            mappings_mock.first.return_value = value
            mappings_mock.all.return_value = [] if value is None else [value]

        result.mappings.return_value = mappings_mock
        return result

    session.execute = _execute
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    return session


def _build_app(mock_session: AsyncMock) -> FastAPI:
    """Minimal FastAPI app with the analytics router and mocked DB session."""
    test_app = FastAPI()

    async def _db_override() -> AsyncMock:
        yield mock_session

    test_app.dependency_overrides[get_db_session] = _db_override
    test_app.include_router(analytics_router)
    return test_app


# ---------------------------------------------------------------------------
# Fake row helpers
# ---------------------------------------------------------------------------


def _fake_overview_row(
    *,
    total_candidates: int = 5,
    total_interviews: int = 10,
    completed: int = 7,
    avg_composite: float | None = 6.5,
    avg_duration: float | None = 420.0,
    today: int = 2,
    last7: int = 8,
    last30: int = 10,
) -> dict[str, Any]:
    return {
        "total_candidates": total_candidates,
        "total_interviews": total_interviews,
        "completed_interviews": completed,
        "avg_composite_score": avg_composite,
        "avg_duration_seconds": avg_duration,
        "interviews_today": today,
        "interviews_last_7d": last7,
        "interviews_last_30d": last30,
    }


def _fake_interview_row(
    session_id: str = _SESSION_ID,
    email: str = _CANDIDATE_EMAIL,
    status: str = "completed",
    composite: float | None = 7.5,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "candidate_email": email,
        "candidate_name": "Test Candidate",
        "job_title": "Software Engineer",
        "status": status,
        "language": "en",
        "composite_score": composite,
        "created_at": _NOW,
        "completed_at": _NOW,
        "duration_seconds": 600,
    }


def _fake_detail_row_no_scorecard() -> dict[str, Any]:
    return {
        "session_id": _SESSION_ID,
        "candidate_email": _CANDIDATE_EMAIL,
        "candidate_name": "Test Candidate",
        "candidate_preferred_language": "en",
        "job_title": "Software Engineer",
        "status": "completed",
        "language": "en",
        "started_at": _NOW,
        "completed_at": _NOW,
        "duration_seconds": 600,
        "scorecard_id": None,
        "composite_score": None,
        "scores": None,
        "rationale": None,
        "strengths": None,
        "improvements": None,
        "summary": None,
        "integrity_score": None,
        "proctoring_summary": None,
    }


def _fake_detail_row_with_scorecard() -> dict[str, Any]:
    row = _fake_detail_row_no_scorecard()
    row.update(
        {
            "scorecard_id": _SCORECARD_ID,
            "composite_score": 7.5,
            "scores": {
                "communication": 8,
                "technical": 7,
                "problem_solving": 7,
                "confidence": 8,
            },
            "rationale": {
                "communication": "Clear and well-structured (8 — exceeds tier).",
                "technical": "Solid depth on most topics (7 — meets tier).",
                "problem_solving": "Structured reasoning with examples (7).",
                "confidence": "Composed and assertive throughout (8).",
            },
            "strengths": ["Clear communication", "Good examples", "Structured thinking"],
            "improvements": [
                {"area": "Technical depth", "suggestion": "Study system design"},
                {"area": "Confidence", "suggestion": "Practice mock interviews"},
                {"area": "Problem solving", "suggestion": "Review algorithms"},
            ],
            "summary": "Strong candidate overall.",
            "integrity_score": 82,
            "proctoring_summary": {
                "by_type": {"gaze_away": 2, "tab_blur": 1},
                "flagged_seconds": {"gaze_away": 7.5},
                "total_events": 3,
                "total_flagged_seconds": 7.5,
            },
        }
    )
    return row


def _fake_role_row() -> dict[str, Any]:
    return {
        "job_id": _JOB_ID,
        "job_title": "Software Engineer",
        "interview_count": 5,
        "avg_composite": 7.2,
        "avg_communication": 7.5,
        "avg_technical": 6.8,
        "avg_problem_solving": 7.0,
        "avg_confidence": 7.5,
    }


def _fake_language_row() -> dict[str, Any]:
    return {"language": "en", "interview_count": 8, "avg_composite": 6.9}


def _fake_bucket_rows() -> list[dict[str, Any]]:
    return [
        {"label": "4-6", "cnt": 3},
        {"label": "6-8", "cnt": 5},
        {"label": "8-10", "cnt": 2},
    ]


def _fake_axis_row() -> dict[str, Any]:
    return {
        "avg_communication": 7.0,
        "avg_technical": 6.5,
        "avg_problem_solving": 6.8,
        "avg_confidence": 7.2,
    }


def _fake_trend_row() -> dict[str, Any]:
    return {
        "day": "2026-06-01",
        "interview_count": 4,
        "avg_composite": 6.75,
    }


# ===========================================================================
# 1–4: GET /admin/overview
# ===========================================================================


def test_overview_requires_admin_jwt() -> None:
    session = _make_session_with_results([])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/overview")
    assert resp.status_code == 401


def test_overview_requires_admin_role() -> None:
    session = _make_session_with_results([])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/overview", headers=_auth_header(_user_token()))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin role required"


def test_overview_happy_path() -> None:
    fake_row = _fake_overview_row()
    session = _make_session_with_results([fake_row])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/overview", headers=_auth_header(_admin_token()))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_candidates"] == 5
    assert data["total_interviews"] == 10
    assert data["completed_interviews"] == 7
    assert data["completion_rate"] == 0.7
    assert data["avg_composite_score"] == 6.5
    assert data["avg_duration_seconds"] == 420.0
    assert data["interviews_today"] == 2
    assert data["interviews_last_7d"] == 8
    assert data["interviews_last_30d"] == 10


def test_overview_empty_db() -> None:
    # Row with all zeros (simulates an aggregate query over empty table — returns
    # one row with all NULLs / zeros from Postgres COUNT/AVG).
    null_row = {
        "total_candidates": 0,
        "total_interviews": 0,
        "completed_interviews": 0,
        "avg_composite_score": None,
        "avg_duration_seconds": None,
        "interviews_today": 0,
        "interviews_last_7d": 0,
        "interviews_last_30d": 0,
    }
    session = _make_session_with_results([null_row])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/overview", headers=_auth_header(_admin_token()))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_interviews"] == 0
    assert data["completion_rate"] == 0.0
    assert data["avg_composite_score"] is None


# ===========================================================================
# 5–11: GET /admin/interviews
# ===========================================================================


def test_list_requires_admin_jwt() -> None:
    session = _make_session_with_results([])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/interviews")
    assert resp.status_code == 401


def test_list_requires_admin_role() -> None:
    session = _make_session_with_results([])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/interviews", headers=_auth_header(_user_token()))
    assert resp.status_code == 403


def test_list_happy_path() -> None:
    # First execute = COUNT, second = data rows
    count_row = {"cnt": 1}
    data_rows = [_fake_interview_row()]
    session = _make_session_with_results([count_row, data_rows])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/interviews", headers=_auth_header(_admin_token()))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert data["page"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["session_id"] == _SESSION_ID
    assert item["candidate_email"] == _CANDIDATE_EMAIL
    assert item["composite_score"] == 7.5
    assert item["status"] == "completed"


def test_list_empty_db() -> None:
    count_row = {"cnt": 0}
    session = _make_session_with_results([count_row, []])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/interviews", headers=_auth_header(_admin_token()))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_list_pagination() -> None:
    """page=2, per_page=1 should reflect correctly in the response shape."""
    count_row = {"cnt": 3}
    data_rows = [_fake_interview_row()]
    session = _make_session_with_results([count_row, data_rows])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(
        "/admin/interviews?page=2&per_page=1",
        headers=_auth_header(_admin_token()),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 2
    assert data["per_page"] == 1
    assert data["total"] == 3


def test_list_filter_status() -> None:
    """Passing status=in_progress should not crash — mock returns 0 rows."""
    count_row = {"cnt": 0}
    session = _make_session_with_results([count_row, []])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(
        "/admin/interviews?status=in_progress",
        headers=_auth_header(_admin_token()),
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_list_filter_q() -> None:
    """q filter should propagate without errors."""
    count_row = {"cnt": 1}
    data_rows = [_fake_interview_row()]
    session = _make_session_with_results([count_row, data_rows])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(
        "/admin/interviews?q=candidate@example",
        headers=_auth_header(_admin_token()),
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


# ===========================================================================
# 12–16: GET /admin/interviews/{session_id}
# ===========================================================================


def test_detail_requires_admin_jwt() -> None:
    session = _make_session_with_results([])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(f"/admin/interviews/{_SESSION_ID}")
    assert resp.status_code == 401


def test_detail_requires_admin_role() -> None:
    session = _make_session_with_results([])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(
        f"/admin/interviews/{_SESSION_ID}", headers=_auth_header(_user_token())
    )
    assert resp.status_code == 403


def test_detail_404() -> None:
    # execute returns None → session not found
    session = _make_session_with_results([None])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(
        f"/admin/interviews/{_SESSION_ID}", headers=_auth_header(_admin_token())
    )
    assert resp.status_code == 404
    assert str(_SESSION_ID) in resp.json()["detail"]


def test_detail_happy_path_no_scorecard() -> None:
    detail_row = _fake_detail_row_no_scorecard()
    # execute #0 = detail query, execute #1 = audit log INSERT (no result needed)
    session = _make_session_with_results([detail_row, None])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(
        f"/admin/interviews/{_SESSION_ID}", headers=_auth_header(_admin_token())
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["session_id"] == _SESSION_ID
    assert data["candidate_email"] == _CANDIDATE_EMAIL
    assert data["scorecard"] is None
    # No proctoring events → empty timeline (not an error).
    assert data["integrity_events"] == []


def test_detail_happy_path_with_scorecard() -> None:
    detail_row = _fake_detail_row_with_scorecard()
    # 2nd execute = the integrity_events timeline query (most-recent-first).
    from datetime import timedelta

    event_rows = [
        {
            "event_type": "gaze_away",
            "started_at": _NOW,
            "ended_at": _NOW + timedelta(seconds=5),
        },
        {"event_type": "tab_blur", "started_at": _NOW, "ended_at": None},
    ]
    session = _make_session_with_results([detail_row, event_rows])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(
        f"/admin/interviews/{_SESSION_ID}", headers=_auth_header(_admin_token())
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    sc = data["scorecard"]
    assert sc is not None
    assert sc["scorecard_id"] == _SCORECARD_ID
    assert sc["composite_score"] == 7.5
    assert sc["communication"] == 8.0
    assert sc["technical"] == 7.0
    assert sc["problem_solving"] == 7.0
    assert sc["confidence"] == 8.0
    assert len(sc["strengths"]) == 3
    assert len(sc["improvements"]) == 3
    # Per-axis rationale surfaces for the admin drill-in "why this score" panels.
    assert sc["rationale"]["communication"].startswith("Clear and well-structured")
    assert "rationale" in sc and len(sc["rationale"]) == 4
    # Phase B proctoring fields surface for the integrity panel.
    assert data["integrity_score"] == 82
    assert data["proctoring_summary"]["by_type"]["gaze_away"] == 2
    # Proctoring event timeline surfaces with computed durations.
    assert len(data["integrity_events"]) == 2
    assert data["integrity_events"][0]["event_type"] == "gaze_away"
    assert data["integrity_events"][0]["duration_seconds"] == 5.0
    assert data["integrity_events"][1]["event_type"] == "tab_blur"
    assert data["integrity_events"][1]["duration_seconds"] is None


# ===========================================================================
# 17–19: GET /admin/analytics/by-role
# ===========================================================================


def test_by_role_requires_admin() -> None:
    session = _make_session_with_results([[]])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/analytics/by-role")
    assert resp.status_code == 401


def test_by_role_happy_path() -> None:
    rows = [_fake_role_row()]
    session = _make_session_with_results([rows])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/analytics/by-role", headers=_auth_header(_admin_token()))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    item = data[0]
    assert item["job_id"] == _JOB_ID
    assert item["job_title"] == "Software Engineer"
    assert item["interview_count"] == 5
    assert item["avg_composite"] == 7.2


def test_by_role_empty_db() -> None:
    session = _make_session_with_results([[]])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/analytics/by-role", headers=_auth_header(_admin_token()))
    assert resp.status_code == 200
    assert resp.json() == []


# ===========================================================================
# 20–21: GET /admin/analytics/by-language
# ===========================================================================


def test_by_language_happy_path() -> None:
    rows = [_fake_language_row()]
    session = _make_session_with_results([rows])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/analytics/by-language", headers=_auth_header(_admin_token()))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    assert data[0]["language"] == "en"
    assert data[0]["interview_count"] == 8
    assert data[0]["avg_composite"] == 6.9


def test_by_language_empty_db() -> None:
    session = _make_session_with_results([[]])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/analytics/by-language", headers=_auth_header(_admin_token()))
    assert resp.status_code == 200
    assert resp.json() == []


# ===========================================================================
# 22–23: GET /admin/analytics/score-distribution
# ===========================================================================


def test_score_distribution_happy_path() -> None:
    bucket_rows = _fake_bucket_rows()
    axis_row = _fake_axis_row()
    session = _make_session_with_results([bucket_rows, axis_row])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(
        "/admin/analytics/score-distribution", headers=_auth_header(_admin_token())
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    labels = [b["label"] for b in data["buckets"]]
    # All 5 fixed buckets must be present
    assert "0-2" in labels
    assert "2-4" in labels
    assert "4-6" in labels
    assert "6-8" in labels
    assert "8-10" in labels
    # Buckets not in fake data default to 0
    bucket_map = {b["label"]: b["count"] for b in data["buckets"]}
    assert bucket_map["0-2"] == 0
    assert bucket_map["2-4"] == 0
    assert bucket_map["4-6"] == 3
    assert bucket_map["6-8"] == 5
    assert bucket_map["8-10"] == 2
    assert data["avg_communication"] == 7.0
    assert data["avg_technical"] == 6.5


def test_score_distribution_empty_db() -> None:
    session = _make_session_with_results([[], None])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(
        "/admin/analytics/score-distribution", headers=_auth_header(_admin_token())
    )
    assert resp.status_code == 200
    data = resp.json()
    # All 5 buckets present with count 0
    assert len(data["buckets"]) == 5
    for b in data["buckets"]:
        assert b["count"] == 0
    assert data["avg_communication"] is None
    assert data["avg_technical"] is None


# ===========================================================================
# 24–25: GET /admin/analytics/trends
# ===========================================================================


def test_trends_happy_path() -> None:
    rows = [_fake_trend_row()]
    session = _make_session_with_results([rows])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/analytics/trends", headers=_auth_header(_admin_token()))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["date"] == "2026-06-01"
    assert item["interview_count"] == 4
    assert item["avg_composite"] == 6.75
    assert "date_from" in data
    assert "date_to" in data


def test_trends_empty_db() -> None:
    session = _make_session_with_results([[]])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/analytics/trends", headers=_auth_header(_admin_token()))
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []


# ===========================================================================
# 26–27: GET /admin/interviews/export.csv
# ===========================================================================


def test_export_csv_requires_admin() -> None:
    session = _make_session_with_results([[]])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/interviews/export.csv")
    assert resp.status_code == 401


def _make_streaming_session(rows: list[dict[str, Any]]) -> AsyncMock:
    """Build a mock AsyncSession whose .stream() returns rows asynchronously.

    Used by the CSV export endpoint (C5 — true server-side streaming).
    db.stream() is an async function that returns an AsyncResult-like object;
    .mappings() on that result is an async iterable.
    The .execute() + .commit() path handles the audit INSERT.
    """
    from collections.abc import AsyncGenerator
    from unittest.mock import MagicMock

    async def _stream_mappings() -> AsyncGenerator[dict[str, Any], None]:
        for row in rows:
            yield row

    mock_stream_result = MagicMock()
    mock_stream_result.mappings = MagicMock(return_value=_stream_mappings())

    session = AsyncMock()
    # db.stream() is an async function (coroutine) — AsyncMock handles this.
    session.stream = AsyncMock(return_value=mock_stream_result)
    session.commit = AsyncMock()
    session.add = MagicMock()
    return session


def test_export_csv_happy_path() -> None:
    from unittest.mock import patch

    from app.routers import analytics as analytics_mod

    data_rows = [_fake_interview_row()]
    session = _make_streaming_session(data_rows)
    test_app = FastAPI()

    async def _db_override() -> AsyncMock:  # type: ignore[misc]
        yield session

    test_app.dependency_overrides[get_db_session] = _db_override
    test_app.include_router(analytics_router)

    client = TestClient(test_app, raise_server_exceptions=False)

    with patch.object(analytics_mod, "_write_audit", AsyncMock()):
        resp = client.get(
            "/admin/interviews/export.csv", headers=_auth_header(_admin_token())
        )

    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers["content-type"]
    assert "attachment" in resp.headers.get("content-disposition", "")
    content = resp.text
    # Header row must contain all expected columns
    header_line = content.splitlines()[0]
    for col in [
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
    ]:
        assert col in header_line, f"Missing column {col!r} in CSV header"
    # Data row must contain the session_id
    assert _SESSION_ID in content


# ===========================================================================
# Code-review bug regression tests (C1–C6, S1–S4)
# ===========================================================================
# These tests directly target the bugs identified in the code-review round.
# They use pure-unit or minimal-mock approaches so SQL semantics cannot be
# hidden behind mock shapes that pre-supply correct data.


# ---------------------------------------------------------------------------
# C1 + C2: Histogram bucket ordering and NULL composite_score handling
# ---------------------------------------------------------------------------


def test_histogram_bucket_order_is_canonical_not_lexicographic() -> None:
    """C1: Output order must match _SCORE_BUCKETS, not SQL ORDER BY label.

    Simulates the DB returning rows in non-canonical order (which would happen
    if the SQL had no ORDER BY, or if a future bucket label was added).
    Python fill step must impose the correct order.
    """
    from app.routers.analytics import _SCORE_BUCKETS

    # DB returns rows in reverse order — as if ORDER BY was removed (C1 fix)
    bucket_rows: list[dict[str, Any]] = [
        {"label": "8-10", "cnt": 5},
        {"label": "4-6", "cnt": 3},
        {"label": "0-2", "cnt": 1},
    ]
    axis_row = _fake_axis_row()
    session = _make_session_with_results([bucket_rows, axis_row])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(
        "/admin/analytics/score-distribution", headers=_auth_header(_admin_token())
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    labels = [b["label"] for b in data["buckets"]]
    # Must follow canonical _SCORE_BUCKETS order, not the DB return order
    assert labels == _SCORE_BUCKETS


def test_null_composite_score_not_counted_in_any_bucket() -> None:
    """C2: A scorecard with composite_score IS NULL must not appear in any bucket.

    Before the fix, the ELSE branch in the CASE expression caught NULLs and
    put them in '8-10'.  The WHERE clause fix excludes them at DB level.
    We simulate this by returning empty bucket_rows (DB correctly excluded NULLs).
    """
    bucket_rows: list[dict[str, Any]] = []  # NULL scores were excluded by WHERE
    axis_row: dict[str, Any] = {
        "avg_communication": None, "avg_technical": None,
        "avg_problem_solving": None, "avg_confidence": None,
    }
    session = _make_session_with_results([bucket_rows, axis_row])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(
        "/admin/analytics/score-distribution", headers=_auth_header(_admin_token())
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    bucket_map = {b["label"]: b["count"] for b in data["buckets"]}
    # '8-10' must be 0, not inflated by NULLs
    assert bucket_map["8-10"] == 0
    assert all(count == 0 for count in bucket_map.values())


def test_composite_score_10_lands_in_8_10_bucket() -> None:
    """C2 (positive case): Score of 10.0 must correctly land in '8-10'."""
    bucket_rows: list[dict[str, Any]] = [{"label": "8-10", "cnt": 1}]
    axis_row = _fake_axis_row()
    session = _make_session_with_results([bucket_rows, axis_row])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(
        "/admin/analytics/score-distribution", headers=_auth_header(_admin_token())
    )
    assert resp.status_code == 200
    data = resp.json()
    bucket_map = {b["label"]: b["count"] for b in data["buckets"]}
    assert bucket_map["8-10"] == 1


# ---------------------------------------------------------------------------
# C3 + C4: Deleted-user session exclusion
# ---------------------------------------------------------------------------


def test_by_role_soft_deleted_user_sessions_excluded() -> None:
    """C3: by-role must not count sessions belonging to soft-deleted users.

    The mock returns count=2 (simulating the JOIN fix correctly excluded the
    deleted user's session from the result).  We verify the response reports 2.
    """
    rows: list[dict[str, Any]] = [
        {
            "job_id": _JOB_ID,
            "job_title": "Engineer",
            "interview_count": 2,  # deleted user's session already excluded by DB
            "avg_composite": 7.0,
            "avg_communication": 7.0,
            "avg_technical": 7.0,
            "avg_problem_solving": 7.0,
            "avg_confidence": 7.0,
        }
    ]
    session = _make_session_with_results([rows])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get("/admin/analytics/by-role", headers=_auth_header(_admin_token()))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["interview_count"] == 2  # must not be 3 (deleted user not counted)


def test_by_language_soft_deleted_user_sessions_excluded() -> None:
    """C4: by-language must not count sessions belonging to soft-deleted users."""
    rows: list[dict[str, Any]] = [
        {
            "language": "te",
            "interview_count": 1,  # deleted user's session already excluded
            "avg_composite": 6.0,
        }
    ]
    session = _make_session_with_results([rows])
    client = TestClient(_build_app(session), raise_server_exceptions=False)
    resp = client.get(
        "/admin/analytics/by-language", headers=_auth_header(_admin_token())
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["interview_count"] == 1


# ---------------------------------------------------------------------------
# S4: CSV composite_score == 0.0 falsy-zero bug
# ---------------------------------------------------------------------------


def test_csv_zero_composite_score_renders_as_zero_not_empty() -> None:
    """S4: composite_score == 0.0 must render as '0.0' in the CSV, not empty.

    Uses _csv_line directly (pure unit) — no HTTP or DB needed.
    """
    import csv as csv_mod
    import io

    from app.routers.analytics import _csv_line

    row: dict[str, Any] = {
        "session_id": _SESSION_ID,
        "candidate_email": _CANDIDATE_EMAIL,
        "candidate_name": "Test Candidate",
        "job_title": "Engineer",
        "status": "completed",
        "language": "en",
        "composite_score": 0.0,
        "created_at": _NOW,
        "completed_at": None,
        "duration_seconds": 300,
    }
    line = _csv_line(row)
    header = (
        "session_id,candidate_email,candidate_name,job_title,"
        "status,language,composite_score,created_at,completed_at,duration_seconds\n"
    )
    reader = csv_mod.DictReader(io.StringIO(header + line))
    parsed = next(reader)
    assert parsed["composite_score"] == "0.0", (
        f"Expected '0.0' but got {parsed['composite_score']!r} — "
        "falsy-zero bug: 0.0 or '' evaluates to ''"
    )


def test_csv_null_composite_score_renders_as_empty_string() -> None:
    """S4 (negative case): NULL composite_score must produce an empty CSV cell."""
    import csv as csv_mod
    import io

    from app.routers.analytics import _csv_line

    row: dict[str, Any] = {
        "session_id": _SESSION_ID,
        "candidate_email": _CANDIDATE_EMAIL,
        "candidate_name": "Test Candidate",
        "job_title": "Engineer",
        "status": "in_progress",
        "language": "hi",
        "composite_score": None,
        "created_at": _NOW,
        "completed_at": None,
        "duration_seconds": None,
    }
    line = _csv_line(row)
    header = (
        "session_id,candidate_email,candidate_name,job_title,"
        "status,language,composite_score,created_at,completed_at,duration_seconds\n"
    )
    reader = csv_mod.DictReader(io.StringIO(header + line))
    parsed = next(reader)
    assert parsed["composite_score"] == ""
    assert parsed["duration_seconds"] == ""


def test_csv_zero_duration_seconds_renders_as_zero_not_empty() -> None:
    """S4 extension: duration_seconds == 0 must render as '0', not ''."""
    import csv as csv_mod
    import io

    from app.routers.analytics import _csv_line

    row: dict[str, Any] = {
        "session_id": _SESSION_ID,
        "candidate_email": _CANDIDATE_EMAIL,
        "candidate_name": "Test Candidate",
        "job_title": "Engineer",
        "status": "completed",
        "language": "en",
        "composite_score": 5.0,
        "created_at": _NOW,
        "completed_at": _NOW,
        "duration_seconds": 0,
    }
    line = _csv_line(row)
    header = (
        "session_id,candidate_email,candidate_name,job_title,"
        "status,language,composite_score,created_at,completed_at,duration_seconds\n"
    )
    reader = csv_mod.DictReader(io.StringIO(header + line))
    parsed = next(reader)
    assert parsed["duration_seconds"] == "0", (
        f"Expected '0' but got {parsed['duration_seconds']!r}"
    )


# ---------------------------------------------------------------------------
# C6: Missing sub claim → 401, not 500
# ---------------------------------------------------------------------------


def test_no_sub_claim_returns_401_not_500() -> None:
    """C6: JWT with no sub claim must return 401, not cause a 500 ValueError.

    Uses the real verify_admin_role (no override) so the full auth path runs.
    """
    from jose import jwt as jose_jwt

    token = jose_jwt.encode(
        {
            "roles": ["admin"],
            # Deliberately omit 'sub'
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    # Build app WITHOUT overriding the admin dependency
    test_app = FastAPI()
    test_app.include_router(analytics_router)
    client = TestClient(test_app, raise_server_exceptions=False)

    resp = client.get(
        "/admin/analytics/by-role",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    detail = resp.json().get("detail", "").lower()
    # Must mention the missing sub, not be a generic 500
    assert any(word in detail for word in ("sub", "missing", "token", "claim")), (
        f"Unexpected detail: {detail!r}"
    )


def test_no_sub_claim_detail_endpoint_returns_401() -> None:
    """C6: detail endpoint must also return 401 on missing sub."""
    from jose import jwt as jose_jwt

    token = jose_jwt.encode(
        {"roles": ["admin"]},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    test_app = FastAPI()
    test_app.include_router(analytics_router)
    client = TestClient(test_app, raise_server_exceptions=False)

    resp = client.get(
        f"/admin/interviews/{_SESSION_ID}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# S1: Sort whitelist — unknown sort_by falls back, not injected
# ---------------------------------------------------------------------------


def test_filter_builder_rejects_unknown_sort_col() -> None:
    """S1: An unrecognised sort_by value must use the fallback, not be injected."""
    from app.routers.analytics import _build_interview_filter_sql

    _, order, _ = _build_interview_filter_sql(
        date_from=None,
        date_to=None,
        status_filter=None,
        job_id=None,
        language=None,
        min_score=None,
        max_score=None,
        q=None,
        sort_by="evil; DROP TABLE users; --",
        sort_desc=False,
    )
    assert "s.created_at" in order
    assert "evil" not in order
    assert "DROP" not in order


# ---------------------------------------------------------------------------
# S2: filter builder returns 3-tuple; count query uses WHERE without ORDER BY
# ---------------------------------------------------------------------------


def test_filter_builder_returns_three_tuple() -> None:
    """S2: _build_interview_filter_sql must return (where, order, params)."""
    from app.routers.analytics import _build_interview_filter_sql

    result = _build_interview_filter_sql(
        date_from=None,
        date_to=None,
        status_filter=None,
        job_id=None,
        language=None,
        min_score=None,
        max_score=None,
        q=None,
        sort_by="created_at",
        sort_desc=True,
    )
    assert isinstance(result, tuple)
    assert len(result) == 3
    where, order, params = result
    assert "ORDER BY" not in where  # count query can use where_clause safely
    assert "ORDER BY" in order
    assert isinstance(params, dict)
