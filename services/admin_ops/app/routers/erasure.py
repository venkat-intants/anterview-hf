"""DPDP right-to-erasure endpoint — S5-004.

DPDP Act 2023, §17: every data principal has the right to erasure of their
personal data. This endpoint initiates a soft-delete of the user's account
and sessions, then schedules full erasure 30 days out. Proctoring integrity
events (biometric-derived gaze/face signals) are HARD-deleted immediately —
they are too sensitive to keep through the 30-day grace window. The 90-day
retention cron separately purges integrity_events via ON DELETE CASCADE when it
hard-deletes the parent session.

Two ways in, one pipeline
-------------------------
``POST /users/me/dpdp/delete`` — the data principal, for their OWN data
    Auth:    any valid, non-revoked platform token. The user_id comes from the
             token's ``sub`` claim and from nowhere else — there is no path or
             body parameter that can name a different person.
    Body:    optional {"reason": str}
    Returns: 202 {"request_id", "user_id", "scheduled_completion",
                  "already_pending"}
    Idempotent: a second request while one is pending returns the EXISTING
                request with ``already_pending: true``, not a 409 and not a
                duplicate row.

``POST /users/{user_id}/dpdp/delete`` — an operator, on someone's behalf
    Auth:    admin JWT (AdminDep).
    Returns: 202, or 409 if a pending request already exists.

    Error paths:
      401 — no / invalid / revoked JWT
      403 — valid JWT, non-admin role
      404 — user_id not found in DB
      409 — pending erasure request already exists for this user
      500 — unexpected DB error (generic message; full exc logged, no PII)

Both paths write the SAME rows through ``_record_erasure_request`` and are
picked up by the same executor. That is the point of DPDP-8: ``DATA-FLOW.md``
promised the data principal a self-service route (DPDP §11) while the code had
only the operator one, and a capability that is documented but absent is worse
than an absent one — it gets cited in a bid and then cannot be demonstrated.
Re-implementing the request flow beside the existing one would have been the
other way to fail: two flows drift, and the one that drifts is the compliance
one nobody exercises weekly.

Why 202 + ``already_pending`` instead of the admin path's 409
-------------------------------------------------------------
Deliberate divergence. An admin issuing a duplicate is making an operator
mistake worth surfacing loudly. A data principal clicking "delete my account"
twice, or retrying on a flaky connection, is exercising a statutory right — an
error page there reads as the platform refusing the request. Same rows either
way; only the reporting differs.

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

from app.admin_auth import AccountHolderDep, AdminDep
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


class SelfErasureResponse(ErasureResponse):
    """202 response for the self-service path.

    ``already_pending`` distinguishes "we accepted your request" from "you
    already had one, here it is". Both are 202 on purpose (see module
    docstring); without the flag the caller cannot tell whether the returned
    ``scheduled_completion`` is 30 days from now or 30 days from whenever they
    first asked — and it is the second date that binds.
    """

    already_pending: bool = False


# ---------------------------------------------------------------------------
# Dependency shortcut
# ---------------------------------------------------------------------------

DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


# ---------------------------------------------------------------------------
# Shared write path — used by BOTH endpoints
# ---------------------------------------------------------------------------


async def _record_erasure_request(
    db: AsyncSession,
    *,
    user_id: _uuid_mod.UUID,
    user: User,
    requested_by: _uuid_mod.UUID,
    actor_type: str,
    channel: str,
    reason: str | None,
    now_utc: datetime,
) -> tuple[_uuid_mod.UUID, datetime]:
    """Soft-delete the account and enqueue the erasure. Does NOT commit.

    The caller owns the transaction so that a failure anywhere below leaves the
    account intact rather than half-deleted with no request row to finish the
    job.

    ``requested_by`` is the audit answer to "who asked": the admin's id on the
    operator path, the data principal's own id on the self-service path. The two
    are therefore distinguishable in the DB by ``requested_by = user_id``, with
    ``audit_log.details.channel`` saying it in words as well.

    Returns ``(request_id, scheduled_for)``.
    """
    scheduled_for = now_utc + timedelta(days=_ERASURE_SCHEDULE_DAYS)
    request_id = _uuid_mod.uuid4()

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

    # 3b-iii. Withdraw every live consent (DPDP §6(6) / §11).
    #         The ledger ROWS are retained deliberately — erasure_executor's
    #         EXCLUDED_TABLES explains why §7 needs them — but a retained row
    #         with revoked_at NULL reads as consent that is still in force, and
    #         after an erasure request it is not. revoked_at already exists for
    #         exactly this; nothing new is invented and nothing is deleted.
    consent_result = await db.execute(
        text(
            "UPDATE dpdp_consent_ledger SET revoked_at = :now "
            "WHERE user_id = :user_id AND revoked_at IS NULL"
        ),
        {"user_id": user_id, "now": now_utc},
    )
    _consent_rc = getattr(consent_result, "rowcount", None)
    log.info(
        "erasure.consents_withdrawn",
        user_id=str(user_id),
        count=_consent_rc if isinstance(_consent_rc, int) else None,
    )

    # 3c. Insert erasure_requests row
    db.add(
        ErasureRequest(
            request_id=request_id,
            user_id=user_id,
            requested_by=requested_by,
            reason=reason,
            status="pending",
            scheduled_for=scheduled_for,
            completed_at=None,
            artifacts=None,
            created_at=now_utc,
        )
    )

    # 3d. Insert audit_log row — action only, no PII
    db.add(
        AuditLog(
            actor_id=requested_by,
            actor_type=actor_type,
            action="dpdp_erasure_requested",
            resource_type="user",
            resource_id=user_id,
            details={"request_id": str(request_id), "channel": channel},
            ip_address=None,
            user_agent=None,
            event_ts=now_utc,
        )
    )

    return request_id, scheduled_for


def _subject_uuid(sub: str) -> _uuid_mod.UUID:
    """Parse a token ``sub`` claim into a user UUID, or refuse the request.

    401 rather than 400/500: a structurally valid token whose subject is not a
    user (a service token, say) is a problem with the credential, and the caller
    can do nothing about it by changing their request body.
    """
    try:
        return _uuid_mod.UUID(sub)
    except ValueError as exc:
        log.warning("erasure.sub_not_uuid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token subject is not a user identifier",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def _find_pending_request(
    db: AsyncSession, user_id: _uuid_mod.UUID
) -> ErasureRequest | None:
    """Return this user's in-flight erasure request, if any."""
    existing_result = await db.execute(
        select(ErasureRequest).where(
            ErasureRequest.user_id == user_id,
            ErasureRequest.status == "pending",
        )
    )
    return existing_result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Endpoint 1 — self-service (DPDP §11), the data principal's own data
#
# Registered BEFORE /users/{user_id}/dpdp/delete and that ordering is
# load-bearing: Starlette matches routes in registration order, and while
# ``user_id: UUID`` would reject the literal "me" with a 422 rather than
# mis-route it, a 422 on the statutory self-service path is not a failure mode
# worth having. Keeping the literal first makes the outcome independent of the
# other route's type annotation.
# ---------------------------------------------------------------------------


@router.post(
    "/users/me/dpdp/delete",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SelfErasureResponse,
    summary="Request erasure of your own personal data (DPDP §11)",
    description=(
        "Data-principal-initiated right to erasure. Soft-deletes the caller's "
        "own account and sessions, withdraws their live consents, purges "
        "proctoring events immediately, and schedules the full purge 30 days "
        "out. The account is identified from the access token — this endpoint "
        "cannot be pointed at anyone else. Idempotent: a second call while a "
        "request is pending returns the existing one with already_pending=true."
    ),
)
async def request_own_erasure(
    caller_sub: AccountHolderDep,
    db: DbSessionDep,
    body: ErasureRequestBody | None = None,
) -> SelfErasureResponse:
    """Initiate DPDP §11 right-to-erasure for the CALLER's own data.

    The subject is derived from the verified token and never from the request —
    the tenancy rule this codebase enforces everywhere. There is deliberately no
    user_id parameter to omit the check on: a self-service erasure endpoint that
    accepted one would be an unauthenticated account-deletion primitive for
    anyone holding any valid token.

    AccountHolderDep, NOT AuthenticatedDep. Deriving identity from the signature
    stops the caller naming someone else; it does not stop a NARROW token acting
    for its own subject. The `guest_candidate` credential from an interview
    invite carries a real user UUID and verifies here (one shared jwt_secret
    across all four services), so "any authenticated token" would have let
    anyone holding a forwarded invite link erase that candidate's account and
    the hiring company's ATS record for them. This route is mounted at the app
    root and both Caddyfiles proxy /users/*/dpdp/* here, so it is publicly
    reachable — the constraint has to live in the dependency.
    """
    user_id = _subject_uuid(caller_sub)

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        # Valid token, no such user — the row is gone from under a live session.
        log.info("erasure.self.user_not_found", user_id=str(user_id))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Idempotency: return the in-flight request rather than minting a second.
    # Checked before any write so a retry cannot re-stamp deleted_at and quietly
    # move the erasure deadline further away.
    existing = await _find_pending_request(db, user_id)
    if existing is not None:
        log.info(
            "erasure.self.already_pending",
            user_id=str(user_id),
            request_id=str(existing.request_id),
        )
        return SelfErasureResponse(
            request_id=str(existing.request_id),
            user_id=str(user_id),
            scheduled_completion=existing.scheduled_for.isoformat(),
            already_pending=True,
        )

    now_utc = datetime.now(UTC)
    try:
        request_id, scheduled_for = await _record_erasure_request(
            db,
            user_id=user_id,
            user=user,
            # The data principal is both subject and requester here.
            requested_by=user_id,
            actor_type="user",
            channel="self_service",
            reason=body.reason if body is not None else None,
            now_utc=now_utc,
        )
        await db.commit()
    except IntegrityError as exc:
        # Two clicks racing past the pre-check; the partial unique index
        # uq_erasure_requests_pending is the real guard. Re-read and return the
        # winner — the loser's caller asked for the same thing and got it.
        await db.rollback()
        existing = await _find_pending_request(db, user_id)
        if existing is None:
            log.error("erasure.self.integrity_error_without_pending", user_id=str(user_id))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred while processing the erasure request.",
            ) from exc
        return SelfErasureResponse(
            request_id=str(existing.request_id),
            user_id=str(user_id),
            scheduled_completion=existing.scheduled_for.isoformat(),
            already_pending=True,
        )
    except SQLAlchemyError as exc:
        await db.rollback()
        log.error(
            "erasure.self.db_error",
            user_id=str(user_id),
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the erasure request.",
        ) from exc

    # Kills the caller's own live tokens, including the one that made this call.
    await _revoke_all_tokens(str(user_id))

    log.info(
        "erasure.self.requested",
        user_id=str(user_id),
        request_id=str(request_id),
        scheduled_for=scheduled_for.isoformat(),
    )

    return SelfErasureResponse(
        request_id=str(request_id),
        user_id=str(user_id),
        scheduled_completion=scheduled_for.isoformat(),
        already_pending=False,
    )


# ---------------------------------------------------------------------------
# Endpoint 2 — operator-initiated, on another user's behalf
# ---------------------------------------------------------------------------


@router.post(
    "/users/{user_id}/dpdp/delete",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ErasureResponse,
    summary="Initiate DPDP right-to-erasure for a user (S5-004)",
    description=(
        "Soft-deletes the user account and their interview sessions, purges "
        "proctoring integrity events immediately, withdraws the user's live "
        "consents, creates an erasure_requests record (status='pending'), and "
        "writes an audit_log entry. "
        "Full data purge is scheduled 30 days from the request date. "
        "Idempotency: returns 409 if a pending erasure request already exists "
        "(the self-service path at /users/me/dpdp/delete returns 202 with the "
        "existing request instead — see the module docstring for why)."
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
    existing = await _find_pending_request(db, user_id)
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
    # 3. Execute all writes in a single transaction (shared with the
    #    self-service path — see _record_erasure_request)
    # ------------------------------------------------------------------
    now_utc = datetime.now(UTC)

    try:
        request_id, scheduled_for = await _record_erasure_request(
            db,
            user_id=user_id,
            user=user,
            requested_by=_subject_uuid(admin_sub),
            actor_type="admin",
            channel="admin",
            reason=reason,
            now_utc=now_utc,
        )
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
