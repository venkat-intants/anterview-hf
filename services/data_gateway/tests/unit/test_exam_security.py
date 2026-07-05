"""Security tests for the applicant exam-take path (HR workflow Phase 2):
the answer key must NEVER reach an applicant, and an invalid magic link must 404.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


def test_public_take_models_have_no_answer_key_fields() -> None:
    """None of the applicant-facing response models may carry correct_index,
    reference_solution, or pass_threshold (defence against widening the schema)."""
    from app.routers.exam_take import (
        ExamResultOut,
        PublicCodingQuestionOut,
        PublicQuestionOut,
        PublicSectionOut,
        TakeExamOut,
    )

    for model in (
        PublicQuestionOut, PublicCodingQuestionOut, PublicSectionOut, TakeExamOut, ExamResultOut
    ):
        fields = set(model.model_fields)
        assert "correct_index" not in fields, f"{model.__name__} leaks correct_index"
        assert "pass_threshold" not in fields, f"{model.__name__} leaks pass_threshold"
        assert "correct" not in fields, f"{model.__name__} leaks correctness"
        assert "reference_solution" not in fields, f"{model.__name__} leaks reference_solution"


def test_serialized_take_payload_contains_no_answer_key() -> None:
    """A fully-populated take payload, serialized to JSON, must not contain the
    answer key in any form."""
    from app.routers.exam_take import PublicQuestionOut, TakeExamOut

    payload = TakeExamOut(
        exam_id=str(uuid.uuid4()),
        title="Test",
        description=None,
        round_id=str(uuid.uuid4()),
        round_title="Round 1",
        round_number=1,
        time_limit_seconds=600,
        total_questions=1,
        allow_retake=False,
        already_submitted=False,
        server_now="2026-06-22T00:00:00+00:00",
        deadline=None,
        max_integrity_violations=3,
        questions=[
            PublicQuestionOut(
                id=str(uuid.uuid4()),
                position=0,
                prompt="2 + 2 = ?",
                options=["3", "4", "5"],
                points=1,
            )
        ],
    )
    blob = payload.model_dump_json()
    assert "correct_index" not in blob
    assert "correct" not in blob
    assert "pass_threshold" not in blob


@pytest.mark.asyncio
async def test_missing_token_is_404() -> None:
    from app.routers.exam_take import get_exam_link_ctx

    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await get_exam_link_ctx(db, x_exam_token=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_token_is_404() -> None:
    from app.routers.exam_take import get_exam_link_ctx

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)  # no assignment resolves
    with pytest.raises(HTTPException) as exc:
        await get_exam_link_ctx(db, x_exam_token="definitely-not-a-real-token")
    assert exc.value.status_code == 404
