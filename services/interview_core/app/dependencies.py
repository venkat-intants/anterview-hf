"""FastAPI dependency providers for interview_core.

interview_core does not manage user state — it only validates JWTs issued by
data_gateway and extracts the subject + roles for downstream use.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, cast

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from shared.auth.jwt import is_token_revoked, verify_access_token

from app.config import settings
from app.redis_client import get_redis

log = structlog.get_logger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing token",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
) -> dict[str, Any]:
    """Extract Bearer token, verify JWT, return decoded payload dict.

    Raises HTTP 401 if the token is absent, malformed, expired, or has been
    revoked by a "log out all devices" (its ``iat`` predates the user's token
    epoch stored in Redis).
    """
    if credentials is None:
        raise _UNAUTHORIZED

    try:
        raw = verify_access_token(
            credentials.credentials,
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
            expected_issuer=settings.jwt_issuer,
            expected_audience=settings.jwt_audience,
        )
        payload: dict[str, Any] = cast(dict[str, Any], raw)
    except JWTError as exc:
        log.warning("auth.jwt_verification_failed", error=str(exc))
        raise _UNAUTHORIZED from exc

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise _UNAUTHORIZED

    # Revocation check: reject tokens issued before a "log out all devices".
    # Shared implementation (shared/auth/jwt.py) — this used to be a local copy,
    # and a sibling copy in feedback_billing drifted into shipping without it.
    # The RETURN TYPE stays dict: get_guest_bound_user reads `session_id` off
    # this payload, and shared.auth.base.User has no such field, so returning a
    # User here would silently disable the guest session binding from 40df357.
    if await is_token_revoked(get_redis, user_id, payload.get("iat")):
        log.info("auth.token_revoked", user_id=user_id)
        raise _UNAUTHORIZED

    return payload


CurrentUserDep = Annotated[dict[str, Any], Depends(get_current_user)]


async def get_non_guest_user(current_user: CurrentUserDep) -> dict[str, Any]:
    """Reject a token whose ONLY role is ``guest_candidate`` (Phase 3, B7).

    A magic-link interview guest is bound to exactly one pre-created session; it
    must never be able to self-mint or list sessions (which would let a leaked
    guest token amplify cost / enumerate other sessions). Any token carrying a
    real role alongside guest_candidate (shouldn't happen) still passes.
    """
    roles = current_user.get("roles") or []
    if "guest_candidate" in roles and not (set(roles) - {"guest_candidate"}):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest interview tokens cannot create or list sessions.",
        )
    return current_user


NonGuestUserDep = Annotated[dict[str, Any], Depends(get_non_guest_user)]


async def get_guest_bound_user(
    session_id: uuid.UUID,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Enforce that a guest token's ``session_id`` claim matches the
    ``{session_id}`` path parameter (security-audit finding, 2026-08).

    A magic-link interview guest is provisioned ONCE per applicant and reused
    across invites (data_gateway ``interview_take.py`` — the same guest
    ``users`` row is redeemed again on a new invite), so one guest identity
    can end up owning SEVERAL sessions. A plain ownership check
    (``session.user_id == sub``) is therefore not sufficient to isolate a
    guest to the one session their link was for: a token minted for session B
    would also pass an ownership check performed against session A, because
    both sessions share the same ``sub``.

    A guest token is minted with an explicit ``session_id`` claim baked in at
    redeem time (see ``_issue_guest_token``); this binds the check to that
    claim instead, at the transport/auth layer, so a guest token can act on
    ONLY the one session it was issued for. Non-guest tokens (candidate/HR/
    admin roles) are unaffected and fall through unchanged.

    This dependency should be used on EVERY route with a ``{session_id}``
    path parameter (rooms, integrity, and any future addition) rather than
    re-implementing the check per router, so new endpoints inherit it instead
    of repeating — or missing — the guard.
    """
    roles = current_user.get("roles") or []
    if "guest_candidate" in roles and current_user.get("session_id") != str(session_id):
        log.info(
            "auth.guest_session_mismatch",
            session_id=str(session_id),
            user_id=current_user.get("sub"),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This guest token is not valid for this session.",
        )
    return current_user


GuestBoundUserDep = Annotated[dict[str, Any], Depends(get_guest_bound_user)]
