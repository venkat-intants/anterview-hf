"""Rendering + axis-weight tests."""

from __future__ import annotations

from shared.intelligence.archetypes import CANONICAL_AXES
from shared.intelligence.coverage import plan_interview
from shared.intelligence.render import (
    DEFAULT_AXIS_WEIGHTS,
    MIN_AXIS_WEIGHT,
    axis_weights,
    render_competency_output_spec,
    render_plan_block,
    render_role_model_block,
    render_scoring_rubric_block,
    render_turn_directive,
)
from shared.intelligence.schema import RoleProfile
from shared.intelligence.taxonomy import baseline_profile

PID = "r" * 32


def _p(title: str) -> RoleProfile:
    return baseline_profile(profile_id=PID, job_title=title)


# ---------------------------------------------------------------------------
# Axis weights
# ---------------------------------------------------------------------------


def test_no_profile_returns_the_legacy_defaults() -> None:
    """Backwards compatibility: an un-profiled session must score exactly as
    it did before the intelligence layer existed."""
    assert axis_weights(None) == DEFAULT_AXIS_WEIGHTS


def test_weights_always_sum_to_exactly_one() -> None:
    for title in [
        "CNC Machine Operator",
        "Customer Support Associate - Voice Process",
        "Staff Nurse",
        "Field Sales Executive",
        "Assistant Professor - Physics",
        "Chief Vibes Officer",
    ]:
        weights = axis_weights(_p(title))
        assert set(weights) == set(CANONICAL_AXES)
        assert sum(weights.values()) == 1.0, f"{title}: {weights}"


def test_no_axis_is_ever_squeezed_below_the_floor() -> None:
    """All four axes are charted per-axis; a near-zero weight misleads a
    human reader who sees a full-size bar for a number that barely counted."""
    for title in ["Customer Support Associate", "CNC Machine Operator", "Staff Nurse"]:
        for axis, weight in axis_weights(_p(title)).items():
            assert weight >= MIN_AXIS_WEIGHT - 1e-9, f"{title}/{axis}={weight}"


def test_a_hands_on_role_weights_technical_above_communication() -> None:
    weights = axis_weights(_p("CNC Machine Operator"))
    assert weights["technical"] > weights["communication"]


def test_a_support_role_weights_communication_above_technical() -> None:
    weights = axis_weights(_p("Customer Support Associate - Voice Process"))
    assert weights["communication"] > weights["technical"]


def test_role_actually_moves_the_weights_off_the_defaults() -> None:
    """If the blend produced the defaults for every role, the engine would be
    doing nothing to scoring."""
    machining = axis_weights(_p("CNC Machine Operator"))
    support = axis_weights(_p("Customer Support Associate - Voice Process"))
    assert machining != support
    assert machining != DEFAULT_AXIS_WEIGHTS


# ---------------------------------------------------------------------------
# Prompt blocks
# ---------------------------------------------------------------------------


def test_role_model_block_carries_the_role_not_a_generic_one() -> None:
    block = render_role_model_block(_p("Staff Nurse (GNM)"))
    assert "[ROLE MODEL]" in block
    assert "Healthcare & Nursing" in block
    assert "Patient Safety" in block
    # The bug this replaces: software vocabulary leaking into a clinical role.
    assert "algorithm" not in block.lower()
    assert "debugging" not in block.lower()


def test_role_model_block_includes_avoid_topics_when_present() -> None:
    profile = _p("Welder")
    profile.avoid_topics = ["Union membership"]
    assert "Union membership" in render_role_model_block(profile)


def test_plan_block_lists_every_turn_exactly_once() -> None:
    plans = plan_interview(_p("Site Engineer - Civil"), 10)
    block = render_plan_block(plans)
    for i in range(1, 11):
        assert f"Q{i} —" in block
    assert "do not read the plan aloud" in block.lower()


def test_turn_directive_names_one_competency() -> None:
    plans = plan_interview(_p("Field Sales Executive"), 10)
    probe = next(p for p in plans if p.kind == "probe")
    directive = render_turn_directive(probe, turn_index=probe.turn_index, max_turns=10)
    assert probe.competency_name is not None
    assert probe.competency_name in directive
    assert "do NOT announce the competency" in directive


def test_intro_and_wrap_directives_do_not_open_a_topic() -> None:
    plans = plan_interview(_p("Welder"), 10)
    intro = render_turn_directive(plans[0], turn_index=1, max_turns=10)
    wrap = render_turn_directive(plans[-1], turn_index=10, max_turns=10)
    assert "OPENING" in intro
    assert "CLOSES" in wrap
    assert "new assessment topic" in wrap


def test_scoring_rubric_states_what_technical_means_for_this_role() -> None:
    block = render_scoring_rubric_block(_p("Staff Nurse (GNM)"))
    assert "never a generic or software-flavoured notion" in block
    assert "weak (0-3)" in block
    assert "Clinical Knowledge" in block
    # Advisory-only guarantee must be stated wherever red flags could appear.
    assert "do NOT compute a composite yourself" in block


def test_red_flags_are_framed_as_observations_never_rejection() -> None:
    profile = _p("Welder")
    profile.red_flags = ["Cannot name a single instrument they have used."]
    block = render_scoring_rubric_block(profile)
    assert "Never treat them as grounds for rejection" in block
    assert "you do not make hiring decisions" in block


def test_competency_output_spec_names_every_competency_id() -> None:
    profile = _p("Data Analyst")
    spec = render_competency_output_spec(profile)
    for cid in profile.competency_ids:
        assert f'"{cid}"' in spec
    assert "do not invent support for it" in spec
