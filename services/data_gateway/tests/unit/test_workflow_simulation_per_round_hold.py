"""The dry run must predict what the runner will actually do, per round.

``workflow_rounds.auto_advance`` overrides ``workflows.auto_advance_rounds``, and
the simulator read only the workflow-level bool. So the one screen whose entire
job is to tell HR what will happen was confidently wrong in both directions: a
workflow set to advance with a final round set to "Hold for my review" showed
"passes every round -> decision" while the runner held, and a manual workflow
with a round opted into automation showed candidates stopping where they do not.

These tests are written against the RESOLVER the runner uses
(``workflows.auto_advance_for``), not against a second copy of the rule — a
simulator that agrees with a reimplementation of the runner is exactly the bug
this file exists to prevent.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.workflow_simulation import WARNING, build_scenarios, evaluate
from app.workflows import auto_advance_for


def _round(rid: str, pos: int, kind: str = "mcq", **over: Any) -> dict[str, Any]:
    """A round as ``load_rounds`` returns one. Note ``auto_advance`` is absent
    unless a test sets it, which is the shape of every row that predates the
    column."""
    base = {
        "id": rid, "position": pos, "title": f"R{pos}", "kind": kind,
        "pass_threshold": 60.0 if kind != "human_review" else None,
        "time_limit_seconds": None, "deadline_days": 7,
        "on_pass_next_round_id": None,
        "exam_round_id": "ex" if kind in ("mcq", "coding") else None,
        "on_fail_next_round_id": None, "fast_track_min_percent": None,
        "on_fast_track_next_round_id": None,
    }
    return {**base, **over}


def _chain(*rounds: dict[str, Any]) -> list[dict[str, Any]]:
    out = [dict(r) for r in rounds]
    for a, b in zip(out, out[1:], strict=False):
        a["on_pass_next_round_id"] = a.get("on_pass_next_round_id") or b["id"]
    return out


def _ctx(rounds: list[dict[str, Any]], *, auto_advance_rounds: bool) -> dict[str, Any]:
    return {
        "workflow": {"auto_advance_rounds": auto_advance_rounds},
        "rounds": rounds, "criteria": {},
        "readiness": {"ex": {"status": "published", "questions": 3, "exam_id": "e"}},
        "kits": set(), "stage_settings": {}, "interviewers": 1,
    }


def _all_pass(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    return next(s for s in scenarios if s["description"] == "passes every round")


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("workflow_default", [True, False])
@pytest.mark.parametrize("override", [None, True, False])
def test_the_walk_stops_exactly_where_the_resolver_says_it_will(
    workflow_default: bool, override: bool | None
) -> None:
    """Every combination of the workflow default and the per-round override,
    checked against ``auto_advance_for`` rather than against a hand-written
    expectation — the two must not be able to drift apart.
    """
    first = _round("a", 0, auto_advance=override)
    rounds = _chain(first, _round("b", 1))
    sc = _all_pass(build_scenarios(rounds, auto_advance=workflow_default))

    advances = auto_advance_for(rounds[0], {"auto_advance_rounds": workflow_default})
    visited = [s["round_id"] for s in sc["steps"]]
    if advances:
        assert visited == ["a", "b"], sc
    else:
        assert visited == ["a"], sc
        assert sc["end"] == "waiting"


def test_a_final_round_can_hold_inside_an_otherwise_automatic_workflow() -> None:
    """The case that motivated per-round control, and the one the simulator got
    backwards: automatic throughout, deliberate at the end."""
    rounds = _chain(_round("a", 0), _round("b", 1, auto_advance=False))
    sc = _all_pass(build_scenarios(rounds, auto_advance=True))

    assert [s["round_id"] for s in sc["steps"]] == ["a", "b"]  # a still advances
    assert sc["end"] == "waiting", "the held final round must not reach the decision"
    assert sc["steps"][-1]["next"] == {"kind": "person"}


def test_a_screening_round_can_advance_inside_an_otherwise_manual_workflow() -> None:
    """And the mirror image, which the old code got wrong the other way: it
    stopped candidates on a round that opts into automation."""
    rounds = _chain(_round("a", 0, auto_advance=True), _round("b", 1, auto_advance=True))
    sc = _all_pass(build_scenarios(rounds, auto_advance=False))

    assert [s["round_id"] for s in sc["steps"]] == ["a", "b"]
    assert sc["end"] == "decision"


def test_a_held_round_that_fails_is_held_not_branched() -> None:
    """A fail branch is only followed by an automatic round — the runner leaves a
    failing candidate on a manual round for a person. The simulator has to show
    the same dead end, per round."""
    rounds = [
        _round("a", 0, on_pass_next_round_id="b", on_fail_next_round_id="b",
               auto_advance=False),
        _round("b", 1),
    ]
    ends = {sc["description"]: sc for sc in build_scenarios(rounds, auto_advance=True)}
    failed = next(s for d, s in ends.items() if "falls below" in d)
    assert [s["round_id"] for s in failed["steps"]] == ["a"]
    assert failed["end"] == "held"
    assert failed["steps"][-1]["next"] == {"kind": "hold"}


# ---------------------------------------------------------------------------
# The warnings HR reads next to the walk
# ---------------------------------------------------------------------------
def _warnings(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Every warning, wherever ``evaluate`` files it. Round-attributed findings
    live under ``rounds[].findings`` and workflow-wide ones under
    ``workflow_findings``; a test that read only one of the two would miss half
    the messages HR sees."""
    out = evaluate(ctx, None)
    found = list(out["workflow_findings"])
    for r in out["rounds"]:
        found.extend(r["findings"])
    return [f for f in found if f["severity"] == WARNING]


def test_the_branch_warning_is_attached_only_to_the_round_that_holds() -> None:
    """The warning carries a round id, so resolving it from the workflow bool
    fired it on rounds where it was false and stayed silent where it was true."""
    rounds = [
        _round("a", 0, on_pass_next_round_id="b", on_fail_next_round_id="b",
               auto_advance=False),
        _round("b", 1, on_pass_next_round_id="c", on_fail_next_round_id="c",
               auto_advance=True),
        _round("c", 2, kind="human_review"),
    ]
    branch = [
        w for w in _warnings(_ctx(rounds, auto_advance_rounds=True))
        if "Branches are only followed" in w["message"]
    ]
    assert [w["round_id"] for w in branch] == ["a"]


def test_the_branch_warning_fires_for_an_inheriting_round_in_a_manual_workflow() -> None:
    rounds = [
        _round("a", 0, on_pass_next_round_id="b", on_fail_next_round_id="b"),
        _round("b", 1, kind="human_review"),
    ]
    branch = [
        w for w in _warnings(_ctx(rounds, auto_advance_rounds=False))
        if "Branches are only followed" in w["message"]
    ]
    assert [w["round_id"] for w in branch] == ["a"]


def test_no_round_advances_says_so_only_when_no_round_advances() -> None:
    rounds = _chain(_round("a", 0), _round("b", 1))
    messages = " ".join(w["message"] for w in _warnings(_ctx(rounds, auto_advance_rounds=False)))
    assert "nobody moves on by themselves" in messages


def test_one_held_round_among_many_names_it_instead_of_claiming_none_advance() -> None:
    """The blanket sentence was flatly false for a workflow with a single held
    round, which is the commonest shape there is."""
    rounds = _chain(_round("a", 0), _round("b", 1, title="Final call", auto_advance=False))
    messages = " ".join(w["message"] for w in _warnings(_ctx(rounds, auto_advance_rounds=True)))
    assert "nobody moves on by themselves" not in messages
    assert "1 of 2 rounds hold for review" in messages
    assert "Final call" in messages


def test_every_round_overriding_to_hold_still_reads_as_all_held() -> None:
    """All held is all held, whether the rounds inherited it or each said so."""
    rounds = _chain(_round("a", 0, auto_advance=False), _round("b", 1, auto_advance=False))
    messages = " ".join(w["message"] for w in _warnings(_ctx(rounds, auto_advance_rounds=True)))
    assert "nobody moves on by themselves" in messages
    assert "rounds hold for review" not in messages


def test_an_all_automatic_workflow_warns_about_neither() -> None:
    rounds = _chain(_round("a", 0), _round("b", 1, auto_advance=True))
    messages = " ".join(w["message"] for w in _warnings(_ctx(rounds, auto_advance_rounds=True)))
    assert "nobody moves on by themselves" not in messages
    assert "hold for review" not in messages


# ---------------------------------------------------------------------------
# The invariant behind all of the above
# ---------------------------------------------------------------------------
def test_the_simulator_and_the_runner_share_one_resolver() -> None:
    """Not style: two copies of this rule is the defect, not the duplication.
    Both modules must import the same function, so a change to how the override
    resolves cannot reach one and miss the other.
    """
    from app import workflow_runner, workflow_simulation, workflows

    assert workflow_simulation.auto_advance_for is workflows.auto_advance_for
    assert workflow_runner.auto_advance_for is workflows.auto_advance_for
