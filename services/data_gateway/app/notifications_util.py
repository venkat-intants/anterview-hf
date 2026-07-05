"""Notification producer helper.

Event handlers call ``create_notification`` to stage an in-app feed item on the
current request's DB session (the caller owns the commit). No-op when the target
user is unknown, so producers can call it unconditionally.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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
) -> None:
    """Stage a notification for ``user_id`` (caller commits). No-op if user_id is None."""
    if user_id is None:
        return
    db.add(
        Notification(
            id=uuid.uuid4(),
            user_id=user_id,
            kind=kind,
            title=title,
            body=body,
            link=link,
            created_at=datetime.now(tz=UTC),
        )
    )
