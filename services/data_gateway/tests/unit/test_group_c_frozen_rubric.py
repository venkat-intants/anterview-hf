"""Unit tests for C8 — rebuilding a frozen rubric into a RoleProfile.

The property under test throughout: a published round's rubric must be
reconstructable from what the round stored, without deriving anything. If this
module ever needs a network call or a taxonomy lookup, the freeze has leaked.
"""

from __future__ import annotations

import pytest
from shared.intelligence import (
    FrozenRubricError,
    composite_from_criteria,
    has_frozen_anchors,
    profile_from_round_criteria,
    renormalise,
)

FULL = [
    {
        "competency_id": "python", "competency_name": "Python proficiency",
        "competency_kind": "technical", "weight": 0.30,
        "anchors": {"low": "cannot write a loop", "mid": "writes working scripts",
                    "high": "idiomatic and tested"},
        "probes": ["walk me through something you built"],
    },
    {
        "competency_id": "solving", "competency_name": "Problem solving",
        "competency_kind": "technical", "weight": 0.25,
        "anchors": {"low": "needs the answer", "mid": "gets there with hints",
                    "high": "decomposes unprompted"},
        "probes": ["describe a bug that took you a while"],
    },
    {
        "competency_id": "comms", "competency_name": "Communication",
        "competency_kind": "communication", "weight": 0.15,
        "anchors": {"low": "hard to follow", "mid": "clear enough",
                    "high": "explains to a non-expert"},
        "probes": ["explain a technical decision to a non-engineer"],
    },
]


def _build(criteria, **kw):
    return profile_from_round_criteria(
        criteria, job_title=kw.pop("job_title", "Python Developer"),
        round_title=kw.pop("round_title", "AI Interview"),
        workflow_id=kw.pop("workflow_id", "wf1"),
        round_id=kw.pop("round_id", "r3"), **kw,
    )


# ===========================================================================
# renormalise
# ===========================================================================
def test_weights_are_renormalised_to_the_rounds_own_scale() -> None:
    """A round assessing three of six competencies is scored out of those three.
    Leaving role-wide weights would silently halve every score it produces."""
    out = renormalise([0.30, 0.25, 0.15])
    assert pytest.approx(sum(out)) == 1.0
    # Proportions are preserved.
    assert out[0] > out[1] > out[2]
    assert pytest.approx(out[0] / out[1], rel=1e-6) == 0.30 / 0.25


def test_renormalise_survives_zero_weights() -> None:
    """Only reachable with corrupt data; an equal split is the least wrong
    answer and beats dividing by zero."""
    assert renormalise([0.0, 0.0]) == [0.5, 0.5]
    assert renormalise([]) == []


def test_single_criterion_gets_full_weight() -> None:
    assert renormalise([0.07]) == [1.0]


# ===========================================================================
# profile_from_round_criteria
# ===========================================================================
def test_rebuilds_a_profile_from_stored_criteria_only() -> None:
    p = _build(FULL)
    assert [c.id for c in p.competencies] == ["python", "solving", "comms"]
    assert pytest.approx(sum(c.weight for c in p.competencies)) == 1.0


def test_profile_id_names_its_provenance() -> None:
    """An audit of a stored scorecard asks where the rubric came from, not what
    hash the inputs had."""
    p = _build(FULL, workflow_id="wf-abc", round_id="rnd-9")
    assert p.profile_id == "frozen:wf-abc:rnd-9"


def test_real_anchors_survive_the_round_trip() -> None:
    p = _build(FULL)
    python = next(c for c in p.competencies if c.id == "python")
    assert python.anchors.high == "idiomatic and tested"
    assert python.probes == ["walk me through something you built"]


def test_missing_anchors_fall_back_generically_rather_than_being_invented() -> None:
    """Inventing role-specific band text nobody wrote would be worse than
    admitting we do not have it — a scorer would treat the invention as the
    employer's own standard."""
    bare = [{**c, "anchors": None, "probes": None} for c in FULL]
    p = _build(bare)
    assert all(c.anchors.mid for c in p.competencies)
    assert "roughly the level the role expects" in p.competencies[0].anchors.mid
    assert all(c.probes for c in p.competencies)


def test_has_frozen_anchors_distinguishes_the_two() -> None:
    assert has_frozen_anchors(FULL) is True
    assert has_frozen_anchors([{**c, "anchors": None} for c in FULL]) is False
    assert has_frozen_anchors([]) is False


def test_an_unrecognised_kind_is_coerced_not_fatal() -> None:
    """competency_kind is free text in the database. One stale value from an
    older taxonomy must not break scoring for an entire round months later."""
    odd = [{**FULL[0], "competency_kind": "cognitive"}, FULL[1], FULL[2]]
    p = _build(odd)
    assert p.competencies[0].kind == "technical"


def test_a_null_kind_is_coerced() -> None:
    odd = [{**FULL[0], "competency_kind": None}, FULL[1], FULL[2]]
    assert _build(odd).competencies[0].kind == "technical"


def test_too_few_criteria_refuses_rather_than_padding() -> None:
    """Padding would fabricate competencies. Refusing lets the caller fall back
    to whole-role scoring instead of grading against a distorted rubric."""
    with pytest.raises(FrozenRubricError, match="at least"):
        _build(FULL[:2])


def test_no_criteria_refuses() -> None:
    with pytest.raises(FrozenRubricError, match="no criteria"):
        _build([])


def test_more_criteria_than_the_schema_allows_keeps_the_heaviest() -> None:
    many = [
        {"competency_id": f"c{i}", "competency_name": f"C{i}",
         "competency_kind": "technical", "weight": (i + 1) / 100}
        for i in range(12)
    ]
    p = _build(many)
    assert len(p.competencies) <= 8
    # The heaviest survived; the lightest were dropped, which moves the score least.
    assert "c11" in {c.id for c in p.competencies}
    assert "c0" not in {c.id for c in p.competencies}


def test_rebuild_is_deterministic() -> None:
    """Same stored rubric, same profile — every time, with no derivation."""
    a, b = _build(FULL), _build(FULL)
    assert a.profile_id == b.profile_id
    assert [(c.id, c.weight, c.anchors.high) for c in a.competencies] == [
        (c.id, c.weight, c.anchors.high) for c in b.competencies
    ]


def test_rebuilt_profile_drives_the_existing_consumers() -> None:
    """The whole point of returning a RoleProfile: everything downstream —
    turn planning, the rubric block, axis weights — works unchanged."""
    from shared.intelligence import axis_weights, plan_interview

    p = _build(FULL)
    plans = plan_interview(p, 10)
    assert len(plans) == 10
    probed = {t.competency_id for t in plans if t.competency_id}
    assert probed <= {"python", "solving", "comms"}
    weights = axis_weights(p)
    assert pytest.approx(sum(weights.values())) == 1.0


# ===========================================================================
# The two-layer split — C8 / D-02
# ===========================================================================
def test_criteria_composite_is_a_weighted_mean() -> None:
    breakdown = {
        "python": {"score": 8, "weight": 0.5},
        "solving": {"score": 6, "weight": 0.3},
        "comms": {"score": 4, "weight": 0.2},
    }
    # 8*.5 + 6*.3 + 4*.2 = 4 + 1.8 + 0.8 = 6.6
    assert composite_from_criteria(breakdown) == 6.6


def test_criteria_composite_handles_an_empty_breakdown() -> None:
    assert composite_from_criteria({}) is None
    assert composite_from_criteria({"a": {"score": 5, "weight": 0}}) is None
