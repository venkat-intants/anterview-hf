"""In-app notifications feed — backs the AppShell header bell.

Any authenticated user reads their own feed. Rows are produced by event handlers
via app.notifications_util.create_notification. read_at NULL = unread.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from shared.auth.base import User
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models import Notification

router = APIRouter(prefix="/notifications", tags=["notifications"])

CurrentUserDep = Annotated[User, Depends(get_current_user)]
DbDep = Annotated[AsyncSession, Depends(get_db_session)]


class NotificationOut(BaseModel):
    id: str
    kind: str
    title: str
    body: str | None
    link: str | None
    read: bool
    created_at: str


class NotificationList(BaseModel):
    items: list[NotificationOut]
    unread_count: int


@router.get("", response_model=NotificationList)
async def list_notifications(
    user: CurrentUserDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> NotificationList:
    uid = uuid.UUID(user.user_id)
    rows = (
        await db.execute(
            select(Notification)
            .where(Notification.user_id == uid)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    unread = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == uid, Notification.read_at.is_(None))
    )
    return NotificationList(
        items=[
            NotificationOut(
                id=str(n.id),
                kind=n.kind,
                title=n.title,
                body=n.body,
                link=n.link,
                read=n.read_at is not None,
                created_at=n.created_at.isoformat(),
            )
            for n in rows
        ],
        unread_count=int(unread or 0),
    )


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID, user: CurrentUserDep, db: DbDep
) -> dict[str, bool]:
    uid = uuid.UUID(user.user_id)
    n = await db.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == uid
        )
    )
    if n is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    if n.read_at is None:
        n.read_at = datetime.now(tz=UTC)
        await db.commit()
    return {"ok": True}


@router.post("/read-all")
async def mark_all_read(user: CurrentUserDep, db: DbDep) -> dict[str, bool]:
    uid = uuid.UUID(user.user_id)
    await db.execute(
        update(Notification)
        .where(Notification.user_id == uid, Notification.read_at.is_(None))
        .values(read_at=datetime.now(tz=UTC))
    )
    await db.commit()
    return {"ok": True}
