"""May this person apply to this opening again yet? — PH3-B4b.

The window runs from the REJECTION, not from the application. Somebody who
applied six months ago and was turned down yesterday is one day into their
cooldown, not six months past it — and measuring from the application would
produce exactly the wrong answer for the only case that matters.

The rejection time comes from ``stage_transitions``, which is append-only and
records who moved a candidate and when. ``enrolments.updated_at`` was the
tempting shortcut and is wrong: a rescore or an edit moves it, so the window
would become "whenever the reconciler last touched this row".

WHAT IS NOT A COOLDOWN
A candidate whose application is still live gets "you have already applied",
which is the existing idempotent reply and is not a refusal. A candidate who was
never rejected has no window to be inside. Both are handled before the cooldown
is consulted, so the refusal below only ever means what it says.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CooldownVerdict:
    """Whether this person may apply, and when they may if not."""

    allowed: bool
    #: When the window ends. None when there is no window.
    until: datetime | None = None
    #: Why it was allowed despite a window — 'override' or None.
    reason: str | None = None

    def message(self) -> str:
        """Candidate-facing, and deliberately not apologetic or vague.

        It says the date rather than "please try later": a person refused with
        no date has no way to act on the refusal, and will simply retry.
        """
        if self.allowed or self.until is None:
            return ""
        return (
            "You applied for this role before and we are not able to consider a "
            f"new application until {self.until.date().isoformat()}."
        )


async def check(
    db: AsyncSession,
    *,
    requisition_id: uuid.UUID,
    applicant_id: uuid.UUID,
    cooldown_days: int | None,
    now: datetime | None = None,
) -> CooldownVerdict:
    """Evaluate the cooldown for one person against one opening.

    ``cooldown_days`` is the requisition's setting. None means nobody has set
    one, which is the default and is not the same as zero — zero is "we
    considered this and decided there is no waiting period".
    """
    if not cooldown_days:
        return CooldownVerdict(allowed=True)

    now = now or datetime.now(tz=UTC)
    row = (
        await db.execute(
            text(
                # The most recent rejection for this person on this opening,
                # including enrolments that were later soft-deleted — deleting
                # the application must not delete the cooldown, or the window
                # becomes trivially escapable.
                "SELECT e.id, e.reapply_override_at,"
                "       (SELECT max(st.occurred_at) FROM stage_transitions st"
                "         WHERE st.enrolment_id = e.id AND st.to_status = 'rejected')"
                "         AS rejected_at"
                "  FROM enrolments e"
                " WHERE e.requisition_id = :r AND e.applicant_id = :a"
                " ORDER BY e.created_at DESC"
                " LIMIT 1"
            ),
            {"r": requisition_id, "a": applicant_id},
        )
    ).mappings().first()

    if row is None or row["rejected_at"] is None:
        # Never applied, or applied and was never rejected. Nothing to wait out.
        return CooldownVerdict(allowed=True)

    if row["reapply_override_at"] is not None:
        log.info(
            "reapply.override_honoured",
            requisition_id=str(requisition_id), applicant_id=str(applicant_id),
        )
        return CooldownVerdict(allowed=True, reason="override")

    until = row["rejected_at"] + timedelta(days=int(cooldown_days))
    if until <= now:
        return CooldownVerdict(allowed=True)
    return CooldownVerdict(allowed=False, until=until)


async def grant_override(
    db: AsyncSession,
    *,
    enrolment_id: uuid.UUID,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    reason: str | None,
) -> dict[str, Any] | None:
    """Let this person apply again despite the cooldown. Caller commits.

    Written on the enrolment the cooldown is measured from, so the grant is
    attached to the specific rejection it forgives rather than being a blanket
    exemption on the person. Returns the row, or None if it is not this
    company's to grant.
    """
    now = datetime.now(tz=UTC)
    row = (
        await db.execute(
            text(
                "UPDATE enrolments"
                "   SET reapply_override_at = :n, reapply_override_by_user_id = :by,"
                "       reapply_override_reason = :why, updated_at = :n"
                " WHERE id = :i AND company_id = :c"
                " RETURNING id, requisition_id, applicant_id"
            ),
            {"i": enrolment_id, "c": company_id, "by": actor_user_id,
             "why": (" ".join((reason or "").split())[:1000]) or None, "n": now},
        )
    ).mappings().first()
    return dict(row) if row else None
