"""Unit tests for the pure MCQ grading logic (HR workflow Phase 2)."""

from __future__ import annotations

from app.exam_grading import (
    GradeInput,
    GradeQuestion,
    grade_breakdown,
    grade_exam,
)


def _qs() -> list[GradeQuestion]:
    return [
        GradeQuestion(question_id="q1", correct_index=0, points=1),
        GradeQuestion(question_id="q2", correct_index=2, points=1),
        GradeQuestion(question_id="q3", correct_index=1, points=2),
    ]


def test_all_correct_is_100_and_passes() -> None:
    r = grade_exam(GradeInput(_qs(), {"q1": 0, "q2": 2, "q3": 1}), pass_threshold=60)
    assert (r.score_raw, r.score_max, r.score_percent, r.passed) == (4, 4, 100, True)


def test_all_wrong_is_zero_and_fails() -> None:
    r = grade_exam(GradeInput(_qs(), {"q1": 1, "q2": 0, "q3": 0}), pass_threshold=60)
    assert (r.score_raw, r.score_percent, r.passed) == (0, 0, False)


def test_unanswered_out_of_range_and_unknown_count_as_wrong() -> None:
    # q1 unanswered, q2 out-of-range index, q3 correct. Unknown qid ignored.
    r = grade_exam(GradeInput(_qs(), {"q2": 99, "q3": 1, "qX": 0}), pass_threshold=50)
    assert r.score_raw == 2  # only q3 (worth 2)
    assert r.score_max == 4
    assert r.score_percent == 50
    assert r.passed is True


def test_score_max_comes_from_questions_not_payload() -> None:
    # A forged payload with extra answers cannot shrink/inflate the denominator.
    r = grade_exam(GradeInput(_qs(), {"q1": 0, "junk": 0}), pass_threshold=10)
    assert r.score_max == 4  # 1 + 1 + 2 from the server questions
    assert r.score_raw == 1


def test_floor_rounding_never_rounds_up_across_threshold() -> None:
    # 1 of 2 single-point questions = 50% exactly; threshold 60 must FAIL.
    two = [
        GradeQuestion("a", 0, 1),
        GradeQuestion("b", 0, 1),
    ]
    r = grade_exam(GradeInput(two, {"a": 0, "b": 1}), pass_threshold=60)
    assert r.score_percent == 50
    assert r.passed is False


def test_threshold_is_inclusive_ge() -> None:
    two = [GradeQuestion("a", 0, 1), GradeQuestion("b", 0, 1)]
    r = grade_exam(GradeInput(two, {"a": 0, "b": 1}), pass_threshold=50)
    assert r.score_percent == 50
    assert r.passed is True  # 50 >= 50


def test_zero_questions_is_percent_zero_not_crash() -> None:
    r = grade_exam(GradeInput([], {}), pass_threshold=0)
    assert (r.score_raw, r.score_max, r.score_percent) == (0, 0, 0)
    assert r.passed is True  # 0 >= 0


def test_points_weighting() -> None:
    qs = [GradeQuestion("a", 0, 1), GradeQuestion("b", 0, 9)]
    # Only the 9-point question correct → 9/10 = 90%.
    r = grade_exam(GradeInput(qs, {"a": 1, "b": 0}), pass_threshold=80)
    assert r.score_percent == 90
    assert r.passed is True


def test_grade_breakdown_marks_each_question() -> None:
    bd = grade_breakdown(GradeInput(_qs(), {"q1": 0, "q2": 99, "q3": 1}))
    assert bd == {"q1": True, "q2": False, "q3": True}
