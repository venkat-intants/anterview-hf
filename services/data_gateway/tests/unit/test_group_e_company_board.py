"""E3 — the company super admin's hiring board.

The health rule is pure and tested here; the query runs against Postgres in
``smoke_group_e_company_board``.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import Any

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _h(**over: Any) -> dict[str, Any]:
    from app.company_board import HealthInput, hiring_health

    base: dict[str, Any] = {
        "has_published_workflow": True, "accepting_applications": True, "target_hires": 4,
        "hired": 0, "rejected": 0, "in_play": 10, "closes_at": NOW + timedelta(days=60),
        "created_at": NOW - timedelta(days=30), "reached_decision": 12,
        "last_movement_at": NOW - timedelta(days=1), "now": NOW,
    }
    return hiring_health(HealthInput(**{**base, **over}))


def test_no_published_workflow_is_its_own_state() -> None:
    taking = _h(has_published_workflow=False, accepting_applications=True)
    quiet = _h(has_published_workflow=False, accepting_applications=False)
    assert taking["band"] == quiet["band"] == "not_published"
    assert "taking applications" in taking["reason"]
    assert "taking applications" not in quiet["reason"]


def test_a_met_target_is_on_track_whatever_the_dates() -> None:
    assert _h(target_hires=2, hired=2, closes_at=NOW - timedelta(days=5))["band"] == "on_track"


def test_nothing_to_project_against_is_watch() -> None:
    assert _h(target_hires=None)["band"] == "watch"
    assert _h(closes_at=None)["band"] == "watch"
    assert _h(created_at=NOW - timedelta(days=3))["band"] == "watch"


def test_past_the_closing_date_short_of_target_is_at_risk() -> None:
    out = _h(closes_at=NOW - timedelta(days=1), hired=1)
    assert out["band"] == "at_risk" and "Past its closing date" in out["reason"]


def test_the_projection_uses_the_pace_of_candidates_and_the_hire_ratio() -> None:
    # 12 reached a decision in 30 days; no decisions yet → assumed 25% → 0.1 hires/day;
    # 4 remaining → 40 days → fills before a 60-day close.
    out = _h()
    assert out["band"] == "on_track" and out["assumed_hire_ratio"] is True
    assert out["projected_fill_date"] == (NOW + timedelta(days=40)).date().isoformat()
    assert out["hires_per_week"] == 0.7

    # Its own decisions replace the assumption: 1 hire in 10 → 10% → fills after close.
    own = _h(hired=1, rejected=9, target_hires=5)
    assert own["assumed_hire_ratio"] is False and own["band"] == "at_risk"
    assert "after the closing date" in own["reason"]


def test_a_projected_fill_after_the_closing_date_is_at_risk() -> None:
    assert _h(closes_at=NOW + timedelta(days=20))["band"] == "at_risk"


def test_no_movement_at_all_is_at_risk_not_silence() -> None:
    out = _h(reached_decision=0)
    assert out["band"] == "at_risk" and out["hires_per_week"] == 0.0


def test_a_fill_with_under_a_week_to_spare_is_watch() -> None:
    assert _h(closes_at=NOW + timedelta(days=44))["band"] == "watch"


def test_a_pipeline_nobody_has_touched_in_two_weeks_is_watch() -> None:
    out = _h(last_movement_at=NOW - timedelta(days=20))
    assert out["band"] == "watch" and "14 days" in out["reason"]


def test_the_board_query_is_company_scoped_and_computed_from_the_ledger() -> None:
    from app.company_board import _BOARD_SQL

    sql = " ".join(_BOARD_SQL.split())
    assert "r.company_id = :c" in sql
    assert "e.company_id = r.company_id" in sql and "w.company_id = r.company_id" in sql
    assert "t.company_id = r.company_id" in sql
    assert "r.status IN ('open', 'paused')" in sql
    assert "stage_transitions" in sql
    assert "enrolment_awaits_human(e.status, e.current_round_id)" in sql


def test_the_board_routes_are_read_only_and_for_the_company_admin() -> None:
    from app.routers import company_board

    methods = {m for route in company_board.router.routes for m in route.methods}  # type: ignore[attr-defined]
    assert methods <= {"GET", "HEAD"}
    for fn in (company_board.get_hiring_board,
               company_board.get_requisition_dashboard_read_only):
        assert "CompanyAdminCtxDep" in str(inspect.signature(fn).parameters["ctx"].annotation)


def test_the_read_only_dashboard_is_hrs_dashboard() -> None:
    from app.routers import company_board, hr_requisitions

    assert "build_requisition_dashboard(" in inspect.getsource(
        company_board.get_requisition_dashboard_read_only
    )
    assert "build_requisition_dashboard(" in inspect.getsource(
        hr_requisitions.requisition_dashboard
    )
