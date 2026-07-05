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


def _db_with(row: tuple, caller_company: uuid.UUID | None) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(fetchone=MagicMock(return_value=row)))
    db.scalar = AsyncMock(return_value=caller_company)
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
