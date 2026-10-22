"""Unit tests for HR pipeline validation + decision guards (HR workflow Phase 4).

The aggregation SQL (tenant isolation, derived status, bool_or, pagination) is
covered by a live end-to-end smoke test; here we lock the cheap guards.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_pipeline_rejects_bad_stage() -> None:
    from app.routers.hr_pipeline import get_pipeline

    with pytest.raises(HTTPException) as exc:
        await get_pipeline((uuid.uuid4(), uuid.uuid4()), AsyncMock(), stage="bogus")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_pipeline_rejects_bad_status() -> None:
    from app.routers.hr_pipeline import get_pipeline

    with pytest.raises(HTTPException) as exc:
        await get_pipeline((uuid.uuid4(), uuid.uuid4()), AsyncMock(), status_f="offer_extended")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_decision_rejects_bad_value() -> None:
    from app.routers.hr_pipeline import DecisionIn, decide_applicant

    with pytest.raises(HTTPException) as exc:
        await decide_applicant(
            uuid.uuid4(), DecisionIn(decision="maybe"), Mock(), (uuid.uuid4(), uuid.uuid4()), AsyncMock()
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_decision_new_to_hired_blocked() -> None:
    from app.models import Applicant
    from app.routers.hr_pipeline import DecisionIn, decide_applicant

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=Applicant(id=uuid.uuid4(), status="new"))
    # No applications: the person-level status is the one the guard reads.
    db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    with pytest.raises(HTTPException) as exc:
        await decide_applicant(
            uuid.uuid4(), DecisionIn(decision="hired", rationale="Strong CV"), Mock(),
            (uuid.uuid4(), uuid.uuid4()), db
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_decision_already_hired_blocked() -> None:
    from app.models import Applicant
    from app.routers.hr_pipeline import DecisionIn, decide_applicant

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=Applicant(id=uuid.uuid4(), status="hired"))
    # No applications: the person-level status is the one the guard reads.
    db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    with pytest.raises(HTTPException) as exc:
        await decide_applicant(
            uuid.uuid4(), DecisionIn(decision="hired", rationale="Strong CV"), Mock(),
            (uuid.uuid4(), uuid.uuid4()), db
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_decision_without_an_application_needs_a_reason() -> None:
    """HR-H1: a person filed under no opening is decided on the applicant
    board's path, which refuses a decision with no reason."""
    from app.models import Applicant
    from app.routers.hr_pipeline import DecisionIn, decide_applicant

    db = AsyncMock()
    db.add = MagicMock()
    db.scalar = AsyncMock(return_value=Applicant(id=uuid.uuid4(), status="interviewed"))
    db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    with pytest.raises(HTTPException) as exc:
        await decide_applicant(
            uuid.uuid4(), DecisionIn(decision="rejected"), Mock(),
            (uuid.uuid4(), uuid.uuid4()), db,
        )
    assert exc.value.status_code == 422
    assert not db.add.called and not db.commit.called


def test_can_hire_follows_the_decision_rule() -> None:
    """HR-H1: the board reads can_hire from the server instead of guessing from
    status; someone with no application falls back to their own status."""
    from app.routers.hr_pipeline import _PIPELINE_SQL, _can_hire

    sql = str(_PIPELINE_SQL)
    assert "enrolment_awaits_human(e.status, e.current_round_id)" in sql
    assert "e.workflow_id IS NULL AND e.status IN ('shortlisted', 'interviewed')" in sql
    eid = uuid.uuid4()
    assert _can_hire({"enrolment_id": eid, "status": "shortlisted", "can_hire": False}) is False
    assert _can_hire({"enrolment_id": eid, "status": "held", "can_hire": True}) is True
    assert _can_hire({"enrolment_id": None, "status": "interviewed", "can_hire": None}) is True
    assert _can_hire({"enrolment_id": None, "status": "new", "can_hire": None}) is False
