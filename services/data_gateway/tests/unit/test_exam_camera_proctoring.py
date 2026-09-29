"""Camera proctoring for exams — DB-free coverage.

Real-Postgres coverage (the consent gate end to end, the DB CHECK on
event_type, the frozen ``camera_in_use``/allowance triggers, the accommodation
relaxation, and the HR proctoring read) lives in
``tests/integration/test_exam_camera_proctoring_http.py``. This file is
everything that does not need a database: the weighted score, the event
vocabulary, the structural "no frame ever" guarantee (at both the pure
function and the full ``IntegrityEventIn`` schema), and the structural
guardrail that camera proctoring never reaches a hiring decision.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from app import exam_camera
from app.routers.exam_take import IntegrityEventIn


# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------
def test_known_event_types_are_exactly_the_five_in_the_contract() -> None:
    assert {
        "fullscreen_exit", "tab_blur", "face_absent", "multiple_faces", "gaze_away",
    } == exam_camera.KNOWN_EVENT_TYPES
    assert {"face_absent", "multiple_faces", "gaze_away"} == exam_camera.RANGED_EVENT_TYPES
    assert exam_camera.CAMERA_EVENT_TYPES == exam_camera.RANGED_EVENT_TYPES


def test_gaze_away_never_counts_as_a_violation() -> None:
    """The contract: gaze_away is context for a human, never evidence strong
    enough to force an auto-submit on its own — for EVERY candidate, not only
    an accommodated one (the relaxation freezes max_violations to None
    separately; this is the unconditional floor beneath that)."""
    assert "gaze_away" not in exam_camera.VIOLATION_EVENT_TYPES
    assert {
        "fullscreen_exit", "tab_blur", "face_absent", "multiple_faces",
    } == exam_camera.VIOLATION_EVENT_TYPES


# ---------------------------------------------------------------------------
# Severity, replacing the flat 100 - 15 × violations
# ---------------------------------------------------------------------------
def test_severity_weights_match_the_contract_defaults() -> None:
    weights = exam_camera.severity_weights()
    assert weights == {
        "multiple_faces": 25,
        "face_absent": 20,
        "fullscreen_exit": 15,
        "tab_blur": 15,
        "gaze_away": 5,
    }
    # gaze_away must stay the LOWEST weight — pinned so a future "improvement"
    # that raises it is a deliberate, reviewed change, not an accident.
    assert weights["gaze_away"] == min(weights.values())


def test_score_from_counts_weights_each_type_independently() -> None:
    assert exam_camera.score_from_counts({}) == 100
    # One multiple_faces (25) costs more than one gaze_away (5).
    assert exam_camera.score_from_counts({"multiple_faces": 1}) == 75
    assert exam_camera.score_from_counts({"gaze_away": 1}) == 95
    # Mixed counts sum.
    assert exam_camera.score_from_counts({"tab_blur": 1, "face_absent": 1}) == 100 - 15 - 20
    # Floored at 0 — a runaway client can never push the score negative.
    assert exam_camera.score_from_counts({"multiple_faces": 10}) == 0
    assert exam_camera.score_from_counts({"gaze_away": 1000}) == 0
    # An unrecognised type (should never reach here — the ingest endpoint
    # rejects it) contributes no penalty rather than raising.
    assert exam_camera.score_from_counts({"unknown_type": 5}) == 100


def test_score_from_counts_is_monotonic_non_increasing_per_type() -> None:
    scores = [exam_camera.score_from_counts({"tab_blur": i}) for i in range(8)]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# No frame, image or landmark array — ever (the pure function)
# ---------------------------------------------------------------------------
def test_metadata_shape_accepts_small_primitive_payloads() -> None:
    assert exam_camera.check_metadata_shape(None) is None
    assert exam_camera.check_metadata_shape({"confidence": 0.82, "ok": True}) == {
        "confidence": 0.82, "ok": True,
    }


def test_metadata_shape_rejects_a_landmark_array() -> None:
    # A MediaPipe landmark array is exactly this shape: a list of coordinates.
    # Deliberately SMALL (well under the byte cap) so this isolates the
    # structural list/dict rejection from the separate oversized-payload
    # check below — a full 468-point array would be caught by either guard,
    # which would let this test pass even if the structural one were removed.
    landmarks = [{"x": 0.1, "y": 0.2, "z": 0.0} for _ in range(5)]
    with pytest.raises(ValueError, match="landmark"):
        exam_camera.check_metadata_shape({"landmarks": landmarks})


def test_metadata_shape_rejects_a_full_size_landmark_array() -> None:
    # The realistic MediaPipe FaceLandmarker shape (468 points) — large enough
    # that it would also blow the byte cap, so this is belt-and-braces on top
    # of the isolated case above, not a replacement for it.
    landmarks = [{"x": 0.1, "y": 0.2, "z": 0.0} for _ in range(468)]
    with pytest.raises(ValueError):
        exam_camera.check_metadata_shape({"landmarks": landmarks})


def test_metadata_shape_rejects_any_list_or_dict_value() -> None:
    with pytest.raises(ValueError, match="arrays and objects"):
        exam_camera.check_metadata_shape({"points": [1, 2, 3]})
    with pytest.raises(ValueError, match="arrays and objects"):
        exam_camera.check_metadata_shape({"nested": {"a": 1}})


def test_metadata_shape_rejects_a_base64_looking_frame() -> None:
    # A single-frame JPEG/PNG data URL is thousands of bytes even tiny; a
    # frame-sized string blows either the per-string cap or the payload cap.
    huge_string = "a" * (exam_camera.METADATA_MAX_STRING_LEN + 1)
    with pytest.raises(ValueError):
        exam_camera.check_metadata_shape({"frame": huge_string})


def test_metadata_shape_rejects_oversized_payload() -> None:
    # Each string is well under METADATA_MAX_STRING_LEN on its own (so the
    # per-string cap never fires), but MANY_KEYS × 150 chars blows the overall
    # byte cap — the check this test is isolating.
    many_small_strings = {f"k{i}": "x" * 150 for i in range(exam_camera.METADATA_MAX_KEYS)}
    with pytest.raises(ValueError, match="too large"):
        exam_camera.check_metadata_shape(many_small_strings)


def test_metadata_shape_rejects_too_many_keys() -> None:
    with pytest.raises(ValueError, match="too many keys"):
        exam_camera.check_metadata_shape({f"k{i}": i for i in range(exam_camera.METADATA_MAX_KEYS + 1)})


# ---------------------------------------------------------------------------
# No frame, image or landmark array — ever, at the FULL schema (what actually
# guards the wire). In the spirit of the corpus tests that assert chunk
# content never reaches a SELECT list: this is the request body itself, not
# just the pure helper.
# ---------------------------------------------------------------------------
_VALID_ATTEMPT_ID = "11111111-1111-1111-1111-111111111111"


def test_ingest_schema_rejects_a_landmark_array_payload() -> None:
    landmarks = [[0.1, 0.2, 0.0] for _ in range(468)]
    with pytest.raises(ValidationError):
        IntegrityEventIn(
            attempt_id=_VALID_ATTEMPT_ID, event_type="gaze_away",
            started_at="2026-09-29T00:00:00Z", ended_at="2026-09-29T00:00:05Z",
            metadata={"landmarks": landmarks},
        )


def test_ingest_schema_rejects_an_unexpected_top_level_frame_field() -> None:
    """extra='forbid': a client that tries to smuggle a frame in as a sibling
    field of the declared ones (not inside metadata at all) is refused, not
    silently ignored."""
    with pytest.raises(ValidationError):
        IntegrityEventIn.model_validate(
            {
                "attempt_id": _VALID_ATTEMPT_ID, "event_type": "tab_blur",
                "frame": "data:image/png;base64,AAAA",
            }
        )


def test_ingest_schema_rejects_unknown_event_type() -> None:
    with pytest.raises(ValidationError):
        IntegrityEventIn(attempt_id=_VALID_ATTEMPT_ID, event_type="copy")
    with pytest.raises(ValidationError):
        IntegrityEventIn(attempt_id=_VALID_ATTEMPT_ID, event_type="")


def test_ingest_schema_requires_ended_at_for_a_ranged_event() -> None:
    with pytest.raises(ValidationError, match="ranged event"):
        IntegrityEventIn(
            attempt_id=_VALID_ATTEMPT_ID, event_type="face_absent",
            started_at="2026-09-29T00:00:00Z",
        )


def test_ingest_schema_accepts_a_well_formed_ranged_event() -> None:
    ev = IntegrityEventIn(
        attempt_id=_VALID_ATTEMPT_ID, event_type="multiple_faces",
        started_at="2026-09-29T00:00:00Z", ended_at="2026-09-29T00:00:06Z",
        metadata={"faces": 2},
    )
    assert ev.event_type == "multiple_faces"


def test_ingest_schema_accepts_an_instantaneous_event_without_ended_at() -> None:
    ev = IntegrityEventIn(attempt_id=_VALID_ATTEMPT_ID, event_type="tab_blur")
    assert ev.ended_at is None


# ---------------------------------------------------------------------------
# Structural guardrail: proctoring never reaches a hiring decision
# (CLAUDE.md hard constraint 9). Auto-submitting the EXAM is fine — that is
# `exam_take._grade_and_finalize`'s job, called from `/exam/submit`, never
# from the proctoring path below.
# ---------------------------------------------------------------------------
_HIRING_DECISION_IDENTIFIERS = (
    "record_result", "advance_applicant_to_interview", "enrolment_awaiting_exam_round",
    "record_transition", "record_final_decision", "stage_transitions",
)


def test_camera_proctoring_never_touches_a_hiring_decision() -> None:
    import app.routers.exam_take as et

    funcs = (
        exam_camera.record_camera_consent,
        exam_camera.has_active_camera_consent,
        exam_camera.score_from_counts,
        exam_camera.check_metadata_shape,
        et.ingest_integrity_event,
        et.record_camera_consent,
    )
    src = "\n".join(inspect.getsource(f) for f in funcs)
    for identifier in _HIRING_DECISION_IDENTIFIERS:
        assert identifier not in src, identifier


def test_camera_proctoring_path_never_special_cases_coding_sections() -> None:
    """'Coding is a SECTION KIND, not a separate flow' (the contract's load-
    bearing fact). Proctoring attaches to the ATTEMPT, so nothing in this
    module or the ingest/consent endpoints should branch on section kind."""
    import app.routers.exam_take as et

    funcs = (
        exam_camera.record_camera_consent,
        exam_camera.has_active_camera_consent,
        et.ingest_integrity_event,
        et.record_camera_consent,
        et.start_attempt,
    )
    src = "\n".join(inspect.getsource(f) for f in funcs)
    assert "kind ==" not in src.replace(" ", "")
    assert "'coding'" not in src
    assert '"coding"' not in src
