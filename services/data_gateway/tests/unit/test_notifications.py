"""Unit tests for the in-app notification feed — DG-7.

108 lines backing the AppShell header bell, previously with no test at all. The
isolation boundary here is per-USER (not per-company): every query filters on
``Notification.user_id`` taken from the session, so another user's notification
id must read as missing rather than forbidden.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from shared.auth.base import User

_NOW = datetime(2026, 8, 7, 9, 30, tzinfo=UTC)


def _user(uid: uuid.UUID | None = None) -> User:
    return User(
        user_id=str(uid or uuid.uuid4()),
        full_name="Candidate",
        email="c@example.com",
        roles=["candidate"],
    )


def _notification(**over: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "kind": "invite_sent",
        "title": "You have an interview",
        "body": "Tomorrow at 10:00",
        "link": "/interview/abc",
        "read_at": None,
        "created_at": _NOW,
    }
    return SimpleNamespace(**{**defaults, **over})


def _rows(*items: Any) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(items)
    return result


def _db(*, scalars: list[Any] | None = None, executes: list[Any] | None = None) -> AsyncMock:
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=list(scalars or []))
    db.execute = AsyncMock(side_effect=list(executes or []))
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# GET /notifications
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_returns_items_and_unread_count() -> None:
    from app.routers.notifications import list_notifications

    unread, read = _notification(), _notification(read_at=_NOW)
    db = _db(scalars=[1], executes=[_rows(unread, read)])

    out = await list_notifications(_user(), db, limit=30)

    assert [i.id for i in out.items] == [str(unread.id), str(read.id)]
    assert [i.read for i in out.items] == [False, True]
    assert out.unread_count == 1
    assert out.items[0].created_at == _NOW.isoformat()


@pytest.mark.asyncio
async def test_list_scopes_the_query_to_the_calling_user() -> None:
    """The user id comes from the verified session, never from a parameter —
    there is no argument on this endpoint that could widen it to another user."""
    from app.routers.notifications import list_notifications

    uid = uuid.uuid4()
    db = _db(scalars=[0], executes=[_rows()])

    await list_notifications(_user(uid), db, limit=5)

    compiled = db.execute.await_args.args[0]
    assert "notifications.user_id" in str(compiled)
    assert compiled.compile().params["user_id_1"] == uid


@pytest.mark.asyncio
async def test_list_unread_count_of_none_is_reported_as_zero() -> None:
    """A COUNT that comes back NULL (no rows at all) must not become None in the
    response model, which is typed int and would 500 on serialisation."""
    from app.routers.notifications import list_notifications

    db = _db(scalars=[None], executes=[_rows()])

    out = await list_notifications(_user(), db, limit=30)

    assert out.unread_count == 0
    assert out.items == []


# ---------------------------------------------------------------------------
# POST /notifications/{id}/read
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mark_read_on_another_users_notification_is_404() -> None:
    """The isolation boundary. 404 rather than 403 so an id cannot be probed for
    existence across accounts."""
    from app.routers.notifications import mark_read

    db = _db(scalars=[None])  # user_id predicate matched nothing
    with pytest.raises(HTTPException) as exc:
        await mark_read(uuid.uuid4(), _user(), db)

    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_read_sets_the_timestamp_and_commits() -> None:
    from app.routers.notifications import mark_read

    n = _notification(read_at=None)
    db = _db(scalars=[n])

    assert await mark_read(n.id, _user(), db) == {"ok": True}
    assert n.read_at is not None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_read_is_idempotent_and_does_not_rewrite_the_timestamp() -> None:
    """Re-reading an already-read item must not move read_at — the bell polls
    this endpoint, and a rewrite would be a pointless write per poll."""
    from app.routers.notifications import mark_read

    already = _notification(read_at=_NOW)
    db = _db(scalars=[already])

    assert await mark_read(already.id, _user(), db) == {"ok": True}
    assert already.read_at == _NOW
    db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# POST /notifications/read-all
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mark_all_read_touches_only_this_users_unread_rows() -> None:
    from app.routers.notifications import mark_all_read

    uid = uuid.uuid4()
    db = _db(executes=[MagicMock()])

    assert await mark_all_read(_user(uid), db) == {"ok": True}

    stmt = db.execute.await_args.args[0]
    rendered = str(stmt)
    assert "UPDATE notifications" in rendered
    assert "notifications.user_id" in rendered
    # Already-read rows are excluded, so a second call is a no-op write rather
    # than one that resets every timestamp in the feed.
    assert "read_at IS NULL" in rendered
    db.commit.assert_awaited_once()
