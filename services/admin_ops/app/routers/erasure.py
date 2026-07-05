"""DPDP right-to-erasure endpoint — S5-004.

DPDP Act 2023, §17: every data principal has the right to erasure of their
personal data. This endpoint initiates a soft-delete of the user's account
and sessions, then schedules full erasure 30 days out. Proctoring integrity
events (biometric-derived gaze/face signals) are HARD-deleted immediately —
they are too sensitive to keep through the 30-day grace window. The 90-day
retention cron separately purges integrity_events via ON DELETE CASCADE when it
hard-deletes the parent session.

Contract:
  POST /admin/users/{user_id}/dpdp/delete
    Auth:    admin JWT required (verify_admin_role dependency from main.py)
    Body:    optional {"reason": str}
    Returns: 202 {"request_id": str, "user_id": str, "scheduled_completion": str}

  Error paths:
    401 — no / invalid JWT           (from verify_admin_role)
    403 — valid JWT, non-admin role   (from verify_admin_role)
    404 — user_id not found in DB
    409 — pending erasure request already exists for this user
    500 — unexpected DB error (generic message; full exc logged, no PII)

PII safety:
  - user email / name / phone are NEVER logged.
  - Only user_id and request_id appear in log events.

security-auditor sign-off required before this endpoint goes to production.
"""

from __future__ import annotations

import uuid as _uuid_mod
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_auth import AdminDep
from app.database import get_db_session
from app.models import AuditLog, ErasureRequest, Session, User
from app.redis_client import get_redis

log = structlog.get_logger(__name__)

router = APIRouter(tags=["dpdp-erasure"])

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

_ERASURE_SCHEDULE_DAYS = 30

# Redis key prefixes — kept in sync with shared.auth.local (do NOT change):
#   auth_epoch:<uid>     per-user token-revocation epoch (kills access tokens)
#   user_sessions:<uid>  set of the user's active refresh-token keys
_TOKEN_EPOCH_PREFIX = "auth_epoch:"
_SESSIONS_PREFIX = "user_sessions:"


async def _revoke_all_tokens(user_id: str) -> None:
    """Revoke every live session for *user_id* — best-effort, never raises.

    An erasure request soft-deletes the account in Postgres, but the user's
    already-issued JWTs live in Redis / the client. Without this, a suspended /
    erasure-requested user keeps full access until their access token expires
    (~15 min) and could rotate the refresh token for its whole TTL. This mirrors
    ``LocalAuthProvider.logout_all``:
      1. bump ``auth_epoch:<uid>`` → any access token with an older ``iat`` is
         rejected by every service's auth dependency;
      2. delete the refresh-token keys + the session index so refresh is dead now
         (``refresh()`` also re-checks ``deleted_at`` as a second guard).
    Redis errors are logged and swallowed so revocation can never fail the
    erasure request itself (the DB soft-delete is already committed).
    """
    try:
        redis = get_redis()
        now_epoch = int(datetime.now(UTC).timestamp())
        # TTL spans the erasure grace window; the row is hard-deleted by then.
        await redis.setex(
            _TOKEN_EPOCH_PREFIX + user_id,
            _ERASURE_SCHEDULE_DAYS * 86400,
            now_epoch,
        )
        sess_key = _SESSIONS_PREFIX + user_id
        members = await redis.smembers(sess_key)
        keys = [m.decode() if isinstance(m, bytes | bytearray) else str(m) for m in members]
        if keys:
            await redis.delete(*keys)
        await redis.delete(sess_key)
        log.info("erasure.tokens_revoked", user_id=user_id, refresh_keys=len(keys))
    except Exception as exc:  # noqa: BLE001 — token revocation is best-effort
        log.warning(
            "erasure.token_revoke_failed",
            user_id=user_id,
            exc_type=type(exc).__name__,
        )


class ErasureRequestBody(BaseModel):
    """Optional request body for the DPDP erasure endpoint."""

    reason: str | None = None


class ErasureResponse(BaseModel):
    """202 Accepted response for a DPDP erasure request."""

    request_id: str
    user_id: str
    scheduled_completion: str


# ---------------------------------------------------------------------------
# Dependency shortcut
# ---------------------------------------------------------------------------

DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/users/{user_id}/dpdp/delete",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ErasureResponse,
    summary="Initiate DPDP right-to-erasure for a user (S5-004)",
    description=(
        "Soft-deletes the user account and their interview sessions, "
        "creates an erasure_requests record (status='pending'), "
        "and writes an audit_log entry. "
        "Full data purge is scheduled 30 days from the request date. "
        "Idempotency: returns 409 if a pending erasure request already exists."
    ),
)
async def request_erasure(
    user_id: _uuid_mod.UUID,
    admin_sub: AdminDep,
    db: DbSessionDep,
    body: ErasureRequestBody | None = None,
) -> ErasureResponse:
    """Initiate DPDP §17 right-to-erasure for *user_id*.

    All DB writes happen inside a single transaction; any failure rolls back
    the entire operation atomically.

    PII note: email/name/phone are NOT logged at any point in this function.
    """
    reason: str | None = body.reason if body is not None else None

    # ------------------------------------------------------------------
    # 1. Check user exists
    # ------------------------------------------------------------------
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        log.info("erasure.user_not_found", user_id=str(user_id))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )

    # ------------------------------------------------------------------
    # 2. Check for duplicate pending erasure request
    # ------------------------------------------------------------------
    existing_result = await db.execute(
        select(ErasureRequest).where(
            ErasureRequest.user_id == user_id,
            ErasureRequest.status == "pending",
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        log.info(
            "erasure.duplicate_request",
            user_id=str(user_id),
            existing_request_id=str(existing.request_id),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A pending erasure request already exists for user {user_id}",
        )

    # ------------------------------------------------------------------
    # 3. Execute all writes in a single transaction
    # ------------------------------------------------------------------
    now_utc = datetime.now(UTC)
    scheduled_for = now_utc + timedelta(days=_ERASURE_SCHEDULE_DAYS)
    request_id = _uuid_mod.uuid4()

    try:
        # 3a. Soft-delete the user account
        user.deleted_at = now_utc

        # 3b. Soft-delete all sessions for this user
        #     Session.deleted_at was added by migration 20260529_0001.
        #     Use UPDATE ... WHERE to avoid loading all session rows.
        sessions_result = await db.execute(
            select(Session).where(
                Session.user_id == user_id,
                Session.deleted_at.is_(None),
            )
        )
        sessions = sessions_result.scalars().all()
        if sessions:
            await db.execute(
                update(Session)
                .where(
                    Session.user_id == user_id,
                    Session.deleted_at.is_(None),
                )
                .values(deleted_at=now_utc)
            )
            log.info(
                "erasure.sessions_soft_deleted",
                user_id=str(user_id),
                count=len(sessions),
            )
        else:
            log.info("erasure.no_sessions_to_delete", user_id=str(user_id))

        # 3b-ii. HARD-delete proctoring integrity events for this user's sessions.
        #        These are biometric-DERIVED (gaze/face) signals — the most
        #        sensitive proctoring data — so they are purged IMMEDIATELY on an
        #        erasure request rather than waiting out the 30-day grace that
        #        applies to less-sensitive soft-deleted session/turn data.
        #        (Sessions are only soft-deleted here, so the FK cascade does not
        #        fire yet — hence the explicit DELETE.)
        purge_result = await db.execute(
            text(
                "DELETE FROM integrity_events "
                "WHERE session_id IN (SELECT id FROM sessions WHERE user_id = :user_id)"
            ),
            {"user_id": user_id},
        )
        _purged_rc = getattr(purge_result, "rowcount", None)
        log.info(
            "erasure.integrity_events_purged",
            user_id=str(user_id),
            count=_purged_rc if isinstance(_purged_rc, int) else None,
        )

        # 3c. Insert erasure_requests row
        erasure_row = ErasureRequest(
            request_id=request_id,
            user_id=user_id,
            requested_by=_uuid_mod.UUID(admin_sub) if admin_sub else _uuid_mod.uuid4(),
            reason=reason,
            status="pending",
            scheduled_for=scheduled_for,
            completed_at=None,
            artifacts=None,
            created_at=now_utc,
        )
        db.add(erasure_row)

        # 3d. Insert audit_log row — action only, no PII
        audit_row = AuditLog(
            actor_id=_uuid_mod.UUID(admin_sub) if admin_sub else None,
            actor_type="admin",
            action="dpdp_erasure_requested",
            resource_type="user",
            resource_id=user_id,
            details={"request_id": str(request_id)},
            ip_address=None,
            user_agent=None,
            event_ts=now_utc,
        )
        db.add(audit_row)

        await db.commit()

    except IntegrityError as exc:
        # Partial unique index uq_erasure_requests_pending fires when two
        # concurrent requests race past the explicit pre-check — DB-level guard.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A pending erasure request already exists for user {user_id}",
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        log.error(
            "erasure.db_error",
            user_id=str(user_id),
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the erasure request.",
        ) from exc

    # Revoke live JWTs now that the soft-delete is committed (best-effort).
    # Without this the user keeps access until their access token expires.
    await _revoke_all_tokens(str(user_id))

    log.info(
        "erasure.requested",
        user_id=str(user_id),
        request_id=str(request_id),
        scheduled_for=scheduled_for.isoformat(),
    )

    # ------------------------------------------------------------------
    # 4. Return 202 Accepted
    # ------------------------------------------------------------------
    return ErasureResponse(
        request_id=str(request_id),
        user_id=str(user_id),
        scheduled_completion=scheduled_for.isoformat(),
    )
