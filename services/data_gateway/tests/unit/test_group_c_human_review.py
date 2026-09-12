"""C4 — a human_review round is a round someone can actually review.

The runner could always record one (``record_result``'s ``passed_override``)
and the smoke exercised it, but nothing a person could reach did: no endpoint,
no screen. A candidate moved onto a review round sat there, and — because the
decision queue only listed held or finished candidates — did not appear on the
one screen built to act on them. A workflow with a review round stalled.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


def _db(row: dict | None) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


# ===========================================================================
# The queue shows them
# ===========================================================================
def test_the_queue_includes_candidates_sitting_on_a_review_round() -> None:
    import inspect

    from app.workflow_runner import decision_queue

    sql = " ".join(inspect.getsource(decision_queue).split())
    assert "wr.kind = 'human_review'" in sql
    assert "OR wr.id IS NOT NULL" in sql, "a candidate on a review round is still missing"
    # …with the competencies that round assesses: the reviewer's checklist (C3).
    assert "rc.competency_name" in sql


# ===========================================================================
# The endpoint
# ===========================================================================
@pytest.mark.asyncio
async def test_reviewing_an_unknown_application_is_a_404() -> None:
    from app.routers.hr_workflows import RoundReviewIn, post_round_review

    with pytest.raises(HTTPException) as exc:
        await post_round_review(
            uuid.uuid4(), RoundReviewIn(passed=True), (uuid.uuid4(), uuid.uuid4()), _db(None)
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_candidate_on_no_round_is_pointed_at_the_final_decision() -> None:
    from app.routers.hr_workflows import RoundReviewIn, post_round_review

    with pytest.raises(HTTPException) as exc:
        await post_round_review(
            uuid.uuid4(), RoundReviewIn(passed=True), (uuid.uuid4(), uuid.uuid4()),
            _db({"current_round_id": None, "kind": None, "title": None}),
        )
    assert exc.value.status_code == 409
    assert "final decision" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_a_scored_round_cannot_be_passed_by_hand() -> None:
    """Otherwise this becomes a way to skip an exam, and the round result would
    claim a person reviewed something the system was supposed to mark."""
    from app.routers.hr_workflows import RoundReviewIn, post_round_review

    with pytest.raises(HTTPException) as exc:
        await post_round_review(
            uuid.uuid4(), RoundReviewIn(passed=True), (uuid.uuid4(), uuid.uuid4()),
            _db({"current_round_id": uuid.uuid4(), "kind": "mcq", "title": "Aptitude"}),
        )
    assert exc.value.status_code == 409
    assert "scored by the system" in str(exc.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("passed", [True, False])
async def test_the_verdict_is_recorded_as_a_persons(
    monkeypatch: pytest.MonkeyPatch, passed: bool
) -> None:
    import app.routers.hr_workflows as hrw

    rid, hr = uuid.uuid4(), uuid.uuid4()
    seen: dict = {}

    async def _record(_db: object, **kw: object) -> object:
        seen.update(kw)
        return MagicMock(as_dict=lambda: {"action": "advanced"})

    monkeypatch.setattr(hrw, "record_result", _record)
    await hrw.post_round_review(
        uuid.uuid4(), hrw.RoundReviewIn(passed=passed, note="strong portfolio"),
        (hr, uuid.uuid4()),
        _db({"current_round_id": rid, "kind": "human_review", "title": "Portfolio"}),
    )
    assert seen["passed_override"] is passed
    assert seen["graded_by"] == "human"
    assert seen["grader_user_id"] == hr
    assert seen["evidence"] == "strong portfolio"
    assert seen["round_id"] == rid
    # Never a score: a person did not produce a percentage.
    assert seen["score"] is None


def test_failing_a_review_holds_rather_than_rejects() -> None:
    """D-05. A reviewer saying "not this round" is not a rejection; rejecting is
    a separate act that says so in the ledger."""
    import inspect

    from app.routers.hr_workflows import post_round_review

    src = inspect.getsource(post_round_review)
    assert "rejected" not in src.replace("never rejects", "")
    assert "HOLDS" in src
