"""Unit tests for Group C — workflow chain validation and coverage.

``validate_chain`` and ``build_coverage`` are pure functions over plain data, so
they are tested directly rather than through a mock. The stateful half
(versioning, publish, clone) is covered by
``tests/integration/smoke_group_c_workflows.py`` against a real engine.
"""

from __future__ import annotations

import pytest

from app.workflows import (
    AI_GRADED_KINDS,
    EXAM_BACKED_KINDS,
    MAX_ROUNDS,
    ROUND_KINDS,
    build_coverage,
    validate_chain,
)

PROFILE = [
    {"id": "python", "name": "Python proficiency", "weight": 0.35},
    {"id": "solving", "name": "Problem solving", "weight": 0.30},
    {"id": "comms", "name": "Communication", "weight": 0.20},
    {"id": "vcs", "name": "Version control", "weight": 0.15},
]


def _round(rid, pos, title="R", kind="mcq", nxt=None, threshold=50, exam="e"):
    return {
        "id": rid, "position": pos, "title": title, "kind": kind,
        "pass_threshold": threshold, "exam_round_id": exam, "on_pass_next_round_id": nxt,
    }


# ===========================================================================
# Round kinds — D-03
# ===========================================================================
def test_exactly_four_round_kinds_ship() -> None:
    assert {"mcq", "coding", "ai_interview", "human_review"} == ROUND_KINDS


def test_portfolio_is_not_a_round_kind() -> None:
    """Deferred to Phase 3: it needs a new grader and introduces a
    prompt-injection surface on candidate-authored content."""
    assert "portfolio" not in ROUND_KINDS
    assert "file_upload" not in ROUND_KINDS


def test_exam_backed_and_ai_graded_kinds_do_not_overlap() -> None:
    """MCQ and coding grade deterministically; only the interview is model-graded.
    Conflating them would let us claim the AI grades an aptitude round, which is
    not true and not substantiable."""
    assert EXAM_BACKED_KINDS.isdisjoint(AI_GRADED_KINDS)
    assert {"ai_interview"} == AI_GRADED_KINDS


# ===========================================================================
# validate_chain
# ===========================================================================
def test_empty_workflow_is_refused() -> None:
    assert validate_chain([]) == ["A workflow needs at least one round."]


def test_too_many_rounds_is_refused() -> None:
    rounds = [_round(str(i), i) for i in range(MAX_ROUNDS + 1)]
    errs = validate_chain(rounds)
    assert any("more than" in e for e in errs)


def test_valid_linear_chain_passes() -> None:
    rounds = [
        _round("a", 0, "Aptitude", nxt="b"),
        _round("b", 1, "Coding", kind="coding", nxt="c"),
        _round("c", 2, "Interview", kind="ai_interview", exam=None, nxt="d"),
        _round("d", 3, "HR Final", kind="human_review", threshold=None, exam=None),
    ]
    assert validate_chain(rounds) == []


def test_cycle_is_refused() -> None:
    """A loop would put the runner in an infinite cycle moving one candidate
    between two rounds forever."""
    rounds = [_round("a", 0, nxt="b"), _round("b", 1, nxt="a")]
    assert any("loop" in e for e in validate_chain(rounds))


def test_three_round_cycle_is_refused() -> None:
    rounds = [_round("a", 0, nxt="b"), _round("b", 1, nxt="c"), _round("c", 2, nxt="a")]
    assert any("loop" in e for e in validate_chain(rounds))


def test_unreachable_round_is_refused() -> None:
    rounds = [_round("a", 0, "First"), _round("b", 1, "Stranded")]
    assert any("Unreachable" in e for e in validate_chain(rounds))


def test_pointer_outside_the_workflow_is_refused() -> None:
    rounds = [_round("a", 0, "First", nxt="somewhere-else")]
    assert any("not in this workflow" in e for e in validate_chain(rounds))


def test_exam_round_without_questions_is_refused() -> None:
    for kind in ("mcq", "coding"):
        rounds = [_round("a", 0, "R", kind=kind, exam=None)]
        assert any("needs questions" in e for e in validate_chain(rounds)), kind


def test_scored_round_without_a_threshold_is_refused() -> None:
    for kind in ("mcq", "coding", "ai_interview"):
        rounds = [_round("a", 0, "R", kind=kind, threshold=None)]
        assert any("advance threshold" in e for e in validate_chain(rounds)), kind


def test_human_review_needs_no_threshold() -> None:
    """A person decides, not a number."""
    rounds = [_round("a", 0, "HR Final", kind="human_review", threshold=None, exam=None)]
    assert validate_chain(rounds) == []


def test_unknown_kind_is_refused() -> None:
    rounds = [_round("a", 0, "R", kind="portfolio", exam=None)]
    assert any("unknown round type" in e for e in validate_chain(rounds))


def test_last_round_may_point_nowhere() -> None:
    """NULL means the candidate goes to the final human decision — never to an
    automatic outcome."""
    assert validate_chain([_round("a", 0, "Only", nxt=None)]) == []


# ===========================================================================
# build_coverage
# ===========================================================================
def test_unassessed_competency_warns() -> None:
    criteria = {"r1": [{"competency_id": "python"}]}
    rows, warnings = build_coverage(PROFILE, criteria, {"r1": "Coding"})
    assert len(rows) == 4
    unassessed = {r.competency_id for r in rows if r.times_assessed == 0}
    assert unassessed == {"solving", "comms", "vcs"}
    assert len(warnings) == 3
    assert all("no round assesses it" in w for w in warnings)


def test_warning_names_the_competency_and_its_weight() -> None:
    """A warning that does not say what to do about it gets ignored."""
    rows, warnings = build_coverage(PROFILE, {}, {})
    vcs = next(w for w in warnings if "Version control" in w)
    assert "0.15" in vcs
    assert "add it to a round" in vcs
    assert rows


def test_two_rounds_is_reinforcement_not_a_warning() -> None:
    """Assessing Python in both the coding round and the interview is normal and
    desirable — flagging it would make the checker noise."""
    criteria = {
        "r1": [{"competency_id": "python"}],
        "r2": [{"competency_id": "python"}],
    }
    _rows, warnings = build_coverage(
        [PROFILE[0]], criteria, {"r1": "Coding", "r2": "Interview"}
    )
    assert warnings == []


def test_three_rounds_is_flagged_as_probably_accidental() -> None:
    criteria = {
        "r1": [{"competency_id": "python"}],
        "r2": [{"competency_id": "python"}],
        "r3": [{"competency_id": "python"}],
    }
    _rows, warnings = build_coverage(
        [PROFILE[0]], criteria, {"r1": "Aptitude", "r2": "Coding", "r3": "Interview"}
    )
    assert len(warnings) == 1
    assert "3 rounds" in warnings[0]
    # It must name where, so HR can act without hunting.
    for title in ("Aptitude", "Coding", "Interview"):
        assert title in warnings[0]


def test_coverage_reports_where_each_competency_is_assessed() -> None:
    criteria = {
        "r1": [{"competency_id": "python"}, {"competency_id": "solving"}],
        "r2": [{"competency_id": "solving"}],
    }
    rows, _ = build_coverage(PROFILE, criteria, {"r1": "Coding", "r2": "Interview"})
    by_id = {r.competency_id: r for r in rows}
    assert by_id["solving"].assessed_in == ["Coding", "Interview"]
    assert by_id["python"].assessed_in == ["Coding"]
    assert by_id["vcs"].assessed_in == []


def test_full_coverage_produces_no_warnings() -> None:
    criteria = {f"r{i}": [{"competency_id": c["id"]}] for i, c in enumerate(PROFILE)}
    titles = {f"r{i}": f"Round {i}" for i in range(len(PROFILE))}
    _rows, warnings = build_coverage(PROFILE, criteria, titles)
    assert warnings == []


# ===========================================================================
# ValidationReport
# ===========================================================================
def test_warnings_do_not_block_publication() -> None:
    """A coverage gap can be a deliberate choice. Refusing to publish over one
    would make the checker something HR routes around rather than reads."""
    from app.workflows import ValidationReport

    rep = ValidationReport(warnings=["'X' is never assessed"])
    assert rep.publishable is True


def test_errors_block_publication() -> None:
    from app.workflows import ValidationReport

    rep = ValidationReport(errors=["The rounds form a loop"])
    assert rep.publishable is False


@pytest.mark.parametrize("kind", sorted(ROUND_KINDS))
def test_every_kind_can_form_a_valid_single_round_workflow(kind: str) -> None:
    rounds = [
        _round(
            "a", 0, "R", kind=kind,
            threshold=None if kind == "human_review" else 50,
            exam="e" if kind in EXAM_BACKED_KINDS else None,
        )
    ]
    assert validate_chain(rounds) == [], kind
