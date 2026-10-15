"""Tenant-isolation tests for GET /users/{id}/profile.

A company-scoped viewer (hr_manager / super_admin) may only see users in its
OWN company; platform roles (admin / platform_owner) see everyone. DB is mocked.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from shared.auth.base import User


def _row(company_id: uuid.UUID | None, roles: list[str]) -> tuple:
    """A profile row matching the SELECT column order in profile.py (17 cols)."""
    return (
        "Jane Doe", "jane@example.com", None, None, None, None, None, None, None,
        None, None, None, None, None, "Acme Technologies", company_id, roles,
    )


def _db_with(
    row: tuple, caller_company: uuid.UUID | None, *, applied_here: bool = False
) -> AsyncMock:
    """scalar() answers the caller's company first, then whether the target has
    a live applicant row in it."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(fetchone=MagicMock(return_value=row)))
    db.scalar = AsyncMock(side_effect=[caller_company, 1 if applied_here else None])
    return db


@pytest.mark.asyncio
async def test_super_admin_cannot_view_other_company_profile() -> None:
    from app.routers.profile import get_user_profile

    company_a, company_b = uuid.uuid4(), uuid.uuid4()
    viewer = User(user_id=str(uuid.uuid4()), full_name="", email="", roles=["super_admin"])
    target_id = uuid.uuid4()
    db = _db_with(_row(company_b, ["candidate"]), caller_company=company_a)

    with pytest.raises(HTTPException) as exc:
        await get_user_profile(target_id, viewer, db)
    assert exc.value.status_code == 404  # out-of-tenant looks like "not found"


@pytest.mark.asyncio
async def test_super_admin_can_view_own_company_profile() -> None:
    from app.routers.profile import get_user_profile

    company = uuid.uuid4()
    viewer = User(user_id=str(uuid.uuid4()), full_name="", email="", roles=["super_admin"])
    db = _db_with(_row(company, ["candidate"]), caller_company=company)

    resp = await get_user_profile(uuid.uuid4(), viewer, db)
    assert resp.full_name == "Jane Doe"
    assert resp.company_name == "Acme Technologies"


@pytest.mark.asyncio
async def test_platform_owner_views_any_company_profile() -> None:
    from app.routers.profile import get_user_profile

    viewer = User(user_id=str(uuid.uuid4()), full_name="", email="", roles=["platform_owner"])
    # Target in some company; caller resolution must NOT even be consulted.
    db = _db_with(_row(uuid.uuid4(), ["candidate"]), caller_company=None)

    resp = await get_user_profile(uuid.uuid4(), viewer, db)
    assert resp.full_name == "Jane Doe"
    db.scalar.assert_not_awaited()  # global viewer skips the tenant check


# ---------------------------------------------------------------------------
# QA ANTHIRE-005 — "View full profile" on an applicant who claimed their account
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hr_can_view_an_applicant_of_their_company_whose_account_has_no_company() -> None:
    """Claiming the account clears users.company_id, so the member test alone
    returned 404 for every activated applicant."""
    from app.routers.profile import get_user_profile

    company = uuid.uuid4()
    viewer = User(user_id=str(uuid.uuid4()), full_name="", email="", roles=["hr_manager"])
    db = _db_with(_row(None, ["candidate"]), caller_company=company, applied_here=True)

    resp = await get_user_profile(uuid.uuid4(), viewer, db)
    assert resp.full_name == "Jane Doe"
    sql, params = db.scalar.await_args_list[1].args
    # Scoped to the CALLER's company and a live, unrejected application.
    assert "WHERE a.user_id = :t AND a.company_id = :c" in str(sql)
    assert "e.status <> 'rejected'" in str(sql)
    assert params["c"] == company
    # Minimised on this basis: nothing about the person's current job.
    assert resp.official_email is None and resp.employment_status is None
    assert resp.company_name is None and resp.roles == []


@pytest.mark.asyncio
async def test_hr_still_cannot_view_a_user_who_never_applied_to_their_company() -> None:
    from app.routers.profile import get_user_profile

    viewer = User(user_id=str(uuid.uuid4()), full_name="", email="", roles=["hr_manager"])
    db = _db_with(_row(None, ["candidate"]), caller_company=uuid.uuid4(), applied_here=False)
    with pytest.raises(HTTPException) as exc:
        await get_user_profile(uuid.uuid4(), viewer, db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_staff_of_another_company_stay_hidden_even_if_they_applied_here() -> None:
    """The applicant path is for candidate accounts only (no company_id)."""
    from app.routers.profile import get_user_profile

    viewer = User(user_id=str(uuid.uuid4()), full_name="", email="", roles=["hr_manager"])
    db = _db_with(_row(uuid.uuid4(), ["hr_manager"]), caller_company=uuid.uuid4(), applied_here=True)
    with pytest.raises(HTTPException) as exc:
        await get_user_profile(uuid.uuid4(), viewer, db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_super_admin_gets_no_candidate_profile_through_the_applicant_path() -> None:
    """Security review M1: only hr_manager reaches a named candidate."""
    from app.routers.profile import get_user_profile

    viewer = User(user_id=str(uuid.uuid4()), full_name="", email="", roles=["super_admin"])
    db = _db_with(_row(None, ["candidate"]), caller_company=uuid.uuid4(), applied_here=True)
    with pytest.raises(HTTPException) as exc:
        await get_user_profile(uuid.uuid4(), viewer, db)
    assert exc.value.status_code == 404
