"""Unit tests for the three-tier admin hierarchy — HR workflow Phase 0+.

  platform_owner  → creates companies + the ONE company super admin
  super_admin     → creates HR managers scoped to its own company

DB is mocked so these run without infrastructure.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from shared.auth.base import AuthProvider, User


def _mock_auth() -> AsyncMock:
    """Return a minimal AuthProvider mock that records logout_all calls."""
    m = AsyncMock(spec=AuthProvider)
    m.logout_all = AsyncMock(return_value=0)
    return m


def _platform_owner() -> User:
    return User(
        user_id=str(uuid.uuid4()), full_name="Owner", email="a@b.c",
        roles=["platform_owner"],
    )


# ---------------------------------------------------------------------------
# require_role
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_role_allows_matching_role() -> None:
    from app.dependencies import require_role

    dep = require_role("platform_owner")
    user = _platform_owner()
    assert await dep(user) is user


@pytest.mark.asyncio
async def test_require_role_denies_missing_role() -> None:
    from app.dependencies import require_role

    dep = require_role("platform_owner")
    sa = User(user_id="x", full_name="", email="", roles=["super_admin"])
    with pytest.raises(HTTPException) as exc:
        await dep(sa)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_role_accepts_any_of_multiple() -> None:
    from app.dependencies import require_role

    dep = require_role("platform_owner", "admin")
    user = User(user_id="x", full_name="", email="", roles=["admin"])
    assert await dep(user) is user


# ---------------------------------------------------------------------------
# create_company (platform_owner)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_company_slugifies_name() -> None:
    from app.routers.admin_hr import CreateCompanyBody, create_company

    db = AsyncMock()
    resp = await create_company(
        CreateCompanyBody(name="Acme College!"), _platform_owner(), db
    )
    assert resp.name == "Acme College!"
    assert resp.slug == "acme-college"
    assert resp.hr_count == 0
    assert resp.has_admin is False
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# create_company_admin (platform_owner) — one per company
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_company_admin_happy_path() -> None:
    from app.routers.admin_hr import CreateUserBody, create_company_admin

    company_id = uuid.uuid4()
    db = AsyncMock()
    # scalars in order: FOR UPDATE lock (1), no existing super admin (None),
    # role-id lookup inside _create_company_user (7).
    db.scalar = AsyncMock(side_effect=[1, None, 7])

    with patch("app.routers.admin_hr._hash_password", new=AsyncMock(return_value="hashed")):
        resp = await create_company_admin(
            company_id,
            CreateUserBody(email="admin@acme.com", full_name="Company Admin"),
            _platform_owner(),
            db,
        )

    assert resp.email == "admin@acme.com"
    assert resp.company_id == str(company_id)
    assert resp.must_change_password is True
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_company_admin_rejects_second_admin_409() -> None:
    from app.routers.admin_hr import CreateUserBody, create_company_admin

    db = AsyncMock()
    # FOR UPDATE lock succeeds (1), then a super admin already exists for it.
    db.scalar = AsyncMock(side_effect=[1, "existing@acme.com"])

    with pytest.raises(HTTPException) as exc:
        await create_company_admin(
            uuid.uuid4(),
            CreateUserBody(email="second@acme.com", full_name="Second"),
            _platform_owner(),
            db,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_create_company_admin_unknown_company_404() -> None:
    from app.routers.admin_hr import CreateUserBody, create_company_admin

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)  # company missing

    with pytest.raises(HTTPException) as exc:
        await create_company_admin(
            uuid.uuid4(),
            CreateUserBody(email="admin@acme.com", full_name="Admin"),
            _platform_owner(),
            db,
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# get_company_admin_ctx — tenant isolation boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_company_admin_ctx_resolves_company() -> None:
    from app.routers.admin_hr import get_company_admin_ctx

    company_id = uuid.uuid4()
    user = User(user_id=str(uuid.uuid4()), full_name="", email="", roles=["super_admin"])
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=company_id)

    uid, cid = await get_company_admin_ctx(user, db)
    assert cid == company_id
    assert str(uid) == user.user_id


@pytest.mark.asyncio
async def test_company_admin_ctx_no_company_403() -> None:
    from app.routers.admin_hr import get_company_admin_ctx

    user = User(user_id=str(uuid.uuid4()), full_name="", email="", roles=["super_admin"])
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)  # not assigned to a company

    with pytest.raises(HTTPException) as exc:
        await get_company_admin_ctx(user, db)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# create_my_hr_manager (company super_admin, scoped to own company)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_my_hr_manager_happy_path() -> None:
    from app.routers.admin_hr import CreateUserBody, create_my_hr_manager

    admin_uid = uuid.uuid4()
    company_id = uuid.uuid4()
    db = AsyncMock()

    with patch("app.routers.admin_hr._hash_password", new=AsyncMock(return_value="hashed")):
        resp = await create_my_hr_manager(
            CreateUserBody(email="hr@acme.com", full_name="HR", password="12345678"),
            (admin_uid, company_id),
            db,
        )

    # The HR is pinned to the CALLER's company — never client-supplied.
    assert resp.email == "hr@acme.com"
    assert resp.company_id == str(company_id)
    assert resp.must_change_password is True
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# delete_company (platform_owner) — soft-deletes the company + its members
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_company_happy_path() -> None:
    from app.routers.admin_hr import delete_company

    member_uid = uuid.uuid4()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=1)  # company exists + locked
    # First execute = SELECT id FROM users (returns member list)
    member_result = MagicMock()
    member_result.fetchall.return_value = [(member_uid,)]
    db.execute = AsyncMock(return_value=member_result)

    auth = _mock_auth()
    await delete_company(uuid.uuid4(), _platform_owner(), db, auth)
    db.commit.assert_awaited_once()
    # Ensure logout_all was called for the member user
    auth.logout_all.assert_awaited_once_with(str(member_uid))


@pytest.mark.asyncio
async def test_delete_company_revocation_failure_does_not_block_204() -> None:
    """A Redis failure during session revocation must not fail the deletion."""
    from app.routers.admin_hr import delete_company

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=1)
    member_result = MagicMock()
    member_result.fetchall.return_value = [(uuid.uuid4(),)]
    db.execute = AsyncMock(return_value=member_result)

    auth = _mock_auth()
    auth.logout_all = AsyncMock(side_effect=RuntimeError("Redis down"))
    # Should NOT raise — best-effort
    await delete_company(uuid.uuid4(), _platform_owner(), db, auth)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_company_unknown_404() -> None:
    from app.routers.admin_hr import delete_company

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)  # company missing / already deleted
    with pytest.raises(HTTPException) as exc:
        await delete_company(uuid.uuid4(), _platform_owner(), db, _mock_auth())
    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# delete_company_admin (platform_owner)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_company_admin_happy_path() -> None:
    from app.routers.admin_hr import delete_company_admin

    admin_uid = uuid.uuid4()
    db = AsyncMock()
    # 1st scalar: _company_or_404 -> exists. 2nd scalar: the super admin's id.
    db.scalar = AsyncMock(side_effect=[1, admin_uid])
    auth = _mock_auth()
    await delete_company_admin(uuid.uuid4(), _platform_owner(), db, auth)
    db.commit.assert_awaited_once()
    # Session revocation must be called for the removed super admin.
    auth.logout_all.assert_awaited_once_with(str(admin_uid))


@pytest.mark.asyncio
async def test_delete_company_admin_none_404() -> None:
    from app.routers.admin_hr import delete_company_admin

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[1, None])  # company exists, but no super admin
    with pytest.raises(HTTPException) as exc:
        await delete_company_admin(uuid.uuid4(), _platform_owner(), db, _mock_auth())
    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# delete_my_hr_manager (company super_admin, scoped) — tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_my_hr_manager_happy_path() -> None:
    from app.routers.admin_hr import delete_my_hr_manager

    caller_uid, company_id = uuid.uuid4(), uuid.uuid4()
    hr_uid = uuid.uuid4()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=1)  # target is an HR in the caller's company
    auth = _mock_auth()
    await delete_my_hr_manager(hr_uid, (caller_uid, company_id), db, auth)
    db.commit.assert_awaited_once()
    # Session revocation must be called for the removed HR.
    auth.logout_all.assert_awaited_once_with(str(hr_uid))


@pytest.mark.asyncio
async def test_delete_my_hr_manager_other_company_404() -> None:
    from app.routers.admin_hr import delete_my_hr_manager

    caller_uid, company_id = uuid.uuid4(), uuid.uuid4()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)  # not an HR in the caller's company
    with pytest.raises(HTTPException) as exc:
        await delete_my_hr_manager(uuid.uuid4(), (caller_uid, company_id), db, _mock_auth())
    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_my_hr_manager_revocation_failure_does_not_block_204() -> None:
    """Session revocation failure must not prevent the HR deletion response."""
    from app.routers.admin_hr import delete_my_hr_manager

    caller_uid, company_id = uuid.uuid4(), uuid.uuid4()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=1)
    auth = _mock_auth()
    auth.logout_all = AsyncMock(side_effect=RuntimeError("Redis down"))
    # Should NOT raise — best-effort revocation
    await delete_my_hr_manager(uuid.uuid4(), (caller_uid, company_id), db, auth)
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# platform_stats — real counts mapped from a single aggregate query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_stats_maps_counts() -> None:
    from app.routers.admin_hr import platform_stats

    db = AsyncMock()
    # (companies, super_admins, hr_managers, candidates, interviews_total, interviews_30d)
    db.execute = AsyncMock(
        return_value=SimpleNamespace(fetchone=MagicMock(return_value=(2, 1, 3, 9, 42, 7)))
    )
    resp = await platform_stats(_platform_owner(), db)
    assert resp.companies == 2
    assert resp.super_admins == 1
    assert resp.hr_managers == 3
    assert resp.candidates == 9
    assert resp.interviews_total == 42
    assert resp.interviews_30d == 7
