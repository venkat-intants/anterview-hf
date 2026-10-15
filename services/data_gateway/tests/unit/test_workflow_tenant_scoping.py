"""Workflow round writes are scoped to the caller's company (M1, L1).

M1: ``set_round_criteria`` resolved the round by id alone. An HR manager of company
A could PUT criteria for company B's round id under A's own workflow: the draft
check read B's workflow, the DELETE wiped B's rubric and the INSERT wrote rows
into it.
L1: ``workflow_rounds.exam_round_id`` was stored without checking that the exam
round belongs to the caller's company.

The DB is faked and records every statement + params, so the assertions are about
the tenant predicates actually sent, not about a mock agreeing with itself.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

COMPANY_A = uuid.uuid4()
COMPANY_B = uuid.uuid4()
WORKFLOW = uuid.uuid4()
ROUND = uuid.uuid4()
CRITERIA = [{"id": "python", "name": "Python", "kind": "technical", "weight": 0.5}]


class _Db:
    """Records (sql, params); `scalars` is consumed in order by db.scalar()."""

    def __init__(self, scalars: list[Any]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._scalars = list(scalars)
        self.scalar = AsyncMock(side_effect=self._scalar)
        self.execute = AsyncMock(side_effect=self._execute)

    async def _scalar(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((str(stmt), dict(params or {})))
        return self._scalars.pop(0) if self._scalars else None

    async def _execute(self, stmt: Any, params: dict[str, Any] | None = None) -> MagicMock:
        self.calls.append((str(stmt), dict(params or {})))
        res = MagicMock()
        res.first.return_value = ("draft", None)  # _assert_draft: status, review_status
        return res


# ------------------------------------------------------------------------------ M1
@pytest.mark.asyncio
async def test_foreign_round_is_not_found_and_nothing_is_deleted_or_written() -> None:
    from app.workflows import RoundNotFoundError, set_round_criteria

    db = _Db(scalars=[None])  # the tenant-scoped lookup finds no such round
    with pytest.raises(RoundNotFoundError):
        await set_round_criteria(
            db, company_id=COMPANY_A, round_id=ROUND, workflow_id=WORKFLOW,  # type: ignore[arg-type]
            criteria=CRITERIA,
        )
    lookup_sql, lookup_params = db.calls[0]
    assert "company_id = :c" in lookup_sql and "workflow_id" in lookup_sql
    assert lookup_params["c"] == COMPANY_A and lookup_params["w"] == WORKFLOW
    assert len(db.calls) == 1, "no DELETE and no INSERT may run for a foreign round"


@pytest.mark.asyncio
async def test_own_round_deletes_and_inserts_only_within_the_company() -> None:
    from app.workflows import set_round_criteria

    db = _Db(scalars=[WORKFLOW])
    n = await set_round_criteria(
        db, company_id=COMPANY_A, round_id=ROUND, workflow_id=WORKFLOW,  # type: ignore[arg-type]
        criteria=CRITERIA,
    )
    assert n == 1
    delete = next((c for c in db.calls if c[0].lstrip().startswith("DELETE")), None)
    assert delete is not None
    assert "company_id = :c" in delete[0] and delete[1]["c"] == COMPANY_A


@pytest.mark.asyncio
async def test_put_criteria_answers_404_for_a_foreign_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import hr_workflows
    from app.workflows import RoundNotFoundError

    db = AsyncMock()
    monkeypatch.setattr(hr_workflows, "_owned_workflow", AsyncMock(return_value={"id": WORKFLOW}))
    monkeypatch.setattr(
        hr_workflows,
        "set_round_criteria",
        AsyncMock(side_effect=RoundNotFoundError("Round not found.")),
    )
    body = hr_workflows.CriteriaIn(criteria=[])
    with pytest.raises(HTTPException) as exc:
        await hr_workflows.put_criteria(
            WORKFLOW, ROUND, body, (uuid.uuid4(), COMPANY_A), db
        )
    assert exc.value.status_code == 404
    db.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_load_criteria_can_be_scoped_to_a_company() -> None:
    from app.workflows import load_criteria

    db = _Db(scalars=[])
    res = MagicMock()
    res.mappings.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=res)
    await load_criteria(db, [ROUND], company_id=COMPANY_A)  # type: ignore[arg-type]
    sql, params = db.execute.call_args.args[0], db.execute.call_args.args[1]
    assert "company_id" in str(sql)
    assert params["company_id"] == COMPANY_A


# ------------------------------------------------------------------------------ L1
@pytest.mark.asyncio
async def test_add_round_refuses_another_companys_exam_round() -> None:
    from app.workflows import WorkflowError, add_round

    # _assert_draft's execute() says draft; the ownership lookup then finds nothing.
    db = _Db(scalars=[None])
    with pytest.raises(WorkflowError):
        await add_round(
            db, company_id=COMPANY_A, workflow_id=WORKFLOW,  # type: ignore[arg-type]
            title="Aptitude", kind="mcq", exam_round_id=uuid.uuid4(),
        )
    sql, params = next(c for c in db.calls if "FROM exam_rounds" in c[0])
    assert "company_id = :c" in sql and params["c"] == COMPANY_A
    assert not any("INSERT INTO workflow_rounds" in c[0] for c in db.calls)


@pytest.mark.asyncio
async def test_update_round_refuses_another_companys_exam_round() -> None:
    from app.workflows import WorkflowError, update_round

    # scalars: workflow's company -> A; exam-round ownership -> not found
    db = _Db(scalars=[COMPANY_A, None])
    with pytest.raises(WorkflowError):
        await update_round(
            db, workflow_id=WORKFLOW, round_id=ROUND,  # type: ignore[arg-type]
            fields={"exam_round_id": uuid.uuid4()},
        )
    assert not any(c[0].lstrip().startswith("UPDATE workflow_rounds") for c in db.calls)


@pytest.mark.asyncio
async def test_update_round_may_still_detach_an_exam_round() -> None:
    from app.workflows import update_round

    db = _Db(scalars=[])
    await update_round(
        db, workflow_id=WORKFLOW, round_id=ROUND,  # type: ignore[arg-type]
        fields={"exam_round_id": None},
    )
    assert any(c[0].lstrip().startswith("UPDATE workflow_rounds") for c in db.calls)


@pytest.mark.asyncio
async def test_readiness_sql_is_filtered_by_company_when_given() -> None:
    from app.workflows import exam_round_readiness

    db = MagicMock()
    res = MagicMock()
    res.mappings.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=res)
    await exam_round_readiness(db, [uuid.uuid4()], company_id=COMPANY_A)
    sql, params = db.execute.call_args.args[0], db.execute.call_args.args[1]
    assert "er.company_id" in str(sql)
    assert params["company_id"] == COMPANY_A
