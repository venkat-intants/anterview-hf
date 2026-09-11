"""Notification producer helper.

Event handlers call ``create_notification`` to stage an in-app feed item on the
current request's DB session (the caller owns the commit). No-op when the target
user is unknown, so producers can call it unconditionally.

Producers that announce an *event* — something a retry or a second sweep could
see again — pass a ``dedupe_key`` naming it (``interview_completed:<invite_id>``).
The row is then inserted with ``ON CONFLICT DO NOTHING`` against the partial
unique index, so the database, not the producer's bookkeeping, guarantees the
event is announced once.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification


async def create_notification(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    kind: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    dedupe_key: str | None = None,
) -> bool:
    """Stage a notification for ``user_id`` (caller commits).

    Returns True when a row was staged, False when there was nobody to tell or
    ``dedupe_key`` has already been used.
    """
    if user_id is None:
        return False
    values = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "kind": kind,
        "title": title,
        "body": body,
        "link": link,
        "created_at": datetime.now(tz=UTC),
    }
    if dedupe_key is None:
        db.add(Notification(**values))
        return True

    stmt = (
        pg_insert(Notification)
        .values(**values, dedupe_key=dedupe_key)
        .on_conflict_do_nothing(
            index_elements=["dedupe_key"],
            index_where=text("dedupe_key IS NOT NULL"),
        )
        .returning(Notification.id)
    )
    return (await db.execute(stmt)).first() is not None
