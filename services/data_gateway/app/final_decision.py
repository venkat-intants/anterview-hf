"""The final human decision on one application — Group E, E2.

A hire or a reject ends a candidacy, so it is the one act D-05 reserves for a
person, and it was reachable through three endpoints with three different sets
of rules: the pipeline board refused to hire a candidate still 'new'; the
generic enrolment mover — which the decision queue used — accepted any status
from any status, so a candidate half-way through an automated round could be
hired and a hire could be quietly moved back to 'new'.

This is the one writer. Every hire or reject recorded on an application goes
through ``record_final_decision``, which:

* refuses a hire while the candidate is still inside an automated round — the
  workflow has not finished telling a person what it knows. A candidate who is
  held, who finished every round, or who is on a human_review round has reached
  a person (``enrolment_awaits_human``). An application on no workflow at all
  (a legacy or hand-run opening) can be hired once shortlisted or interviewed,
  as the pipeline board always allowed;
* allows a reject from any live status — rejecting is always a person's
  explicit act, never the system's — and from 'hired', as an audited reversal;
* refuses hiring over a rejection. Reopening a rejected candidate is a separate
  decision this does not make on anyone's behalf;
* requires a reason, recorded against the person on the stage ledger and in the
  append-only audit log;
* takes the candidate off any round they were sitting on and clears a hold, so a
  hired candidate is not still counted at a review round;
* stages the candidate's decision email on the same transaction.

The caller commits, and rolls back on ``DecisionRefusedError``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Applicant, AuditLog
from app.requisitions import record_round_move, record_transition

log = structlog.get_logger(__name__)

DECISIONS: frozenset[str] = frozenset({"hired", "rejected"})
MIN_REASON_CHARS = 3

# Statuses a hire is allowed from when the application is on no workflow.
_HIREABLE_WITHOUT_WORKFLOW: frozenset[str] = frozenset({"shortlisted", "interviewed"})


class DecisionRefusedError(Exception):
    """The decision was not recorded. Carries the HTTP status to answer with."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def refusal(
    decision: str, status: str, awaits_human: bool, workflow_id: object | None
) -> tuple[int, str] | None:
    """Why this decision may not be recorded now, or None when it may.

    Pure, so the rules can be read and tested apart from the database.
    """
    if decision not in DECISIONS:
        return 400, f"decision must be one of {sorted(DECISIONS)}"
    if status == decision:
        return 409, f"This application is already {decision}."
    if decision == "hired":
        if status == "rejected":
            return 409, (
                "This application was rejected. Hiring over a rejection is refused — "
                "reopening a rejected candidate is a separate decision."
            )
        reached_a_person = awaits_human or (
            workflow_id is None and status in _HIREABLE_WITHOUT_WORKFLOW
        )
        if not reached_a_person:
            return 409, (
                "They have not reached a decision yet — they are still in the workflow. "
                "A hire can be recorded once they finish it or are held for you."
            )
    return None


_STATE_SQL = """
SELECT e.status, e.applicant_id, e.current_round_id, e.workflow_id, e.requisition_id,
       COALESCE(r.title, e.target_job_title) AS title,
       wr.title AS round_title,
       enrolment_awaits_human(e.status, e.current_round_id) AS awaits_human
  FROM enrolments e
  LEFT JOIN job_requisitions r ON r.id = e.requisition_id
  LEFT JOIN workflow_rounds wr ON wr.id = e.current_round_id
 WHERE e.id = :e AND e.company_id = :c AND e.deleted_at IS NULL
 FOR UPDATE OF e
"""


async def record_final_decision(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    enrolment_id: uuid.UUID,
    decision: str,
    reason: str | None,
    actor_user_id: uuid.UUID,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Record a hire or reject on one application. Caller commits.

    Raises ``DecisionRefusedError`` before writing anything when the decision may not
    be recorded.
    """
    why = (reason or "").strip()
    if len(why) < MIN_REASON_CHARS:
        raise DecisionRefusedError(
            422, "Say why. A final decision is recorded against your name."
        )

    row = (
        await db.execute(text(_STATE_SQL), {"e": enrolment_id, "c": company_id})
    ).mappings().first()
    if row is None:
        raise DecisionRefusedError(404, "Application not found.")

    previous = str(row["status"])
    refused = refusal(decision, previous, bool(row["awaits_human"]), row["workflow_id"])
    if refused is not None:
        raise DecisionRefusedError(*refused)

    if row["current_round_id"] is not None:
        await record_round_move(
            db,
            enrolment_id=enrolment_id,
            company_id=company_id,
            to_round_id=None,
            actor_user_id=actor_user_id,
            automated=False,
            reason=f"left {row['round_title'] or 'their round'} on a final decision",
        )
    await record_transition(
        db,
        enrolment_id=enrolment_id,
        company_id=company_id,
        to_status=decision,
        actor_user_id=actor_user_id,
        automated=False,
        reason=why,
    )
    if previous == "held":
        await db.execute(
            text("UPDATE enrolments SET held_at = NULL, held_reason = NULL WHERE id = :e"),
            {"e": enrolment_id},
        )

    now = datetime.now(tz=UTC)
    reversal = previous == "hired"
    db.add(
        AuditLog(
            actor_id=actor_user_id,
            actor_type="user",
            action=f"enrolment.decision.{decision}",
            resource_type="enrolment",
            resource_id=enrolment_id,
            details={
                "company_id": str(company_id),
                "requisition_id": (
                    str(row["requisition_id"]) if row["requisition_id"] else None
                ),
                "previous_status": previous,
                "reason": why,
                "reversal": reversal,
            },
            ip_address=ip_address,
            user_agent=user_agent,
            event_ts=now,
        )
    )

    applicant = await db.get(Applicant, row["applicant_id"])
    if applicant is not None:
        # Imported here: the router module drags the storage, scoring and
        # embedding clients in, and this module is imported by routers too.
        from app.routers.hr_applicants import (  # noqa: PLC0415
            email_applicant_decision,
        )

        await email_applicant_decision(
            db, applicant=applicant, decision=decision, company_id=company_id,
            job_title=row["title"],
        )

    log.info(
        "enrolment.final_decision",
        enrolment_id=str(enrolment_id), decision=decision, previous=previous,
        actor=str(actor_user_id), reversal=reversal,
    )
    return {
        "enrolment_id": str(enrolment_id),
        "status": decision,
        "previous_status": previous,
        "decided_by": str(actor_user_id),
        "decided_at": now.isoformat(),
        "reversal": reversal,
    }
