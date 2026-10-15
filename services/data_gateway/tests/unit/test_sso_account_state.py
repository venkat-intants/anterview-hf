"""SSO callbacks must respect account state and must not keep a squatter's password.

M4: ``INSERT ... ON CONFLICT DO UPDATE SET is_active = true`` revived soft-deleted
(DPDP-erased) and admin-suspended accounts on the next SSO sign-in.
H2: Google's upsert conflicted on ``email`` and kept ``password_hash``, so an
attacker who registered the victim's address first (registration issues a session
without proving the address) kept a working password after the victim signed in
with Google.

DB is faked: what is under test is the decision logic and the shape of the SQL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

NOW = datetime.now(tz=UTC)


def _row(**kw: object) -> MagicMock:
    row = MagicMock()
    for k, v in kw.items():
        setattr(row, k, v)
    return row


def _res(first: object = None, fetchone: object = None) -> MagicMock:
    r = MagicMock()
    r.first = MagicMock(return_value=first)
    r.fetchone = MagicMock(return_value=fetchone)
    return r


def _db(*results: MagicMock) -> AsyncMock:
    db = AsyncMock()
    queue = list(results)

    async def _execute(*_a: Any, **_k: Any) -> MagicMock:
        return queue.pop(0) if queue else _res()

    db.execute = AsyncMock(side_effect=_execute)
    return db


def _compiled(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _do_update_clause(sql: str) -> str:
    return sql.split("DO UPDATE SET", 1)[1]


# --------------------------------------------------------------------------- Google
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        _row(id=uuid.uuid4(), password_hash=None, email_verified_at=NOW,
             deleted_at=NOW, is_active=False),
        _row(id=uuid.uuid4(), password_hash="$2b$x", email_verified_at=NOW,
             deleted_at=NOW, is_active=True),
        _row(id=uuid.uuid4(), password_hash=None, email_verified_at=NOW,
             deleted_at=None, is_active=False),
    ],
    ids=["deleted+inactive", "deleted", "suspended"],
)
async def test_google_refuses_deleted_or_suspended_and_writes_nothing(state: MagicMock) -> None:
    from app.routers.sso_google import _upsert_google_user

    db = _db(_res(first=state))
    with pytest.raises(HTTPException) as exc:
        await _upsert_google_user(db, email="a@example.com", full_name="A", now_utc=NOW)
    assert exc.value.status_code == 403
    # The refusal is the SAME one the privileged-account gate uses.
    assert exc.value.detail == "GOOGLE_SIGNIN_CANDIDATES_ONLY"
    assert db.execute.await_count == 1, "only the lookup may run; nothing may be written"


@pytest.mark.asyncio
async def test_google_upsert_no_longer_sets_is_active() -> None:
    from app.routers.sso_google import _upsert_google_user

    uid = uuid.uuid4()
    live = _row(id=uid, password_hash=None, email_verified_at=NOW, deleted_at=None, is_active=True)
    db = _db(_res(first=live), _res(fetchone=(uid,)))
    out_id, taken = await _upsert_google_user(db, email="a@example.com", full_name="A", now_utc=NOW)
    assert (out_id, taken) == (uid, False)
    clause = _do_update_clause(_compiled(db.execute.call_args_list[1].args[0]))
    assert "is_active" not in clause


@pytest.mark.asyncio
async def test_google_takes_over_an_unverified_password_account() -> None:
    from app.routers.sso_google import _upsert_google_user

    uid = uuid.uuid4()
    squatted = _row(
        id=uid, password_hash="$2b$attacker", email_verified_at=None,
        deleted_at=None, is_active=True,
    )
    db = _db(_res(first=squatted), _res(), _res(fetchone=(uid,)))
    out_id, taken = await _upsert_google_user(db, email="v@example.com", full_name="V", now_utc=NOW)
    assert taken is True and out_id == uid
    update_sql = str(db.execute.call_args_list[1].args[0])
    assert "password_hash = NULL" in update_sql and "email_verified_at" in update_sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        _row(id=uuid.uuid4(), password_hash="$2b$x", email_verified_at=NOW,
             deleted_at=None, is_active=True),
        _row(id=uuid.uuid4(), password_hash=None, email_verified_at=None,
             deleted_at=None, is_active=True),
        None,
    ],
    ids=["verified-password", "passwordless", "new-account"],
)
async def test_google_leaves_verified_passwordless_and_new_accounts_alone(
    state: MagicMock | None,
) -> None:
    from app.routers.sso_google import _upsert_google_user

    uid = uuid.uuid4()
    db = _db(_res(first=state), _res(fetchone=(uid,)))
    _, taken = await _upsert_google_user(db, email="a@example.com", full_name="A", now_utc=NOW)
    assert taken is False
    assert db.execute.await_count == 2  # lookup + upsert, no password-clearing UPDATE


# ------------------------------------------------------------------------ Naipunyam
def _profile() -> MagicMock:
    return _row(name="N", phone=None, preferred_language="en", email="n@example.com")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        _row(id=uuid.uuid4(), deleted_at=NOW, is_active=True),
        _row(id=uuid.uuid4(), deleted_at=None, is_active=False),
    ],
    ids=["deleted", "suspended"],
)
async def test_naipunyam_refuses_deleted_or_suspended_and_writes_nothing(state: MagicMock) -> None:
    from app.routers.sso_naipunyam import _upsert_naipunyam_user

    db = _db(_res(first=state))
    with pytest.raises(HTTPException) as exc:
        await _upsert_naipunyam_user(
            db, user_id=uuid.uuid4(), email="n@example.com", naipunyam_uid="n1",
            profile=_profile(), now_utc=NOW,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "NAIPUNYAM_SIGNIN_CANDIDATES_ONLY"
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_naipunyam_upsert_no_longer_sets_is_active() -> None:
    from app.routers.sso_naipunyam import _upsert_naipunyam_user

    uid = uuid.uuid4()
    db = _db(
        _res(first=_row(id=uid, deleted_at=None, is_active=True)),
        _res(fetchone=(uid,)),
    )
    out = await _upsert_naipunyam_user(
        db, user_id=uuid.uuid4(), email="n@example.com", naipunyam_uid="n1",
        profile=_profile(), now_utc=NOW,
    )
    assert out == uid
    clause = _do_update_clause(_compiled(db.execute.call_args_list[1].args[0]))
    assert "is_active" not in clause
