"""Intelligence-layer wiring tests for the scorer.

Covers the seam only — the engine itself is tested in shared/intelligence/tests.
The load-bearing property here is that a session WITHOUT a role profile scores
exactly as it did before the intelligence layer, because that is the path every
legacy session and every failed derivation takes.
"""

from __future__ import annotations

import json

from shared.intelligence import DEFAULT_AXIS_WEIGHTS, axis_weights, baseline_profile

from app.scorer import (
    _WEIGHTS,
    _compute_composite,
    _extract_competency_breakdown,
    _render_prompt,
    _splice_role_context,
)

PID = "s" * 32


def _profile(title: str):
    return baseline_profile(profile_id=PID, job_title=title)


def _rendered() -> str:
    return _render_prompt(
        job_title="Welder",
        experience_level="entry",
        lang_name="English",
        turns=[{"role": "ai", "text": "Hello"}, {"role": "user", "text": "Hi"}],
    )


# ---------------------------------------------------------------------------
# Composite weighting
# ---------------------------------------------------------------------------


def test_composite_without_weights_is_the_legacy_formula() -> None:
    scores = {"communication": 8, "technical": 6, "problem_solving": 7, "confidence": 5}
    expected = round(sum(_WEIGHTS[k] * scores[k] for k in _WEIGHTS), 2)
    assert _compute_composite(scores) == expected
    assert _compute_composite(scores, DEFAULT_AXIS_WEIGHTS) == expected


def test_a_hands_on_role_reweights_the_composite_towards_technical() -> None:
    """Same axis scores, different role → different composite.

    A strong-technical / weak-communication candidate should score better for a
    machining role than for a support role, and previously scored identically.
    """
    scores = {"communication": 3, "technical": 9, "problem_solving": 8, "confidence": 6}

    machining = _compute_composite(scores, axis_weights(_profile("CNC Machine Operator")))
    support = _compute_composite(
        scores, axis_weights(_profile("Customer Support Associate - Voice Process"))
    )

    assert machining > support
    # Both must remain on the same 0-10 scale as every other scorecard, or the
    # analytics dashboard is comparing incomparable numbers.
    assert 0.0 <= support <= 10.0
    assert 0.0 <= machining <= 10.0


def test_composite_stays_on_scale_for_every_role() -> None:
    perfect = dict.fromkeys(_WEIGHTS, 10)
    zero = dict.fromkeys(_WEIGHTS, 0)
    for title in ["Welder", "Staff Nurse", "Data Analyst", "Chief Vibes Officer"]:
        weights = axis_weights(_profile(title))
        assert _compute_composite(perfect, weights) == 10.0
        assert _compute_composite(zero, weights) == 0.0


# ---------------------------------------------------------------------------
# Prompt splicing
# ---------------------------------------------------------------------------


def test_role_context_is_spliced_above_the_transcript() -> None:
    """Below the transcript it reads as commentary, not as instructions."""
    spliced = _splice_role_context(_rendered(), "## ROLE BLOCK")
    assert spliced.index("## ROLE BLOCK") < spliced.index("## Transcript")


def test_splice_falls_back_to_appending_if_the_marker_moves() -> None:
    """A template edit must not silently drop the rubric."""
    assert "## ROLE BLOCK" in _splice_role_context("no marker here", "## ROLE BLOCK")


def test_multiple_splices_all_land_above_the_transcript() -> None:
    text = _splice_role_context(_rendered(), "## FIRST")
    text = _splice_role_context(text, "## SECOND")
    idx = text.index("## Transcript")
    assert text.index("## FIRST") < idx
    assert text.index("## SECOND") < idx


# ---------------------------------------------------------------------------
# Per-competency breakdown
# ---------------------------------------------------------------------------


def test_breakdown_keeps_only_competencies_that_are_in_the_profile() -> None:
    """The model occasionally invents a competency id; it must not be stored."""
    profile = _profile("Welder")
    real = profile.competencies[0].id
    raw = {
        real: {"score": 7, "evidence": "Described a full setup."},
        "invented_competency": {"score": 9, "evidence": "Not in the profile."},
    }
    breakdown = _extract_competency_breakdown(raw, profile)
    assert set(breakdown) == {real}
    assert breakdown[real]["score"] == 7
    assert breakdown[real]["name"] == profile.competencies[0].name


def test_breakdown_skips_malformed_entries_instead_of_failing() -> None:
    """Supplementary evidence must never cost the candidate their scorecard."""
    profile = _profile("Welder")
    ids = profile.competency_ids
    raw = {
        ids[0]: {"score": "not a number"},
        ids[1]: "not a dict",
        ids[2]: {"score": 6, "evidence": "ok"},
    }
    breakdown = _extract_competency_breakdown(raw, profile)
    assert set(breakdown) == {ids[2]}


def test_breakdown_clamps_out_of_range_scores() -> None:
    profile = _profile("Welder")
    cid = profile.competency_ids[0]
    breakdown = _extract_competency_breakdown({cid: {"score": 99}}, profile)
    assert breakdown[cid]["score"] == 10


def test_breakdown_is_json_serialisable_for_the_jsonb_column() -> None:
    """It is stored inside the existing rationale JSONB — must round-trip."""
    profile = _profile("Data Analyst")
    raw = {c.id: {"score": 6, "evidence": "x"} for c in profile.competencies}
    breakdown = _extract_competency_breakdown(raw, profile)
    assert json.loads(json.dumps(breakdown)) == breakdown
