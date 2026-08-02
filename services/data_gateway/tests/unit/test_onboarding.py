"""Tests for self-serve onboarding + the practice plan.

The plan-building logic is pure (``_build_plan``), so most of this exercises
real behaviour without a database. The load-bearing cases are the ones about
ABSENCE: a competency nobody has practised must never look like a competency
practised badly, and readiness must not sag just because a topic has not come
up yet.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from shared.intelligence import baseline_profile, compute_profile_id

from app.routers.onboarding import (
    GOALS,
    LEVELS,
    OnboardingSubmit,
    _build_plan,
    _competency_history,
    _is_self_serve,
    _profile_for,
)


def _profile(title: str = "CNC Machine Operator", level: str = "entry"):
    return baseline_profile(
        profile_id=compute_profile_id(job_title=title, seniority=level),
        job_title=title,
        seniority=level,  # type: ignore[arg-type]
    )


def _user(roles: list[str]) -> MagicMock:
    u = MagicMock()
    u.roles = roles
    u.user_id = "u-1"
    return u


# ---------------------------------------------------------------------------
# Who gets the wizard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        ([], True),
        (["candidate"], True),
        (["guest_candidate"], True),
        (["hr_manager"], False),
        (["super_admin"], False),
        (["platform_owner"], False),
        (["admin"], False),
        # A candidate who is ALSO an HR manager is an operator; do not
        # interrupt them with "what job are you practising for?".
        (["candidate", "hr_manager"], False),
    ],
)
def test_only_self_serve_users_are_offered_onboarding(
    roles: list[str], expected: bool
) -> None:
    assert _is_self_serve(_user(roles)) is expected


# ---------------------------------------------------------------------------
# Submission validation
# ---------------------------------------------------------------------------


def test_target_role_whitespace_is_collapsed() -> None:
    body = OnboardingSubmit(target_role="  CNC   Machine  Operator ")
    assert body.target_role == "CNC Machine Operator"


def test_blank_target_role_is_rejected() -> None:
    with pytest.raises(ValueError):
        OnboardingSubmit(target_role="   ")


def test_unknown_goal_is_rejected_so_the_ui_can_trust_the_set() -> None:
    with pytest.raises(ValueError):
        OnboardingSubmit(target_role="Welder", goal="vibes")


@pytest.mark.parametrize("goal", sorted(GOALS))
def test_every_advertised_goal_validates(goal: str) -> None:
    assert OnboardingSubmit(target_role="Welder", goal=goal).goal == goal


def test_unknown_level_and_language_fall_back_rather_than_failing() -> None:
    """A stale client must not 422 the one form standing between a student and
    their first interview."""
    body = OnboardingSubmit(target_role="Welder", target_level="wizard", preferred_language="fr")
    assert body.target_level == "entry"
    assert body.preferred_language == "en"


def test_goal_is_optional() -> None:
    assert OnboardingSubmit(target_role="Welder").goal is None


# ---------------------------------------------------------------------------
# The role model behind the plan
# ---------------------------------------------------------------------------


def test_profile_for_classifies_a_non_it_role() -> None:
    p = _profile_for("Staff Nurse (GNM)", "entry")
    assert p.domain_family == "healthcare_nursing"
    assert any("Patient" in c.name for c in p.competencies)


def test_profile_for_normalises_a_junk_level() -> None:
    assert _profile_for("Welder", "wizard").seniority == "entry"


@pytest.mark.parametrize("level", sorted(LEVELS))
def test_every_level_is_carried_through(level: str) -> None:
    assert _profile_for("Data Analyst", level).seniority == level


# ---------------------------------------------------------------------------
# Plan building — the day-zero case
# ---------------------------------------------------------------------------


def test_plan_is_useful_before_any_interview() -> None:
    """A brand-new student is at peak motivation; an empty dashboard wastes it."""
    profile = _profile()
    plan = _build_plan(
        profile, {}, target_role="CNC Machine Operator", target_level="entry",
        interviews=0, last_at=None,
    )

    assert plan.ready is True
    assert len(plan.competencies) == len(profile.competencies)
    # Every competency is listed with its target, so they can see what they
    # are about to be assessed on.
    assert all(c.target for c in plan.competencies)
    assert all(c.score is None and c.attempts == 0 for c in plan.competencies)
    assert plan.readiness is None
    assert plan.focus_competency_id is None
    assert len(plan.never_probed) == len(profile.competencies)


def test_never_probed_is_none_not_zero() -> None:
    """'Never practised' and 'practised badly' are different facts."""
    profile = _profile()
    probed = profile.competencies[0].id
    plan = _build_plan(
        profile, {probed: [6.0]}, target_role="X", target_level="entry",
        interviews=1, last_at=None,
    )
    scored = {c.id: c for c in plan.competencies}
    assert scored[probed].score == 6.0
    for cid, c in scored.items():
        if cid != probed:
            assert c.score is None, "an unprobed competency must not read as 0"


# ---------------------------------------------------------------------------
# Plan building — scoring
# ---------------------------------------------------------------------------


def test_score_is_the_mean_and_trend_is_the_last_delta() -> None:
    profile = _profile()
    cid = profile.competencies[0].id
    plan = _build_plan(
        profile, {cid: [4.0, 6.0, 9.0]}, target_role="X", target_level="entry",
        interviews=3, last_at=None,
    )
    comp = next(c for c in plan.competencies if c.id == cid)
    assert comp.score == pytest.approx(6.3, abs=0.05)  # (4+6+9)/3
    assert comp.attempts == 3
    assert comp.trend == pytest.approx(3.0)  # 9 - 6


def test_trend_needs_two_attempts() -> None:
    profile = _profile()
    cid = profile.competencies[0].id
    plan = _build_plan(
        profile, {cid: [7.0]}, target_role="X", target_level="entry",
        interviews=1, last_at=None,
    )
    assert next(c for c in plan.competencies if c.id == cid).trend is None


def test_readiness_ignores_unprobed_competencies() -> None:
    """Weighting against the full set would make readiness fall purely because
    a topic has not come up — which reads as 'you got worse'."""
    profile = _profile()
    cid = profile.competencies[0].id

    one = _build_plan(profile, {cid: [8.0]}, target_role="X", target_level="entry",
                      interviews=1, last_at=None)
    # A single 8/10 on the only probed competency is 80% readiness, NOT
    # 8 * that competency's weight.
    assert one.readiness == pytest.approx(80.0, abs=0.1)


def test_readiness_is_bounded_and_weighted() -> None:
    profile = _profile()
    perfect = {c.id: [10.0] for c in profile.competencies}
    zero = {c.id: [0.0] for c in profile.competencies}
    assert _build_plan(profile, perfect, target_role="X", target_level="entry",
                       interviews=1, last_at=None).readiness == pytest.approx(100.0)
    assert _build_plan(profile, zero, target_role="X", target_level="entry",
                       interviews=1, last_at=None).readiness == pytest.approx(0.0)


def test_focus_is_the_weakest_probed_competency() -> None:
    """Recommending something never practised would be unactionable noise."""
    profile = _profile()
    strong, weak = profile.competencies[0].id, profile.competencies[1].id
    plan = _build_plan(
        profile, {strong: [9.0], weak: [3.0]}, target_role="X", target_level="entry",
        interviews=2, last_at=None,
    )
    assert plan.focus_competency_id == weak
    assert plan.focus_competency_name is not None


def test_unknown_competency_ids_in_history_are_ignored() -> None:
    """History from a previous target role must not leak onto the new plan."""
    profile = _profile()
    plan = _build_plan(
        profile, {"some_old_role_competency": [9.0]}, target_role="X",
        target_level="entry", interviews=1, last_at=None,
    )
    assert plan.readiness is None
    assert all(c.score is None for c in plan.competencies)


def test_two_roles_produce_different_plans() -> None:
    nurse = _build_plan(_profile("Staff Nurse"), {}, target_role="Staff Nurse",
                        target_level="entry", interviews=0, last_at=None)
    welder = _build_plan(_profile("Welder"), {}, target_role="Welder",
                         target_level="entry", interviews=0, last_at=None)
    assert [c.name for c in nurse.competencies] != [c.name for c in welder.competencies]


# ---------------------------------------------------------------------------
# History extraction from scorecards
# ---------------------------------------------------------------------------


def _db_returning(rows: list[Any]) -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    return db


def _row(rationale: Any, when: datetime | None = None) -> MagicMock:
    r = MagicMock()
    r.rationale = rationale
    r.created_at = when or datetime(2026, 8, 1, tzinfo=UTC)
    return r


async def test_history_reads_the_nested_competency_breakdown() -> None:
    """The scorer stores _competencies as a nested object, not a JSON string."""
    rows = [
        _row({"_competencies": {"machining": {"score": 6, "name": "Machining"}}}),
        _row({"_competencies": {"machining": {"score": 8, "name": "Machining"}}}),
    ]
    history, count, last = await _competency_history(_db_returning(rows), "u-1")
    assert history == {"machining": [6.0, 8.0]}
    assert count == 2
    assert last is not None


async def test_scorecards_without_a_breakdown_still_count_as_interviews() -> None:
    """Pre-intelligence-layer scorecards contribute no competency evidence but
    are absolutely still interviews the student sat."""
    rows = [_row({"communication": "did fine"}), _row(None)]
    history, count, _ = await _competency_history(_db_returning(rows), "u-1")
    assert history == {}
    assert count == 2


async def test_malformed_breakdown_entries_are_skipped_not_fatal() -> None:
    rows = [
        _row(
            {
                "_competencies": {
                    "good": {"score": 7},
                    "bad_type": "not a dict",
                    "bad_score": {"score": "high"},
                }
            }
        )
    ]
    history, count, _ = await _competency_history(_db_returning(rows), "u-1")
    assert history == {"good": [7.0]}
    assert count == 1


async def test_no_scorecards_yields_empty_history() -> None:
    history, count, last = await _competency_history(_db_returning([]), "u-1")
    assert history == {}
    assert count == 0
    assert last is None


async def test_history_is_ordered_oldest_first_so_trend_is_meaningful() -> None:
    """Trend is last-minus-previous; reversed order would invert every arrow."""
    rows = [
        _row({"_competencies": {"c": {"score": 3}}}, datetime(2026, 7, 1, tzinfo=UTC)),
        _row({"_competencies": {"c": {"score": 9}}}, datetime(2026, 8, 1, tzinfo=UTC)),
    ]
    history, _, last = await _competency_history(_db_returning(rows), "u-1")
    assert history["c"] == [3.0, 9.0]
    assert last == datetime(2026, 8, 1, tzinfo=UTC)
