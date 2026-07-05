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
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.interview_link import hash_interview_token, mint_interview_token
from app.mailer import enqueue_email, notify
from app.models import Applicant, InterviewInvite, Job, Scorecard
from app.notifications_util import create_notification
from app.routers.hr_applicants import DbSessionDep, HrCtxDep, _get_owned

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-interviews"])

_LANGS = {"en", "hi", "te"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class InviteCreateIn(BaseModel):
    applicant_id: uuid.UUID
    job_id: uuid.UUID | None = None
    job_title: str | None = Field(default=None, max_length=300)
    level: str | None = None
    language: str = "en"
    scheduled_at: datetime | None = None
    ttl_hours: int | None = Field(default=None, ge=1, le=8760)

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str) -> str:
        if v not in _LANGS:
            raise ValueError(f"language must be one of {sorted(_LANGS)}")
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


class InviteOut(BaseModel):
    invite_id: str
    applicant_id: str
    applicant_name: str
    job_title: str
    language: str
    status: str
    scheduled_at: str | None
    expires_at: str
    created_at: str
    composite_score: float | None
    scorecard_id: str | None


class EligibleApplicantOut(BaseModel):
    id: str
    full_name: str
    target_job_title: str
    target_level: str
    status: str
    ats_overall: int | None
    passed_exam: bool
    has_active_invite: bool


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
async def _has_passed_exam(db: AsyncSession, company_id: uuid.UUID, applicant_id: uuid.UUID) -> bool:
    row = await db.scalar(
        text(
            "SELECT 1 FROM exam_attempts WHERE applicant_id = :aid AND company_id = :cid "
            "AND passed IS TRUE AND status = 'submitted' AND deleted_at IS NULL LIMIT 1"
        ),
        {"aid": applicant_id, "cid": company_id},
    )
    return row is not None


async def _get_owned_invite(
    db: AsyncSession, company_id: uuid.UUID, invite_id: uuid.UUID
) -> InterviewInvite:
    inv = await db.scalar(
        select(InterviewInvite).where(
            InterviewInvite.id == invite_id,
            InterviewInvite.company_id == company_id,
            InterviewInvite.deleted_at.is_(None),
        )
    )
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found.")
    return inv


async def _create_job_from_applicant(
    db: AsyncSession,
    applicant: Applicant,
    *,
    created_by_user_id: uuid.UUID | None,
    language: str = "en",
    job_title: str | None = None,
    level: str | None = None,
) -> Job:
    """Create a tenant-owned Job from the applicant's screening role so the
    interviewer prompt is grounded. Shared by manual invite + auto-advance."""
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
        jd_text=applicant.target_jd_text,
        created_by_user_id=created_by_user_id,  # tenant-owned (M4)
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    await db.flush()
    return job


async def _resolve_job(
    db: AsyncSession, hr_uid: uuid.UUID, company_id: uuid.UUID, applicant: Applicant, body: InviteCreateIn
) -> Job:
    """Pick the interview job: an explicit tenant-owned/global job_id, else create a
    tenant-owned job from the applicant's screening role so the prompt is grounded."""
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
        job_title=body.job_title, level=body.level,
    )


async def _email_interview_invite(
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
    """Stage the branded candidate interview-invite email on ``db`` (caller commits).

    Values are escaped inside the template renderer (a candidate name / job title
    can contain markup). ``magic_link=None`` renders the "use your original link"
    copy (reschedule path — the link is not re-minted)."""
    if not applicant.email:
        return
    when = scheduled_at.strftime("%d %b %Y, %H:%M UTC") if scheduled_at else None
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
    scheduled_at: datetime | None = None,
    notify_user_id: uuid.UUID | None = None,
) -> InterviewInvite | None:
    """Auto-advance: mint an interview invite for an exam-passed applicant + email
    the candidate the link. Caller owns the commit. Returns the invite, or None if
    an active invite already exists (idempotent — never double-invites).

    Reuses the exact invite-mint shape as create_invite (lazy provisioning; the
    guest user/session/consent are created on first redeem in interview_take.py).
    """
    # Idempotent: don't create a second invite if one is already active.
    existing = await db.scalar(
        select(InterviewInvite).where(
            InterviewInvite.applicant_id == applicant.id,
            InterviewInvite.company_id == company_id,
            InterviewInvite.status.in_(("invited", "consumed")),
            InterviewInvite.deleted_at.is_(None),
        )
    )
    if existing is not None:
        # One active interview invite per applicant per company — don't double-invite
        # (e.g. a prior manual invite or another exam pass). Log so it isn't silent.
        log.info(
            "hr.interview.auto_advance_skipped_existing",
            applicant_id=str(applicant.id), company_id=str(company_id),
        )
        return None

    job = await _create_job_from_applicant(
        db, applicant, created_by_user_id=created_by_user_id, language=language
    )
    now = datetime.now(tz=UTC)
    raw_token = mint_interview_token()
    invite = InterviewInvite(
        id=uuid.uuid4(),
        company_id=company_id,
        applicant_id=applicant.id,
        job_id=job.id,
        created_by_user_id=created_by_user_id,
        token_hash=hash_interview_token(raw_token, settings.interview_link_secret),
        language=language,
        expires_at=now + timedelta(hours=settings.interview_link_ttl_hours),
        scheduled_at=scheduled_at,
        status="invited",
        created_at=now,
        updated_at=now,
    )
    db.add(invite)
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
    await _email_interview_invite(
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
@router.get("/interviews/eligible-applicants", response_model=list[EligibleApplicantOut])
async def eligible_applicants(
    ctx: HrCtxDep,
    db: DbSessionDep,
    source: Annotated[str, Query()] = "any",
) -> list[EligibleApplicantOut]:
    """Applicants ready for an interview: shortlisted and/or passed an exam."""
    _hr_uid, company_id = ctx
    rows = (
        await db.execute(
            select(Applicant)
            .where(Applicant.company_id == company_id, Applicant.deleted_at.is_(None))
            .order_by(Applicant.ats_overall.desc().nullslast(), Applicant.created_at.desc())
        )
    ).scalars().all()

    out: list[EligibleApplicantOut] = []
    for a in rows:
        passed = await _has_passed_exam(db, company_id, a.id)
        is_shortlisted = a.status == "shortlisted"
        if source == "shortlisted" and not is_shortlisted:
            continue
        if source == "exam_passed" and not passed:
            continue
        if source == "any" and not (is_shortlisted or passed):
            continue
        active = await db.scalar(
            select(InterviewInvite.id).where(
                InterviewInvite.applicant_id == a.id,
                InterviewInvite.company_id == company_id,
                InterviewInvite.status.in_(("invited", "consumed")),
                InterviewInvite.deleted_at.is_(None),
            )
        )
        out.append(
            EligibleApplicantOut(
                id=str(a.id), full_name=a.full_name, target_job_title=a.target_job_title,
                target_level=a.target_level, status=a.status, ats_overall=a.ats_overall,
                passed_exam=passed, has_active_invite=active is not None,
            )
        )
    return out


@router.post("/interviews", status_code=status.HTTP_201_CREATED, response_model=InviteResult)
async def create_invite(body: InviteCreateIn, ctx: HrCtxDep, db: DbSessionDep) -> InviteResult:
    hr_uid, company_id = ctx
    applicant = await _get_owned(db, company_id, body.applicant_id)

    # Funnel gate: only shortlisted or exam-passed applicants are interview-eligible.
    if applicant.status != "shortlisted" and not await _has_passed_exam(db, company_id, applicant.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Applicant must be shortlisted or have passed an exam before an interview.",
        )

    if body.scheduled_at is not None and body.scheduled_at < datetime.now(tz=UTC):
        raise HTTPException(status_code=422, detail="scheduled_at cannot be in the past.")

    job = await _resolve_job(db, hr_uid, company_id, applicant, body)

    # Rotate any active invite for this (applicant, job) so the active partial-unique holds.
    prior = await db.scalar(
        select(InterviewInvite).where(
            InterviewInvite.applicant_id == applicant.id,
            InterviewInvite.job_id == job.id,
            InterviewInvite.company_id == company_id,
            InterviewInvite.status.in_(("invited", "consumed")),
            InterviewInvite.deleted_at.is_(None),
        )
    )
    now = datetime.now(tz=UTC)
    if prior is not None:
        prior.status = "revoked"
        prior.updated_at = now
        await db.flush()

    ttl_hours = body.ttl_hours or settings.interview_link_ttl_hours
    raw_token = mint_interview_token()
    invite = InterviewInvite(
        id=uuid.uuid4(),
        company_id=company_id,
        applicant_id=applicant.id,
        job_id=job.id,
        created_by_user_id=hr_uid,
        token_hash=hash_interview_token(raw_token, settings.interview_link_secret),
        language=body.language,
        expires_at=now + timedelta(hours=ttl_hours),
        scheduled_at=body.scheduled_at,
        status="invited",
        created_at=now,
        updated_at=now,
    )
    db.add(invite)
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
    await _email_interview_invite(
        db, applicant=applicant, job_title=job.title, magic_link=magic_link,
        scheduled_at=invite.scheduled_at, language=body.language, company_id=company_id,
        invite_id=invite.id,
    )
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


class InviteRescheduleIn(BaseModel):
    scheduled_at: datetime

    @field_validator("scheduled_at")
    @classmethod
    def _future(cls, v: datetime) -> datetime:
        if v < datetime.now(tz=UTC):
            raise ValueError("scheduled_at cannot be in the past.")
        return v


@router.patch("/interviews/{invite_id}", response_model=InviteResult)
async def reschedule_invite(
    invite_id: uuid.UUID, body: InviteRescheduleIn, ctx: HrCtxDep, db: DbSessionDep
) -> InviteResult:
    """Change an invite's scheduled_at (only while still invited/consumed, not
    completed/expired/revoked). Re-emails the candidate the new time. The magic
    link is NOT re-minted, so the response carries no raw token."""
    _hr_uid, company_id = ctx
    inv = await _get_owned_invite(db, company_id, invite_id)
    if inv.status not in ("invited", "consumed"):
        raise HTTPException(
            status_code=409, detail="Only an active invite can be rescheduled."
        )
    inv.scheduled_at = body.scheduled_at
    inv.updated_at = datetime.now(tz=UTC)

    applicant = await db.scalar(select(Applicant).where(Applicant.id == inv.applicant_id))
    job_title = await db.scalar(select(Job.title).where(Job.id == inv.job_id)) or "the role"
    if applicant is not None:
        # magic_link is NOT re-minted on reschedule — the template renders the "use
        # your original link" copy. Staged on this transaction, then worker-delivered.
        await _email_interview_invite(
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

    # Lazy completion: a scorecard exists -> flip consumed -> completed (frees the slot).
    #
    # NOTE (idempotency): the status flip from "consumed" → "completed" happens AT MOST
    # ONCE per invite — once the status is "completed" the condition
    # ``inv.status == "consumed"`` is false on subsequent GETs, so no further
    # notification is enqueued.  The notification (in-app feed only, no email) is
    # therefore fire-once.
    #
    # Email delivery for "interview_completed" has been MOVED OUT of this GET handler
    # entirely to prevent a per-refresh email storm (each list refresh would re-send
    # the email to the HR manager).  The email is now sent by the interview_take
    # completion path (where it fires exactly once when the session completes).  This
    # GET only flips the status and creates the in-app notification (email=False).
    now = datetime.now(tz=UTC)
    dirty = False
    out: list[InviteOut] = []
    for inv, name, title, scorecard_id, composite in rows:
        if scorecard_id is not None and inv.status == "consumed":
            inv.status = "completed"
            inv.updated_at = now
            dirty = True
            # In-app notification only — NO email here (see note above).
            await notify(
                db,
                user_id=inv.created_by_user_id,
                kind="interview_completed",
                title="Interview completed",
                body=f"{name} finished their interview — scorecard ready",
                link="/hr/interviews",
                email=False,  # email=False: fire-once email is sent at completion, not on list poll
                company_id=company_id,
            )
        # A non-completed invite past its expiry is effectively dead (redeem
        # already 404s it) — surface it as 'expired' instead of a stale
        # 'invited'/'consumed' so HR sees real link state.
        eff_status = (
            "expired"
            if inv.status in ("invited", "consumed") and inv.expires_at <= now
            else inv.status
        )
        out.append(
            InviteOut(
                invite_id=str(inv.id), applicant_id=str(inv.applicant_id), applicant_name=name,
                job_title=title, language=inv.language, status=eff_status,
                scheduled_at=inv.scheduled_at.isoformat() if inv.scheduled_at else None,
                expires_at=inv.expires_at.isoformat(), created_at=inv.created_at.isoformat(),
                composite_score=float(composite) if composite is not None else None,
                scorecard_id=str(scorecard_id) if scorecard_id else None,
            )
        )
    if dirty:
        await db.commit()
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
    return InviteOut(
        invite_id=str(inv.id), applicant_id=str(inv.applicant_id), applicant_name=name or "",
        job_title=title or "", language=inv.language, status=inv.status,
        scheduled_at=inv.scheduled_at.isoformat() if inv.scheduled_at else None,
        expires_at=inv.expires_at.isoformat(), created_at=inv.created_at.isoformat(),
        composite_score=float(composite) if composite is not None else None,
        scorecard_id=str(sc) if sc else None,
    )


@router.post("/interviews/{invite_id}/revoke", response_model=InviteOut)
async def revoke_invite(invite_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> InviteOut:
    _hr_uid, company_id = ctx
    inv = await _get_owned_invite(db, company_id, invite_id)
    # Kills the link + future redeems immediately. Cannot claw back an already-issued
    # in-flight guest JWT (no jti denylist) — mitigated by the short TTL + single-use.
    inv.status = "revoked"
    inv.updated_at = datetime.now(tz=UTC)
    await db.commit()
    name = await db.scalar(select(Applicant.full_name).where(Applicant.id == inv.applicant_id))
    title = await db.scalar(select(Job.title).where(Job.id == inv.job_id))
    return InviteOut(
        invite_id=str(inv.id), applicant_id=str(inv.applicant_id), applicant_name=name or "",
        job_title=title or "", language=inv.language, status=inv.status,
        scheduled_at=inv.scheduled_at.isoformat() if inv.scheduled_at else None,
        expires_at=inv.expires_at.isoformat(), created_at=inv.created_at.isoformat(),
        composite_score=None, scorecard_id=None,
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
