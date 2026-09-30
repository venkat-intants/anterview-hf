"""Camera proctoring for exams — DB-free coverage.

Real-Postgres coverage (the consent gate end to end, the DB CHECK on
event_type, the frozen ``camera_in_use``/allowance triggers, the accommodation
relaxation, and the HR proctoring read) lives in
``tests/integration/test_exam_camera_proctoring_http.py``. This file is
everything that does not need a database: the weighted score, the event
vocabulary (including the restored ``copy``/``paste`` types — code review FIX
1), the structural "no metadata, ever" guarantee at the schema level (code
review FIX 3), and the structural guardrail that camera proctoring never
reaches a hiring decision.
"""

from __future__ import annotations

import inspect
import re

import pytest
from pydantic import ValidationError

from app import exam_camera
from app.models import ExamIntegrityEvent
from app.routers.exam_take import IntegrityEventIn


# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------
def test_known_event_types_include_copy_and_paste_alongside_the_five_contract_types() -> None:
    """copy/paste were restored (code review FIX 1, 2026-09-29): they existed
    before the camera-proctoring branch (an unconstrained event_type accepted
    and stored them, though never scored and never a violation) and briefly
    422'd after the branch's first pass tightened the vocabulary without
    checking what the client already sent."""
    assert {
        "fullscreen_exit", "tab_blur", "face_absent", "multiple_faces", "gaze_away",
        "copy", "paste",
    } == exam_camera.KNOWN_EVENT_TYPES
    assert {"face_absent", "multiple_faces", "gaze_away"} == exam_camera.RANGED_EVENT_TYPES
    assert exam_camera.CAMERA_EVENT_TYPES == exam_camera.RANGED_EVENT_TYPES
    # copy/paste are instantaneous (no ended_at), like fullscreen_exit/tab_blur —
    # never ranged.
    assert "copy" in exam_camera.INSTANT_EVENT_TYPES
    assert "paste" in exam_camera.INSTANT_EVENT_TYPES
    assert "copy" not in exam_camera.RANGED_EVENT_TYPES
    assert "paste" not in exam_camera.RANGED_EVENT_TYPES


def test_the_orm_model_declares_the_same_vocabulary_the_db_check_enforces() -> None:
    """The follow-up review's second MUST FIX, pinned so it cannot drift again.

    ``models.ExamIntegrityEvent``'s own ``CheckConstraint`` declaration went on
    naming the pre-fix FIVE types after migration ``f6b8d0a2c4e6`` had widened
    the live constraint to seven. Nothing in CI runs ``alembic check`` or
    ``create_all()``, so it was inert — but it is a loaded gun for the next
    ``alembic revision --autogenerate`` on this table, which diffs the MODEL
    against the DB and would have happily proposed narrowing the constraint
    back, silently reintroducing the copy/paste rejection this branch fixed.

    Asserting model-against-``exam_camera`` rather than model-against-migration
    keeps one source of truth: the vocabulary the application actually accepts.
    """
    constraint = next(
        c
        for c in ExamIntegrityEvent.__table__.constraints
        if getattr(c, "name", None) == "ck_exam_integrity_events_event_type"
    )
    declared = set(re.findall(r"'([a-z_]+)'", str(constraint.sqltext)))
    assert declared == set(exam_camera.KNOWN_EVENT_TYPES)


def test_gaze_away_never_counts_as_a_violation() -> None:
    """The contract: gaze_away is context for a human, never evidence strong
    enough to force an auto-submit on its own — for EVERY candidate, not only
    an accommodated one (the relaxation freezes max_violations to None
    separately; this is the unconditional floor beneath that)."""
    assert "gaze_away" not in exam_camera.VIOLATION_EVENT_TYPES
    assert {
        "fullscreen_exit", "tab_blur", "face_absent", "multiple_faces",
    } == exam_camera.VIOLATION_EVENT_TYPES


def test_copy_and_paste_do_not_count_as_violations_either() -> None:
    """Restoring copy/paste (code review FIX 1) gives them a severity weight
    (they now cost real score, which they never did before) but does NOT
    silently promote them to an auto-submit trigger they never were: before
    this branch the pre-existing ingest accepted and stored them but never
    scored or counted them as a violation. A bare copy or paste has too many
    innocent explanations on its own (reviewing your own answer, searching an
    unfamiliar term, pasting a candidate ID) to force an auto-submit
    unaccompanied by a stronger signal."""
    assert "copy" not in exam_camera.VIOLATION_EVENT_TYPES
    assert "paste" not in exam_camera.VIOLATION_EVENT_TYPES


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
        "paste": 10,
        "copy": 5,
        "gaze_away": 5,
    }
    # gaze_away must stay AT the lowest weight (tied with copy is fine — they
    # are both weak solo signals for different reasons) — pinned so a future
    # "improvement" that raises it above copy/paste is a deliberate, reviewed
    # change, not an accident.
    assert weights["gaze_away"] == min(weights.values())
    # The ordering the code review reasoned about explicitly: a paste (brings
    # outside content IN) outweighs a copy (takes content OUT), but neither
    # comes close to another face on camera, and paste stays below the two
    # browser-integrity signals (pasting alone is not evidence of leaving the
    # exam environment the way tabbing away or exiting fullscreen is).
    assert weights["copy"] < weights["paste"] < weights["fullscreen_exit"]
    assert weights["paste"] < weights["multiple_faces"]
    assert weights["copy"] < weights["multiple_faces"]


def test_score_from_counts_weights_each_type_independently() -> None:
    assert exam_camera.score_from_counts({}) == 100
    # One multiple_faces (25) costs more than one gaze_away (5).
    assert exam_camera.score_from_counts({"multiple_faces": 1}) == 75
    assert exam_camera.score_from_counts({"gaze_away": 1}) == 95
    # copy (5) and paste (10) now cost real score — restored, not merely
    # accepted-and-ignored the way they were before this branch.
    assert exam_camera.score_from_counts({"copy": 1}) == 95
    assert exam_camera.score_from_counts({"paste": 1}) == 90
    # Mixed counts sum.
    assert exam_camera.score_from_counts({"tab_blur": 1, "face_absent": 1}) == 100 - 15 - 20
    assert exam_camera.score_from_counts({"copy": 1, "paste": 1}) == 100 - 5 - 10
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
# No metadata, ever (code review FIX 3, 2026-09-29) — the FULL schema is the
# guard now, not a shape-checker on a field that should never have existed.
# In the spirit of the corpus tests that assert chunk content never reaches a
# SELECT list: this is the request body itself, not a helper function.
# ---------------------------------------------------------------------------
_VALID_ATTEMPT_ID = "11111111-1111-1111-1111-111111111111"


def test_integrity_event_in_has_no_metadata_field_at_all() -> None:
    """The structural guarantee, checked directly against the model: there is
    no field named ``metadata`` to populate, benign-looking or not."""
    assert "metadata" not in IntegrityEventIn.model_fields


def test_ingest_schema_rejects_any_metadata_field_however_innocuous() -> None:
    """Before this fix, a shape-checker let through anything that LOOKED
    innocuous — a name, a phone number, a health detail all pass a
    "no lists, no dicts, short strings" check. Removing the field entirely
    (extra='forbid') refuses ALL of them, not just the ones that look
    dangerous."""
    with pytest.raises(ValidationError):
        IntegrityEventIn.model_validate(
            {
                "attempt_id": _VALID_ATTEMPT_ID, "event_type": "tab_blur",
                "metadata": {"confidence": 0.82, "ok": True},
            }
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
        IntegrityEventIn(attempt_id=_VALID_ATTEMPT_ID, event_type="screenshot")
    with pytest.raises(ValidationError):
        IntegrityEventIn(attempt_id=_VALID_ATTEMPT_ID, event_type="")


def test_ingest_schema_accepts_the_restored_copy_and_paste_types() -> None:
    """The regression this branch's FIX 1 closes: copy/paste must validate,
    not 422, and — being instantaneous — need no ended_at."""
    for event_type in ("copy", "paste"):
        ev = IntegrityEventIn(attempt_id=_VALID_ATTEMPT_ID, event_type=event_type)
        assert ev.event_type == event_type
        assert ev.ended_at is None


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
    # Both sides stripped. This compared "kind ==" — with a space — against a
    # string every space had just been removed from, so it could not fail
    # whatever the source said, and reported a guarantee it never checked.
    flat = src.replace(" ", "")
    assert "kind==" not in flat
    assert "kind!=" not in flat
    assert "'coding'" not in src
    assert '"coding"' not in src
