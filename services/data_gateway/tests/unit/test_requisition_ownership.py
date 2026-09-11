"""B1 — who owns an opening, and when it closes.

DB mocked. The owner rule itself (company, role, active) runs against Postgres
in ``tests/integration/smoke_group_b_ownership.py``; here: the rule is applied
where it must be, and the closing-date check.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


def test_a_closing_date_in_the_past_is_refused() -> None:
    from app.routers.hr_requisitions import RequisitionIn, RequisitionPatch

    yesterday = datetime.now(tz=UTC) - timedelta(days=1)
    for model, kw in ((RequisitionIn, {"title": "Welder"}), (RequisitionPatch, {})):
        with pytest.raises(ValidationError, match="cannot be in the past"):
            model(closes_at=yesterday, **kw)


def test_a_future_or_absent_closing_date_is_fine() -> None:
    from app.routers.hr_requisitions import RequisitionIn

    assert RequisitionIn(title="Welder").closes_at is None
    soon = datetime.now(tz=UTC) + timedelta(days=30)
    assert RequisitionIn(title="Welder", closes_at=soon).closes_at == soon


def test_a_naive_closing_date_is_read_as_utc() -> None:
    from app.routers.hr_requisitions import RequisitionIn

    naive = (datetime.now(tz=UTC) + timedelta(days=3)).replace(tzinfo=None)
    got = RequisitionIn(title="Welder", closes_at=naive).closes_at
    assert got is not None and got.tzinfo is not None


def test_the_owner_rule_is_company_role_and_active() -> None:
    """One predicate for the team list and the check — if either loosened, the
    picker would offer someone the server refuses, or the reverse."""
    from app.routers.hr_requisitions import _TEAM_SQL

    sql = " ".join(str(_TEAM_SQL).split())
    assert "u.company_id = :c" in sql
    assert "u.deleted_at IS NULL AND u.is_active" in sql
    assert "r.name = 'hr_manager'" in sql


@pytest.mark.asyncio
async def test_an_owner_outside_the_rule_is_refused() -> None:
    from app.routers.hr_requisitions import _check_owner

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(first=MagicMock(return_value=None)))
    with pytest.raises(HTTPException) as exc:
        await _check_owner(db, uuid.uuid4(), uuid.uuid4())
    assert exc.value.status_code == 422
    assert "active HR user at this company" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_the_patch_checks_a_new_owner_before_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect: the PATCH wrote any owner id it was given."""
    import app.routers.hr_requisitions as hrr

    async def _owned(*_: object) -> dict:
        return {}

    async def _refuse(*_: object) -> None:
        raise HTTPException(status_code=422, detail="nope")

    monkeypatch.setattr(hrr, "_owned", _owned)
    monkeypatch.setattr(hrr, "_check_owner", _refuse)
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await hrr.update_requisition(
            uuid.uuid4(), hrr.RequisitionPatch(owner_user_id=uuid.uuid4()),
            (uuid.uuid4(), uuid.uuid4()), db,
        )
    assert exc.value.status_code == 422
    db.execute.assert_not_awaited()
