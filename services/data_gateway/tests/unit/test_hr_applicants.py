"""Unit tests for HR applicant screening — focus on the tenant-isolation and
validation logic. DB is mocked; the full upload→score→rank flow + cross-tenant
isolation are covered by live end-to-end verification.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from shared.auth.base import User


@pytest.mark.asyncio
async def test_get_hr_company_no_company_403() -> None:
    from app.routers.hr_applicants import get_hr_company

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)  # HR has no company_id
    user = User(user_id=str(uuid.uuid4()), full_name="", email="", roles=["hr_manager"])
    with pytest.raises(HTTPException) as exc:
        await get_hr_company(user, db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_hr_company_returns_uid_and_company() -> None:
    from app.routers.hr_applicants import get_hr_company

    uid, cid = uuid.uuid4(), uuid.uuid4()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=cid)
    user = User(user_id=str(uid), full_name="", email="", roles=["hr_manager"])
    got_uid, got_cid = await get_hr_company(user, db)
    assert got_uid == uid
    assert got_cid == cid


@pytest.mark.asyncio
async def test_get_owned_404_when_not_in_company() -> None:
    """Cross-tenant / missing applicant -> 404 (the isolation boundary)."""
    from app.routers.hr_applicants import _get_owned

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await _get_owned(db, uuid.uuid4(), uuid.uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_status_rejects_invalid_value() -> None:
    from app.routers.hr_applicants import StatusUpdate, update_applicant_status

    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await update_applicant_status(
            uuid.uuid4(), StatusUpdate(status="bogus"), (uuid.uuid4(), uuid.uuid4()), db
        )
    assert exc.value.status_code == 400


def test_name_from_filename_humanizes() -> None:
    from app.routers.hr_applicants import _name_from_filename

    assert _name_from_filename("Jane_Doe_Resume.pdf") == "Jane Doe Resume"
    assert _name_from_filename("C:/tmp/john-smith.PDF") == "john smith"
    assert _name_from_filename("résumé.pdf") == "résumé"
    assert _name_from_filename(".pdf") == "Unnamed candidate"
    assert len(_name_from_filename("x" * 500 + ".pdf")) <= 200


@pytest.mark.asyncio
async def test_bulk_upload_rejects_oversized_batch() -> None:
    """More than the per-batch cap -> 400 before any file is touched."""
    from app.routers.hr_applicants import _MAX_BULK_FILES, bulk_upload_applicants

    db = AsyncMock()
    too_many = [object()] * (_MAX_BULK_FILES + 1)
    with pytest.raises(HTTPException) as exc:
        await bulk_upload_applicants(
            files=too_many,  # type: ignore[arg-type]
            target_job_title="Engineer",
            ctx=(uuid.uuid4(), uuid.uuid4()),
            db=db,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_bulk_upload_empty_batch_400() -> None:
    from app.routers.hr_applicants import bulk_upload_applicants

    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await bulk_upload_applicants(
            files=[], target_job_title="Engineer", ctx=(uuid.uuid4(), uuid.uuid4()), db=db
        )
    assert exc.value.status_code == 400


def test_apply_score_maps_fields() -> None:
    from app.models import Applicant
    from app.routers.hr_applicants import _apply_score

    a = Applicant()
    _apply_score(
        a,
        {
            "overall": 77,
            "breakdown": {"skills_match": 80, "experience_relevance": 70},
            "strengths": ["x"],
            "concerns": ["y"],
            "recommendation": "strong_fit",
            "summary": "Strong.",
        },
    )
    assert a.ats_overall == 77
    assert a.ats_recommendation == "strong_fit"
    assert a.ats_breakdown == {"skills_match": 80, "experience_relevance": 70}
