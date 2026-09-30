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

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

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
        """For the EMAIL, not the anonymous reply. Says the date, because a
        person refused with no date has no way to act on the refusal.

        It is deliberately not what the apply endpoint returns. Those routes
        are anonymous and take any address, so answering "you were turned down
        for this role, come back on the 5th of December" told whoever typed it
        that a named person had applied, that they had been REJECTED, and
        roughly when — employment-outcome data about a third party, handed to
        a stranger who only needed a public link and an address. The endpoint
        now gives the same reply it gives a live application, and this
        sentence goes to the address instead.
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


async def stage(
    db: AsyncSession,
    *,
    enrolment_id: uuid.UUID,
    company_id: uuid.UUID,
    resume_s3_key: str | None,
    answers: dict[str, Any] | None,
    token_hash: str,
) -> str | None:
    """Record a second attempt WITHOUT acting on it. Caller commits.

    The application is accepted — nobody is turned away, and the cooldown has
    already decided whether it may be accepted at all. What does not happen
    here is any change to what HR sees about this person.

    WHY NOT JUST REOPEN IT
    Both doors into an application are anonymous by necessity and identify a
    person by an address typed into a public form. `apply_activation` says
    plainly what that is worth: "Anyone can type anyone's address into an
    application form", and the requisition id is documented as not a secret. So
    reopening on submission let one unauthenticated request move a real
    person's status, overwrite the screening answers they had already given,
    attach a stranger's PDF to their application as the CV it was submitted
    with, and spend an override HR had granted them. `final_decision.py`
    refuses an AUTHORISED HR manager from hiring over a rejection because
    reopening someone is "a separate decision this does not make on anyone's
    behalf"; letting an anonymous caller make it was the same decision with
    less authority behind it.

    So the attempt waits here, on the rejected enrolment, until `confirm`
    below is reached through a link emailed to the address. That link is the
    proof the address never was.

    THE CV IS A COLUMN, NOT A LOOSE OBJECT. Erasure collects a person's resume
    objects by reading the columns that name them, so an object nothing names
    has no deletion path and survives a completed erasure (DPDP §12). The
    answers are held here too rather than written to `application_answers`,
    which upserts per (enrolment, question) and would otherwise let a stranger
    overwrite what the real candidate had already answered.
    """
    # What the attempt this one replaces was holding. Returned so the caller
    # can delete it: these columns were the only thing naming that object, and
    # an object nothing names is one erasure cannot find.
    superseded = await db.scalar(
        text(
            "SELECT reapply_resume_s3_key FROM enrolments"
            " WHERE id = :i AND company_id = :c FOR UPDATE"
        ),
        {"i": enrolment_id, "c": company_id},
    )
    await db.execute(
        text(
            "UPDATE enrolments"
            "   SET reapply_requested_at = now(), reapply_resume_s3_key = :k,"
            "       reapply_answers = CAST(:a AS jsonb), reapply_token_hash = :th,"
            "       updated_at = now()"
            " WHERE id = :i AND company_id = :c"
        ),
        {
            "i": enrolment_id,
            "c": company_id,
            "k": resume_s3_key,
            # str keys, because validate_answers returns (UUID, value) pairs
            # and json.dumps refuses a UUID key outright. Rebuilt as UUIDs in
            # `confirm`. Getting this wrong took every reapplication to an
            # opening with screening questions to a 503, deterministically.
            "a": json.dumps({str(k): v for k, v in answers.items()}) if answers else None,
            "th": token_hash,
        },
    )
    log.info(
        "reapply.staged", enrolment_id=str(enrolment_id), has_cv=bool(resume_s3_key),
        superseded=bool(superseded),
    )
    return str(superseded) if superseded else None


async def staged_for_token(
    db: AsyncSession, *, token_hash: str
) -> dict[str, Any] | None:
    """The ONE staged attempt a confirmation link was minted for.

    This replaces a lookup keyed on ``applicants.user_id``, which a security
    re-audit found returned everything that person had staged — across every
    company. Two consequences, both real: a link for company A also confirmed
    whatever was staged at company B, and an attacker who knew a victim's
    address could keep re-staging so that the victim, following their own
    link, authenticated the attacker's CV and answers.

    Binding the hash to the row makes both impossible. The index on it is
    UNIQUE where not null, so one link can never name two attempts, and
    re-staging overwrites the hash — which retires the previous link by
    construction rather than by remembering to.
    """
    row = (
        await db.execute(
            text(
                "SELECT id, company_id, applicant_id, status,"
                "       reapply_resume_s3_key, reapply_answers"
                "  FROM enrolments"
                " WHERE reapply_token_hash = :th AND reapply_requested_at IS NOT NULL"
                "   AND deleted_at IS NULL"
                " FOR UPDATE"
            ),
            {"th": token_hash},
        )
    ).mappings().first()
    return dict(row) if row else None


async def confirm(
    db: AsyncSession,
    *,
    enrolment_id: uuid.UUID,
    company_id: uuid.UUID,
    applicant_id: uuid.UUID,
    requisition_id: uuid.UUID | None = None,
    cooldown_days: int | None = None,
) -> bool:
    """Apply a staged reapplication now that the address has been proven.

    Caller commits. Returns False when there was nothing staged, so a link
    followed twice is a no-op rather than a second reopening.

    Everything the anonymous request deliberately did not do happens here, in
    one transaction, on behalf of somebody who has demonstrated they receive
    mail at the address: the status moves, the CV this attempt was submitted
    with becomes the application's, the answers are written, and an override —
    if one is what let them past the cooldown — is spent.
    """
    from app.application_questions import store_answers  # noqa: PLC0415
    from app.requisitions import record_transition  # noqa: PLC0415

    staged = (
        await db.execute(
            text(
                "SELECT status, reapply_resume_s3_key, reapply_answers FROM enrolments"
                " WHERE id = :i AND company_id = :c AND reapply_requested_at IS NOT NULL"
                " FOR UPDATE"
            ),
            {"i": enrolment_id, "c": company_id},
        )
    ).mappings().first()
    if staged is None:
        return False

    # STILL REJECTED? The link is valid for up to a week, and the application
    # does not stand still in that time. HR may have used the override and
    # reopened the person by hand, or moved them on; a stale link must not then
    # drag a shortlisted or interviewing candidate back to `new` and replace
    # the CV on their application with the staged one. And on a `hired`
    # enrolment `record_transition` refuses outright, which the endpoint's
    # broad except turns into a permanent 503 on every retry rather than an
    # answer. Treated as "already dealt with", which is what it is.
    if staged["status"] != "rejected":
        log.info(
            "reapply.confirm_superseded",
            enrolment_id=str(enrolment_id), status=str(staged["status"]),
        )
        await clear_staged(db, enrolment_id=enrolment_id, company_id=company_id)
        return False

    # RE-EVALUATED HERE, not trusted from submission. Time has passed — the
    # window may have moved, and an override granted then may have been spent
    # elsewhere since. Spending the grant is gated on it being what allows
    # this, which is the whole point of Gate.spend_override; passing a literal
    # True destroyed a recorded HR exception on every confirmation, including
    # the two cases the property exists to exclude.
    verdict = CooldownVerdict(allowed=True)
    if requisition_id is not None:
        verdict = await check(
            db,
            requisition_id=requisition_id,
            applicant_id=applicant_id,
            cooldown_days=cooldown_days,
        )
        if not verdict.allowed:
            log.info("reapply.confirm_refused", enrolment_id=str(enrolment_id))
            return False

    await record_transition(
        db,
        enrolment_id=enrolment_id,
        company_id=company_id,
        to_status="new",
        actor_user_id=None,
        automated=True,
        # Says what actually happened, and no more. The previous wording
        # asserted the candidate had applied again, on a ledger that is
        # append-only, at a point where nobody had proved who submitted it.
        reason="the candidate applied again after a rejection and confirmed it by email",
    )
    if staged["reapply_resume_s3_key"]:
        await db.execute(
            text(
                "UPDATE enrolments SET applied_resume_s3_key = :k, updated_at = now()"
                " WHERE id = :i AND company_id = :c"
            ),
            {"k": staged["reapply_resume_s3_key"], "i": enrolment_id, "c": company_id},
        )
    if staged["reapply_answers"]:
        await store_answers(
            db,
            company_id=company_id,
            enrolment_id=enrolment_id,
            # Back to the (UUID, value) pairs store_answers binds as uuid.
            answers=[
                (uuid.UUID(k), v) for k, v in dict(staged["reapply_answers"]).items()
            ],
        )
    await clear_staged(db, enrolment_id=enrolment_id, company_id=company_id)
    if verdict.reason == "override":
        await consume_override(db, enrolment_id=enrolment_id)
    log.info("reapply.confirmed", enrolment_id=str(enrolment_id))
    return True


async def clear_staged(
    db: AsyncSession, *, enrolment_id: uuid.UUID, company_id: uuid.UUID
) -> str | None:
    """Forget a staged reapplication, and say which CV object it was holding.

    Caller commits, and the caller decides what to do with the returned key.
    `confirm` ignores it, because by then the object is named by
    `applied_resume_s3_key`; a path that ABANDONS a staged attempt must delete
    it, since these columns were the only thing naming it and an object
    nothing names is one erasure cannot find.

    Read before write, not `UPDATE ... RETURNING`: RETURNING yields the NEW row,
    so returning the column this statement just set to NULL would hand every
    caller a None and quietly strand the object.
    """
    held = await db.scalar(
        text(
            "SELECT reapply_resume_s3_key FROM enrolments"
            " WHERE id = :i AND company_id = :c FOR UPDATE"
        ),
        {"i": enrolment_id, "c": company_id},
    )
    await db.execute(
        text(
            "UPDATE enrolments"
            "   SET reapply_requested_at = NULL, reapply_resume_s3_key = NULL,"
            "       reapply_answers = NULL, reapply_token_hash = NULL,"
            "       updated_at = now()"
            " WHERE id = :i AND company_id = :c"
        ),
        {"i": enrolment_id, "c": company_id},
    )
    return str(held) if held else None


async def purge_stale_staged(
    db: AsyncSession, *, now: datetime | None = None, limit: int = 500
) -> list[str]:
    """Forget staged reapplications nobody ever confirmed. Returns CV keys.

    Called from the DPDP retention cron, not a bespoke sweep, for the same
    reason ``application_drafts.purge_expired`` is: a staged attempt past its
    confirmation window is personal data past its purpose, which is exactly
    what that cron exists for.

    WHY IT HAS TO EXIST. A staged attempt holds the candidate's screening
    answers and a CV, and it is applied only when a link emailed to the
    address is followed. Most will be; some never are — a changed address, a
    spam folder, second thoughts. Without this those rows keep that data for
    ever, and the CV object is named by ``reapply_resume_s3_key`` alone, so it
    is invisible to everything except an erasure request that may never come.
    The migration that added these columns said "the retention sweep will want
    to find the stale ones" and then did not build one; this is it.

    THE WINDOW IS THE TOKEN'S OWN LIFETIME. Past ``apply_activation_ttl_hours``
    the link cannot be redeemed, so the attempt is unreachable rather than
    merely old — keeping it any longer holds data nothing can act on. The
    token row is left to the auth-token sweep; clearing the hash here is what
    makes the link dead even if that sweep is behind.

    Deliberately NOT a rejection of the application. The candidate applied and
    was told the application was received; what expires is the second
    attempt's pending state, not any record that they applied.
    """
    now = now or datetime.now(tz=UTC)
    cutoff = now - timedelta(hours=settings.apply_activation_ttl_hours)
    rows = (
        await db.execute(
            text(
                "SELECT id, reapply_resume_s3_key FROM enrolments"
                " WHERE reapply_requested_at IS NOT NULL"
                "   AND reapply_requested_at < :cutoff"
                " ORDER BY reapply_requested_at"
                " LIMIT :lim"
                " FOR UPDATE SKIP LOCKED"
            ),
            {"cutoff": cutoff, "lim": limit},
        )
    ).mappings().all()
    if not rows:
        return []

    ids = [r["id"] for r in rows]
    await db.execute(
        text(
            "UPDATE enrolments"
            "   SET reapply_requested_at = NULL, reapply_resume_s3_key = NULL,"
            "       reapply_answers = NULL, reapply_token_hash = NULL,"
            "       updated_at = now()"
            " WHERE id = ANY(:ids)"
        ),
        {"ids": ids},
    )
    keys = [str(r["reapply_resume_s3_key"]) for r in rows if r["reapply_resume_s3_key"]]
    log.info("reapply.stale_purged", rows=len(ids), objects=len(keys))
    return keys
