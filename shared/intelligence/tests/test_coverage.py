"""Planner tests — coverage is the property the whole engine is bought for."""

from __future__ import annotations

import pytest

from shared.intelligence.coverage import coverage_report, plan_for_turn, plan_interview
from shared.intelligence.schema import RoleProfile
from shared.intelligence.taxonomy import baseline_profile

PID = "p" * 32


def _profile(title: str = "CNC Machine Operator") -> RoleProfile:
    return baseline_profile(profile_id=PID, job_title=title)


@pytest.mark.parametrize("max_turns", [1, 2, 3, 4, 5, 8, 10, 15, 20])
def test_plan_length_always_matches_turn_budget(max_turns: int) -> None:
    """The plan must be exactly as long as the enforced turn budget.

    The live worker enforces MAX_CANDIDATE_ANSWERS in code. A plan shorter than
    that leaves the final questions unguided; longer, and the last competency
    is never reached.
    """
    plans = plan_interview(_profile(), max_turns)
    assert len(plans) == max_turns
    assert [p.turn_index for p in plans] == list(range(1, max_turns + 1))


def test_bookends_present_for_a_normal_interview() -> None:
    plans = plan_interview(_profile(), 10)
    assert plans[0].kind == "intro"
    assert plans[-1].kind == "wrap"
    # Intro and wrap carry no competency — scoring a self-introduction as
    # "technical depth" is noise.
    assert plans[0].competency_id is None
    assert plans[-1].competency_id is None


def test_very_short_interview_drops_the_wrap_for_a_probe() -> None:
    plans = plan_interview(_profile(), 3)
    assert len(plans) == 3
    assert plans[0].kind == "intro"
    assert any(p.kind == "probe" for p in plans)


def test_planning_is_deterministic() -> None:
    a = plan_interview(_profile(), 10)
    b = plan_interview(_profile(), 10)
    assert [p.model_dump() for p in a] == [p.model_dump() for p in b]


def test_heaviest_competency_gets_the_most_turns() -> None:
    profile = _profile()
    plans = plan_interview(profile, 10)
    counts: dict[str, int] = {}
    for plan in plans:
        if plan.competency_id:
            counts[plan.competency_id] = counts.get(plan.competency_id, 0) + 1

    heaviest = max(profile.competencies, key=lambda c: c.weight)
    assert counts.get(heaviest.id, 0) == max(counts.values())


def test_no_competency_is_probed_twice_in_a_row() -> None:
    """Breadth guarantee — back-to-back repeats are what made the old prose
    instruction feel like an interrogation on one topic."""
    plans = plan_interview(_profile(), 10)
    probes = [p.competency_id for p in plans if p.kind == "probe"]
    for a, b in zip(probes, probes[1:], strict=False):
        assert a != b, f"adjacent duplicate competency {a!r} in {probes}"


def test_probe_hints_rotate_within_a_competency() -> None:
    """A competency with several slots must ask different things each time."""
    profile = _profile()
    plans = plan_interview(profile, 12)
    by_comp: dict[str, list[str]] = {}
    for plan in plans:
        if plan.kind == "probe" and plan.competency_id and plan.probe_hint:
            by_comp.setdefault(plan.competency_id, []).append(plan.probe_hint)

    for cid, hints in by_comp.items():
        comp = profile.competency(cid)
        assert comp is not None
        # Distinct until the probe list is exhausted, then it may wrap.
        expected_distinct = min(len(hints), len(comp.probes))
        assert len(set(hints)) == expected_distinct


def test_role_drives_the_plan_not_a_fixed_structure() -> None:
    """A support role and a machining role must produce different plans.

    This is the concrete replacement for the hardcoded 'Q2-Q6 technical,
    Q7-Q9 behavioural' structure, which assumed every role splits that way.
    """
    support = plan_interview(_profile("Customer Support Associate - Voice Process"), 10)
    machining = plan_interview(_profile("CNC Machine Operator"), 10)

    support_kinds = [p.competency_name for p in support if p.kind == "probe"]
    machining_kinds = [p.competency_name for p in machining if p.kind == "probe"]
    assert support_kinds != machining_kinds


def test_plan_for_turn_matches_the_full_plan() -> None:
    profile = _profile()
    plans = plan_interview(profile, 10)
    for i, expected in enumerate(plans, start=1):
        assert plan_for_turn(profile, i, 10).model_dump() == expected.model_dump()


def test_plan_for_turn_clamps_an_overrun() -> None:
    """A session that overruns its budget still gets a directive, not a crash."""
    profile = _profile()
    assert plan_for_turn(profile, 99, 10).turn_index == 10
    assert plan_for_turn(profile, 0, 10).turn_index == 1


def test_coverage_report_names_uncovered_competencies() -> None:
    """With fewer probe slots than competencies, the gap must be reported.

    Silence here is what lets a scorer invent a confident score for something
    the interview never asked about.
    """
    profile = _profile()
    plans = plan_interview(profile, 4)  # intro + 2 probes + wrap
    report = coverage_report(profile, plans)

    assert report.total_turns == 4
    assert report.probe_turns == 2
    assert len(report.uncovered) == len(profile.competencies) - 2
    covered = {r.competency_id for r in report.rows if r.planned_turns > 0}
    assert covered.isdisjoint(set(report.uncovered))


def test_coverage_report_is_empty_of_gaps_for_a_long_interview() -> None:
    profile = _profile()
    report = coverage_report(profile, plan_interview(profile, 14))
    assert report.uncovered == []
    assert sum(r.planned_turns for r in report.rows) == report.probe_turns
