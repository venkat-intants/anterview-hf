"""Three additive contract changes, DB mocked.

1. The openings list carries ``has_published_jd`` / ``has_published_workflow``,
   computed inside the one list statement (no per-row query).
2. The question-bank summary carries ``kind_counts``, folded from the same
   aggregate as ``counts``.
3. An opening can be created with its budget, under the SAME rule as PATCH.

What a mock cannot show — that Postgres plans the EXISTS through
``uq_workflows_one_published`` — is stated in the module-level comments of the
router, not asserted here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

COMPANY = uuid.uuid4()
HR = uuid.uuid4()


def _row(i: int, **kw: Any) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    return {
        "id": uuid.uuid4(), "title": f"Role {i}", "level": "mid", "status": "open",
        "from_backfill": False, "created_at": now, "closes_at": None, **kw,
    }


def _mappings(rows: list[dict[str, Any]]) -> MagicMock:
    res = MagicMock()
    res.mappings.return_value.all.return_value = rows
    return res


def _list_db(rows: list[dict[str, Any]]) -> AsyncMock:
    """list statement, then the two rollup statements (_counts, _awaiting)."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_mappings(rows), _mappings([]), _mappings([])])
    return db


# ===========================================================================
# 1. Openings list
# ===========================================================================
@pytest.mark.asyncio
async def test_list_reports_both_setup_facts_for_positive_and_negative_rows() -> None:
    from app.routers.hr_requisitions import list_requisitions

    rows = [
        _row(1, has_published_jd=True, has_published_workflow=True),
        _row(2, has_published_jd=False, has_published_workflow=False),
        _row(3, has_published_jd=True, has_published_workflow=False),
    ]
    out = await list_requisitions((HR, COMPANY), _list_db(rows), None, 50, 0)
    got = [(o.has_published_jd, o.has_published_workflow) for o in out]
    assert got == [(True, True), (False, False), (True, False)]


@pytest.mark.asyncio
async def test_list_query_count_does_not_grow_with_rows() -> None:
    from app.routers.hr_requisitions import list_requisitions

    counts = []
    for n in (1, 40):
        rows = [_row(i, has_published_jd=True, has_published_workflow=False) for i in range(n)]
        db = _list_db(rows)
        await list_requisitions((HR, COMPANY), db, None, 50, 0)
        counts.append(db.execute.await_count)
    assert counts[0] == counts[1] == 3


@pytest.mark.asyncio
async def test_list_statement_is_company_scoped_and_uses_exists_not_a_loop() -> None:
    from app.routers.hr_requisitions import list_requisitions

    other = uuid.uuid4()
    db = _list_db([])
    await list_requisitions((HR, other), db, None, 50, 0)
    stmt, params = db.execute.await_args_list[0].args
    sql = " ".join(str(stmt).split())
    assert params["c"] == other  # the session's company, never another's
    assert "WHERE company_id = :c AND deleted_at IS NULL" in sql
    assert "EXISTS (SELECT 1 FROM workflows w" in sql
    assert "w.requisition_id = job_requisitions.id" in sql
    assert "w.status = 'published' AND w.deleted_at IS NULL" in sql
    assert "published_jd_version_id IS NOT NULL) AS has_published_jd" in sql


@pytest.mark.asyncio
async def test_single_opening_reads_leave_the_facts_null() -> None:
    from app.routers.hr_requisitions import _to_out

    out = _to_out(_row(1))
    assert out.has_published_jd is None and out.has_published_workflow is None


def test_openapi_describes_the_new_fields() -> None:
    from app.routers.hr_requisitions import RequisitionIn, RequisitionOut

    props = RequisitionOut.model_json_schema()["properties"]
    assert {"has_published_jd", "has_published_workflow"} <= set(props)
    in_props = RequisitionIn.model_json_schema()["properties"]
    assert {
        "budget_amount", "budget_currency", "budget_basis", "budget_period", "budget_notes",
    } <= set(in_props)


# ===========================================================================
# 2. Bank kind_counts
# ===========================================================================
def _bank() -> MagicMock:
    now = datetime.now(tz=UTC)
    b = MagicMock()
    b.id, b.name, b.description, b.created_at, b.updated_at = uuid.uuid4(), "B", None, now, now
    return b


def _bank_db(grouped: list[tuple[str, str, int]]) -> AsyncMock:
    res = MagicMock()
    res.tuples.return_value.all.return_value = grouped
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    db.scalar = AsyncMock(return_value=0)
    return db


@pytest.mark.asyncio
async def test_kind_counts_exclude_retired_and_counts_keeps_every_status() -> None:
    from app.question_banks import _bank_summary

    db = _bank_db([
        ("approved", "mcq", 3), ("approved", "coding", 1), ("draft", "mcq", 2),
        ("in_review", "coding", 1), ("retired", "mcq", 5), ("retired", "coding", 4),
    ])
    out = await _bank_summary(db, company_id=COMPANY, bank=_bank())
    assert out["counts"] == {
        "approved": 4, "draft": 2, "in_review": 1, "retired": 9,
    }
    assert out["kind_counts"] == {"mcq": 5, "coding": 2}
    # one aggregate for counts + kind_counts together
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_kind_counts_of_an_empty_bank_are_zero_not_missing() -> None:
    from app.question_banks import _bank_summary

    out = await _bank_summary(_bank_db([]), company_id=COMPANY, bank=_bank())
    assert out["counts"] == {}
    assert out["kind_counts"] == {"mcq": 0, "coding": 0}


@pytest.mark.asyncio
async def test_kind_counts_and_counts_cannot_disagree() -> None:
    from app.question_banks import _bank_summary

    out = await _bank_summary(
        _bank_db([("approved", "mcq", 2), ("draft", "coding", 3), ("retired", "mcq", 7)]),
        company_id=COMPANY, bank=_bank(),
    )
    non_retired = sum(v for k, v in out["counts"].items() if k != "retired")
    assert sum(out["kind_counts"].values()) == non_retired


# ===========================================================================
# 3. Create with a budget
# ===========================================================================
_FULL = {
    "budget_amount": 1_500_000, "budget_currency": "inr", "budget_basis": "total",
    "budget_period": "annual", "budget_notes": "FY27 plan",
}


def _create_db() -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    return db


@pytest.mark.asyncio
async def test_create_with_full_budget_persists_in_the_same_insert_and_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.routers.hr_requisitions as hrr

    async def _owned(*_: object) -> dict[str, Any]:
        return _row(1)

    monkeypatch.setattr(hrr, "_owned", _owned)
    db = _create_db()
    body = hrr.RequisitionIn(title="Welder", **_FULL)
    await hrr.create_requisition(body, (HR, COMPANY), db)

    stmt, params = db.execute.await_args_list[0].args
    sql = " ".join(str(stmt).split())
    assert "budget_amount, budget_currency, budget_basis, budget_period, budget_notes" in sql
    assert (params["bamt"], params["bcur"], params["bbas"], params["bper"], params["bnotes"]) == (
        1_500_000, "INR", "total", "annual", "FY27 plan",
    )
    # One INSERT, one commit: the budget and the opening land together or not at all.
    assert db.commit.await_count == 1
    assert "approval_status" not in sql  # a new opening takes the column default: draft


@pytest.mark.asyncio
async def test_create_without_budget_binds_nulls(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.routers.hr_requisitions as hrr

    async def _owned(*_: object) -> dict[str, Any]:
        return _row(1)

    monkeypatch.setattr(hrr, "_owned", _owned)
    db = _create_db()
    await hrr.create_requisition(hrr.RequisitionIn(title="Welder"), (HR, COMPANY), db)
    params = db.execute.await_args_list[0].args[1]
    assert [params[k] for k in ("bamt", "bcur", "bbas", "bper", "bnotes")] == [None] * 5
    assert db.commit.await_count == 1


@pytest.mark.parametrize("missing", ["budget_currency", "budget_basis", "budget_period"])
def test_create_with_a_partial_budget_is_refused_with_the_patch_message(missing: str) -> None:
    from app.routers.hr_requisitions import RequisitionIn, RequisitionPatch

    kw = {k: v for k, v in _FULL.items() if k != missing}
    with pytest.raises(ValidationError) as on_create:
        RequisitionIn(title="Welder", **kw)
    with pytest.raises(ValidationError) as on_patch:
        RequisitionPatch(**kw)
    msg = "a budget needs an amount, a currency, a basis (per_hire or total)"
    assert msg in str(on_create.value)
    assert msg in str(on_patch.value)


def test_create_and_patch_share_one_budget_implementation() -> None:
    from app.routers.hr_requisitions import BudgetFields, RequisitionIn, RequisitionPatch

    assert issubclass(RequisitionIn, BudgetFields)
    assert issubclass(RequisitionPatch, BudgetFields)


def test_create_budget_bounds_match_patch() -> None:
    from app.routers.hr_requisitions import RequisitionIn

    with pytest.raises(ValidationError):
        RequisitionIn(title="Welder", **{**_FULL, "budget_amount": -1})
    with pytest.raises(ValidationError):
        RequisitionIn(title="Welder", **{**_FULL, "budget_currency": "RUPEES"})
    with pytest.raises(ValidationError):
        RequisitionIn(title="Welder", **{**_FULL, "budget_basis": "weekly"})


@pytest.mark.asyncio
async def test_a_failed_insert_with_a_budget_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.routers.hr_requisitions as hrr

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(HTTPException) as exc:
        await hrr.create_requisition(hrr.RequisitionIn(title="Welder", **_FULL), (HR, COMPANY), db)
    assert exc.value.status_code == 503
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
