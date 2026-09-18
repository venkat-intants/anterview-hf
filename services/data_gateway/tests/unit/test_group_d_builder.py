"""D2/D3 — changing a round's type, and coverage as one number."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _db() -> AsyncMock:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value="draft")
    # _assert_draft reads (status, review_status) — PH4-O6 locks a version under review.
    res = MagicMock()
    res.first.return_value = ("draft", "draft")
    db.execute = AsyncMock(return_value=res)
    return db


async def _patch(fields: dict) -> dict:
    from app.workflows import update_round

    db = _db()
    await update_round(db, workflow_id=uuid.uuid4(), round_id=uuid.uuid4(), fields=fields)
    return db.execute.call_args.args[1]


@pytest.mark.asyncio
async def test_turning_a_test_into_a_human_review_clears_what_cannot_apply() -> None:
    """Otherwise the exam, threshold and time limit stay on the row, invisible
    in the panel, and a later type change silently brings them back."""
    params = await _patch({"kind": "human_review"})
    assert params["pass_threshold"] is None
    assert params["exam_round_id"] is None
    assert params["time_limit_seconds"] is None


@pytest.mark.asyncio
async def test_an_ai_interview_drops_the_exam_but_keeps_its_threshold() -> None:
    params = await _patch({"kind": "ai_interview"})
    assert params["exam_round_id"] is None
    assert "pass_threshold" not in params


@pytest.mark.asyncio
async def test_switching_between_test_kinds_keeps_the_attached_exam() -> None:
    params = await _patch({"kind": "coding"})
    assert "exam_round_id" not in params


@pytest.mark.asyncio
async def test_nothing_is_invented_for_the_new_type() -> None:
    """A human review turned into an MCQ gets no made-up threshold; validation
    tells HR to set one."""
    params = await _patch({"kind": "mcq"})
    assert "pass_threshold" not in params


def test_coverage_as_a_share_of_the_roles_weight() -> None:
    from app.workflows import CoverageRow, ValidationReport

    rep = ValidationReport(coverage=[
        CoverageRow("python", "Python", 0.6, ["Coding"]),
        CoverageRow("git", "Version Control", 0.1, []),
        CoverageRow("comms", "Communication", 0.3, ["AI Interview"]),
    ])
    assert rep.weighted_coverage == 0.9
    assert rep.as_dict()["weighted_coverage"] == 0.9


def test_no_role_model_means_no_coverage_figure() -> None:
    from app.workflows import ValidationReport

    assert ValidationReport().weighted_coverage is None


def test_the_round_patch_accepts_a_type_change() -> None:
    """The model used to omit it, so the API dropped the field silently."""
    from pydantic import ValidationError

    from app.routers.hr_workflows import RoundPatch

    assert RoundPatch(kind="human_review").model_dump(exclude_unset=True) == {
        "kind": "human_review"
    }
    with pytest.raises(ValidationError):
        RoundPatch(kind="essay")
