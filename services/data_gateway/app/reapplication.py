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


async def consume_override(db: AsyncSession, *, enrolment_id: uuid.UUID) -> None:
    """Spend the override once the person has actually applied again.

    Without this a grant is permanent: the check honours
    ``reapply_override_at`` whenever it is set, so one exception would exempt
    that person from every future cooldown on this opening. An override
    forgives ONE rejection, which is what it was granted for.

    Only the timestamp is cleared. Who granted it and why stay on the row, so
    the exception is still on the record after it has been spent.

    Be precise about where that record is readable, because this docstring
    used to overstate it: NOTHING reads ``reapply_override_by_user_id`` or
    ``reapply_override_reason`` — not a router, not a query, not a screen. They
    are written here and in ``grant_override`` and read by nobody, so today the
    only place HR can actually see a grant is the audit log. Surfacing them on
    the applicant drawer is worth doing; until it is, do not let this claim be
    read as more than "the columns hold it".
    """
    await db.execute(
        text(
            "UPDATE enrolments SET reapply_override_at = NULL, updated_at = now()"
            " WHERE id = :i"
        ),
        {"i": enrolment_id},
    )


@dataclass(frozen=True)
class Gate:
    """What the reapplication rule says about one incoming application.

    ONE predicate, because there are TWO doors into an application — the
    one-shot form (``POST /apply/{id}``) and a saved draft being finished
    (``POST /apply/draft/submit``) — and they had two hand-written copies of
    this decision. They drifted: the one-shot form was fixed so a rejected
    candidate falls through to the cooldown, and the draft route was not, so
    the same person was still told "we have your application" if they had used
    "Save and finish later". Everything below is therefore decided here and
    read by both.
    """

    #: Answer "we have your application" and stop. Not a refusal.
    already_applied: bool
    #: A rejected application is being made live again by a second attempt.
    reapplying: bool
    #: The cooldown's answer. ``allowed=True`` whenever it does not apply.
    verdict: CooldownVerdict

    @property
    def spend_override(self) -> bool:
        """Whether an override was actually what let this through.

        Gated on the REASON, not merely on the reapplication succeeding.
        ``check`` returns early with ``allowed=True`` when no cooldown is
        configured and when the window has simply elapsed — neither of which
        consults the grant. Spending it in those cases destroys an exception
        somebody recorded, that nobody used, on an opening that may be given a
        cooldown tomorrow.
        """
        return self.reapplying and self.verdict.reason == "override"


async def gate(
    db: AsyncSession,
    *,
    requisition_id: uuid.UUID,
    cooldown_days: int | None,
    applicant_id: uuid.UUID | None,
    enrolment_id: uuid.UUID | None,
    enrolment_status: str | None,
) -> Gate:
    """Decide what happens to an application from someone we may already hold.

    The three arguments after ``cooldown_days`` are what the caller's own
    lookup found for this email on this opening: no applicant, an applicant
    with no application here, or an applicant with one and its status.
    """
    allowed = CooldownVerdict(allowed=True)
    if applicant_id is None:
        return Gate(already_applied=False, reapplying=False, verdict=allowed)

    # A REJECTED application is not a live one, so the idempotent reply must
    # not answer for it — that is what shadowed this whole rule.
    reapplying = enrolment_id is not None and enrolment_status == "rejected"
    if enrolment_id is not None and not reapplying:
        return Gate(already_applied=True, reapplying=False, verdict=allowed)

    verdict = await check(
        db,
        requisition_id=requisition_id,
        applicant_id=applicant_id,
        cooldown_days=cooldown_days,
    )
    return Gate(already_applied=False, reapplying=reapplying, verdict=verdict)


async def reopen(
    db: AsyncSession,
    *,
    enrolment_id: uuid.UUID,
    company_id: uuid.UUID,
    resume_s3_key: str | None,
    spend_override: bool,
) -> None:
    """Make a rejected application live again for a second attempt.

    Caller commits. Shared by both doors for the same reason ``gate`` is.

    THE CV IS ADOPTED HERE, AND THAT IS NOT COSMETIC. One person is enrolled
    into an opening once, so ``enrol_applicant`` finds the rejected row and
    returns it untouched — which left the CV this application was just
    submitted with referenced by nothing at all. Erasure collects a person's
    resume objects by reading the columns that name them
    (``applicants.resume_s3_key``, ``enrolments.applied_resume_s3_key`` and
    ``scored_resume_s3_key``, ``application_drafts.resume_s3_key``); it does
    not sweep the applicant prefix. So an unadopted object had no deletion
    path: a DPDP erasure would complete and leave the candidate's CV in the
    bucket. ``applied_resume_s3_key`` is the column whose documented meaning is
    "the CV this application was SUBMITTED with", and on a second attempt that
    is the new one.

    The CV it replaces is NOT deleted. For a first-time applicant the same key
    is also ``applicants.resume_s3_key``, so deleting it here would destroy the
    person's own CV; leaving it keeps it named by that column and therefore
    still reachable by erasure.

    WHAT THIS DELIBERATELY DOES NOT DO IS RESCORE. The scorer reads
    ``applicants.resume_text``, and the returning-applicant path refuses to
    write that from an anonymous request — an unverified caller holding an
    address could otherwise replace someone's CV and scores in a company's ATS.
    So clearing ``ats_*`` here would not score the new CV; it would blank the
    score and then refill it from the OLD text, which is churn that reads like
    a rescore. The application therefore carries the first attempt's score, the
    ledger entry below says a new CV arrived, and scoring a reapplication
    properly needs a verified identity — see the note in the PH3 checklist.
    """
    # Imported here rather than at module scope: app.requisitions imports the
    # workflow side of the world, and this module is imported by it.
    from app.requisitions import record_transition  # noqa: PLC0415

    await record_transition(
        db,
        enrolment_id=enrolment_id,
        company_id=company_id,
        to_status="new",
        actor_user_id=None,  # the candidate applied; nobody moved them
        automated=True,
        reason="the candidate applied again after a rejection, with a new CV",
    )
    if resume_s3_key:
        await db.execute(
            text(
                "UPDATE enrolments SET applied_resume_s3_key = :k, updated_at = now()"
                " WHERE id = :i AND company_id = :c"
            ),
            {"k": resume_s3_key, "i": enrolment_id, "c": company_id},
        )
    if spend_override:
        await consume_override(db, enrolment_id=enrolment_id)
