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


class _HrDirectory:
    """A stand-in for the users ⋈ user_roles lookup in delete_my_hr_manager,
    driven by the SQL the router actually emits.

    A `db.scalar = AsyncMock(return_value=...)` answers the same regardless of
    the query, so the tenancy predicate could be deleted from the router and the
    isolation test would stay green. This fake instead interprets the statement:
    the target must be a known HR manager, and while the SQL still carries the
    ``u.company_id = :cid`` predicate the HR's company must equal the bound
    :cid. Remove that predicate and the cross-company lookup starts matching —
    which is precisely what test_delete_my_hr_manager_other_company_404 fails on.
    """

    def __init__(self, hr_managers: dict[uuid.UUID, uuid.UUID]) -> None:
        self._hr = hr_managers
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def scalar(self, stmt: object, params: dict[str, object] | None = None) -> int | None:
        sql = " ".join(str(stmt).split())
        params = params or {}
        self.calls.append((sql, params))
        company = self._hr.get(params["uid"])  # type: ignore[arg-type]
        if company is None:
            return None
        if "u.company_id = :cid" in sql and company != params.get("cid"):
            return None
        return 1


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
async def test_delete_company_tombstones_slug() -> None:
    """The soft-delete must release the slug (slug || '-deleted-' || id) so a
    company with the same name can be created again afterwards."""
    from app.routers.admin_hr import delete_company

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=1)
    member_result = MagicMock()
    member_result.fetchall.return_value = []
    db.execute = AsyncMock(return_value=member_result)

    await delete_company(uuid.uuid4(), _platform_owner(), db, _mock_auth())

    company_updates = [
        str(call.args[0])
        for call in db.execute.await_args_list
        if "UPDATE companies" in str(call.args[0])
    ]
    assert company_updates, "expected an UPDATE companies statement"
    assert "slug = slug || '-deleted-' || id::text" in company_updates[0]


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
    caller_uid, company_id = uuid.uuid4(), uuid.uuid4()
    hr_uid = uuid.uuid4()
    directory = _HrDirectory({hr_uid: company_id})  # HR belongs to the caller's company
    db = AsyncMock()
    db.scalar = directory.scalar
    auth = _mock_auth()

    from app.routers.admin_hr import delete_my_hr_manager

    await delete_my_hr_manager(hr_uid, (caller_uid, company_id), db, auth)
    db.commit.assert_awaited_once()
    # Session revocation must be called for the removed HR.
    auth.logout_all.assert_awaited_once_with(str(hr_uid))
    # The company scope is bound from the caller's session ctx, never from input.
    assert directory.calls[0][1]["cid"] == company_id


@pytest.mark.asyncio
async def test_delete_my_hr_manager_other_company_404() -> None:
    """The isolation boundary: a super admin of company A may not delete an HR
    of company B, even knowing their user id.

    The same directory answers the happy path above, so this 404 comes from the
    company scoping in the query — not from a fake that refuses everything.
    """
    caller_uid, company_a, company_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    other_hr_uid = uuid.uuid4()
    directory = _HrDirectory({other_hr_uid: company_b})
    db = AsyncMock()
    db.scalar = directory.scalar

    from app.routers.admin_hr import delete_my_hr_manager

    with pytest.raises(HTTPException) as exc:
        await delete_my_hr_manager(other_hr_uid, (caller_uid, company_a), db, _mock_auth())
    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()
    # Nothing may be written before the scope check — no soft-delete, no audit row.
    db.execute.assert_not_awaited()


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
