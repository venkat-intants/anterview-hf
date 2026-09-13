"""E1 — what the per-opening dashboard adds: the rules, and how the data is read.

The rules are pure and tested here. The queries run against a real Postgres in
``smoke_group_e_dashboard``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _facts(**over: Any) -> Any:
    from app.requisition_dashboard import DashboardFacts

    base: dict[str, Any] = {"requisition_id": "req-1", "status": "open", "accepting_public": False,
                            "has_published_workflow": True, "now": NOW}
    return DashboardFacts(**{**base, **over})


def _keys(items: list[dict[str, Any]]) -> list[str]:
    return [i["key"] for i in items]


def test_a_quiet_opening_needs_nothing() -> None:
    from app.requisition_dashboard import attention_items

    assert attention_items(_facts()) == []


def test_held_candidates_are_named_as_held_not_rejected() -> None:
    from app.requisition_dashboard import attention_items

    fresh = attention_items(_facts(held=2, awaiting=2, longest_hold_days=1))
    old = attention_items(_facts(held=2, awaiting=2, longest_hold_days=5))
    assert fresh[0]["key"] == "held" and fresh[0]["severity"] == "info"
    assert old[0]["severity"] == "warning"
    assert "held, not rejected" in old[0]["body"]
    assert "rejected" not in old[0]["title"].lower()
    assert old[0]["link"] == "/hr/requisitions/req-1/decisions"


def test_a_decision_backlog_escalates_with_the_wait() -> None:
    from app.requisition_dashboard import attention_items

    assert attention_items(_facts(awaiting=2, longest_wait_days=1)) == []
    warn = attention_items(_facts(awaiting=2, longest_wait_days=4))
    crit = attention_items(_facts(awaiting=2, longest_wait_days=15))
    assert _keys(warn) == ["decision_backlog"] and warn[0]["severity"] == "warning"
    assert crit[0]["severity"] == "critical"


def test_the_held_are_not_counted_twice_in_the_backlog() -> None:
    from app.requisition_dashboard import attention_items

    items = attention_items(_facts(awaiting=3, held=3, longest_wait_days=20, longest_hold_days=20))
    assert _keys(items) == ["held"]


def test_links_about_to_expire_and_lapsed_ones() -> None:
    from app.requisition_dashboard import attention_items

    items = attention_items(_facts(expiring_links=2, lapsed_links=1))
    assert _keys(items) == ["links_expiring", "links_lapsed"]
    assert "48 hours" in items[0]["title"]
    assert "does not reject" in items[0]["body"]


def test_incomplete_scoring() -> None:
    from app.requisition_dashboard import attention_items

    items = attention_items(_facts(scoring_failed=1, scoring_pending=4))
    assert _keys(items) == ["scoring_failed", "scoring_pending"]
    assert items[0]["severity"] == "warning" and items[1]["severity"] == "info"


def test_workflow_problems() -> None:
    from app.requisition_dashboard import attention_items

    none_live = attention_items(_facts(has_published_workflow=False, accepting_public=True,
                                       without_workflow=3))
    assert none_live[0]["key"] == "no_workflow"
    assert "3 candidates" in none_live[0]["body"]
    # A closed opening with no workflow is finished, not a problem.
    assert attention_items(_facts(status="closed", has_published_workflow=False,
                                  without_workflow=3)) == []

    hard = attention_items(_facts(low_pass_rounds=[("Technical Test", 12, 25)]))
    assert hard[0]["key"] == "low_pass:Technical Test"
    assert hard[0]["link"] == "/hr/requisitions/req-1/workflow"

    draft = attention_items(_facts(published_version=2, draft_version=3))
    assert _keys(draft) == ["draft_pending"]


def test_closing_dates() -> None:
    from app.requisition_dashboard import attention_items

    soon = attention_items(_facts(closes_at=NOW + timedelta(days=4), target_hires=3, hired=1))
    assert _keys(soon) == ["closing_short"] and "1 of 3 hired" in soon[0]["title"]
    # On target: nothing to say.
    assert attention_items(_facts(closes_at=NOW + timedelta(days=4), target_hires=3,
                                  hired=3)) == []
    past = attention_items(_facts(closes_at=NOW - timedelta(days=1)))
    assert _keys(past) == ["past_close"]


def test_worst_first() -> None:
    from app.requisition_dashboard import attention_items

    items = attention_items(_facts(scoring_pending=1, expiring_links=1, awaiting=2,
                                   longest_wait_days=30))
    assert [i["severity"] for i in items] == ["critical", "warning", "info"]


def test_manual_steps_are_the_human_gates_with_counts() -> None:
    from app.requisition_dashboard import manual_steps

    steps = manual_steps({"ready_to_shortlist": 2, "shortlist_threshold": 7, "not_started": 5,
                          "awaiting_review": 1, "held": 0, "finished": 3}, "req-1")
    assert [(s["key"], s["count"]) for s in steps] == [
        ("ready_to_shortlist", 2), ("to_screen", 3), ("awaiting_review", 1), ("finished", 3),
    ]
    assert "7/10" in steps[0]["label"]
    assert steps[-1]["link"] == "/hr/requisitions/req-1/decisions"


def test_stage_timing_follows_the_live_workflow() -> None:
    from app.requisition_dashboard import stage_timing

    rows = [{"key": "shortlisted", "median_days": 1.25, "n": 9},
            {"key": "r1", "median_days": 3, "n": 6},
            {"key": "old-round", "median_days": 9, "n": 2}]
    rounds = [{"id": "r1", "title": "Technical Test"}, {"id": "r2", "title": "AI Interview"}]
    out = stage_timing(rows, rounds)
    assert [t["label"] for t in out] == [
        "Shortlisted", "Reached Technical Test", "Reached AI Interview", "Hired",
    ]
    assert out[0]["median_days"] == 1.2 or out[0]["median_days"] == 1.3
    assert out[2] == {"key": "r2", "label": "Reached AI Interview", "median_days": None,
                      "count": 0}
    # A round from an archived version is not listed.
    assert all(t["key"] != "old-round" for t in out)


def test_every_query_is_scoped_and_reads_the_ledger() -> None:
    import app.requisition_dashboard as d

    for name in ("_PROGRESS_SQL", "_TIMING_SQL", "_COMPOSITE_SQL", "_LINKS_SQL", "_HELD_SQL",
                 "_ACTIVITY_SQL", "_ACTIVITY_SUMMARY_SQL", "_WORKFLOW_STATE_SQL"):
        sql = " ".join(getattr(d, name).split())
        assert ":r" in sql and ":c" in sql, name
    assert "enrolment_awaits_human" in d._PROGRESS_SQL
    assert "stage_transitions" in d._TIMING_SQL
    assert "rr.superseded_at IS NULL" in d._COMPOSITE_SQL


def test_the_dashboard_endpoint_returns_the_new_sections() -> None:
    import inspect

    from app.routers.hr_requisitions import build_requisition_dashboard, requisition_dashboard

    src = inspect.getsource(build_requisition_dashboard)
    assert "gather_dashboard(" in src and "**extras" in src
    assert "build_requisition_dashboard(" in inspect.getsource(requisition_dashboard)
