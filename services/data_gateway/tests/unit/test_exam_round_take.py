"""Unit tests (DB-free) for the round-based exam take path: request-schema caps,
the rolling integrity score, and the no-secret-leak guarantee for section payloads.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import settings
from app.routers.exam_take import (
    CodingSubmitIn,
    PublicSectionOut,
    RunCodeCustomIn,
    SubmitIn,
    _public_coding_question,
    _score_from_violations,
)


def test_integrity_score_decreases_and_floors() -> None:
    assert _score_from_violations(0) == 100
    assert _score_from_violations(1) == 85
    assert _score_from_violations(2) == 70
    # Floored at 0 — a runaway client can never push the score negative.
    assert _score_from_violations(7) == 0
    assert _score_from_violations(1000) == 0
    # Monotonic non-increasing.
    scores = [_score_from_violations(i) for i in range(10)]
    assert scores == sorted(scores, reverse=True)


def test_run_code_custom_caps_stdin() -> None:
    big = "x" * (settings.code_max_stdin_bytes + 1)
    with pytest.raises(ValidationError):
        RunCodeCustomIn(question_id=uuid.uuid4(), language="python", source="", stdin=big)


def test_run_code_custom_caps_source() -> None:
    big = "x" * (settings.code_max_source_bytes + 1)
    with pytest.raises(ValidationError):
        RunCodeCustomIn(question_id=uuid.uuid4(), language="python", source=big, stdin="")


def test_coding_submit_caps_answers_and_submissions() -> None:
    # Both SubmitIn and CodingSubmitIn must bound the answers map (DoS guard).
    big_answers = {str(uuid.uuid4()): 0 for _ in range(settings.exam_max_answers + 1)}
    with pytest.raises(ValidationError):
        CodingSubmitIn(attempt_id=uuid.uuid4(), answers=big_answers)
    with pytest.raises(ValidationError):
        SubmitIn(attempt_id=uuid.uuid4(), answers=big_answers)


def test_submit_in_carries_both_answer_and_submission_maps() -> None:
    s = SubmitIn(
        attempt_id=uuid.uuid4(),
        answers={"q1": 1},
        submissions={"c1": {"language": "python", "source": "print(1)"}},  # type: ignore[dict-item]
    )
    assert s.answers == {"q1": 1}
    assert s.submissions["c1"].language == "python"


def test_section_payload_never_leaks_coding_secrets() -> None:
    q = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        position=0,
        prompt="Add two numbers",
        starter_code=None,
        allowed_languages=["python"],
        points=100,
        time_limit_ms=2000,
        reference_solution="SECRET_SOLUTION",
        test_cases=[
            {"stdin": "1 2", "expected_output": "3", "is_sample": True, "weight": 1},
            {"stdin": "9 9", "expected_output": "HIDDEN_SECRET", "is_sample": False, "weight": 1},
        ],
    )
    coding = _public_coding_question(q)
    section = PublicSectionOut(
        id="s1", title="Coding", kind="coding", position=1,
        time_limit_seconds=None, coding_questions=[coding],
    )
    blob = section.model_dump_json()
    for secret in ("HIDDEN_SECRET", "SECRET_SOLUTION", "reference_solution"):
        assert secret not in blob
    # Only the sample case is exposed.
    assert len(coding.sample_tests) == 1
    assert coding.sample_tests[0].expected_output == "3"
