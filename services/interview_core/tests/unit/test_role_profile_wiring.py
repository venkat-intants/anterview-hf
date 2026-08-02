"""Intelligence-layer wiring tests for interview_core.

The shared engine has its own suite (shared/intelligence/tests). These cover
the seams: that the worker and the graph actually consume a role profile, and
that both degrade to their previous behaviour without one.
"""

from __future__ import annotations

import pytest
from shared.intelligence import baseline_profile, plan_interview

from app.graph.prompts import (
    render_follow_up_user_prompt,
    render_interviewer_system_prompt,
)
from app.graph.state import build_initial_state
from app.worker.interview_worker import (
    MAX_CANDIDATE_ANSWERS,
    SessionContext,
    _extract_required_skills,
    _interviewer_instructions,
)

PID = "w" * 32


def _profile(title: str):
    return baseline_profile(profile_id=PID, job_title=title)


# ---------------------------------------------------------------------------
# Worker instructions
# ---------------------------------------------------------------------------


def test_instructions_without_a_profile_keep_the_legacy_structure() -> None:
    """No profile → the exact pre-intelligence-layer prompt.

    This is the fallback every session takes when derivation fails, so it must
    stay intact.
    """
    text = _interviewer_instructions("Backend Engineer", "en")
    assert "Q1  — Ask the candidate to introduce themselves." in text
    assert "Q7–Q9 — Behavioural questions" in text
    assert "[ROLE MODEL]" not in text
    assert "[QUESTION PLAN]" not in text


def test_instructions_with_a_profile_use_the_role_driven_plan() -> None:
    text = _interviewer_instructions(
        "CNC Machine Operator", "en", role_profile=_profile("CNC Machine Operator")
    )
    assert "[ROLE MODEL]" in text
    assert "[QUESTION PLAN]" in text
    assert "Mechanical & Manufacturing" in text
    # The fixed split must be gone — it is what made every role look the same.
    assert "Q7–Q9 — Behavioural questions" not in text


def test_role_driven_plan_covers_every_enforced_turn() -> None:
    """The prompt plan must be exactly MAX_CANDIDATE_ANSWERS long.

    Shorter and the final questions are unguided; longer and the last
    competency is never reached, because the code-enforced cap fires first.
    """
    text = _interviewer_instructions(
        "Staff Nurse", "en", role_profile=_profile("Staff Nurse")
    )
    for i in range(1, MAX_CANDIDATE_ANSWERS + 1):
        assert f"Q{i} —" in text


def test_two_different_roles_produce_different_interviews() -> None:
    """The whole point: the plan must follow the role, not a fixed template."""
    nurse = _interviewer_instructions(
        "Staff Nurse", "en", role_profile=_profile("Staff Nurse")
    )
    welder = _interviewer_instructions(
        "Welder - MIG/TIG", "en", role_profile=_profile("Welder - MIG/TIG")
    )
    assert nurse != welder
    assert "Patient Safety" in nurse
    assert "Patient Safety" not in welder


def test_pii_and_no_decision_guardrails_survive_the_role_block() -> None:
    """The role model must never displace the platform-wide guardrails."""
    text = _interviewer_instructions(
        "Field Sales Executive", "en", role_profile=_profile("Field Sales Executive")
    )
    assert "Never ask for personal data" in text
    assert "Never reveal scoring or make hiring decisions." in text


def test_resume_grounding_still_applies_with_a_profile() -> None:
    text = _interviewer_instructions(
        "Welder",
        "en",
        "10 years of structural MIG welding at Larsen shipyards.",
        role_profile=_profile("Welder"),
    )
    assert "CANDIDATE BACKGROUND" in text
    assert "Ground your questions in the candidate's background" in text


# ---------------------------------------------------------------------------
# _extract_required_skills — jobs.competencies JSONB has drifted in shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("blob", "expected"),
    [
        ({"required": ["SQL", "Power BI"]}, ["SQL", "Power BI"]),
        ({"required_skills": ["Welding"]}, ["Welding"]),
        ({"must_have": ["Tally"]}, ["Tally"]),
        (["CNC", "Fanuc"], ["CNC", "Fanuc"]),
        ({"technical": ["Lathe"], "soft": ["Teamwork"]}, ["Lathe", "Teamwork"]),
        ({}, []),
        (None, []),
        ("not a container", []),
        ({"required": ["  ", "SQL"]}, ["SQL"]),
    ],
)
def test_extract_required_skills_handles_every_observed_shape(
    blob: object, expected: list[str]
) -> None:
    assert _extract_required_skills(blob) == expected


def test_session_context_defaults_are_safe() -> None:
    ctx = SessionContext()
    assert ctx.job_title == "the role"
    assert ctx.required_skills == []
    assert ctx.interview_type == "screening"


# ---------------------------------------------------------------------------
# Graph prompts
# ---------------------------------------------------------------------------


def test_system_prompt_without_a_profile_is_unchanged() -> None:
    """Backwards compatibility guard for every existing caller and test."""
    assert "[ROLE MODEL]" not in render_interviewer_system_prompt(
        job_title="Backend Engineer", language="en", max_turns=5
    )


def test_system_prompt_with_a_profile_gains_the_role_model() -> None:
    text = render_interviewer_system_prompt(
        job_title="Staff Nurse",
        language="en",
        max_turns=10,
        role_profile=_profile("Staff Nurse"),
    )
    assert "[ROLE MODEL]" in text
    assert "Healthcare & Nursing" in text


def test_follow_up_without_a_profile_keeps_the_prose_rotation() -> None:
    text = render_follow_up_user_prompt("I built APIs.", turn_count=2, max_turns=10)
    assert "ROTATE coverage across the four screening" in text


def test_follow_up_with_a_profile_names_one_competency_from_the_plan() -> None:
    """The competency is picked in code, so the prompt can name it outright."""
    profile = _profile("CNC Machine Operator")
    plans = plan_interview(profile, 10)

    text = render_follow_up_user_prompt(
        "I ran a Fanuc lathe.", turn_count=2, max_turns=10, role_profile=profile
    )
    # turn_count is completed answers, so this drives turn 3.
    expected = plans[2]
    assert expected.competency_name is not None
    assert expected.competency_name in text
    assert "I ran a Fanuc lathe." in text
    assert "ROTATE coverage across the four screening" not in text


def test_follow_up_turn_alignment_walks_the_whole_plan() -> None:
    """Every planned competency must be reachable as the interview advances."""
    profile = _profile("Field Sales Executive")
    plans = plan_interview(profile, 10)
    for turn_count in range(0, 10):
        text = render_follow_up_user_prompt(
            "ok", turn_count=turn_count, max_turns=10, role_profile=profile
        )
        plan = plans[turn_count]
        if plan.kind == "probe":
            assert plan.competency_name is not None
            assert plan.competency_name in text
        elif plan.kind == "wrap":
            assert "CLOSES the interview" in text


def test_build_initial_state_carries_the_profile() -> None:
    profile = _profile("Welder")
    state = build_initial_state(
        session_id="s1", job_id="j1", job_title="Welder", role_profile=profile
    )
    assert state["role_profile"] is profile

    # Default stays None so existing callers are untouched.
    bare = build_initial_state(session_id="s2", job_id="j1", job_title="Welder")
    assert bare["role_profile"] is None
