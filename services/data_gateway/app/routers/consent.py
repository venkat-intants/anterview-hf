"""DPDP consent ledger endpoints — S3-011 / S4-009 / S4-010.

DPDP Act 2023, §7: every piece of personal data processing requires prior
explicit consent. §11 grants users the right to withdraw consent at any time.
This router records, queries, and revokes that consent.

Contract:
  POST   /consent        → 201 ConsentResponse (first grant)
                         | 200 ConsentResponse (idempotent — already consented,
                                                or race caught by unique index)
                         | 400 (invalid purpose)
                         | 401 (missing/invalid JWT)
  GET    /consent/status → 200 ConsentStatus
                         | 401
  DELETE /consent        → 200 ConsentRevocationResponse (ALL active rows revoked —
                               both interview_voice_recording and video_capture —
                               and every non-terminal session stamped
                               status='consent_withdrawn')
                         | 404 (no active consent of any type to revoke)
                         | 401

S4-009 race safety:
  The partial unique index ``ix_dpdp_consent_active_unique`` (added by migration
  20260528_0001) enforces ONE active consent row per (user_id, consent_type,
  purpose) at the database level. If a concurrent POST races past the explicit
  idempotency pre-check, the second INSERT raises IntegrityError. The handler
  catches that, re-queries for the winning row, and returns 200 — making the
  endpoint fully race-proof. The pre-check remains as a fast-path that avoids
  write attempts when there is no race.
"""

from __future__ import annotations

import hashlib
import uuid as _uuid_mod
from datetime import UTC, datetime
from typing import Annotated, Any, cast

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from shared.auth.base import User
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import DbSessionDep
from app.dependencies import get_current_user
from app.models import DpdpConsent
from app.models import Session as InterviewSession
from app.retention import CONSENT_WITHDRAWN_STATUS
from app.schemas.consent import (
    ConsentRequest,
    ConsentResponse,
    ConsentRevocationResponse,
    ConsentStatus,
    RevokedConsentItem,
)

# DG-2: the trusted-proxy hop arithmetic moved to app/utils/request_ip.py so
# app/rate_limit.py — infrastructure that runs before a route is chosen — no
# longer has to import this route module to resolve a client IP. The private
# names below are kept as aliases because four modules and the consent tests
# import them; they are the same objects, not copies.
from app.utils.request_ip import (
    extract_client_ip as _extract_client_ip,
)
from app.utils.request_ip import (
    extract_user_agent as _extract_user_agent,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/consent", tags=["consent"])

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------
# Day-1 voice type is the default so existing callers (which send no
# consent_type) keep recording the same row they always have. video_capture is
# the Phase A addition for candidate webcam / proctoring. Both share the
# 'interview' purpose and the same dpdp_consent_ledger table — no schema change
# is needed because consent_type is a text column already covered by the
# partial unique index ix_dpdp_consent_active_unique (user_id, consent_type,
# purpose).
_CONSENT_TYPE = "interview_voice_recording"
_VIDEO_CONSENT_TYPE = "video_capture"
_VALID_CONSENT_TYPES = frozenset({_CONSENT_TYPE, _VIDEO_CONSENT_TYPE})
_VALID_PURPOSES = frozenset({"interview"})

# ---------------------------------------------------------------------------
# Dependency shortcuts
# ---------------------------------------------------------------------------
CurrentUserDep = Annotated[User, Depends(get_current_user)]
# DbSessionDep now lives next to get_db_session in app/database.py (DG-1);
# re-exported here so existing imports of app.routers.consent.DbSessionDep keep
# resolving to the same alias object.


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _hash_value(raw: str) -> str:
    """Return sha256(raw + settings.consent_ip_salt) as a 64-char hex string.

    The salt prevents rainbow-table attacks against hashed IPs.
    Result is safe to store — no raw PII leaks.
    """
    salted = raw + settings.consent_ip_salt
    return hashlib.sha256(salted.encode("utf-8")).hexdigest()


async def _find_active_consent(
    db: AsyncSession,
    user_id: str,
    consent_type: str = _CONSENT_TYPE,
) -> DpdpConsent | None:
    """Return the active (granted, not revoked) consent row for the given type.

    consent_type defaults to the voice type so existing callers are unaffected.
    """
    user_uuid = _uuid_mod.UUID(user_id)
    stmt = select(DpdpConsent).where(
        DpdpConsent.user_id == user_uuid,
        DpdpConsent.consent_type == consent_type,
        DpdpConsent.purpose == "interview",
        DpdpConsent.granted.is_(True),
        DpdpConsent.revoked_at.is_(None),
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


# Sessions that have not reached a terminal state yet. These are the only rows a
# withdrawal can change: a completed or failed session's status records what
# happened to the interview, and rewriting it would falsify that history.
_NON_TERMINAL_SESSION_STATUSES = ("created", "in_progress")


async def _mark_sessions_consent_withdrawn(db: AsyncSession, user_id: str) -> int:
    """Stamp the user's in-flight sessions ``consent_withdrawn`` (DPDP §6(4)).

    Returns the number of session rows updated. Staged on the caller's
    transaction — the caller commits.

    DPDP-6: the status vocabulary has carried ``consent_withdrawn`` since the
    retention job was written, and no code path wrote it. A candidate who
    withdrew mid-interview therefore landed in the audit trail under a generic
    terminal status, indistinguishable from one who closed the tab — which is
    precisely the distinction §6(4) exists to make auditable, and precisely what
    a regulator asks to see. It is also load-bearing downstream: ``retention.py``
    purges this status at the NEXT nightly window instead of waiting out the
    90-day retention window, so the status is what actually causes the withdrawn
    candidate's data to leave the system early.

    Scope note: this covers the sessions row, which data_gateway owns. Tearing
    down a LIVE WebSocket belongs to the interview_core consent watchdog, which
    re-reads consent per turn and closes with ``consent_required``; if that
    watchdog later writes its own terminal status it must preserve this one
    rather than overwrite it.
    """
    result = await db.execute(
        update(InterviewSession)
        .where(
            InterviewSession.user_id == _uuid_mod.UUID(user_id),
            InterviewSession.status.in_(_NON_TERMINAL_SESSION_STATUSES),
        )
        .values(status=CONSENT_WITHDRAWN_STATUS, updated_at=datetime.now(UTC))
    )
    # AsyncSession.execute is typed as returning Result, which declares no
    # rowcount; a DML statement actually returns a CursorResult, which does.
    # Narrowed rather than ignored, so a future change that stops issuing DML
    # here is caught — same treatment as app/retention.py.
    return int(cast("CursorResult[Any]", result).rowcount or 0)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ConsentResponse,
    summary="Record DPDP consent for voice interview recording",
    description=(
        "Idempotent. Returns HTTP 201 on first grant. "
        "Returns HTTP 200 with the existing row if the user has already consented. "
        "Accepts purpose='interview' only."
    ),
)
async def record_consent(
    request: Request,
    body: ConsentRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    response: Response,
) -> ConsentResponse:
    """Record explicit DPDP consent for voice/PII processing.

    PII safety:
    - Client IP and User-Agent are sha256-hashed with a server-side salt
      before storage. Raw values are never written to the DB or logs.
    - Logs emit only: user_id, consent_id, and idempotency status.
    """
    if body.purpose not in _VALID_PURPOSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid purpose '{body.purpose}'. Accepted values: {sorted(_VALID_PURPOSES)}",
        )
    if body.consent_type not in _VALID_CONSENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid consent_type '{body.consent_type}'. "
                f"Accepted values: {sorted(_VALID_CONSENT_TYPES)}"
            ),
        )

    # Idempotency check — avoids duplicate rows and lets frontend call POST safely
    existing = await _find_active_consent(db, current_user.user_id, body.consent_type)
    if existing is not None:
        log.info(
            "consent.record.idempotent",
            user_id=current_user.user_id,
            consent_id=str(existing.id),
        )
        # Override to 200 — idempotent return, not a new resource creation
        response.status_code = status.HTTP_200_OK
        return ConsentResponse(
            consented=True,
            consent_id=str(existing.id),
            granted_at=existing.granted_at.isoformat(),
        )

    now_utc = datetime.now(UTC)

    # Hash PII — never store raw values
    raw_ip = _extract_client_ip(request)
    raw_ua = _extract_user_agent(request)
    ip_hash = _hash_value(raw_ip)
    ua_hash = _hash_value(raw_ua)

    evidence = {
        "version": body.version,
        "ip_hash": ip_hash,
        "user_agent_hash": ua_hash,
        "consented_at_iso": now_utc.isoformat(),
    }

    consent_row = DpdpConsent(
        user_id=_uuid_mod.UUID(current_user.user_id),
        consent_type=body.consent_type,
        granted=True,
        granted_at=now_utc,
        revoked_at=None,
        purpose=body.purpose,
        evidence=evidence,
    )
    db.add(consent_row)
    try:
        await db.commit()
        await db.refresh(consent_row)
    except IntegrityError:
        # S4-009: concurrent POST raced past the explicit pre-check and hit the
        # partial unique index (ix_dpdp_consent_active_unique). Roll back the
        # failed INSERT, re-query for the winning row, and return 200 — same
        # idempotent response as the fast-path above.
        await db.rollback()
        race_winner = await _find_active_consent(db, current_user.user_id, body.consent_type)
        if race_winner is None:
            # Should not happen: the IntegrityError proves an active row exists.
            # If we somehow get here (e.g. revoked between our commit failure and
            # the re-query) treat it as an internal error so the caller retries.
            log.error(
                "consent.record.race_winner_missing",
                user_id=current_user.user_id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Consent insert conflict; please retry.",
            ) from None
        log.info(
            "consent.record.race_caught",
            user_id=current_user.user_id,
            consent_id=str(race_winner.id),
        )
        response.status_code = status.HTTP_200_OK
        return ConsentResponse(
            consented=True,
            consent_id=str(race_winner.id),
            granted_at=race_winner.granted_at.isoformat(),
        )

    log.info(
        "consent.record.created",
        user_id=current_user.user_id,
        consent_id=str(consent_row.id),
        # evidence jsonb intentionally NOT logged (contains derivable PII hashes)
    )

    return ConsentResponse(
        consented=True,
        consent_id=str(consent_row.id),
        granted_at=consent_row.granted_at.isoformat(),
    )


@router.get(
    "/status",
    status_code=status.HTTP_200_OK,
    response_model=ConsentStatus,
    summary="Check whether the current user has active DPDP consent",
    description=(
        "Returns consented=true with the consent_id and granted_at timestamp "
        "if the user has an active interview_voice_recording consent. "
        "Returns consented=false with nulls otherwise."
    ),
)
async def get_consent_status(
    current_user: CurrentUserDep,
    db: DbSessionDep,
    consent_type: str = _CONSENT_TYPE,
) -> ConsentStatus:
    """Return active consent status for the current user.

    consent_type query param defaults to the voice type (backward compatible);
    pass ?consent_type=video_capture to check webcam consent.
    """
    if consent_type not in _VALID_CONSENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid consent_type '{consent_type}'. "
                f"Accepted values: {sorted(_VALID_CONSENT_TYPES)}"
            ),
        )
    existing = await _find_active_consent(db, current_user.user_id, consent_type)

    if existing is None:
        log.info("consent.status.none", user_id=current_user.user_id)
        return ConsentStatus(consented=False, consent_id=None, granted_at=None)

    log.info(
        "consent.status.active",
        user_id=current_user.user_id,
        consent_id=str(existing.id),
    )
    return ConsentStatus(
        consented=True,
        consent_id=str(existing.id),
        granted_at=existing.granted_at.isoformat(),
    )


@router.delete(
    "",
    status_code=status.HTTP_200_OK,
    response_model=ConsentRevocationResponse,
    summary="Revoke ALL DPDP consents (DPDP §11 — right to withdraw)",
    description=(
        "Sets revoked_at = now() on every active consent row for the user, "
        "covering both 'interview_voice_recording' (voice/audio) and "
        "'video_capture' (webcam / proctoring biometric). "
        "Returns 200 with the list of revoked rows. "
        "Returns 404 if no active consent of any type exists. "
        "Idempotent in the sense that a second DELETE returns 404 consistently."
    ),
)
async def revoke_consent(
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> ConsentRevocationResponse:
    """Revoke ALL active DPDP consents for the current user (DPDP Act 2023, §11).

    DPDP §11 grants every data principal the right to withdraw consent at any
    time without restriction. A candidate must be able to retract both their
    voice-recording consent AND their video-capture (webcam / proctoring
    biometric) consent in a single action. This endpoint revokes every active
    consent row — regardless of type — so the candidate's full withdrawal is
    honoured atomically.

    After revocation:
      - interview_core/app/consent_guard.py ``has_active_consent`` returns False
        for both types (its SQL already filters ``revoked_at IS NULL``).
      - Any attempt to open a new interview WebSocket is rejected with 4003
        ``consent_required`` until the user re-grants consent via POST /consent.
      - Every non-terminal session of this user is stamped
        ``status='consent_withdrawn'`` (DPDP-6), which both records the reason in
        the audit trail and makes the session purgeable at the next nightly
        retention window rather than after the full 90 days.

    PII safety:
      - Only user_id and consent_id(s) are logged — no PII.
    """
    now_utc = datetime.now(UTC)
    revoked_items: list[RevokedConsentItem] = []

    for consent_type in _VALID_CONSENT_TYPES:
        row = await _find_active_consent(db, current_user.user_id, consent_type)
        if row is not None:
            row.revoked_at = now_utc
            revoked_items.append(
                RevokedConsentItem(
                    consent_type=consent_type,
                    consent_id=str(row.id),
                    revoked_at=now_utc.isoformat(),
                )
            )

    if not revoked_items:
        log.info(
            "consent.revoke.no_active",
            user_id=current_user.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active consent to revoke",
        )

    # DPDP-6 / §6(4): record WHY these sessions ended, on the same transaction as
    # the revocation itself. Staging it separately would allow a crash between
    # the two to leave a revoked consent whose in-flight sessions still read
    # 'in_progress' — the exact ambiguity this write removes.
    sessions_withdrawn = await _mark_sessions_consent_withdrawn(db, current_user.user_id)

    await db.commit()

    log.info(
        "consent.revoke.done",
        user_id=current_user.user_id,
        consent_types=[item.consent_type for item in revoked_items],
        consent_ids=[item.consent_id for item in revoked_items],
        sessions_withdrawn=sessions_withdrawn,
    )

    return ConsentRevocationResponse(
        revoked=True,
        items=revoked_items,
    )
