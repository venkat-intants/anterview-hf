"""L6: the analytics `admin` role cannot erase staff accounts or itself.

Before: any token holding `admin` could POST /users/{id}/dpdp/delete for ANY user,
including a platform_owner or a company super_admin, which soft-deletes the account,
revokes its tokens and schedules the 30-day purge. Authorisation only -- the
executor and retention logic are untouched.

Reuses the erasure suite's mock-session app builder so the routes are exercised
through their real dependencies (real signed tokens, no live DB or Redis).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from shared.auth.jwt import issue_access_token

from app.config import settings
from app.routers import erasure as erasure_mod
from tests.test_erasure import (
    _ADMIN_SUB,
    _ERASURE_URL,
    _KNOWN_USER_ID,
    _build_test_app,
    _fake_user,
    _make_mock_session,
)


def _token(roles: list[str], sub: str = _ADMIN_SUB) -> str:
    return issue_access_token(
        user_id=sub,
        roles=roles,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )


def _post(roles: list[str], url: str = _ERASURE_URL, sub: str = _ADMIN_SUB) -> Any:
    session = _make_mock_session(user=_fake_user(), existing_erasure=None)
    client = TestClient(_build_test_app(session), raise_server_exceptions=False)
    return client.post(url, headers={"Authorization": f"Bearer {_token(roles, sub)}"}), session


def test_analytics_admin_cannot_erase_a_staff_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        erasure_mod, "_target_staff_roles", AsyncMock(return_value={"platform_owner"})
    )
    resp, session = _post(["admin"])
    assert resp.status_code == 403, resp.text
    assert "platform owner" in resp.json()["detail"].lower()
    # Nothing was written: no erasure row, no soft-delete.
    assert not any("erasure_requests" in sql and "INSERT" in sql for sql in session.executed_sql)
    session.commit.assert_not_awaited()


def test_platform_owner_can_erase_a_staff_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        erasure_mod, "_target_staff_roles", AsyncMock(return_value={"hr_manager"})
    )
    # platform_owner also holds `admin` (it passes the route's own admin gate).
    resp, _session = _post(["platform_owner", "admin"])
    assert resp.status_code == 202, resp.text


def test_analytics_admin_can_still_erase_an_ordinary_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(erasure_mod, "_target_staff_roles", AsyncMock(return_value=set()))
    resp, _session = _post(["admin"])
    assert resp.status_code == 202, resp.text


def test_operator_route_refuses_self_erasure() -> None:
    resp, session = _post(["admin"], url=f"/admin/users/{_ADMIN_SUB}/dpdp/delete")
    assert resp.status_code == 403
    assert "/users/me/dpdp/delete" in resp.json()["detail"]
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_target_staff_roles_ignores_candidate_roles() -> None:
    """Deny-by-default: anything that is not a candidate role counts as staff."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=[("candidate",), ("interviewer",)])
    assert await erasure_mod._target_staff_roles(db, uuid.UUID(_KNOWN_USER_ID)) == {"interviewer"}
