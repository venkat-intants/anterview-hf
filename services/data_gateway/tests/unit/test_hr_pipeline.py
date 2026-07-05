"""Unit tests for HR pipeline validation + decision guards (HR workflow Phase 4).

The aggregation SQL (tenant isolation, derived status, bool_or, pagination) is
covered by a live end-to-end smoke test; here we lock the cheap guards.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock

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
    with pytest.raises(HTTPException) as exc:
        await decide_applicant(
            uuid.uuid4(), DecisionIn(decision="hired"), Mock(), (uuid.uuid4(), uuid.uuid4()), db
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_decision_already_hired_blocked() -> None:
    from app.models import Applicant
    from app.routers.hr_pipeline import DecisionIn, decide_applicant

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=Applicant(id=uuid.uuid4(), status="hired"))
    with pytest.raises(HTTPException) as exc:
        await decide_applicant(
            uuid.uuid4(), DecisionIn(decision="hired"), Mock(), (uuid.uuid4(), uuid.uuid4()), db
        )
    assert exc.value.status_code == 409
