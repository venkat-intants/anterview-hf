"""Openings close themselves once their closing date has passed.

A closing date already stopped applications on its own (the public gate in
app/publishing.py refuses them), but the opening stayed "open" — on the list,
on the dashboard and in every count — until somebody remembered to close it.
This pass closes it for them.

WHAT IT WILL NOT DO, AND WHY
Closing by hand is refused while candidates are still in the opening, unless a
person acknowledges them (AC-12, routers/hr_requisitions.set_requisition_status),
and nothing automated ever ends a candidacy (D-05). A background job cannot
acknowledge anything. So an opening past its closing date is closed
automatically ONLY when nobody is still in it — every enrolment hired or
rejected, or none at all. One with candidates still in progress is left open,
and its owner is told once (per closing date) that it is waiting on their
decisions. The candidates are never touched either way.

WHERE IT RUNS
Inside the scheduled-publishing pass (app/scheduled_publishing.py), in the same
session and transaction: no new loop and no extra database wake-ups — a
serverless database is billed by wake-ups, and our own pollers have already
burned a month's compute once. The loop also wakes at the next closing date, so
the tolerance is the same as for scheduled publishing.

WHICH OPENINGS
Status-based: any open or paused opening past its date, live to the public or
not — a draft that never went live but whose date has passed is closed too.

COST, STATED
There is no index on closes_at: the close, nudge and wake-up queries scan
job_requisitions on every pass. That is a few hundred rows per company today;
add a partial index (closes_at) WHERE closes_at IS NOT NULL AND deleted_at IS
NULL AND status IN ('open','paused') before it is not.

STARVATION, AVOIDED THE SAME WAY scheduled_publishing AVOIDS IT
Both queries filter out what they cannot act on (an opening with candidates
still in it never matches the close query; one already notified for this
closing date, or with nobody to notify, never matches the nudge query), so a
backlog of stuck rows can never fill the batch and starve the rest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.notifications_util import create_notification

log = structlog.get_logger(__name__)

#: Bound on one pass, as in scheduled_publishing: a bad data migration that
#: back-dates ten thousand closing dates must not become one huge transaction.
BATCH = 100

#: The opening a pass is working on, for scheduled_publishing's failure log
#: (security review L4). Ids only.
_current: dict[str, str | None] = {"requisition_id": None}

#: Statuses a closing date can move to closed. A closed opening is done.
_LIVE = ("open", "paused")

# An enrolment that still needs a person: not hired, not rejected, not deleted.
# Spelled the same way as TERMINAL_STATUSES in app/requisitions.py.
_UNRESOLVED = (
    "EXISTS (SELECT 1 FROM enrolments e"
    "         WHERE e.requisition_id = r.id AND e.deleted_at IS NULL"
    "           AND e.status NOT IN ('hired', 'rejected'))"
)

_DUE = (
    "r.closes_at IS NOT NULL AND r.closes_at <= :now"
    " AND r.deleted_at IS NULL AND r.status IN ('open', 'paused')"
)


def _overdue_key(requisition_id: Any, closes_at: datetime) -> str:
    """One nudge per opening per closing date: moving the date re-arms it."""
    return f"requisition.closing_overdue:{requisition_id}:{int(closes_at.timestamp())}"


async def close_expired(db: AsyncSession, *, now: datetime) -> int:
    """Close every due opening with nobody left in it. No commit; returns the count."""
    rows = (
        await db.execute(
            text(
                "SELECT r.id, r.company_id, r.title, r.status, r.closes_at,"
                "       (SELECT u.id FROM users u"
                "         WHERE u.id = COALESCE(r.owner_user_id, r.created_by_user_id)"
                "           AND u.company_id = r.company_id AND u.is_active"
                "           AND u.deleted_at IS NULL) AS owner,"
                "       r.publish_at"
                "  FROM job_requisitions r"
                f" WHERE {_DUE} AND NOT {_UNRESOLVED}"  # nosec B608 — module constants, values bound
                " ORDER BY r.closes_at"
                " LIMIT :lim"
                # Two instances on one database never close the same row twice.
                " FOR UPDATE OF r SKIP LOCKED"
            ),
            {"now": now, "lim": BATCH},
        )
    ).mappings().all()
    closed = 0
    for row in rows:
        # Exposed for the caller's failure log: which opening a pass died on.
        _current["requisition_id"] = str(row["id"])
        # Re-checked IN the write: a candidate HR added between the read and
        # here keeps the opening open. The row lock does not cover enrolments.
        done = await db.scalar(
            text(
                # publish_at cleared too: a schedule on a closed opening can
                # never fire and would otherwise sit in blocked_backlog for ever.
                "UPDATE job_requisitions r SET status = 'closed', publish_at = NULL,"
                " updated_at = :now"
                f" WHERE r.id = :i AND {_DUE} AND NOT {_UNRESOLVED}"  # nosec B608 — module constants, values bound
                " RETURNING r.id"
            ),
            {"i": row["id"], "now": now},
        )
        if done is None:
            continue
        closed += 1
        db.add(
            AuditLog(
                # The same action a person's close writes, so every reader of
                # the trail sees a close; `automatic` says who did it. No actor:
                # nobody did — attributing it to the owner would be false.
                actor_id=None,
                actor_type="system",
                action="requisition.status.closed",
                resource_type="job_requisition",
                resource_id=row["id"],
                details={
                    "company_id": str(row["company_id"]),
                    "previous_status": row["status"],
                    "title": row["title"],
                    "automatic": True,
                    "reason": "closing_date_passed",
                    "closes_at": row["closes_at"].isoformat(),
                    # A person scheduled this publish; closing cancelled it.
                    **({"cancelled_publish_at": row["publish_at"].isoformat()}
                       if row["publish_at"] is not None else {}),
                },
                event_ts=now,
            )
        )
        await create_notification(
            db,
            user_id=row["owner"],
            kind="requisition_auto_closed",
            title=f"{row['title']} closed - its closing date passed",
            body="Nobody was still in it. Reopen it with a new closing date if you need more applicants.",
            link=f"/hr/requisitions/{row['id']}",
            dedupe_key=f"requisition.auto_closed:{row['id']}:{int(row['closes_at'].timestamp())}",
        )
        log.info("requisition.auto_closed", requisition_id=str(row["id"]),
                 company_id=str(row["company_id"]))
    return closed


async def nudge_overdue(db: AsyncSession, *, now: datetime) -> int:
    """Tell owners of due openings that still have candidates. No commit."""
    rows = (
        await db.execute(
            text(
                "SELECT r.id, r.title, r.closes_at, u.id AS owner,"
                "       (SELECT count(*) FROM enrolments e"
                "         WHERE e.requisition_id = r.id AND e.deleted_at IS NULL"
                "           AND e.status NOT IN ('hired', 'rejected')) AS waiting"
                "  FROM job_requisitions r"
                # The recipient must be a live, active user of THIS company: an
                # off-boarded owner's feed is read by nobody, and created_by has
                # no FK, so it can name a user that is gone (its notification
                # insert would fail the whole batch).
                "  JOIN users u ON u.id = COALESCE(r.owner_user_id, r.created_by_user_id)"
                "   AND u.company_id = r.company_id AND u.is_active AND u.deleted_at IS NULL"
                f" WHERE {_DUE} AND {_UNRESOLVED}"  # nosec B608 — module constants, values bound
                "   AND NOT EXISTS (SELECT 1 FROM notifications n"
                "                    WHERE n.dedupe_key = 'requisition.closing_overdue:'"
                "                          || r.id::text || ':'"
                "                          || floor(extract(epoch FROM r.closes_at))::bigint::text)"
                " ORDER BY r.closes_at"
                " LIMIT :lim"
            ),
            {"now": now, "lim": BATCH},
        )
    ).mappings().all()
    told = 0
    for row in rows:
        n = int(row["waiting"])
        if await create_notification(
            db,
            user_id=row["owner"],
            kind="requisition_closing_overdue",
            title=f"{row['title']} is past its closing date",
            body=(
                f"{n} candidate{'s are' if n != 1 else ' is'} still in progress, so it was not "
                "closed automatically. Close it once they finish, or close it yourself now - "
                "nobody is rejected for you."
            ),
            link=f"/hr/requisitions/{row['id']}?tab=pipeline",
            dedupe_key=_overdue_key(row["id"], row["closes_at"]),
        ):
            told += 1
    return told


#: Closing-date wake-ups are rounded UP to this boundary. Nobody is promised
#: to-the-second closing, and without it any HR manager could set openings to
#: close a second apart and keep the shared, platform-wide loop — and a
#: serverless database billed by wake-ups — running once a second for every
#: tenant (security review M2). Closing dates need no approval, so the
#: approval argument that bounds scheduled publishing does not apply here.
WAKE_GRANULARITY_SECONDS = 60


def _round_up(ts: datetime, seconds: int) -> datetime:
    epoch = ts.timestamp()
    rounded = -(-epoch // seconds) * seconds  # ceiling to the boundary
    return datetime.fromtimestamp(rounded, tz=UTC)


async def next_closing_at(db: AsyncSession, *, now: datetime) -> datetime | None:
    """When the loop should next wake for a closing date, rounded up to
    ``WAKE_GRANULARITY_SECONDS`` so many dates a second apart cost one pass."""
    result: datetime | None = await db.scalar(
        text(
            "SELECT MIN(closes_at) FROM job_requisitions"
            " WHERE closes_at IS NOT NULL AND closes_at > :now"
            "   AND deleted_at IS NULL AND status IN ('open', 'paused')"
        ),
        {"now": now},
    )
    if result is None:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return _round_up(result, WAKE_GRANULARITY_SECONDS)


def closing_date_passed(closes_at: datetime | None, now: datetime | None = None) -> bool:
    """The opening's closing date is behind it (shared with the reopen guard)."""
    if closes_at is None:
        return False
    if closes_at.tzinfo is None:
        closes_at = closes_at.replace(tzinfo=UTC)
    return closes_at <= (now or datetime.now(tz=UTC))
