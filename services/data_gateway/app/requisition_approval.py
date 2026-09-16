"""The requisition approval lifecycle — PH3-B2.

    draft ──submit──▶ pending_approval ──approve──▶ approved
      ▲                     │
      │                     └──reject──▶ rejected ──resubmit──▶ pending_approval
      └──────────────────────────────────────────────┘

WHO APPROVES
A company's ``super_admin`` approves requisitions its own ``hr_manager``s
raised. There is no separate approver role: the hierarchy already has exactly
one authority above HR inside a company, and inventing a second grant would add
RBAC surface without adding control. The company comes from the authenticated
session, so a super admin cannot reach another tenant's requisition — that is
unreachable rather than merely refused.

SELF-APPROVAL IS STRUCTURALLY IMPOSSIBLE
A user holds exactly one role (``admin_hr`` grants one row in ``user_roles``),
so a super admin does not also hold ``hr_manager`` — and ``create_requisition``
requires ``hr_manager``. A super admin therefore cannot raise a requisition at
all, and an HR manager cannot approve one. The separation is a property of the
role model rather than a check anybody has to remember to write, which is why
there is no "may not approve your own submission" branch below: there is no code
path that could reach it.

The cost of that is a real operational dependency: a company with no active
super admin has nobody who can approve, and HR's submissions will queue. That is
logged as a warning at submission time rather than refused — blocking HR from
doing its half would not conjure an approver, and the queue is recoverable the
moment the platform owner provisions one.

WHY APPROVAL IS NOT A COMPANY-LEVEL TOGGLE
A gate that ships switched off is not a gate. Requisitions that existed before
this story were grandfathered to ``approved`` by the migration so nothing went
dark; everything created afterwards goes through the lifecycle.

THE STATE MACHINE IS SEPARATE FROM ``status``
``status`` (open/paused/closed) is the operational lifecycle and is untouched
here. ``approved`` does not imply ``open``, and closing an opening does not
un-approve it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

DRAFT = "draft"
PENDING = "pending_approval"
APPROVED = "approved"
REJECTED = "rejected"

APPROVAL_STATES: frozenset[str] = frozenset({DRAFT, PENDING, APPROVED, REJECTED})

#: Which transitions are legal. Expressed as data so the refusal message can
#: name what WAS possible, and so a reader can see the whole machine at once.
#:
#: Note what is absent: nothing returns to ``draft``. A rejected requisition is
#: edited and resubmitted, which keeps the rejection visible in the audit trail
#: instead of erasing it by reverting the state.
_TRANSITIONS: dict[str, frozenset[str]] = {
    DRAFT: frozenset({PENDING}),
    PENDING: frozenset({APPROVED, REJECTED}),
    REJECTED: frozenset({PENDING}),
    # Deliberately terminal. Withdrawing an approval would take a live opening
    # off the web as a side effect of an administrative action; pausing or
    # closing it is the operation that means that, and it already exists.
    APPROVED: frozenset(),
}

_MAX_NOTE = 1000


class ApprovalError(RuntimeError):
    """An illegal transition. Carries what was possible from here."""

    def __init__(self, current: str, attempted: str) -> None:
        allowed = sorted(_TRANSITIONS.get(current, frozenset()))
        self.current = current
        self.attempted = attempted
        self.allowed = allowed
        super().__init__(
            f"A requisition that is '{current}' cannot become '{attempted}'."
            + (f" From here it can only become: {', '.join(allowed)}." if allowed
               else " This is a final state.")
        )


def can_transition(current: str, target: str) -> bool:
    return target in _TRANSITIONS.get(current, frozenset())


def clean_note(note: str | None) -> str | None:
    if note is None:
        return None
    cleaned = " ".join(note.split())[:_MAX_NOTE]
    return cleaned or None


async def _load(
    db: AsyncSession, *, requisition_id: uuid.UUID, company_id: uuid.UUID
) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT id, title, approval_status, public_apply_enabled, status"
                "  FROM job_requisitions"
                " WHERE id = :i AND company_id = :c AND deleted_at IS NULL"
            ),
            {"i": requisition_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        # Indistinguishable from "does not exist", like every other tenant-
        # scoped read here: a 403 would confirm the row belongs to someone.
        raise LookupError("no such requisition for this company")
    return dict(row)


async def submit_for_approval(
    db: AsyncSession,
    *,
    requisition_id: uuid.UUID,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> dict[str, Any]:
    """Send a draft (or a rejected requisition) for approval. Caller commits."""
    row = await _load(db, requisition_id=requisition_id, company_id=company_id)
    current = str(row["approval_status"])
    if not can_transition(current, PENDING):
        raise ApprovalError(current, PENDING)

    # Not a refusal: see the module docstring. HR has done its part and the
    # submission is valid; what is missing is somebody to act on it, and that is
    # an operational problem for the platform owner rather than a bad request.
    if not await _company_has_approver(db, company_id=company_id):
        log.warning(
            "requisition.approval.no_approver",
            company_id=str(company_id), requisition_id=str(requisition_id),
        )

    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE job_requisitions"
            "   SET approval_status = :s, submitted_for_approval_at = :n,"
            "       submitted_by_user_id = :by, approval_note = :note,"
            # Cleared together with the status, or the CHECK constraint that
            # ties 'decided' to 'has a decision time' would fail — and a
            # resubmitted requisition would still carry the old decision.
            "       approval_decided_at = NULL, approval_decided_by_user_id = NULL,"
            "       updated_at = :n"
            " WHERE id = :i AND company_id = :c"
        ),
        {"s": PENDING, "n": now, "by": actor_user_id, "note": clean_note(note),
         "i": requisition_id, "c": company_id},
    )
    log.info(
        "requisition.approval.submitted",
        requisition_id=str(requisition_id), previous=current,
    )
    return {**row, "approval_status": PENDING, "previous": current}


async def decide(
    db: AsyncSession,
    *,
    requisition_id: uuid.UUID,
    company_id: uuid.UUID,
    approver_user_id: uuid.UUID,
    approve: bool,
    note: str | None = None,
) -> dict[str, Any]:
    """Approve or reject a pending requisition. Caller commits.

    Rejecting does NOT touch ``public_apply_enabled``. Nothing that is pending
    can be public in the first place — the publish gate refuses it — so there is
    nothing to take down, and silently flipping an HR setting as a side effect
    of a decision would be a change nobody could see in the audit trail.
    """
    row = await _load(db, requisition_id=requisition_id, company_id=company_id)
    current = str(row["approval_status"])
    target = APPROVED if approve else REJECTED
    if not can_transition(current, target):
        raise ApprovalError(current, target)

    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE job_requisitions"
            "   SET approval_status = :s, approval_decided_at = :n,"
            "       approval_decided_by_user_id = :by, approval_note = :note,"
            "       updated_at = :n"
            " WHERE id = :i AND company_id = :c"
        ),
        {"s": target, "n": now, "by": approver_user_id, "note": clean_note(note),
         "i": requisition_id, "c": company_id},
    )
    log.info(
        "requisition.approval.decided",
        requisition_id=str(requisition_id), decision=target,
        approver=str(approver_user_id),
    )
    return {**row, "approval_status": target, "previous": current}


async def _company_has_approver(db: AsyncSession, *, company_id: uuid.UUID) -> bool:
    """Whether anybody at this company can act on an approval request."""
    found = await db.scalar(
        text(
            "SELECT 1 FROM users u"
            "  JOIN user_roles ur ON ur.user_id = u.id"
            "  JOIN roles r ON r.id = ur.role_id AND r.name = 'super_admin'"
            " WHERE u.company_id = :c AND u.deleted_at IS NULL"
            " LIMIT 1"
        ),
        {"c": company_id},
    )
    return found is not None


async def pending_for_company(
    db: AsyncSession, *, company_id: uuid.UUID, limit: int = 100
) -> list[dict[str, Any]]:
    """The approval queue: what this company's super admin is being asked for.

    Oldest first — an approval queue is a waiting list, and the person who has
    been waiting longest should not be at the bottom of the screen.
    """
    rows = (
        await db.execute(
            text(
                "SELECT r.id, r.title, r.level, r.department, r.location,"
                "       r.target_hires, r.budget_amount, r.budget_currency,"
                "       r.budget_basis, r.budget_period, r.budget_notes,"
                "       r.submitted_for_approval_at, r.submitted_by_user_id,"
                "       u.full_name AS submitted_by_name, r.approval_note"
                "  FROM job_requisitions r"
                "  LEFT JOIN users u ON u.id = r.submitted_by_user_id"
                " WHERE r.company_id = :c AND r.deleted_at IS NULL"
                "   AND r.approval_status = :s"
                " ORDER BY r.submitted_for_approval_at ASC NULLS LAST"
                " LIMIT :lim"
            ),
            {"c": company_id, "s": PENDING, "lim": limit},
        )
    ).mappings().all()
    return [dict(r) for r in rows]
