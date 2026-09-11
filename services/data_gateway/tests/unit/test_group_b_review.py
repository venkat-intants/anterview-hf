"""Unit tests for B3 — splitting by candidate, merging openings, confirming.

End to end in ``tests/integration/smoke_group_b_review.py``; these pin the
refusals, because each one guards a decision about someone's candidacy.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError


def _db() -> AsyncMock:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    return db


def _rows(rows: list) -> MagicMock:
    return MagicMock(all=MagicMock(return_value=rows),
                     mappings=MagicMock(return_value=MagicMock(
                         all=MagicMock(return_value=rows), first=MagicMock(return_value=None))))


# ===========================================================================
# Split by candidate
# ===========================================================================
def test_a_split_must_name_someone() -> None:
    from app.routers.hr_requisitions import SplitIn

    with pytest.raises(ValidationError):
        SplitIn(new_title="Data Engineer")
    assert SplitIn(new_title="Data Engineer", enrolment_ids=[uuid.uuid4()])
    assert SplitIn(new_title="Data Engineer", source_titles=["Senior Dev"])


@pytest.mark.asyncio
async def test_split_moves_exactly_the_candidates_named(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.requisitions as rq

    e1, e2, e3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    same = "python developer"  # the backfill's shape: everyone normalises alike
    rows = [{"id": e, "target_job_title": same, "workflow_id": None, "full_name": n}
            for e, n in [(e1, "Ravi"), (e2, "Meena"), (e3, "Arjun")]]
    src = MagicMock(mappings=MagicMock(return_value=MagicMock(
        first=MagicMock(return_value={"title": "Python Developer", "level": "mid"}))))
    db = _db()
    db.execute.side_effect = [src, _rows(rows)] + [_rows([(e2, "new")])] * 10
    ledger: list[uuid.UUID] = []

    async def _entry(_db: object, **kw: object) -> None:
        ledger.append(kw["enrolment_id"])  # type: ignore[arg-type]

    monkeypatch.setattr(rq, "record_ledger_entry", _entry)
    out = await rq.split_requisition(db, company_id=uuid.uuid4(), requisition_id=uuid.uuid4(),
                                     enrolment_ids=[e2], new_title="Data Engineer", level=None,
                                     actor_user_id=uuid.uuid4())

    assert out["moved"] == 1 and out["left_behind"] == 2
    assert ledger == [e2]


@pytest.mark.asyncio
async def test_split_refuses_a_candidate_who_is_not_in_the_opening() -> None:
    import app.requisitions as rq

    src = MagicMock(mappings=MagicMock(return_value=MagicMock(
        first=MagicMock(return_value={"title": "Python Developer", "level": "mid"}))))
    db = _db()
    db.execute.side_effect = [src, _rows([{"id": uuid.uuid4(), "target_job_title": "x",
                                           "workflow_id": None, "full_name": "Ravi"}])]
    with pytest.raises(ValueError, match="not in this opening"):
        await rq.split_requisition(db, company_id=uuid.uuid4(), requisition_id=uuid.uuid4(),
                                   enrolment_ids=[uuid.uuid4()], new_title="Data Engineer",
                                   level=None, actor_user_id=uuid.uuid4())


# ===========================================================================
# Merge two openings
# ===========================================================================
async def _merge(db: AsyncMock, source: uuid.UUID, into: uuid.UUID) -> dict:
    import app.requisitions as rq

    return await rq.merge_requisitions(db, company_id=uuid.uuid4(), source_id=source,
                                       into_id=into, actor_user_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_an_opening_cannot_be_merged_into_itself() -> None:
    same = uuid.uuid4()
    with pytest.raises(ValueError, match="into itself"):
        await _merge(_db(), same, same)


@pytest.mark.asyncio
async def test_an_opening_with_a_workflow_cannot_be_merged_away() -> None:
    """Its candidates are assessed against that workflow's rubric; moving them
    would silently change what they are assessed on."""
    s, t = uuid.uuid4(), uuid.uuid4()
    db = _db()
    db.execute.return_value = _rows([(s, "Nurse"), (t, "Staff Nurse")])
    db.scalar.return_value = 1  # a workflow exists
    with pytest.raises(ValueError, match="own hiring workflow"):
        await _merge(db, s, t)


@pytest.mark.asyncio
async def test_someone_in_both_openings_blocks_the_merge_by_name() -> None:
    s, t = uuid.uuid4(), uuid.uuid4()
    db = _db()
    db.execute.side_effect = [_rows([(s, "Analyst"), (t, "Data Analyst")]),
                              _rows([("Ravi",)])]
    with pytest.raises(ValueError, match="Ravi"):
        await _merge(db, s, t)


@pytest.mark.asyncio
async def test_another_companys_opening_is_not_found() -> None:
    s, t = uuid.uuid4(), uuid.uuid4()
    db = _db()
    db.execute.return_value = _rows([(s, "Analyst")])  # the target did not match the company
    with pytest.raises(ValueError, match="not found"):
        await _merge(db, s, t)


@pytest.mark.asyncio
async def test_a_merge_moves_everyone_records_each_and_retires_the_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.requisitions as rq

    s, t, e1, e2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = _db()
    db.execute.side_effect = [_rows([(s, "Analyst"), (t, "Data Analyst")]),
                              _rows([]),  # nobody in both
                              _rows([(e1, "new"), (e2, "shortlisted")]),
                              MagicMock(), MagicMock()]
    ledger: list[dict] = []

    async def _entry(_db: object, **kw: object) -> None:
        ledger.append(kw)

    monkeypatch.setattr(rq, "record_ledger_entry", _entry)
    out = await _merge(db, s, t)

    assert out == {"into_requisition_id": str(t), "title": "Data Analyst", "moved": 2}
    assert [x["enrolment_id"] for x in ledger] == [e1, e2]
    assert all(x["automated"] is False for x in ledger)
    retire = str(db.execute.call_args_list[-1].args[0])
    assert "status = 'closed', deleted_at = :n" in retire


# ===========================================================================
# Confirm, and the review list
# ===========================================================================
def test_confirm_is_its_own_audited_action() -> None:
    from app.routers.hr_requisitions import confirm_requisition

    src = inspect.getsource(confirm_requisition)
    assert "from_backfill = false" in src
    assert 'action="requisition.confirm"' in src


def test_the_review_counts_only_live_applications() -> None:
    from app.routers.hr_requisitions import review_backfill

    src = inspect.getsource(review_backfill)
    assert "e.requisition_id = r.id AND e.deleted_at IS NULL" in src
    assert '"unfiled_applicants"' in src
