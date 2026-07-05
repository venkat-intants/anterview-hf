"""Unit tests for the proctoring integrity-scoring logic (app/proctoring.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.proctoring import KNOWN_EVENT_TYPES, compute_integrity

_T0 = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


def _ranged(event_type: str, seconds: float) -> dict:
    return {
        "event_type": event_type,
        "started_at": _T0,
        "ended_at": _T0 + timedelta(seconds=seconds),
    }


def _instant(event_type: str) -> dict:
    return {"event_type": event_type, "started_at": _T0, "ended_at": None}


def test_no_events_is_perfect_score() -> None:
    score, summary = compute_integrity([])
    assert score == 100
    assert summary["total_events"] == 0
    assert summary["total_flagged_seconds"] == 0
    assert summary["by_type"] == {}


def test_gaze_away_penalised_per_second() -> None:
    # 10s gaze_away * 0.5/s = 5 penalty → 95.
    score, summary = compute_integrity([_ranged("gaze_away", 10)])
    assert score == 95
    assert summary["by_type"]["gaze_away"] == 1
    assert summary["flagged_seconds"]["gaze_away"] == 10.0
    assert summary["total_flagged_seconds"] == 10.0


def test_face_absent_heavier_than_gaze() -> None:
    # 10s face_absent * 1.5/s = 15 → 85.
    score, _ = compute_integrity([_ranged("face_absent", 10)])
    assert score == 85


def test_instantaneous_events_penalised_per_occurrence() -> None:
    # tab_blur(3) + paste(4) = 7 → 93.
    score, summary = compute_integrity([_instant("tab_blur"), _instant("paste")])
    assert score == 93
    assert summary["by_type"]["tab_blur"] == 1
    assert summary["by_type"]["paste"] == 1


def test_score_clamped_at_zero() -> None:
    # 100s multiple_faces * 2/s = 200 penalty → clamps to 0, not negative.
    score, _ = compute_integrity([_ranged("multiple_faces", 100)])
    assert score == 0


def test_unknown_event_counted_but_no_penalty() -> None:
    score, summary = compute_integrity([_instant("sneezed")])
    assert score == 100  # no penalty for unknown type
    assert summary["by_type"]["sneezed"] == 1
    assert summary["total_events"] == 1


def test_missing_ended_at_contributes_zero_duration() -> None:
    # gaze_away with no ended_at → 0 seconds → no penalty.
    score, summary = compute_integrity(
        [{"event_type": "gaze_away", "started_at": _T0, "ended_at": None}]
    )
    assert score == 100
    assert summary["flagged_seconds"].get("gaze_away", 0) == 0


def test_mixed_session() -> None:
    events = [
        _ranged("gaze_away", 4),       # 2.0
        _ranged("face_absent", 2),     # 3.0
        _instant("tab_blur"),          # 3.0
        _instant("tab_blur"),          # 3.0
        _instant("fullscreen_exit"),   # 2.0
    ]
    score, summary = compute_integrity(events)
    # total penalty = 2 + 3 + 3 + 3 + 2 = 13 → 87
    assert score == 87
    assert summary["by_type"]["tab_blur"] == 2
    assert summary["total_events"] == 5


def test_absurd_duration_is_clamped() -> None:
    """A buggy/forged multi-year duration is clamped, not allowed to dominate.

    Regression test for the performance.now()-vs-Date.now() unit bug that once
    produced a ~169-year gaze_away event. Even a 1-year duration must clamp to
    the 900s cap → 900*0.5 = 450 penalty → score floors at 0, never negative,
    and the flagged_seconds reflects the clamp, not the bogus value.
    """
    one_year_s = 365 * 24 * 3600
    score, summary = compute_integrity([_ranged("gaze_away", one_year_s)])
    assert score == 0
    assert summary["flagged_seconds"]["gaze_away"] == 900.0


def test_known_event_types_nonempty() -> None:
    assert "gaze_away" in KNOWN_EVENT_TYPES
    assert "tab_blur" in KNOWN_EVENT_TYPES
    assert "multiple_faces" in KNOWN_EVENT_TYPES
