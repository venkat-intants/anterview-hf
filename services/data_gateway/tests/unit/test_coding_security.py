"""Security tests for the coding take path — the answer key must never leak.

Mirrors test_exam_security.py: the candidate-facing coding serializer must NOT
carry reference_solution or any HIDDEN test case's expected_output/stdin (the
coding analog of hiding correct_index).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.routers.exam_take import (
    PublicCodingQuestionOut,
    TakeExamOut,
    _public_coding_question,
)


def _question_with_secrets() -> SimpleNamespace:
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        position=0,
        prompt="Add two numbers",
        starter_code="def solve(): ...",
        allowed_languages=["python", "cpp"],
        points=100,
        time_limit_ms=2000,
        reference_solution="SECRET_SOLUTION_print(a+b)",
        test_cases=[
            {"stdin": "1 2", "expected_output": "3", "is_sample": True, "weight": 1},
            {"stdin": "9 9", "expected_output": "HIDDEN_SECRET", "is_sample": False, "weight": 1},
        ],
    )


def test_public_coding_model_has_no_reference_solution_field() -> None:
    fields = set(PublicCodingQuestionOut.model_fields)
    assert "reference_solution" not in fields
    assert "test_cases" not in fields  # only sample_tests is exposed


def test_public_coding_question_strips_hidden_and_solution() -> None:
    out = _public_coding_question(_question_with_secrets())
    # Only the SAMPLE case is exposed.
    assert len(out.sample_tests) == 1
    assert out.sample_tests[0].expected_output == "3"
    # Hidden expected output is nowhere in the serialized payload.
    dumped = out.model_dump_json()
    assert "HIDDEN_SECRET" not in dumped
    assert "SECRET_SOLUTION" not in dumped


def test_take_exam_out_never_serializes_secrets() -> None:
    coding = _public_coding_question(_question_with_secrets())
    payload = TakeExamOut(
        exam_id="e", title="t", description=None, kind="coding",
        round_id="r", round_title="Round 1", round_number=1,
        time_limit_seconds=None, total_questions=1, allow_retake=False,
        already_submitted=False, server_now="now", deadline=None,
        max_integrity_violations=3,
        questions=[], coding_questions=[coding],
    )
    blob = payload.model_dump_json()
    for secret in ("HIDDEN_SECRET", "SECRET_SOLUTION", "reference_solution"):
        assert secret not in blob
