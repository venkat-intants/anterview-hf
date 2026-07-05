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
                               both interview_voice_recording and video_capture)
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
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from shared.auth.base import User
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.dependencies import get_current_user
from app.models import DpdpConsent
from app.schemas.consent import (
    ConsentRequest,
    ConsentResponse,
    ConsentRevocationResponse,
    ConsentStatus,
    RevokedConsentItem,
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
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


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


def _extract_client_ip(request: Request) -> str:
    """Extract the real client IP using the configured trusted-proxy count.

    S4-012 — trusted proxy count gate:

    The naive approach of reading the leftmost X-Forwarded-For value is
    exploitable: any HTTP client can inject ``X-Forwarded-For: 1.2.3.4`` and
    cause the server to record sha256("1.2.3.4"+salt) instead of the real IP.
    This breaks audit integrity in the DPDP consent ledger.

    Algorithm:
      - If ``settings.trusted_proxy_count == 0``: ignore X-Forwarded-For
        entirely and return ``request.client.host`` (or "unknown").  This is
        the correct setting for local dev and any deployment with no reverse
        proxy in front of the app.
      - If ``settings.trusted_proxy_count > 0``: parse X-Forwarded-For as a
        left-to-right list of IPs (original-client first). Each trusted proxy
        appends the address it received the request FROM, so with N trusted
        proxies the real client is the entry at index
        ``len(hops) - trusted_proxy_count`` — i.e. the Nth entry counted from
        the right (the standard Werkzeug-ProxyFix model). Counting from the
        trusted right end means any number of attacker-*prepended* entries are
        ignored. If the header is absent or has fewer entries than
        ``trusted_proxy_count``, fall back to ``request.client.host``.

    Our production topology is a single Caddy reverse proxy (Convention: it
    sets X-Forwarded-For to the client IP it observed and STRIPS any client-
    supplied XFF, because the public client is not in Caddy's trusted_proxies).
    So Caddy emits a single-entry XFF and ``trusted_proxy_count=1`` is correct.

    Example with trusted_proxy_count=1 (single Caddy hop):
      X-Forwarded-For: "1.2.3.4"
        hops = ["1.2.3.4"]
        real_index = 1 - 1 = 0  →  "1.2.3.4"  ✓

    Example with an attacker prepend that somehow survives, tpc=1:
      X-Forwarded-For: "attacker, real-client"
        hops = ["attacker", "real-client"]
        real_index = 2 - 1 = 1  →  "real-client"  ✓  (prepend ignored)

    Example with a future CDN in front of Caddy, trusted_proxy_count=2:
      X-Forwarded-For: "1.2.3.4, <cdn-edge-ip>"
        real_index = 2 - 2 = 0  →  "1.2.3.4"  ✓

    Raw IP values are NEVER logged — only the sha256 hash is stored.
    """
    direct_host: str = request.client.host if request.client is not None else "unknown"

    if settings.trusted_proxy_count == 0:
        # No trusted proxies configured — ignore X-Forwarded-For entirely.
        # A client-supplied header could spoof any IP; using the direct
        # socket address is the only safe option in this topology.
        return direct_host

    # trusted_proxy_count > 0: infrastructure proxies sit between the client
    # and the app.  Resolve the real client IP from the XFF chain.
    xff: str = request.headers.get("X-Forwarded-For", "")
    if not xff:
        # No header present — the request arrived without passing through the
        # expected proxy chain.  Fall back to the direct connection address.
        return direct_host

    hops: list[str] = [h.strip() for h in xff.split(",") if h.strip()]
    real_index: int = len(hops) - settings.trusted_proxy_count

    if real_index < 0:
        # Fewer hops than expected (e.g. proxy chain not fully established in
        # staging).  Fall back to the direct connection address rather than
        # picking a potentially attacker-controlled entry.
        return direct_host

    return hops[real_index]


def _extract_user_agent(request: Request) -> str:
    """Extract User-Agent header, defaulting to empty string."""
    return request.headers.get("User-Agent", "")


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

    await db.commit()

    log.info(
        "consent.revoke.done",
        user_id=current_user.user_id,
        consent_types=[item.consent_type for item in revoked_items],
        consent_ids=[item.consent_id for item in revoked_items],
    )

    return ConsentRevocationResponse(
        revoked=True,
        items=revoked_items,
    )
