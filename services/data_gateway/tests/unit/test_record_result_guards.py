"""record_result must not act on a decided candidacy or a round the candidate left.

The hold paths had no status or current-round guard (only ``_advance`` did), so a
late exam submit could pull a rejected enrolment back to ``held``, and for a hired
one ``record_transition``'s 409 escaped past the exam submit handler and the
graded attempt was never saved. No database: the loaders are faked.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app import workflow_runner


def _enrolment(status: str, current_round: uuid.UUID | None) -> dict[str, object]:
    return {
        "id": uuid.uuid4(), "company_id": uuid.uuid4(), "applicant_id": uuid.uuid4(),
        "requisition_id": uuid.uuid4(), "status": status, "workflow_id": uuid.uuid4(),
        "current_round_id": current_round, "target_job_title": "Welder",
        "full_name": "A B", "email": "a@example.com",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["hired", "rejected"])
async def test_terminal_enrolment_is_a_noop(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    rid = uuid.uuid4()
    enr = _enrolment(status, rid)
    loader = AsyncMock(return_value=enr)
    monkeypatch.setattr(workflow_runner, "_load_enrolment", loader)
    hold = AsyncMock()
    monkeypatch.setattr(workflow_runner, "_hold", hold)
    db = AsyncMock()

    out = await workflow_runner.record_result(
        db, enrolment_id=enr["id"], round_id=rid, score=1, max_score=10  # type: ignore[arg-type]
    )

    assert out.action == "noop"
    hold.assert_not_awaited()
    db.execute.assert_not_awaited()  # no round_results row either
    assert loader.await_args.kwargs.get("lock") is True  # serialised with a decision


@pytest.mark.asyncio
async def test_result_for_a_round_the_candidate_left_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enr = _enrolment("shortlisted", uuid.uuid4())
    monkeypatch.setattr(workflow_runner, "_load_enrolment", AsyncMock(return_value=enr))
    hold = AsyncMock()
    monkeypatch.setattr(workflow_runner, "_hold", hold)
    db = AsyncMock()

    for current in (uuid.uuid4(), None):
        enr["current_round_id"] = current
        out = await workflow_runner.record_result(
            db, enrolment_id=enr["id"], round_id=uuid.uuid4(), score=1, max_score=10  # type: ignore[arg-type]
        )
        assert out.action == "noop"
    hold.assert_not_awaited()
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_exam_round_lookup_skips_terminal_and_is_ordered() -> None:
    db = AsyncMock()
    result = AsyncMock()
    result.first = lambda: None
    db.execute = AsyncMock(return_value=result)

    await workflow_runner.enrolment_awaiting_exam_round(
        db, applicant_id=uuid.uuid4(), exam_round_id=uuid.uuid4()
    )
    sql = str(db.execute.await_args.args[0])
    assert "NOT IN ('hired','rejected')" in sql
    assert "ORDER BY" in sql
