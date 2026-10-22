"""Every admin mutation lands in the DPDP audit feed, readably â€” against a real,
migrated Postgres (audit AD-F3 and AD-F8).

The platform owner's "DPDP audit log" read ``audit_log``, but creating a
company, a super admin or an HR manager and toggling a feature flag never wrote
to it, and the rows that did appear showed the raw action code and the actor
"admin". These tests drive the router functions on one connection whose outer
transaction is rolled back, so the routers' own commits become savepoints and
nothing is left behind.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from shared.auth.base import User
from shared.db.engine import build_engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.routers import admin_hr

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
    )
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            session = AsyncSession(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint",
            )
            try:
                yield session
            finally:
                await session.close()
                await outer.rollback()
    finally:
        await engine.dispose()


async def _owner(db: AsyncSession) -> User:
    uid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, full_name) VALUES (:i, :e, 'Priya Owner')"),
        {"i": uid, "e": f"owner-{uid.hex[:10]}@example.com"},
    )
    return User(user_id=str(uid), full_name="Priya Owner", email="", roles=["platform_owner"])


async def _actions(db: AsyncSession) -> list[str]:
    rows = (await db.execute(text("SELECT action FROM audit_log"))).fetchall()
    return [r[0] for r in rows]


@pytest.mark.asyncio
async def test_every_admin_create_and_toggle_is_audited_and_reads_as_a_sentence(
    db: AsyncSession,
) -> None:
    owner = await _owner(db)
    tag = uuid.uuid4().hex[:8]
    with patch.object(admin_hr, "_hash_password", new=AsyncMock(return_value="x")):
        company = await admin_hr.create_company(
            admin_hr.CreateCompanyBody(name=f"Acme {tag}"), owner, db
        )
        cid = uuid.UUID(company.id)
        sa = await admin_hr.create_company_admin(
            cid,
            admin_hr.CreateUserBody(email=f"sa-{tag}@example.com", full_name="Sam Admin"),
            owner,
            db,
        )
        hr = await admin_hr.create_my_hr_manager(
            admin_hr.CreateUserBody(email=f"hr-{tag}@example.com", full_name="Hema HR"),
            (uuid.UUID(sa.user_id), cid),
            db,
        )
        await admin_hr.create_my_interviewer(
            admin_hr.CreateUserBody(email=f"iv-{tag}@example.com", full_name="Ivan Panel"),
            (uuid.UUID(sa.user_id), cid),
            db,
        )
    flag_key = await db.scalar(text("SELECT key FROM feature_flags ORDER BY key LIMIT 1"))
    assert flag_key is not None
    await admin_hr.toggle_feature_flag(
        flag_key, admin_hr.FeatureFlagToggle(enabled=True), owner, db
    )

    # AD-F8: the create response says whether the set-password email was queued.
    assert sa.email_queued is True
    assert hr.email_queued is True

    actions = await _actions(db)
    for expected in (
        "create_company", "create_company_admin", "create_hr_manager",
        "create_interviewer", "toggle_feature_flag",
    ):
        assert expected in actions, expected
    # One row per create: the interviewer path used to write its own on top.
    assert actions.count("create_interviewer") == 1

    feed = await admin_hr.audit_log(owner, db, limit=200)
    by_action = {e.action: e for e in feed if e.kind == "admin_action"}
    assert by_action["create_company"].summary == f"Created company Acme {tag}"
    assert by_action["create_company"].actor == "Priya Owner"
    assert by_action["create_company_admin"].summary == "Added super admin Sam Admin"
    assert by_action["create_hr_manager"].summary == "Added HR manager Hema HR"
    assert by_action["create_hr_manager"].actor == "Sam Admin"
    assert by_action["create_interviewer"].summary == "Added interviewer Ivan Panel"
    assert by_action["toggle_feature_flag"].summary == f"Turned feature flag {flag_key} on"


@pytest.mark.asyncio
async def test_resend_invite_is_tenant_scoped_pending_only_and_audited(
    db: AsyncSession,
) -> None:
    owner = await _owner(db)
    tag = uuid.uuid4().hex[:8]
    with patch.object(admin_hr, "_hash_password", new=AsyncMock(return_value="x")):
        a = await admin_hr.create_company(admin_hr.CreateCompanyBody(name=f"A {tag}"), owner, db)
        b = await admin_hr.create_company(admin_hr.CreateCompanyBody(name=f"B {tag}"), owner, db)
        cid_a, cid_b = uuid.UUID(a.id), uuid.UUID(b.id)
        sa_a = await admin_hr.create_company_admin(
            cid_a, admin_hr.CreateUserBody(email=f"sa-a-{tag}@example.com", full_name="SA A"),
            owner, db,
        )
        sa_b = await admin_hr.create_company_admin(
            cid_b, admin_hr.CreateUserBody(email=f"sa-b-{tag}@example.com", full_name="SA B"),
            owner, db,
        )
        hr = await admin_hr.create_my_hr_manager(
            admin_hr.CreateUserBody(email=f"hr-{tag}@example.com", full_name="Hema HR"),
            (uuid.UUID(sa_a.user_id), cid_a),
            db,
        )
    hr_id = uuid.UUID(hr.user_id)

    # Another company's super admin cannot reach this HR manager.
    with pytest.raises(HTTPException) as exc:
        await admin_hr.resend_my_hr_manager_invite(hr_id, (uuid.UUID(sa_b.user_id), cid_b), db)
    assert exc.value.status_code == 404

    # Nor can the HR path be used on an interviewer id, or vice versa.
    with pytest.raises(HTTPException) as exc:
        await admin_hr.resend_my_interviewer_invite(hr_id, (uuid.UUID(sa_a.user_id), cid_a), db)
    assert exc.value.status_code == 404

    out = await admin_hr.resend_my_hr_manager_invite(hr_id, (uuid.UUID(sa_a.user_id), cid_a), db)
    assert out.email_queued is True
    assert "resend_set_password_link" in await _actions(db)

    platform = await admin_hr.resend_company_admin_invite(cid_a, owner, db)
    assert platform.email_queued is True

    # Once they have chosen a password, a resend is refused.
    await db.execute(
        text("UPDATE users SET must_change_password = false WHERE id = :i"), {"i": hr_id}
    )
    with pytest.raises(HTTPException) as exc:
        await admin_hr.resend_my_hr_manager_invite(hr_id, (uuid.UUID(sa_a.user_id), cid_a), db)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_only_the_newest_set_password_link_is_live(db: AsyncSession) -> None:
    """Each resend used to add a live link without retiring the earlier ones,
    so every mailed link worked until one was used. Now the newest supersedes
    the rest."""
    owner = await _owner(db)
    tag = uuid.uuid4().hex[:8]
    with patch.object(admin_hr, "_hash_password", new=AsyncMock(return_value="x")):
        co = await admin_hr.create_company(admin_hr.CreateCompanyBody(name=f"T {tag}"), owner, db)
        cid = uuid.UUID(co.id)
        sa = await admin_hr.create_company_admin(
            cid, admin_hr.CreateUserBody(email=f"sa-t-{tag}@example.com", full_name="SA T"),
            owner, db,
        )
    sa_id = uuid.UUID(sa.user_id)
    for _ in range(2):
        out = await admin_hr.resend_company_admin_invite(cid, owner, db)
        assert out.email_queued is True
    counts = (
        await db.execute(
            text(
                "SELECT count(*) FILTER (WHERE consumed_at IS NULL),"
                "       count(*) FILTER (WHERE consumed_at IS NOT NULL)"
                "  FROM auth_tokens WHERE user_id = :u AND kind = 'password_reset'"
            ),
            {"u": sa_id},
        )
    ).one()
    assert tuple(counts) == (1, 2)
