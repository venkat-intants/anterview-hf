"""Admin mutations are audited, the feed reads as sentences, and the console is
told honestly whether a set-password email went out (audit AD-F3, AD-F8).

DB is mocked; tests/integration/test_admin_audit_feed_db.py runs the same paths
against a migrated Postgres.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from shared.auth.base import User


def _platform_owner() -> User:
    return User(
        user_id=str(uuid.uuid4()), full_name="Owner", email="a@b.c", roles=["platform_owner"],
    )


def _recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    from app.routers import admin_hr

    audits: list[dict[str, object]] = []

    async def _audit(_db: object, **kw: object) -> None:
        audits.append(kw)

    monkeypatch.setattr(admin_hr, "_audit", _audit)
    return audits


def test_audit_summary_reads_as_a_sentence() -> None:
    from app.routers.admin_hr import _audit_summary

    assert _audit_summary("create_hr_manager", None, "Hema HR") == "Added HR manager Hema HR"
    assert (
        _audit_summary("delete_interviewer", {"assignments_withdrawn": 2}, "Ivan")
        == "Removed interviewer Ivan (2 open interviews withdrawn)"
    )
    assert (
        _audit_summary("delete_hr_manager", {"assignments_withdrawn": 1}, None)
        == "Removed HR manager a removed account (1 open interview withdrawn)"
    )
    assert (
        _audit_summary("toggle_feature_flag", {"key": "agents", "enabled": False}, None)
        == "Turned feature flag agents off"
    )
    # An action nobody mapped yet is humanised, never dropped or shown raw.
    assert _audit_summary("candidate.exam_link.minted", None, None) == "Candidate exam link minted"


@pytest.mark.asyncio
async def test_create_company_writes_an_audit_row(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import admin_hr

    audits = _recorder(monkeypatch)
    db = AsyncMock()
    await admin_hr.create_company(admin_hr.CreateCompanyBody(name="Acme"), _platform_owner(), db)
    assert [a["action"] for a in audits] == ["create_company"]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_toggle_feature_flag_writes_an_audit_row(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import admin_hr

    audits = _recorder(monkeypatch)
    db = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = ("agents", "Agents", None, True, None)
    db.execute = AsyncMock(return_value=result)
    await admin_hr.toggle_feature_flag(
        "agents", admin_hr.FeatureFlagToggle(enabled=True), _platform_owner(), db
    )
    assert audits[0]["action"] == "toggle_feature_flag"
    assert audits[0]["details"] == {"key": "agents", "enabled": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("creator", "action"),
    [("create_my_hr_manager", "create_hr_manager"), ("create_my_interviewer", "create_interviewer")],
)
async def test_staff_creation_is_audited_once_in_the_same_commit(
    monkeypatch: pytest.MonkeyPatch, creator: str, action: str
) -> None:
    from app.routers import admin_hr

    audits = _recorder(monkeypatch)
    monkeypatch.setattr(admin_hr, "enqueue_email", AsyncMock(return_value=None))
    db = AsyncMock()
    admin_uid = uuid.uuid4()
    with patch("app.routers.admin_hr._hash_password", new=AsyncMock(return_value="hashed")):
        await getattr(admin_hr, creator)(
            admin_hr.CreateUserBody(email="p@acme.com", full_name="P"),
            (admin_uid, uuid.uuid4()),
            db,
        )
    assert [a["action"] for a in audits] == [action]
    assert audits[0]["actor_id"] == admin_uid


@pytest.mark.asyncio
async def test_create_hr_manager_reports_when_the_email_was_not_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed send must not be reported as "a link was emailed"."""
    from app.routers import admin_hr

    async def _fail(*_a: object, **_k: object) -> None:
        raise RuntimeError("mail down")

    monkeypatch.setattr(admin_hr, "enqueue_email", _fail)
    db = AsyncMock()
    with patch("app.routers.admin_hr._hash_password", new=AsyncMock(return_value="hashed")):
        resp = await admin_hr.create_my_hr_manager(
            admin_hr.CreateUserBody(email="hr@acme.com", full_name="HR"),
            (uuid.uuid4(), uuid.uuid4()),
            db,
        )
    assert resp.email_queued is False


@pytest.mark.asyncio
async def test_a_skipped_send_is_not_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import admin_hr

    monkeypatch.setattr(admin_hr, "enqueue_email", AsyncMock(return_value=None))
    db = AsyncMock()
    with patch("app.routers.admin_hr._hash_password", new=AsyncMock(return_value="hashed")):
        resp = await admin_hr.create_my_hr_manager(
            admin_hr.CreateUserBody(email="hr@acme.com", full_name="HR"),
            (uuid.uuid4(), uuid.uuid4()),
            db,
        )
    assert resp.email_queued is False


@pytest.mark.asyncio
async def test_a_queued_send_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import admin_hr

    monkeypatch.setattr(admin_hr, "enqueue_email", AsyncMock(return_value=object()))
    db = AsyncMock()
    with patch("app.routers.admin_hr._hash_password", new=AsyncMock(return_value="hashed")):
        resp = await admin_hr.create_my_hr_manager(
            admin_hr.CreateUserBody(email="hr@acme.com", full_name="HR"),
            (uuid.uuid4(), uuid.uuid4()),
            db,
        )
    assert resp.email_queued is True


@pytest.mark.asyncio
async def test_resend_refuses_an_account_that_already_set_a_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import admin_hr

    audits = _recorder(monkeypatch)
    db = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = ("hr@acme.com", "HR", False)
    db.execute = AsyncMock(return_value=result)
    with pytest.raises(HTTPException) as exc:
        await admin_hr.resend_my_hr_manager_invite(uuid.uuid4(), (uuid.uuid4(), uuid.uuid4()), db)
    assert exc.value.status_code == 409
    assert audits == []


@pytest.mark.asyncio
async def test_resend_scopes_the_lookup_to_the_callers_company_and_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import admin_hr

    _recorder(monkeypatch)
    db = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = None
    db.execute = AsyncMock(return_value=result)
    company_id = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        await admin_hr.resend_my_interviewer_invite(uuid.uuid4(), (uuid.uuid4(), company_id), db)
    assert exc.value.status_code == 404
    stmt, params = db.execute.await_args.args
    sql = " ".join(str(stmt).split())
    assert "u.company_id = :cid" in sql and "r.name = :role" in sql
    assert params["cid"] == company_id
    assert params["role"] == "interviewer"
