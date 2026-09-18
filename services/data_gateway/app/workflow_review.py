"""Workflow review and approval — PH4-O6.

    draft ──submit──▶ in_review ──approve──▶ approved ──publish──▶ published
      ▲    ▲             │   │                  │
      │    │             │   └─request changes─▶ changes_requested ─submit─┐
      │    └──withdraw───┘                      │                          │
      └─────────────────reopen──────────────────┘◀─────────────────────────┘

WHO DOES WHAT (decision D4-2)
HR managers author a version, submit it, withdraw it, reopen an approved one
and publish an approved one. The company's super admin approves it or asks for
changes. The two roles are different accounts — a user holds one role — so the
author cannot approve their own version; the check below says so anyway, in
case that ever stops being true.

WHAT APPROVAL IS OF
A specific version, at a specific content. Submission records a fingerprint of
the version (settings, rounds, branches, criteria) and runs the O2 simulation;
an error in either blocks the submission. While ``in_review`` or ``approved``
the version is locked, by ``_assert_draft`` here and by the database trigger
(migration a2b4c6d8e0f1). Approval re-checks the fingerprint and re-validates:
"validation must pass before approval" is checked at the moment of approval,
not trusted from the submission. Reopening an approved version clears the
approval — there is no way to approve one thing and publish another.

THE RECORD
Every action writes ``workflow_review_events`` (append-only) and an audit row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.notifications_util import create_notification
from app.workflow_simulation import simulate
from app.workflows import validate, workflow_fingerprint

log = structlog.get_logger(__name__)

DRAFT, IN_REVIEW, CHANGES, APPROVED = "draft", "in_review", "changes_requested", "approved"
NOTE_MAX = 1000
CHANGES_NOTE_MIN = 10


class ReviewError(Exception):
    def __init__(self, status_code: int, detail: str | dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def _clean(note: str | None) -> str | None:
    cleaned = (note or "").strip()
    if len(cleaned) > NOTE_MAX:
        raise ReviewError(422, f"A note is at most {NOTE_MAX} characters.")
    return cleaned or None


# Locked FOR UPDATE: two reviewers acting at once see one another's result.
_LOAD_SQL = """
SELECT w.id, w.company_id, w.requisition_id, w.version, w.status,
       w.review_status, w.submitted_by_user_id, w.review_fingerprint,
       w.created_by_user_id, r.title AS opening_title
  FROM workflows w JOIN job_requisitions r ON r.id = w.requisition_id
 WHERE w.id = :i AND w.company_id = :c AND w.deleted_at IS NULL
 FOR UPDATE OF w
"""


async def _load(
    db: AsyncSession, *, company_id: uuid.UUID, workflow_id: uuid.UUID
) -> dict[str, Any]:
    row = (
        await db.execute(text(_LOAD_SQL), {"i": workflow_id, "c": company_id})
    ).mappings().first()
    if row is None:
        raise ReviewError(404, "Workflow not found.")
    return dict(row)


async def _record(
    db: AsyncSession,
    *,
    wf: dict[str, Any],
    action: str,
    actor: uuid.UUID,
    note: str | None,
    fingerprint: str | None = None,
    simulation_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "INSERT INTO workflow_review_events (id, company_id, workflow_id, action,"
            " actor_user_id, note, fingerprint, simulation_id, created_at)"
            " VALUES (:i, :c, :w, :a, :u, :n, :fp, :sim, :t)"
        ),
        {"i": uuid.uuid4(), "c": wf["company_id"], "w": wf["id"], "a": action, "u": actor,
         "n": note, "fp": fingerprint,
         "sim": uuid.UUID(simulation_id) if simulation_id else None, "t": now},
    )
    db.add(
        AuditLog(
            actor_id=actor,
            actor_type="user",
            action=f"workflow.review.{action}",
            resource_type="workflow",
            resource_id=wf["id"],
            details={
                "company_id": str(wf["company_id"]),
                "requisition_id": str(wf["requisition_id"]),
                "version": wf["version"],
                "note": note,
                "fingerprint": fingerprint,
                **(extra or {}),
            },
            ip_address=ip_address,
            user_agent=user_agent,
            event_ts=now,
        )
    )


async def _super_admins(db: AsyncSession, company_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await db.execute(
                text(
                    "SELECT u.id FROM users u"
                    "  JOIN user_roles ur ON ur.user_id = u.id"
                    "  JOIN roles r ON r.id = ur.role_id AND r.name = 'super_admin'"
                    " WHERE u.company_id = :c AND u.deleted_at IS NULL AND u.is_active"
                ),
                {"c": company_id},
            )
        ).scalars().all()
    )


# ---------------------------------------------------------------------------
# HR: submit, withdraw, reopen
# ---------------------------------------------------------------------------
async def submit(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    workflow_id: uuid.UUID,
    actor: uuid.UUID,
    note: str | None,
    profile_competencies: list[dict[str, Any]] | None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Send a draft for approval. Caller commits.

    Validation and the dry run both run first; any error blocks the submission
    and comes back to the author as the report, so a reviewer is never asked to
    approve something that cannot run.
    """
    note = _clean(note)
    wf = await _load(db, company_id=company_id, workflow_id=workflow_id)
    if wf["status"] != "draft" or wf["review_status"] not in (DRAFT, CHANGES):
        raise ReviewError(
            409, "Only a draft that is not already in review can be submitted."
        )
    report = await validate(db, workflow_id, profile_competencies)
    if not report.publishable:
        raise ReviewError(422, {"message": "Fix these before submitting for review.",
                                "validation": report.as_dict()})
    sim = await simulate(
        db, company_id=company_id, workflow_id=workflow_id, actor=actor,
        profile_competencies=profile_competencies, ip_address=ip_address,
        user_agent=user_agent,
    )
    if sim["errors"]:
        raise ReviewError(422, {"message": "The dry run found errors. Fix them first.",
                                "simulation": sim})
    fingerprint = sim["fingerprint"]
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE workflows SET review_status = 'in_review', submitted_for_review_at = :n,"
            " submitted_by_user_id = :u, reviewed_at = NULL, reviewed_by_user_id = NULL,"
            " review_note = :note, review_fingerprint = :fp, updated_at = :n"
            " WHERE id = :i"
        ),
        {"n": now, "u": actor, "note": note, "fp": fingerprint, "i": workflow_id},
    )
    await _record(db, wf=wf, action="submitted", actor=actor, note=note,
                  fingerprint=fingerprint, simulation_id=sim["simulation_id"],
                  ip_address=ip_address, user_agent=user_agent,
                  extra={"simulation_status": sim["status"], "warnings": sim["warnings"]})
    approvers = await _super_admins(db, company_id)
    if not approvers:
        log.warning("workflow.review.no_approver", company_id=str(company_id),
                    workflow_id=str(workflow_id))
    for uid in approvers:
        await create_notification(
            db, user_id=uid, kind="workflow_review_requested",
            title=f"Workflow v{wf['version']} for {wf['opening_title']} needs your review",
            body="A workflow version is waiting for approval before it can go live.",
            link=f"/superadmin/workflow-reviews/{workflow_id}",
        )
    log.info("workflow.review.submitted", workflow_id=str(workflow_id))
    return {"review_status": IN_REVIEW, "simulation": sim, "approvers": len(approvers)}


async def withdraw(
    db: AsyncSession, *, company_id: uuid.UUID, workflow_id: uuid.UUID, actor: uuid.UUID,
    ip_address: str | None = None, user_agent: str | None = None,
) -> dict[str, Any]:
    """Take a version out of review to keep editing it. Caller commits."""
    wf = await _load(db, company_id=company_id, workflow_id=workflow_id)
    if wf["review_status"] != IN_REVIEW:
        raise ReviewError(409, "Only a version waiting for review can be withdrawn.")
    await db.execute(
        text(
            "UPDATE workflows SET review_status = 'draft', review_fingerprint = NULL,"
            " updated_at = now() WHERE id = :i"
        ),
        {"i": workflow_id},
    )
    await _record(db, wf=wf, action="withdrawn", actor=actor, note=None,
                  ip_address=ip_address, user_agent=user_agent)
    return {"review_status": DRAFT}


async def reopen(
    db: AsyncSession, *, company_id: uuid.UUID, workflow_id: uuid.UUID, actor: uuid.UUID,
    note: str | None = None, ip_address: str | None = None, user_agent: str | None = None,
) -> dict[str, Any]:
    """Return an approved, unpublished version to draft. Clears the approval. Caller commits."""
    note = _clean(note)
    wf = await _load(db, company_id=company_id, workflow_id=workflow_id)
    if wf["status"] != "draft" or wf["review_status"] != APPROVED:
        raise ReviewError(409, "Only an approved version that is not yet live can be reopened.")
    await db.execute(
        text(
            "UPDATE workflows SET review_status = 'draft', review_fingerprint = NULL,"
            " reviewed_at = NULL, reviewed_by_user_id = NULL, updated_at = now()"
            " WHERE id = :i"
        ),
        {"i": workflow_id},
    )
    await _record(db, wf=wf, action="reopened", actor=actor, note=note,
                  ip_address=ip_address, user_agent=user_agent)
    return {"review_status": DRAFT}


# ---------------------------------------------------------------------------
# Super admin: approve, request changes
# ---------------------------------------------------------------------------
async def approve(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    workflow_id: uuid.UUID,
    reviewer: uuid.UUID,
    note: str | None,
    profile_competencies: list[dict[str, Any]] | None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Approve the version that was submitted — and only that. Caller commits."""
    note = _clean(note)
    wf = await _load(db, company_id=company_id, workflow_id=workflow_id)
    if wf["review_status"] != IN_REVIEW:
        raise ReviewError(409, "Only a version waiting for review can be approved.")
    if wf["submitted_by_user_id"] == reviewer or wf["created_by_user_id"] == reviewer:
        raise ReviewError(403, "A workflow cannot be approved by the person who wrote it.")
    current = await workflow_fingerprint(db, workflow_id)
    if current != wf["review_fingerprint"]:
        # Unreachable while the lock holds; checked because approval is OF the
        # submitted content, and a stale approval would be worse than none.
        raise ReviewError(409, "This version changed after it was submitted. Ask for it again.")
    report = await validate(db, workflow_id, profile_competencies)
    if not report.publishable:
        raise ReviewError(422, {"message": "This version no longer validates.",
                                "validation": report.as_dict()})
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE workflows SET review_status = 'approved', reviewed_at = :n,"
            " reviewed_by_user_id = :u, review_note = :note, updated_at = :n WHERE id = :i"
        ),
        {"n": now, "u": reviewer, "note": note, "i": workflow_id},
    )
    await _record(db, wf=wf, action="approved", actor=reviewer, note=note, fingerprint=current,
                  ip_address=ip_address, user_agent=user_agent)
    if wf["submitted_by_user_id"]:
        await create_notification(
            db, user_id=wf["submitted_by_user_id"], kind="workflow_review_decided",
            title=f"Workflow v{wf['version']} for {wf['opening_title']} was approved",
            body="It can be published now.",
            link=f"/hr/requisitions/{wf['requisition_id']}/workflow",
        )
    return {"review_status": APPROVED}


async def request_changes(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    workflow_id: uuid.UUID,
    reviewer: uuid.UUID,
    note: str | None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Send a version back with what to change. The note is required. Caller commits."""
    note = _clean(note)
    if not note or len(note) < CHANGES_NOTE_MIN:
        raise ReviewError(
            422, f"Say what needs to change (at least {CHANGES_NOTE_MIN} characters)."
        )
    wf = await _load(db, company_id=company_id, workflow_id=workflow_id)
    if wf["review_status"] != IN_REVIEW:
        raise ReviewError(409, "Only a version waiting for review can be sent back.")
    if wf["submitted_by_user_id"] == reviewer or wf["created_by_user_id"] == reviewer:
        raise ReviewError(403, "A workflow cannot be reviewed by the person who wrote it.")
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE workflows SET review_status = 'changes_requested', reviewed_at = :n,"
            " reviewed_by_user_id = :u, review_note = :note, review_fingerprint = NULL,"
            " updated_at = :n WHERE id = :i"
        ),
        {"n": now, "u": reviewer, "note": note, "i": workflow_id},
    )
    await _record(db, wf=wf, action="changes_requested", actor=reviewer, note=note,
                  ip_address=ip_address, user_agent=user_agent)
    if wf["submitted_by_user_id"]:
        await create_notification(
            db, user_id=wf["submitted_by_user_id"], kind="workflow_review_decided",
            title=f"Changes requested on workflow v{wf['version']} for {wf['opening_title']}",
            body=note, link=f"/hr/requisitions/{wf['requisition_id']}/workflow",
        )
    return {"review_status": CHANGES}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
async def history(
    db: AsyncSession, *, company_id: uuid.UUID, workflow_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT e.action, e.note, e.created_at, e.simulation_id,"
                "       u.full_name AS actor_name"
                "  FROM workflow_review_events e LEFT JOIN users u ON u.id = e.actor_user_id"
                " WHERE e.workflow_id = :w AND e.company_id = :c ORDER BY e.created_at"
            ),
            {"w": workflow_id, "c": company_id},
        )
    ).mappings().all()
    return [
        {"action": r["action"], "note": r["note"], "actor_name": r["actor_name"],
         "at": r["created_at"].isoformat(),
         "simulation_id": str(r["simulation_id"]) if r["simulation_id"] else None}
        for r in rows
    ]


async def pending_for_company(db: AsyncSession, *, company_id: uuid.UUID) -> list[dict[str, Any]]:
    """The super admin's queue: versions waiting for review, oldest first."""
    rows = (
        await db.execute(
            text(
                "SELECT w.id, w.version, w.requisition_id, r.title AS opening_title,"
                "       w.submitted_for_review_at, u.full_name AS submitted_by_name,"
                "       w.review_note,"
                "       (SELECT count(*) FROM workflow_rounds x"
                "         WHERE x.workflow_id = w.id AND x.deleted_at IS NULL) AS rounds"
                "  FROM workflows w"
                "  JOIN job_requisitions r ON r.id = w.requisition_id"
                "  LEFT JOIN users u ON u.id = w.submitted_by_user_id"
                " WHERE w.company_id = :c AND w.review_status = 'in_review'"
                "   AND w.deleted_at IS NULL"
                " ORDER BY w.submitted_for_review_at ASC NULLS LAST"
            ),
            {"c": company_id},
        )
    ).mappings().all()
    return [
        {"workflow_id": str(r["id"]), "version": r["version"],
         "requisition_id": str(r["requisition_id"]), "opening_title": r["opening_title"],
         "submitted_at": r["submitted_for_review_at"].isoformat()
         if r["submitted_for_review_at"] else None,
         "submitted_by_name": r["submitted_by_name"], "note": r["review_note"],
         "rounds": int(r["rounds"] or 0)}
        for r in rows
    ]
