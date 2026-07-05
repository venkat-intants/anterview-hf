"""Applicant interview magic-link redemption — HR workflow Phase 3 (PUBLIC, no login).

An applicant opens an interview magic link (token in the URL #fragment, sent as the
X-Interview-Token header) and is dropped into the EXISTING avatar interview without
an account. LAZY provisioning happens here on first redeem:
  1. mint a 'guest_candidate' users row (password_hash NULL, company pinned, the
     applicant's resume_text copied so the worker grounds the prompt),
  2. record the applicant's OWN DPDP consent (they tick a box on the landing page),
  3. create the interview sessions row for that guest,
  4. issue a SHORT-LIVED guest access token bound to that one session_id.

HARD GUARANTEES:
  - Every failure is a uniform 404 (never reveals existence; folds not-yet-scheduled in).
  - Re-enterable within validity: a started ('consumed') invite may be redeemed again to
    reconnect to the SAME session until a scorecard exists or the link expires; the join
    window gates only the FIRST start (closing the tab / a mid-interview drop can rejoin).
  - The guest token's role is 'guest_candidate' ONLY (rejected by candidate/HR routes)
    and carries a session_id claim interview_core binds to one room.
  - Consent is the applicant's own act (consent_granted flag) — no server-asserted consent.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Cookie, Header, HTTPException, Response, status
from pydantic import BaseModel
from shared.auth.jwt import issue_access_token
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.interview_link import hash_interview_token
from app.models import Applicant, InterviewInvite, Job
from app.rate_limit import rate_limit
from app.routers.hr_applicants import DbSessionDep

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/interview-invite", tags=["interview-take"])

_NOT_AVAILABLE = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Interview link not available."
)
_DEFAULT_AVATAR = "anna"  # hardcoded — no data_gateway -> interview_core import (m5)

# httpOnly cookie carrying the raw invite token, set on redeem so a guest who
# RELOADS the interview page mid-session can transparently resume (POST /resume)
# WITHOUT any login. It is XSS-safe (httpOnly), expires with the invite link, and
# only ever re-issues a fresh guest token for the SAME already-started session.
_IV_RESUME_COOKIE = "iv_resume_token"


def _set_resume_cookie(response: Response, raw_token: str, expires_at: datetime, now: datetime) -> None:
    """Set the httpOnly resume cookie, capped to the invite's own expiry.

    Security posture (reviewed):
      - path is scoped to the resume endpoint ONLY, so the raw token is attached
        to that single POST — never to /auth, /hr, /admin, etc. (minimises where
        a bearer-equivalent secret travels).
      - samesite follows settings (must be 'none' in prod because the SPA on
        Vercel and this API on Railway are cross-site, so the cookie has to be
        sendable cross-site for reload-resume to work; 'strict' would silently
        break resume in prod). CSRF impact is nil regardless: /resume is
        idempotent, only ever re-issues a guest token bound to the SAME session
        the cookie already owns, and the response is cross-origin-unreadable
        (explicit CORS allow-list, no wildcard).
    """
    max_age = max(0, int((expires_at - now).total_seconds()))
    response.set_cookie(
        key=_IV_RESUME_COOKIE,
        value=raw_token,
        max_age=max_age,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/interview-invite/resume",
    )


class InviteInfoOut(BaseModel):
    applicant_name: str
    job_title: str
    level: str
    language: str
    status: str
    already_completed: bool
    scheduled_at: str | None


class RedeemIn(BaseModel):
    consent_granted: bool = False


class RedeemOut(BaseModel):
    session_id: str
    access_token: str
    language: str
    user_id: str
    full_name: str
    email: str | None
    roles: list[str]


async def _scorecard_exists(db: AsyncSession, session_id: uuid.UUID | None) -> bool:
    if session_id is None:
        return False
    row = await db.scalar(
        text("SELECT 1 FROM scorecards WHERE session_id = :sid LIMIT 1"), {"sid": session_id}
    )
    return row is not None


async def _session_completed(db: AsyncSession, session_id: uuid.UUID | None) -> bool:
    """True once the interview itself is finished — the worker flips sessions.status to
    'completed' at interview-end, BEFORE scoring runs. Keying off this (in addition to
    the scorecard) blocks re-entry during the scoring lag and even if scoring later
    fails (no scorecard is ever written). Deliberately NOT triggered by 'abandoned' /
    'in_progress' — a mid-interview drop MUST stay reconnectable."""
    if session_id is None:
        return False
    st = await db.scalar(text("SELECT status FROM sessions WHERE id = :sid"), {"sid": session_id})
    return st == "completed"


def _issue_guest_token(
    inv: InterviewInvite,
    session_id: uuid.UUID,
    guest_user_id: uuid.UUID,
    full_name: str,
) -> RedeemOut:
    """Mint the short-lived guest token (role guest_candidate ONLY, session_id-bound)
    and assemble the redeem response. Shared by the first-start and reconnect paths."""
    access_token = issue_access_token(
        str(guest_user_id),
        ["guest_candidate"],
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        extra_claims={"session_id": str(session_id)},
    )
    log.info(
        "interview.invite.redeemed",
        company_id=str(inv.company_id),
        session_id=str(session_id),
    )
    return RedeemOut(
        session_id=str(session_id),
        access_token=access_token,
        language=inv.language,
        user_id=str(guest_user_id),
        full_name=full_name,
        email=None,
        roles=["guest_candidate"],
    )


@router.get("", response_model=InviteInfoOut, dependencies=[rate_limit("interview_preview", 30)])
async def preview_invite(
    db: DbSessionDep,
    x_interview_token: Annotated[str | None, Header(alias="X-Interview-Token")] = None,
) -> InviteInfoOut:
    """Intro-card preview for the applicant. Never issues a token. 404 if unknown."""
    if not x_interview_token:
        raise _NOT_AVAILABLE
    th = hash_interview_token(x_interview_token, settings.interview_link_secret)
    inv = await db.scalar(
        select(InterviewInvite).where(
            InterviewInvite.token_hash == th, InterviewInvite.deleted_at.is_(None)
        )
    )
    if inv is None:
        raise _NOT_AVAILABLE
    applicant = await db.scalar(
        select(Applicant).where(
            Applicant.id == inv.applicant_id, Applicant.company_id == inv.company_id
        )
    )
    job = await db.scalar(select(Job).where(Job.id == inv.job_id))
    if applicant is None or job is None:
        raise _NOT_AVAILABLE
    completed = inv.status == "completed" or await _scorecard_exists(db, inv.session_id)
    return InviteInfoOut(
        applicant_name=applicant.full_name,
        job_title=job.title,
        level=job.level,
        language=inv.language,
        status=inv.status,
        already_completed=completed,
        scheduled_at=inv.scheduled_at.isoformat() if inv.scheduled_at else None,
    )


@router.post(
    "/redeem",
    response_model=RedeemOut,
    dependencies=[rate_limit("interview_redeem", settings.rate_limit_login_per_minute)],
)
async def redeem_invite(
    body: RedeemIn,
    db: DbSessionDep,
    response: Response,
    x_interview_token: Annotated[str | None, Header(alias="X-Interview-Token")] = None,
) -> RedeemOut:
    """Redeem the magic link.

    FIRST start (status 'invited'): provision guest+session, record consent, flip to
    'consumed'. Gated by the join window (settings.interview_join_window_minutes),
    anchored at scheduled_at — or at this first click when the invite is unscheduled.

    RECONNECT (status 'consumed', not yet scored): reuse the same guest+session and
    re-issue a fresh guest token. NOT window-gated — supports closing the tab or a
    mid-interview drop, until a scorecard exists or the link hard-expires.
    """
    if not x_interview_token:
        raise _NOT_AVAILABLE
    now = datetime.now(tz=UTC)
    th = hash_interview_token(x_interview_token, settings.interview_link_secret)

    # Resolve + LOCK the invite. Allow 'invited' (first start) AND 'consumed' (reconnect
    # to an already-started session); exclude revoked/expired/completed and hard-expired
    # links. Uniform 404 on any miss (anti-enumeration). The scheduled_at / window check
    # moves into the first-start branch below. with_for_update serializes concurrent
    # redeems of the SAME token.
    inv = await db.scalar(
        select(InterviewInvite)
        .where(
            InterviewInvite.token_hash == th,
            InterviewInvite.deleted_at.is_(None),
            InterviewInvite.status.in_(("invited", "consumed")),
            InterviewInvite.expires_at > now,
        )
        .with_for_update()
    )
    if inv is None:
        raise _NOT_AVAILABLE

    # Tenant-scoped applicant + active job (M4: a tenant-owned job must match company).
    applicant = await db.scalar(
        select(Applicant).where(
            Applicant.id == inv.applicant_id,
            Applicant.company_id == inv.company_id,
            Applicant.deleted_at.is_(None),
        )
    )
    job = await db.scalar(select(Job).where(Job.id == inv.job_id, Job.is_active.is_(True)))
    if applicant is None or job is None:
        raise _NOT_AVAILABLE
    if job.created_by_user_id is not None:
        owner_company = await db.scalar(
            text("SELECT company_id FROM users WHERE id = :uid"),
            {"uid": job.created_by_user_id},
        )
        if owner_company is not None and owner_company != inv.company_id:
            raise _NOT_AVAILABLE

    # A finished interview is never re-enterable. "Finished" = the worker marked the
    # session 'completed' (fires at interview-end, before scoring) OR a scorecard exists
    # (durable backstop if the status write failed or scoring lagged). A mid-interview
    # 'abandoned' is NOT finished — it stays reconnectable (the whole point of this flow).
    if await _session_completed(db, inv.session_id) or await _scorecard_exists(db, inv.session_id):
        raise _NOT_AVAILABLE

    # Consent is the applicant's OWN act (landing-page checkbox) — required on every redeem.
    if not body.consent_granted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Consent is required to begin the interview.",
        )

    # ---- RECONNECT: already started ('consumed'), not finished, link still valid.
    # NOT gated by the join window — a candidate who closed the tab or dropped mid-
    # interview rejoins the SAME session until it completes or the link hard-expires.
    if inv.status == "consumed":
        gid = inv.guest_user_id
        sid = inv.session_id
        if gid is None or sid is None:
            raise _NOT_AVAILABLE  # malformed 'consumed' row — fail closed
        inv.updated_at = now
        await db.commit()
        result = _issue_guest_token(inv, sid, gid, applicant.full_name)
        _set_resume_cookie(response, x_interview_token, inv.expires_at, now)
        return result

    # ---- FIRST START (status 'invited'). The join window gates first-start ONLY,
    # anchored at scheduled_at; an unscheduled invite anchors at this first click (so its
    # first start is always in-window). Both bounds fold into the uniform 404 (anti-enum).
    if inv.scheduled_at is not None:
        window = timedelta(minutes=settings.interview_join_window_minutes)
        if now < inv.scheduled_at or now > inv.scheduled_at + window:
            raise _NOT_AVAILABLE

    # --- provision-or-reuse the guest user (one per applicant) ---
    guest_user_id = applicant.user_id
    if guest_user_id is None:
        guest_user_id = uuid.uuid4()
        guest_email = f"invite+{guest_user_id}@guest.intants.local"
        try:
            await db.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, full_name, company_id, "
                    "resume_text, preferred_language, is_active, must_change_password, "
                    "created_at, updated_at) VALUES "
                    "(:id, :email, NULL, :fn, :cid, :rt, :lang, true, false, :now, :now)"
                ),
                {
                    "id": guest_user_id, "email": guest_email, "fn": applicant.full_name,
                    "cid": inv.company_id, "rt": applicant.resume_text or "",
                    "lang": inv.language, "now": now,
                },
            )
            await db.execute(
                text(
                    "INSERT INTO user_roles (user_id, role_id, assigned_at) VALUES "
                    "(:uid, (SELECT id FROM roles WHERE name = 'guest_candidate'), :now)"
                ),
                {"uid": guest_user_id, "now": now},
            )
            await db.execute(
                text("UPDATE applicants SET user_id = :uid, updated_at = :now WHERE id = :aid"),
                {"uid": guest_user_id, "aid": applicant.id, "now": now},
            )
            await db.flush()
        except IntegrityError:
            # Lost a race (uq_applicants_user_id) — reuse the winner's guest user.
            await db.rollback()
            guest_user_id = await db.scalar(
                select(Applicant.user_id).where(Applicant.id == inv.applicant_id)
            )
            if guest_user_id is None:
                raise _NOT_AVAILABLE from None
            # Re-lock the invite after rollback dropped our transaction.
            inv = await db.scalar(
                select(InterviewInvite)
                .where(InterviewInvite.id == inv.id, InterviewInvite.status == "invited")
                .with_for_update()
            )
            if inv is None:
                raise _NOT_AVAILABLE from None

    # --- record the applicant's DPDP consent against the guest user (idempotent) ---
    has_consent = await db.scalar(
        text(
            "SELECT 1 FROM dpdp_consent_ledger WHERE user_id = :uid "
            "AND consent_type = 'interview_voice_recording' AND purpose = 'interview' "
            "AND granted = TRUE AND revoked_at IS NULL LIMIT 1"
        ),
        {"uid": guest_user_id},
    )
    if not has_consent:
        await db.execute(
            text(
                "INSERT INTO dpdp_consent_ledger "
                "(id, user_id, consent_type, granted, granted_at, purpose, evidence) VALUES "
                "(:id, :uid, 'interview_voice_recording', true, :now, 'interview', "
                "CAST(:ev AS jsonb))"
            ),
            {
                "id": uuid.uuid4(), "uid": guest_user_id, "now": now,
                "ev": json.dumps(
                    {
                        "source": "interview_invite_landing",
                        "applicant_id": str(applicant.id),
                        "company_id": str(inv.company_id),
                        "consented_at_iso": now.isoformat(),
                    }
                ),
            },
        )

    # --- provision-or-reuse the session ---
    session_id = inv.session_id
    if session_id is None:
        session_id = uuid.uuid4()
        avatar = inv.avatar_id or _DEFAULT_AVATAR
        await db.execute(
            text(
                "INSERT INTO sessions (id, user_id, job_id, language, status, started_at, "
                'metadata, presenter_id, created_at, updated_at) VALUES '
                "(:id, :uid, :jid, :lang, 'created', :now, CAST(:meta AS jsonb), :pid, :now, :now)"
            ),
            {
                "id": session_id, "uid": guest_user_id, "jid": inv.job_id,
                "lang": inv.language, "now": now, "pid": avatar,
                "meta": json.dumps(
                    {"source": "hr_invite", "applicant_id": str(applicant.id),
                     "company_id": str(inv.company_id)}
                ),
            },
        )

    # --- start flip + bind. status 'consumed' now means "started" (reconnectable),
    # NOT "dead" — a re-redeem returns to this same session until it completes/expires.
    inv.guest_user_id = guest_user_id
    inv.session_id = session_id
    inv.status = "consumed"
    inv.consumed_at = now
    inv.updated_at = now
    await db.commit()
    result = _issue_guest_token(inv, session_id, guest_user_id, applicant.full_name)
    _set_resume_cookie(response, x_interview_token, inv.expires_at, now)
    return result


@router.post(
    "/resume",
    response_model=RedeemOut,
    dependencies=[rate_limit("interview_resume", 30)],
)
async def resume_invite(
    db: DbSessionDep,
    response: Response,
    iv_cookie: Annotated[str | None, Cookie(alias=_IV_RESUME_COOKIE)] = None,
) -> RedeemOut:
    """Resume an in-progress interview after a page RELOAD, using the httpOnly
    resume cookie set at redeem.

    Reconnect-ONLY: re-issues a fresh guest token for the SAME already-started
    session. No new consent (captured at first redeem) and NO login. This is what
    keeps a mid-interview refresh from bouncing the (account-less) applicant to a
    login wall. Uniform 404 on any miss — the frontend then shows a "re-open your
    link" message, never /login.
    """
    if not iv_cookie:
        raise _NOT_AVAILABLE
    now = datetime.now(tz=UTC)
    th = hash_interview_token(iv_cookie, settings.interview_link_secret)
    # Only an already-STARTED ('consumed'), non-expired, non-revoked invite resumes.
    inv = await db.scalar(
        select(InterviewInvite)
        .where(
            InterviewInvite.token_hash == th,
            InterviewInvite.deleted_at.is_(None),
            InterviewInvite.status == "consumed",
            InterviewInvite.expires_at > now,
        )
        .with_for_update()
    )
    if inv is None:
        raise _NOT_AVAILABLE
    # A finished interview is never resumable.
    if await _session_completed(db, inv.session_id) or await _scorecard_exists(db, inv.session_id):
        raise _NOT_AVAILABLE
    applicant = await db.scalar(
        select(Applicant).where(
            Applicant.id == inv.applicant_id,
            Applicant.company_id == inv.company_id,
            Applicant.deleted_at.is_(None),
        )
    )
    gid, sid = inv.guest_user_id, inv.session_id
    if applicant is None or gid is None or sid is None:
        raise _NOT_AVAILABLE
    inv.updated_at = now
    await db.commit()
    result = _issue_guest_token(inv, sid, gid, applicant.full_name)
    _set_resume_cookie(response, iv_cookie, inv.expires_at, now)  # slide the TTL
    return result
