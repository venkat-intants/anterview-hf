"""L10: email-verify and password-reset tokens are consumed by ONE conditional UPDATE.

The old shape read the token row, tested ``consumed_at``/``expires_at`` in Python and
then set ``consumed_at``: two concurrent submissions of one reset link both passed the
test and both succeeded. The predicate now lives in the UPDATE and ``RETURNING`` says
who won. DB is faked; what is asserted is the SQL shape and the branch taken.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

UID = uuid.uuid4()


def _res(first: Any = None) -> MagicMock:
    r = MagicMock()
    r.first = MagicMock(return_value=first)
    return r


def _db(*results: MagicMock) -> AsyncMock:
    db = AsyncMock()
    queue = list(results)

    async def _execute(*_a: Any, **_k: Any) -> MagicMock:
        return queue.pop(0) if queue else _res()

    db.execute = AsyncMock(side_effect=_execute)
    return db


def _sqls(db: AsyncMock) -> list[str]:
    return [str(c.args[0]) for c in db.execute.call_args_list]


@pytest.mark.asyncio
async def test_losing_the_race_or_a_dead_token_returns_none_and_touches_nothing_else() -> None:
    from app.routers.auth import _consume_auth_token

    db = _db(_res(None))  # the conditional UPDATE matched no row
    assert await _consume_auth_token(db, "tok", "password_reset", datetime.now(tz=UTC)) is None
    assert len(_sqls(db)) == 1


@pytest.mark.asyncio
async def test_winning_consumes_atomically_then_sweeps_the_users_other_tokens() -> None:
    from app.routers.auth import _consume_auth_token

    db = _db(_res((UID,)), _res())
    out = await _consume_auth_token(db, "tok", "password_reset", datetime.now(tz=UTC))
    assert out == UID
    first, second = _sqls(db)
    assert "UPDATE auth_tokens" in first and "RETURNING user_id" in first
    assert "consumed_at IS NULL" in first and "expires_at > :now" in first
    assert "user_id = :uid" in second and "consumed_at IS NULL" in second


@pytest.mark.asyncio
async def test_verify_email_rejects_when_the_conditional_update_matches_nothing() -> None:
    from app.routers.auth import VerifyEmailBody, verify_email

    db = _db(_res(None))
    with pytest.raises(HTTPException) as exc:
        await verify_email(VerifyEmailBody(token="x" * 20), db)
    assert exc.value.status_code == 400
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_password_rejects_without_hashing_when_the_token_is_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import auth as auth_mod

    hashed = MagicMock()
    monkeypatch.setattr(auth_mod.bcrypt, "hashpw", hashed)
    db = _db(_res(None))
    provider = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await auth_mod.reset_password(
            auth_mod.ResetPasswordBody(token="x" * 20, new_password="Another-Pass-1"), db, provider
        )
    assert exc.value.status_code == 400
    hashed.assert_not_called()  # a bogus token must not cost a bcrypt round
    provider.logout_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_password_success_updates_user_and_revokes_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import auth as auth_mod

    monkeypatch.setattr(auth_mod.bcrypt, "hashpw", lambda pw, salt: b"hashed")
    db = _db(_res((UID,)), _res(), _res())
    provider = AsyncMock()
    await auth_mod.reset_password(
        auth_mod.ResetPasswordBody(token="x" * 20, new_password="Another-Pass-1"), db, provider
    )
    assert any("SET password_hash" in s for s in _sqls(db))
    provider.logout_all.assert_awaited_once_with(str(UID))
