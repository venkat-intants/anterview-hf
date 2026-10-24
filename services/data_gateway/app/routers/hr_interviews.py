"""HR 'invite to interview' + results — HR workflow Phase 3.

An HR manager invites an exam-passed (or shortlisted) applicant into the EXISTING
avatar interview. At invite time we mint ONLY an opaque-token interview_invites row
(lazy provisioning — the guest user/session/consent are created when the applicant
first redeems the link in interview_take.py). HR then sees invite status + the
graded interview result once the avatar session completes.

MULTI-TENANT: every endpoint is company-scoped (reuses get_hr_company/HrCtxDep).
Cross-company ids return 404.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import column, exists, or_, select, table, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import accommodations
from app.config import settings
from app.database import DbSessionDep
from app.dependencies import HrCtxDep
from app.email_time import format_email_datetime
from app.interview_link import hash_interview_token, mint_interview_token
from app.mailer import enqueue_email
from app.models import Applicant, AuditLog, InterviewInvite, Job, Scorecard
from app.models import Session as SessionModel
from app.notifications_util import create_notification
from app.reminders import rearm_interview_reminders
from app.requisitions import ApplicationChoice, choose_application
from app.routers.hr_applicants import _get_owned
from app.utils.ownership import get_owned
from app.utils.request_ip import extract_client_ip, extract_user_agent

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-interviews"])

_LANGS = {"en", "hi", "te"}

#: How the candidate meets the interviewer. ``avatar`` uses whatever
#: ``AVATAR_PROVIDER`` is configured on the instance; ``audio`` is voice only
#: regardless of it. The toggle deliberately cannot pick a VENDOR — see
#: migration f3a5c7e9b1d4.
#:
#: The DB CHECK (``ck_interview_invites_avatar_mode``) is the arbiter, not this
#: set: interview_core reads the same column and has its own copy of these two
#: values, so a third mode means the migration, this set and the worker's, and
#: the constraint is what makes forgetting one of them fail loudly instead of
#: writing a mode no worker can interpret.
AVATAR_MODES = {"avatar", "audio"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class InviteCreateIn(BaseModel):
    applicant_id: uuid.UUID
    # The application the interview is for (B5). Optional when the person has
    # exactly one; required, in effect, when they have several.
    enrolment_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    job_title: str | None = Field(default=None, max_length=300)
    level: str | None = None
    language: str = "en"
    # 'avatar' (use the configured provider) or 'audio' (voice only). Defaults
    # to 'avatar' so an existing caller that omits it is unchanged.
    avatar_mode: str = "avatar"
    scheduled_at: datetime | None = None
    ttl_hours: int | None = Field(default=None, ge=1, le=8760)
    # The application already has a live link. Without this the request is
    # refused (409) rather than silently revoking the link the candidate holds:
    # HR confirms the replacement in the UI, and a copilot proposal or a stale
    # tab re-firing the same request cannot rotate the link behind anyone's back.
    replace_existing: bool = False

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str) -> str:
        if v not in _LANGS:
            raise ValueError(f"language must be one of {sorted(_LANGS)}")
        return v

    @field_validator("avatar_mode")
    @classmethod
    def _avatar_mode(cls, v: str) -> str:
        if v not in AVATAR_MODES:
            raise ValueError(f"avatar_mode must be one of {sorted(AVATAR_MODES)}")
        return v


class InviteResult(BaseModel):
    invite_id: str
    applicant_id: str
    applicant_name: str
    job_title: str
    magic_link: str  # raw token embedded — returned ONCE, at mint time only
    expires_at: str
    scheduled_at: str | None
    status: str
    # True when an identical request was answered with the invite it already
    # made (a double submit or a re-fired proposal): no new link was minted and
    # no second email went out, so ``magic_link`` is empty.
    reused: bool = False


class InviteOut(BaseModel):
    invite_id: str
    applicant_id: str
    applicant_name: str
    job_title: str
    language: str
    avatar_mode: str
    status: str
    scheduled_at: str | None
    expires_at: str
    created_at: str
    composite_score: float | None
    scorecard_id: str | None
    # Display-only, derived from the stored row (nothing is written):
    # ``missed_slot`` — still 'invited', but the scheduled join window has
    # closed, so the candidate can no longer start until HR reschedules.
    # ``can_reschedule`` — the server would accept a PATCH for this invite.
    missed_slot: bool = False
    can_reschedule: bool = False


class EligibleApplicantOut(BaseModel):
    id: str
    enrolment_id: str | None = None
    opening_title: str | None = None
    full_name: str
    target_job_title: str
    target_level: str
    status: str
    ats_overall: int | None
    passed_exam: bool
    # A link the candidate can still use: an unexpired 'invited' one, or an
    # unexpired started interview that is not over (application_progress).
    has_active_invite: bool
    # The started half of that alone. The server refuses to replace it (409),
    # so the form says "end the started interview first" instead of offering
    # a replacement.
    has_started_invite: bool = False


class InterviewOutcome(BaseModel):
    invite_id: str
    applicant_id: str
    applicant_name: str
    status: str
    session_status: str | None
    scorecard_id: str | None
    composite_score: float | None
    scores: dict[str, Any] | None
    strengths: list[Any] | None
    improvements: list[Any] | None
    summary: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _get_owned_invite(
    db: AsyncSession, company_id: uuid.UUID, invite_id: uuid.UUID
) -> InterviewInvite:
    return await get_owned(db, InterviewInvite, company_id, invite_id, noun="Invite")


async def _lock_applicant_invites(
    db: AsyncSession, company_id: uuid.UUID, applicant_id: uuid.UUID
) -> None:
    """Serialise every invite decision for one person at one company.

    Creating, replacing and rescheduling all read "which links does this person
    already hold" and then act on the answer. Without a lock two concurrent
    requests (a double submit, the case the duplicate window exists for) both
    read "none", both mint a Job and both insert, and because their jobs differ
    the (applicant_id, job_id) unique index does not stop the second live link.
    The lock is transaction-scoped, so it is released by the commit or rollback
    that ends the request. Keyed on the person rather than the application:
    coarser, never wrong, and an invite recorded against no application
    competes with every application anyway.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"interview_invite:{company_id}:{applicant_id}"},
    )


#: A later invite for the same application that is live or finished. Once one
#: exists the older link must stay dead: reviving it would hand the candidate a
#: second working link next to the new one (IV-H2 through the back door).
_newer = aliased(InterviewInvite)
_SUPERSEDED = exists().where(
    _newer.applicant_id == InterviewInvite.applicant_id,
    _newer.company_id == InterviewInvite.company_id,
    _newer.id != InterviewInvite.id,
    _newer.created_at > InterviewInvite.created_at,
    _newer.deleted_at.is_(None),
    _newer.status.in_(("invited", "consumed", "completed")),
    or_(
        _newer.enrolment_id == InterviewInvite.enrolment_id,
        _newer.enrolment_id.is_(None),
        InterviewInvite.enrolment_id.is_(None),
    ),
)

#: The application the invite is for is still open: the person is not erased,
#: the application is not withdrawn (a withdrawn enrolment is soft deleted and
#: leaves the view) and it has not been decided. Narrower than the create gate
#: on purpose: an application that moved on to 'interviewed' or 'held' may
#: still need its AI interview moved.
_progress = table(
    "application_progress",
    column("company_id"), column("applicant_id"), column("enrolment_id"),
    column("stored_status"),
)
_APPLICATION_OPEN = exists().where(
    _progress.c.company_id == InterviewInvite.company_id,
    _progress.c.applicant_id == InterviewInvite.applicant_id,
    or_(InterviewInvite.enrolment_id.is_(None),
        _progress.c.enrolment_id == InterviewInvite.enrolment_id),
    _progress.c.stored_status.not_in(("hired", "rejected")),
)


async def _interview_finished(db: AsyncSession, inv: InterviewInvite) -> bool:
    """The started interview is over: scored, or the worker has marked its
    session completed (scoring may still be running). The same test
    interview_take uses to refuse re-entry. 'abandoned' is NOT over: a dropped
    call stays reconnectable."""
    if inv.session_id is None:
        return False
    if await db.scalar(
        select(Scorecard.scorecard_id).where(Scorecard.session_id == inv.session_id)
    ) is not None:
        return True
    session_status = await db.scalar(
        text("SELECT status FROM sessions WHERE id = :sid"), {"sid": inv.session_id}
    )
    return bool(session_status == "completed")


async def _reschedule_blockers(db: AsyncSession, inv: InterviewInvite) -> tuple[bool, bool]:
    """(superseded, application_open) for one invite, from the same expressions
    the list uses, so the button and the PATCH can never disagree."""
    row = (
        await db.execute(
            select(_SUPERSEDED.label("superseded"), _APPLICATION_OPEN.label("open"))
            .select_from(InterviewInvite)
            .where(InterviewInvite.id == inv.id)
        )
    ).one()
    return bool(row.superseded), bool(row.open)


#: Why HR ended an interview the candidate had started (revoke with force).
EndReason = Literal["candidate_left", "link_shared", "technical_problem", "other"]


#: How long a scheduled link stays alive after its join window closes. First
#: start is gated to ``scheduled_at .. scheduled_at + join window``
#: (interview_take.redeem_invite), but a candidate who starts late in the window
#: must still be able to reconnect after a dropped call, which needs the link.
_RECONNECT_GRACE = timedelta(hours=2)

#: An identical invite request repeated within this long is answered with the
#: invite it already made instead of minting a second link and email.
_DEDUPE_WINDOW = timedelta(minutes=10)


def invite_expiry(
    now: datetime,
    scheduled_at: datetime | None,
    *,
    ttl_hours: int,
    extra_days: int | None = None,
    current: datetime | None = None,
) -> datetime:
    """When an invite's link stops working.

    The TTL alone used to decide it, so an interview scheduled further out than
    the TTL (48h by default) was emailed with a link that died before the slot.
    A scheduled link now lives at least until the join window plus a reconnect
    grace has passed. ``current`` is the existing expiry on a reschedule: the
    link is extended when needed and never shortened.
    """
    expiry = now + timedelta(hours=ttl_hours, days=extra_days or 0)
    if scheduled_at is not None:
        floor = (
            scheduled_at
            + timedelta(minutes=settings.interview_join_window_minutes)
            + _RECONNECT_GRACE
        )
        expiry = max(expiry, floor)
    if current is not None:
        expiry = max(expiry, current)
    return expiry


def _effective_status(inv: InterviewInvite, *, has_scorecard: bool, now: datetime) -> str:
    """Status for DISPLAY only; nothing is written. A scorecard existing means
    the interview is finished whether or not the sweep has caught up, and a
    non-completed invite past its expiry is dead (redeem already 404s it)."""
    if has_scorecard and inv.status == "consumed":
        return "completed"
    if inv.status in ("invited", "consumed") and inv.expires_at <= now:
        return "expired"
    return inv.status


def _missed_slot(inv: InterviewInvite, now: datetime) -> bool:
    """Still 'invited' but the scheduled join window has closed: the candidate
    can no longer start (interview_take refuses first start outside it)."""
    if inv.status != "invited" or inv.scheduled_at is None:
        return False
    window = timedelta(minutes=settings.interview_join_window_minutes)
    return now > inv.scheduled_at + window


def _invite_out(
    inv: InterviewInvite,
    *,
    name: str | None,
    title: str | None,
    scorecard_id: Any = None,
    composite: Any = None,
    now: datetime | None = None,
    superseded: bool = False,
    application_open: bool = True,
) -> InviteOut:
    now = now or datetime.now(tz=UTC)
    return InviteOut(
        invite_id=str(inv.id), applicant_id=str(inv.applicant_id), applicant_name=name or "",
        job_title=title or "", language=inv.language, avatar_mode=inv.avatar_mode,
        status=_effective_status(inv, has_scorecard=scorecard_id is not None, now=now),
        scheduled_at=inv.scheduled_at.isoformat() if inv.scheduled_at else None,
        expires_at=inv.expires_at.isoformat(), created_at=inv.created_at.isoformat(),
        composite_score=float(composite) if composite is not None else None,
        scorecard_id=str(scorecard_id) if scorecard_id else None,
        missed_slot=_missed_slot(inv, now),
        # Reschedule is for an interview nobody has started. Allowed on an
        # expired-but-unstarted link too: moving the slot re-extends it, which
        # is the recovery for a missed slot. Never once a later invite exists
        # for the application, or the application is decided or withdrawn
        # (see reschedule_invite).
        can_reschedule=(
            inv.status == "invited" and inv.deleted_at is None
            and not superseded and application_open
        ),
    )


async def _create_job_from_applicant(
    db: AsyncSession,
    applicant: Applicant,
    *,
    created_by_user_id: uuid.UUID | None,
    language: str = "en",
    job_title: str | None = None,
    level: str | None = None,
    jd_text: str | None = None,
) -> Job:
    """Create a tenant-owned Job from the applicant's screening role so the
    interviewer prompt is grounded. Shared by manual invite + auto-advance.

    Pass the APPLICATION's title/level/JD when the interview is for one (B5):
    the applicant row describes only their latest application, and an
    interview grounded in the wrong job description asks the wrong questions."""
    title = (job_title or applicant.target_job_title or "the role").strip()
    lvl = (level or applicant.target_level or "mid").strip()
    now = datetime.now(tz=UTC)
    job = Job(
        id=uuid.uuid4(),
        title=title,
        description=title,
        level=lvl if lvl in ("entry", "mid", "senior") else "mid",
        language=language,
        nos_codes=[],
        competencies={},
        is_active=True,
        jd_text=jd_text if jd_text is not None else applicant.target_jd_text,
        created_by_user_id=created_by_user_id,  # tenant-owned (M4)
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    await db.flush()
    return job


async def _resolve_job(
    db: AsyncSession, hr_uid: uuid.UUID, company_id: uuid.UUID, applicant: Applicant,
    body: InviteCreateIn, role: ApplicationChoice | None = None,
) -> Job:
    """Pick the interview job: an explicit tenant-owned/global job_id, else create a
    tenant-owned job from the application's role so the prompt is grounded.
    What HR typed wins; then the application's own title/level/JD (B5); then
    the applicant row's."""
    if body.job_id is not None:
        job = await db.scalar(select(Job).where(Job.id == body.job_id, Job.is_active.is_(True)))
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found or inactive.")
        # M4: a tenant-owned job must belong to this company. A NULL owner means a
        # GLOBAL/platform job (e.g. seeded role catalog) intentionally usable by any
        # tenant — the company check is skipped for those by design.
        if job.created_by_user_id is not None:
            owner_company = await db.scalar(
                text("SELECT company_id FROM users WHERE id = :uid"),
                {"uid": job.created_by_user_id},
            )
            if owner_company is not None and owner_company != company_id:
                raise HTTPException(status_code=404, detail="Job not found.")
        return job

    return await _create_job_from_applicant(
        db, applicant, created_by_user_id=hr_uid, language=body.language,
        job_title=body.job_title or (role.title if role else None),
        level=body.level or (role.level if role else None),
        jd_text=role.jd_text if role else None,
    )


async def _tell_candidate_about_invite(
    db: AsyncSession,
    *,
    applicant: Applicant,
    job_title: str,
    magic_link: str | None,
    scheduled_at: datetime | None,
    language: str,
    company_id: uuid.UUID,
    invite_id: uuid.UUID | None = None,
    rescheduled: bool = False,
) -> None:
    """Tell the candidate an invite exists — in-app AND by email (caller commits).

    Values are escaped inside the template renderer (a candidate name / job title
    can contain markup). ``magic_link=None`` renders the "use your original link"
    copy (reschedule path — the link is not re-minted)."""
    when = format_email_datetime(scheduled_at) if scheduled_at else None

    # In-app first, email second — and deliberately in that order.
    #
    # The invite used to exist ONLY as an email. A candidate who missed it had
    # no way to discover the interview: their applications page still read
    # "Shortlisted · watch your email", their notification list was empty, and
    # nothing anywhere in their account mentioned an invitation. If the mail was
    # filtered, mistyped or — as in local development — caught by a mail sink,
    # the invite was unreachable while looking perfectly healthy to HR.
    #
    # Staged on the caller's transaction like the email, so a candidate is never
    # told about an invite that failed to persist.
    await create_notification(
        db,
        user_id=applicant.user_id,
        kind="interview_invite",
        title=(
            "Your interview has been rescheduled"
            if rescheduled
            else "You have an interview invitation"
        ),
        body=(
            f"{job_title or 'A role'}"
            + (f" · {when}" if when else "")
            + ". Open your applications to start it."
        ),
        link="/applications",
    )

    # Silent no-op when the applicant has no address on file. That is a real
    # case (HR can add an applicant without one) and not an error — but it is
    # exactly why the notification above is not inside this guard.
    if not applicant.email:
        return
    await enqueue_email(
        db,
        to=applicant.email,
        template="interview_invite",
        lang=language,
        ctx={
            "name": applicant.full_name,
            "job_title": job_title or "the role",
            "interview_url": magic_link,
            "when": when,
            "rescheduled": rescheduled,
        },
        company_id=company_id,
        related_kind="interview_invite",
        related_id=invite_id,
    )


async def advance_applicant_to_interview(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    applicant: Applicant,
    created_by_user_id: uuid.UUID | None,
    language: str = "en",
    # Defaults to 'avatar' so every existing caller keeps today's behaviour.
    # The workflow runner passes the round's choice; a bare call gets the
    # instance default, which is what AVATAR_PROVIDER already gave it.
    avatar_mode: str = "avatar",
    scheduled_at: datetime | None = None,
    notify_user_id: uuid.UUID | None = None,
    enrolment_id: uuid.UUID | None = None,
    workflow_round_id: uuid.UUID | None = None,
) -> InterviewInvite | None:
    """Auto-advance: mint an interview invite for an exam-passed applicant + email
    the candidate the link. Caller owns the commit. Returns the invite, or None if
    an active invite already exists (idempotent — never double-invites).

    Reuses the exact invite-mint shape as create_invite (lazy provisioning; the
    guest user/session/consent are created on first redeem in interview_take.py).

    ``enrolment_id`` — the application the pass was for (B5). The invite is
    created linked to it, and the interview is grounded in THAT opening's role
    rather than the person's latest one; an active invite for a different
    application no longer blocks this one.

    ``workflow_round_id`` (PH4-D2) — the workflow round this invite is for, so
    a round-scoped accommodation is found; ``None`` from a manual invite
    outside any workflow, where only an applicant-wide or application-scoped
    adjustment can apply.
    """
    role = None
    if enrolment_id is not None:
        role = await choose_application(
            db, applicant_id=applicant.id, company_id=company_id, enrolment_id=enrolment_id
        )
    # Same lock as create_invite: the check below and the insert after it must
    # not interleave with a hand-made invite for the same person.
    await _lock_applicant_invites(db, company_id, applicant.id)
    # Idempotent: don't create a second invite if one is already active — for
    # this application (or one recorded against no application at all).
    active = select(InterviewInvite).where(
        InterviewInvite.applicant_id == applicant.id,
        InterviewInvite.company_id == company_id,
        InterviewInvite.status.in_(("invited", "consumed")),
        InterviewInvite.deleted_at.is_(None),
    )
    if enrolment_id is not None:
        active = active.where(
            or_(InterviewInvite.enrolment_id == enrolment_id,
                InterviewInvite.enrolment_id.is_(None))
        )
    existing = await db.scalar(active)
    if existing is not None:
        # One active interview invite per application — don't double-invite
        # (e.g. a prior manual invite or another exam pass). Log so it isn't silent.
        log.info(
            "hr.interview.auto_advance_skipped_existing",
            applicant_id=str(applicant.id), company_id=str(company_id),
            enrolment_id=str(enrolment_id) if enrolment_id else None,
        )
        return None

    job = await _create_job_from_applicant(
        db, applicant, created_by_user_id=created_by_user_id, language=language,
        job_title=role.title if role else None, level=role.level if role else None,
        jd_text=role.jd_text if role else None,
    )
    now = datetime.now(tz=UTC)
    # PH4-D2: any recorded deadline extension applies to this invite's expiry too.
    adj_row = await accommodations.effective_for(
        db, company_id=company_id, applicant_id=applicant.id, enrolment_id=enrolment_id,
        workflow_round_id=workflow_round_id,
    )
    extra_days = adj_row.deadline_extension_days if adj_row else None
    raw_token = mint_interview_token()
    invite = InterviewInvite(
        id=uuid.uuid4(),
        company_id=company_id,
        applicant_id=applicant.id,
        enrolment_id=enrolment_id,
        job_id=job.id,
        created_by_user_id=created_by_user_id,
        token_hash=hash_interview_token(raw_token, settings.interview_link_secret),
        language=language,
        avatar_mode=avatar_mode,
        expires_at=invite_expiry(
            now, scheduled_at, ttl_hours=settings.interview_link_ttl_hours,
            extra_days=extra_days,
        ),
        scheduled_at=scheduled_at,
        accommodation_id=adj_row.id if adj_row else None,
        status="invited",
        created_at=now,
        updated_at=now,
    )
    db.add(invite)
    if adj_row is not None:
        await db.flush()
        await accommodations.record_applied(
            db, company_id=company_id, accommodation_id=adj_row.id,
            target_kind="interview_invite", target_id=invite.id,
        )
    if notify_user_id is not None:
        await create_notification(
            db,
            user_id=notify_user_id,
            kind="auto_advance",
            title="Candidate auto-advanced to interview",
            body=f"{applicant.full_name} passed the final round · {job.title}",
            link="/hr/interviews",
        )
    base = settings.interview_link_base_url.rstrip("/")
    magic_link = f"{base}/interview-invite#{raw_token}"
    # Stage on the same transaction the caller commits → the candidate email is
    # sent iff the invite persists (atomic), then delivered by the outbox worker.
    await _tell_candidate_about_invite(
        db, applicant=applicant, job_title=job.title, magic_link=magic_link,
        scheduled_at=scheduled_at, language=language, company_id=company_id,
        invite_id=invite.id,
    )
    log.info(
        "hr.interview.auto_advanced",
        invite_id=str(invite.id), company_id=str(company_id), applicant_id=str(applicant.id),
    )
    return invite


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
# Per APPLICATION (B5), from the same view as the pipeline board: someone
# shortlisted for one opening is interview-eligible for that opening, not for
# every opening they applied to — and an exam passed for one role is not a pass
# for another.
_ELIGIBLE_SQL = text(
    """
SELECT applicant_id, enrolment_id, opening_title, full_name, target_job_title,
       target_level, stored_status, ats_overall,
       COALESCE(exam_passed, false) AS passed_exam, has_active_invite,
       has_started_invite
  FROM application_progress
 WHERE company_id = :cid
   AND stored_status NOT IN ('hired', 'rejected')
   AND (   (:src = 'shortlisted' AND stored_status = 'shortlisted')
        OR (:src = 'exam_passed' AND exam_passed IS TRUE)
        OR (:src = 'any' AND (stored_status = 'shortlisted' OR exam_passed IS TRUE)))
 ORDER BY ats_overall DESC NULLS LAST, applied_at DESC
"""
)


@router.get("/interviews/eligible-applicants", response_model=list[EligibleApplicantOut])
async def eligible_applicants(
    ctx: HrCtxDep,
    db: DbSessionDep,
    source: Annotated[str, Query()] = "any",
) -> list[EligibleApplicantOut]:
    """Applications ready for an interview: shortlisted and/or passed an exam.

    One entry per application, so a person can appear once per opening; each
    carries the ``enrolment_id`` to send back when creating the invite.
    """
    _hr_uid, company_id = ctx
    rows = (
        await db.execute(_ELIGIBLE_SQL, {"cid": company_id, "src": source})
    ).mappings().all()
    return [
        EligibleApplicantOut(
            id=str(r["applicant_id"]),
            enrolment_id=str(r["enrolment_id"]) if r["enrolment_id"] else None,
            opening_title=r["opening_title"],
            full_name=r["full_name"], target_job_title=r["target_job_title"],
            target_level=r["target_level"], status=r["stored_status"],
            ats_overall=r["ats_overall"], passed_exam=bool(r["passed_exam"]),
            has_active_invite=bool(r["has_active_invite"]),
            has_started_invite=bool(r["has_started_invite"]),
        )
        for r in rows
    ]


# The gate, for one application (or, for someone filed under no opening, for
# the person): the same predicate as _ELIGIBLE_SQL, so the list and the create
# can never disagree about who may be invited.
_GATE_SQL = text(
    """
SELECT 1 FROM application_progress
 WHERE company_id = :cid AND applicant_id = :a
   AND enrolment_id IS NOT DISTINCT FROM CAST(:e AS uuid)
   AND stored_status NOT IN ('hired', 'rejected')
   AND (stored_status = 'shortlisted' OR exam_passed IS TRUE)
"""
)


@router.post("/interviews", status_code=status.HTTP_201_CREATED, response_model=InviteResult)
async def create_invite(
    body: InviteCreateIn, request: Request, response: Response, ctx: HrCtxDep,
    db: DbSessionDep,
) -> InviteResult:
    hr_uid, company_id = ctx
    applicant = await _get_owned(db, company_id, body.applicant_id)

    # B5: an interview is for one application — the one named, or the only one.
    # With several and none named it cannot be attributed, and an interview
    # nobody can place against an opening is invisible on every board.
    try:
        app_ = await choose_application(
            db, applicant_id=applicant.id, company_id=company_id,
            enrolment_id=body.enrolment_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Application not found.") from exc
    if app_.ambiguous:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{applicant.full_name} has applied to {len(app_.live)} openings, "
                "so choose which one this interview is for."
            ),
        )

    # Funnel gate: only shortlisted or exam-passed applications are interview-eligible.
    eligible = await db.scalar(
        _GATE_SQL, {"cid": company_id, "a": applicant.id, "e": app_.enrolment_id}
    )
    if eligible is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Applicant must be shortlisted or have passed an exam before an interview.",
        )

    now = datetime.now(tz=UTC)
    if body.scheduled_at is not None and body.scheduled_at < now:
        raise HTTPException(
            status_code=422, detail="The interview time cannot be in the past."
        )

    # The live link this application already has, if any. Matched on the
    # APPLICATION (applicant + enrolment + company), never on job_id: the form
    # sends no job_id, so _resolve_job mints a fresh Job on every call and a
    # job-keyed lookup never found the old invite. Both links then stayed live
    # and the candidate could sit the AI interview twice (IV-H2). An invite
    # recorded against no application counts too, as in the workflow path.
    #
    # Expired links are read as well. Nothing sweeps an 'invited' row to
    # 'expired', so a link past its expiry is still 'invited' on disk, and a
    # reschedule extends a link again. Left alone it could be revived next to
    # the new one; it is retired below instead.
    await _lock_applicant_invites(db, company_id, applicant.id)
    prior_q = select(InterviewInvite).where(
        InterviewInvite.applicant_id == applicant.id,
        InterviewInvite.company_id == company_id,
        InterviewInvite.status.in_(("invited", "consumed")),
        InterviewInvite.deleted_at.is_(None),
    )
    if app_.enrolment_id is not None:
        prior_q = prior_q.where(
            or_(InterviewInvite.enrolment_id == app_.enrolment_id,
                InterviewInvite.enrolment_id.is_(None))
        )
    else:
        prior_q = prior_q.where(InterviewInvite.enrolment_id.is_(None))
    priors = list((await db.scalars(prior_q.order_by(InterviewInvite.created_at.desc()))).all())

    for prior in priors:
        if prior.status != "consumed" or prior.expires_at <= now:
            # An expired started link redeems nothing (interview_take checks the
            # expiry), so it cannot be a second way in and does not block.
            continue
        # A consumed invite whose interview has finished (scored, or the worker
        # has marked the session completed) is not a live one; it is left as it
        # is and does not block a new invite.
        if await _interview_finished(db, prior):
            continue
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{applicant.full_name} has already started this interview, so the link "
                "cannot be replaced. If they dropped out and will not return, end the "
                "started interview first, then send a new link."
            ),
        )
    live = [p for p in priors if p.status == "invited" and p.expires_at > now]
    stale = [p for p in priors if p.status == "invited" and p.expires_at <= now]

    if live and not body.replace_existing:
        newest = live[0]
        identical = (
            newest.language == body.language
            and newest.avatar_mode == body.avatar_mode
            and newest.scheduled_at == body.scheduled_at
            and now - newest.created_at < _DEDUPE_WINDOW
        )
        if identical:
            # A repeat of the request that made this invite (double submit,
            # re-fired proposal, retried network call). Answer with what it
            # already made: no second link, no second email. Only for a request
            # that did not ask to replace: a confirmed replacement is HR saying
            # "the candidate needs a new link" (a lost email, say), and that is
            # honoured even moments after the last one.
            response.status_code = status.HTTP_200_OK
            title = await db.scalar(select(Job.title).where(Job.id == newest.job_id))
            log.info("hr.interview.invite_deduplicated", invite_id=str(newest.id),
                     company_id=str(company_id))
            return InviteResult(
                invite_id=str(newest.id), applicant_id=str(applicant.id),
                applicant_name=applicant.full_name, job_title=title or "",
                magic_link="", expires_at=newest.expires_at.isoformat(),
                scheduled_at=newest.scheduled_at.isoformat() if newest.scheduled_at else None,
                status=newest.status, reused=True,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{applicant.full_name} already has an active interview link for this "
                "opening, so no new link was sent. To send one anyway, replace the "
                "existing link from AI interviews."
            ),
        )

    job = await _resolve_job(db, hr_uid, company_id, applicant, body, role=app_)
    # Only reachable with an explicit job_id: a started link on that same job
    # still occupies the one-active-invite-per-(applicant, job) index, and the
    # insert would fail with a 500 rather than say why.
    if any(p.status == "consumed" and p.job_id == job.id for p in priors):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{applicant.full_name} already has a started interview on record for "
                "that job. End it first, or send the invite without choosing a job."
            ),
        )

    # Replaced links (HR confirmed) and dead ones nobody swept (no confirmation
    # needed: the candidate cannot use them) are both retired, so exactly one
    # 'invited' row is left and no reschedule can bring an old one back.
    for prior, reason in [*((p, "replaced") for p in live),
                          *((p, "superseded") for p in stale)]:
        prior.status = "revoked"
        prior.updated_at = now
        _audit_invite(db, request, actor=hr_uid, action="interview_invite.revoked",
                      invite_id=prior.id,
                      details={"company_id": str(company_id), "previous_status": "invited",
                               "reason": reason})
    if live or stale:
        await db.flush()

    ttl_hours = body.ttl_hours or settings.interview_link_ttl_hours
    # PH4-D2: any recorded deadline extension applies to this invite's expiry
    # too — application-scoped or applicant-wide (a manual invite has no
    # workflow round to be scoped to).
    adj_row = await accommodations.effective_for(
        db, company_id=company_id, applicant_id=applicant.id, enrolment_id=app_.enrolment_id,
    )
    extra_days = adj_row.deadline_extension_days if adj_row else None
    raw_token = mint_interview_token()
    invite = InterviewInvite(
        id=uuid.uuid4(),
        company_id=company_id,
        applicant_id=applicant.id,
        enrolment_id=app_.enrolment_id,
        job_id=job.id,
        created_by_user_id=hr_uid,
        token_hash=hash_interview_token(raw_token, settings.interview_link_secret),
        language=body.language,
        avatar_mode=body.avatar_mode,
        expires_at=invite_expiry(
            now, body.scheduled_at, ttl_hours=ttl_hours, extra_days=extra_days
        ),
        scheduled_at=body.scheduled_at,
        accommodation_id=adj_row.id if adj_row else None,
        status="invited",
        created_at=now,
        updated_at=now,
    )
    db.add(invite)
    if adj_row is not None:
        await db.flush()
        await accommodations.record_applied(
            db, company_id=company_id, accommodation_id=adj_row.id,
            target_kind="interview_invite", target_id=invite.id,
        )
    # Notify the inviting HR (their own activity feed).
    await create_notification(
        db,
        user_id=hr_uid,
        kind="invite_sent",
        title="Interview invite sent",
        body=f"{applicant.full_name} · {job.title}",
        link="/hr/interviews",
    )

    base = settings.interview_link_base_url.rstrip("/")
    magic_link = f"{base}/interview-invite#{raw_token}"
    # Stage the candidate email on this same transaction (atomic with the invite);
    # the outbox worker delivers it. HR still gets the link in the response to copy
    # manually if needed.
    await _tell_candidate_about_invite(
        db, applicant=applicant, job_title=job.title, magic_link=magic_link,
        scheduled_at=invite.scheduled_at, language=body.language, company_id=company_id,
        invite_id=invite.id,
    )
    _audit_invite(db, request, actor=hr_uid, action="interview_invite.created",
                  invite_id=invite.id,
                  details={"company_id": str(company_id), "applicant_id": str(applicant.id),
                           "scheduled_at": invite.scheduled_at.isoformat()
                           if invite.scheduled_at else None,
                           "expires_at": invite.expires_at.isoformat()})
    await db.commit()
    log.info("hr.interview.invited", invite_id=str(invite.id), company_id=str(company_id))
    return InviteResult(
        invite_id=str(invite.id),
        applicant_id=str(applicant.id),
        applicant_name=applicant.full_name,
        job_title=job.title,
        magic_link=magic_link,
        expires_at=invite.expires_at.isoformat(),
        scheduled_at=invite.scheduled_at.isoformat() if invite.scheduled_at else None,
        status=invite.status,
    )



def _audit_invite(
    db: AsyncSession, request: Request, *, actor: uuid.UUID, action: str,
    invite_id: uuid.UUID, details: dict[str, Any],
) -> None:
    """PH4-A2 #25: scheduling changes to an AI interview are audited too — the
    human-interview loops are, and a candidate's schedule is one record."""
    db.add(AuditLog(
        actor_id=actor, actor_type="user", action=action, resource_type="interview_invite",
        resource_id=invite_id, details=details, ip_address=extract_client_ip(request),
        user_agent=extract_user_agent(request), event_ts=datetime.now(tz=UTC),
    ))


class InviteRescheduleIn(BaseModel):
    scheduled_at: datetime

    @field_validator("scheduled_at")
    @classmethod
    def _future(cls, v: datetime) -> datetime:
        if v < datetime.now(tz=UTC):
            raise ValueError("The new interview time cannot be in the past.")
        return v


@router.patch("/interviews/{invite_id}", response_model=InviteResult)
async def reschedule_invite(
    invite_id: uuid.UUID, body: InviteRescheduleIn, request: Request, ctx: HrCtxDep,
    db: DbSessionDep,
) -> InviteResult:
    """Change an invite's scheduled_at, only while nobody has started it.

    A started ('consumed') interview is refused: moving it would email the
    candidate "your interview has been rescheduled" in the middle of it and
    change nothing about the running session. An unstarted link past its expiry
    may be moved; the new slot re-extends it, which is how HR recovers a missed
    slot. Re-emails the candidate the new time. The magic link is NOT re-minted,
    so the response carries no raw token."""
    hr_uid, company_id = ctx
    inv = await _get_owned_invite(db, company_id, invite_id)
    # Locked like create_invite, and re-read under the lock, so a new invite
    # cannot land between the checks below and the commit.
    await _lock_applicant_invites(db, company_id, inv.applicant_id)
    await db.refresh(inv)
    if inv.status != "invited":
        raise HTTPException(
            status_code=409,
            detail=(
                "Only an interview the candidate has not started can be rescheduled."
                if inv.status == "consumed"
                else f"This interview is {inv.status}, so it cannot be rescheduled."
            ),
        )
    # Moving the slot re-extends the link, so it must not resurrect a link that
    # a later invite replaced, or email a candidate whose application has since
    # been decided or withdrawn.
    superseded, application_open = await _reschedule_blockers(db, inv)
    if superseded:
        raise HTTPException(
            status_code=409,
            detail=(
                "A newer interview invite exists for this application, so this older "
                "link cannot be rescheduled. Reschedule the newer one instead."
            ),
        )
    if not application_open:
        raise HTTPException(
            status_code=409,
            detail=(
                "This application has been decided or withdrawn, so the interview "
                "cannot be rescheduled."
            ),
        )
    previous = inv.scheduled_at
    if inv.scheduled_at != body.scheduled_at:
        # The reminders already sent were for the old slot; the new one needs
        # its own 24h and 1h nudges.
        await rearm_interview_reminders(db, inv.id)
    inv.scheduled_at = body.scheduled_at
    # The link must still work at the new time (IV-H1): extend, never shorten.
    inv.expires_at = invite_expiry(
        datetime.now(tz=UTC), body.scheduled_at,
        ttl_hours=settings.interview_link_ttl_hours, current=inv.expires_at,
    )
    _audit_invite(db, request, actor=hr_uid, action="interview_invite.rescheduled",
                  invite_id=inv.id,
                  details={"company_id": str(company_id),
                           "from": previous.isoformat() if previous else None,
                           "to": body.scheduled_at.isoformat() if body.scheduled_at else None})
    inv.updated_at = datetime.now(tz=UTC)

    applicant = await db.scalar(select(Applicant).where(Applicant.id == inv.applicant_id))
    job_title = await db.scalar(select(Job.title).where(Job.id == inv.job_id)) or "the role"
    if applicant is not None:
        # magic_link is NOT re-minted on reschedule — the template renders the "use
        # your original link" copy. Staged on this transaction, then worker-delivered.
        await _tell_candidate_about_invite(
            db, applicant=applicant, job_title=job_title, magic_link=None,
            scheduled_at=inv.scheduled_at, language=inv.language, company_id=company_id,
            invite_id=inv.id, rescheduled=True,
        )
    await db.commit()
    log.info("hr.interview.rescheduled", invite_id=str(inv.id), company_id=str(company_id))
    return InviteResult(
        invite_id=str(inv.id),
        applicant_id=str(inv.applicant_id),
        applicant_name=applicant.full_name if applicant else "",
        job_title=job_title,
        magic_link="",  # not re-minted on reschedule
        expires_at=inv.expires_at.isoformat(),
        scheduled_at=inv.scheduled_at.isoformat() if inv.scheduled_at else None,
        status=inv.status,
    )


@router.get("/interviews", response_model=list[InviteOut])
async def list_invites(
    ctx: HrCtxDep,
    db: DbSessionDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    applicant_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[InviteOut]:
    _hr_uid, company_id = ctx
    stmt = (
        select(
            InterviewInvite, Applicant.full_name, Job.title,
            Scorecard.scorecard_id, Scorecard.composite_score,
            _SUPERSEDED.label("superseded"), _APPLICATION_OPEN.label("application_open"),
        )
        .join(Applicant, Applicant.id == InterviewInvite.applicant_id)
        .join(Job, Job.id == InterviewInvite.job_id)
        .outerjoin(Scorecard, Scorecard.session_id == InterviewInvite.session_id)
        .where(InterviewInvite.company_id == company_id, InterviewInvite.deleted_at.is_(None))
        .order_by(InterviewInvite.created_at.desc())
    )
    if status_filter:
        stmt = stmt.where(InterviewInvite.status == status_filter)
    if applicant_id is not None:
        stmt = stmt.where(InterviewInvite.applicant_id == applicant_id)
    rows = (await db.execute(stmt)).all()

    # This handler no longer WRITES. It used to flip consumed -> completed here
    # and announce the interview from inside a GET, which meant HR learned that
    # somebody had finished only if HR happened to open this page — the event had
    # no announcer of its own. Both the durable flip and the notification now
    # belong to the reminders sweep (`reminders._interview_completed`), which is
    # driven by the scorecard appearing rather than by anyone reading a list.
    #
    # A previous note here claimed the completion EMAIL had been "moved out of
    # this GET handler" to the interview_take completion path. It had not: there
    # is no interview_completed template and no such call site, and the claim
    # arrived with the initial import — so it described a move that never
    # happened. HR is told in-app; the candidate gets the results_ready email.
    now = datetime.now(tz=UTC)
    out: list[InviteOut] = []
    for inv, name, title, scorecard_id, composite, superseded, application_open in rows:
        # Effective status for DISPLAY only (see _effective_status): HR sees the
        # truth immediately while the durable transition stays single-owner.
        out.append(_invite_out(
            inv, name=name, title=title, scorecard_id=scorecard_id, composite=composite,
            now=now, superseded=bool(superseded), application_open=bool(application_open),
        ))
    return out


@router.get("/interviews/{invite_id}", response_model=InviteOut)
async def get_invite(invite_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> InviteOut:
    _hr_uid, company_id = ctx
    inv = await _get_owned_invite(db, company_id, invite_id)
    name = await db.scalar(select(Applicant.full_name).where(Applicant.id == inv.applicant_id))
    title = await db.scalar(select(Job.title).where(Job.id == inv.job_id))
    sc = None
    composite = None
    if inv.session_id is not None:
        row = (
            await db.execute(
                select(Scorecard.scorecard_id, Scorecard.composite_score).where(
                    Scorecard.session_id == inv.session_id
                )
            )
        ).first()
        if row is not None:
            sc, composite = row
    superseded, application_open = await _reschedule_blockers(db, inv)
    return _invite_out(
        inv, name=name, title=title, scorecard_id=sc, composite=composite,
        superseded=superseded, application_open=application_open,
    )


class InviteRevokeIn(BaseModel):
    # Ending an interview the candidate has STARTED is a separate, deliberate
    # act: without ``force`` a started link is refused, so a stale tab or a
    # re-fired request cannot cut a candidate off mid-interview.
    force: bool = False
    # Why, as a fixed code, never free text. The reason lands in audit_log,
    # which is append-only and whose erasure redaction (admin_ops
    # erasure_executor step 5l, AR-5) covers only named decision shapes: prose
    # typed here about a candidate would outlive their erasure.
    reason: EndReason | None = None


@router.post("/interviews/{invite_id}/revoke", response_model=InviteOut)
async def revoke_invite(
    invite_id: uuid.UUID, request: Request, ctx: HrCtxDep, db: DbSessionDep,
    body: InviteRevokeIn | None = None,
) -> InviteOut:
    """Kill an invite's link.

    An unused ('invited') link is revoked as it always was. A started
    ('consumed') one is refused by default: the candidate may be mid-interview
    and reconnecting after a dropped call. With ``force`` and a reason it is
    ended anyway. That is the way out for an interview the candidate abandoned
    (otherwise nothing can be re-sent until the link expires, days later) and
    the kill switch for a link that was forwarded or leaked. The interview's
    session is marked abandoned and the act is audited as
    'revoked_in_progress'. A finished interview has nothing left to end.
    """
    hr_uid, company_id = ctx
    body = body or InviteRevokeIn()
    inv = await _get_owned_invite(db, company_id, invite_id)
    await _lock_applicant_invites(db, company_id, inv.applicant_id)
    await db.refresh(inv)
    now = datetime.now(tz=UTC)
    in_progress = False
    if inv.status == "consumed":
        if await _interview_finished(db, inv):
            raise HTTPException(
                status_code=409,
                detail="This interview has finished, so there is nothing left to end.",
            )
        if not body.force:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The candidate has already started this interview, so the link cannot "
                    "be revoked. End the started interview instead if they will not "
                    "return."
                ),
            )
        if body.reason is None:
            raise HTTPException(
                status_code=422,
                detail="Give a reason for ending an interview the candidate has started.",
            )
        in_progress = True
    elif inv.status != "invited":
        # A completed, expired or already-revoked one has nothing left to revoke.
        raise HTTPException(
            status_code=409,
            detail=f"This interview is {inv.status}, so there is no link to revoke.",
        )
    details: dict[str, Any] = {"company_id": str(company_id), "previous_status": inv.status}
    if in_progress:
        details["reason"] = "revoked_in_progress"
        details["end_reason"] = body.reason
        details["session_id"] = str(inv.session_id) if inv.session_id else None
    _audit_invite(db, request, actor=hr_uid, action="interview_invite.revoked",
                  invite_id=inv.id, details=details)
    if in_progress and inv.session_id is not None:
        # The session ends here as far as the platform is concerned. Only a
        # session still open is touched; a terminal one keeps what it records.
        await db.execute(
            update(SessionModel)
            .where(SessionModel.id == inv.session_id,
                   SessionModel.status.in_(("created", "in_progress")))
            .values(status="abandoned", updated_at=now)
        )
    # Kills the link and every later redeem or reconnect at once (both require
    # 'invited' or 'consumed'). Cannot claw back a guest JWT already issued (no
    # jti denylist); its short TTL bounds that.
    inv.status = "revoked"
    inv.updated_at = now
    await db.commit()
    log.info("hr.interview.revoked", invite_id=str(inv.id), company_id=str(company_id),
             in_progress=in_progress)
    name = await db.scalar(select(Applicant.full_name).where(Applicant.id == inv.applicant_id))
    title = await db.scalar(select(Job.title).where(Job.id == inv.job_id))
    return _invite_out(inv, name=name, title=title)


class AvatarModeIn(BaseModel):
    avatar_mode: str

    @field_validator("avatar_mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in AVATAR_MODES:
            raise ValueError(f"avatar_mode must be one of {sorted(AVATAR_MODES)}")
        return v


@router.patch("/interviews/{invite_id}/avatar-mode", response_model=InviteOut)
async def set_invite_avatar_mode(
    invite_id: uuid.UUID,
    body: AvatarModeIn,
    request: Request,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> InviteOut:
    """Switch one interview between the avatar and audio-only, before it starts.

    WHY THIS EXISTS AND IS NOT JUST A FIELD ON CREATE. Most invites are not
    hand-made: the workflow runner mints them when a candidate clears the round
    before the AI interview, with no human in the loop. A mode that could only
    be chosen at mint time would therefore be unreachable for the majority of
    interviews — HR would own the setting on paper and not in practice.

    ONLY WHILE 'invited', and the reason is the copy. The mode is copied onto
    the ``sessions`` row at first redeem; after that the worker has read it, or
    is about to. Allowing a change later would present HR a control that
    silently does nothing for the interview in front of them — worse than no
    control. A started interview is therefore refused with the status, not
    quietly ignored.
    """
    hr_uid, company_id = ctx
    inv = await _get_owned_invite(db, company_id, invite_id)
    if inv.status != "invited":
        raise HTTPException(
            status_code=409,
            detail=(
                f"This interview is already {inv.status} - the mode is fixed once "
                "the candidate starts. Revoke it and send a new invite to change it."
            ),
        )
    previous = inv.avatar_mode
    if previous != body.avatar_mode:
        inv.avatar_mode = body.avatar_mode
        inv.updated_at = datetime.now(tz=UTC)
        _audit_invite(
            db, request, actor=hr_uid, action="interview_invite.avatar_mode_changed",
            invite_id=inv.id,
            details={"company_id": str(company_id), "from": previous, "to": body.avatar_mode},
        )
        await db.commit()
        log.info(
            "hr.interview.avatar_mode_changed",
            invite_id=str(inv.id), company_id=str(company_id),
            previous=previous, avatar_mode=body.avatar_mode,
        )
    name = await db.scalar(select(Applicant.full_name).where(Applicant.id == inv.applicant_id))
    title = await db.scalar(select(Job.title).where(Job.id == inv.job_id))
    superseded, application_open = await _reschedule_blockers(db, inv)
    return _invite_out(
        inv, name=name, title=title, superseded=superseded, application_open=application_open,
    )


@router.get("/interviews/{invite_id}/result", response_model=InterviewOutcome)
async def invite_result(invite_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> InterviewOutcome:
    _hr_uid, company_id = ctx
    inv = await _get_owned_invite(db, company_id, invite_id)
    name = await db.scalar(select(Applicant.full_name).where(Applicant.id == inv.applicant_id))
    session_status: str | None = None
    sc: Scorecard | None = None
    if inv.session_id is not None:
        session_status = await db.scalar(
            text("SELECT status FROM sessions WHERE id = :sid"), {"sid": inv.session_id}
        )
        sc = await db.scalar(
            select(Scorecard).where(Scorecard.session_id == inv.session_id)
        )
    return InterviewOutcome(
        invite_id=str(inv.id), applicant_id=str(inv.applicant_id), applicant_name=name or "",
        status=inv.status, session_status=session_status,
        scorecard_id=str(sc.scorecard_id) if sc else None,
        composite_score=float(sc.composite_score) if sc and sc.composite_score is not None else None,
        scores=sc.scores if sc else None,
        strengths=sc.strengths if sc else None,
        improvements=sc.improvements if sc else None,
        summary=sc.summary if sc else None,
    )
