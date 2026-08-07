"""Shared JWT dependencies for admin_ops.

Single authoritative implementation of the token guards, imported by every
router that needs one.  Eliminates the duplicate copies that previously lived
in main.py (verify_admin_role) and erasure.py (_verify_admin_role).

Two dependencies, one decode
----------------------------
``verify_authenticated_user``
    Any valid, non-revoked platform token. Returns the ``sub`` claim.
``verify_admin_role``
    The same, plus ``"admin"`` in the ``roles`` list.

They share :func:`_authenticated_subject` rather than one calling the other,
because the DPDP §11 self-service erasure endpoint (routers/erasure.py) needs
the *identity* with no role requirement at all — a candidate erasing their own
data holds no admin role and never will. Two independent decode paths would be
two places for the revocation check to be forgotten, and the one that gets
forgotten is always the newer one.

Contract
--------
- Returns the ``sub`` claim (user_id string) from the JWT on success.
- Raises HTTP 401 if the Authorization header is absent or the token is
  invalid / expired, or has been revoked by a "log out all devices" (its
  ``iat`` predates the per-user epoch stored in Redis).
- Raises HTTP 403 (``verify_admin_role`` only) if the token is valid but the
  ``roles`` list does not contain ``"admin"``.

The platform issues a ``roles`` LIST claim, not a singular ``role`` string.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from shared.auth.jwt import is_token_revoked, verify_access_token

from app.config import settings
from app.redis_client import get_redis

log = structlog.get_logger(__name__)

_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Authorization header required",
    headers={"WWW-Authenticate": "Bearer"},
)


async def _authenticated_subject(
    credentials: HTTPAuthorizationCredentials | None,
) -> tuple[str, dict[str, Any]]:
    """Decode, validate and revocation-check a bearer token.

    Returns ``(sub, payload)``. Raises 401 for a missing / invalid / expired /
    revoked token. Deliberately says nothing about roles — that is the caller's
    decision, and keeping it here would make a role the price of being
    authenticated.
    """
    if credentials is None:
        raise _UNAUTHORIZED
    try:
        payload: dict[str, Any] = verify_access_token(
            credentials.credentials,
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
            expected_issuer=settings.jwt_issuer,
            expected_audience=settings.jwt_audience,
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    sub = str(payload.get("sub") or "")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing sub claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Revocation check: reject tokens issued before a "log out all devices".
    # Shared implementation — see shared/auth/jwt.py.
    if await is_token_revoked(get_redis, sub, payload.get("iat")):
        log.info("admin_auth.token_revoked", user_id=sub)
        raise _UNAUTHORIZED

    return sub, payload


async def verify_authenticated_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """FastAPI dependency — any valid, non-revoked token. Returns the sub claim.

    The returned value is the ONLY acceptable source of identity for an endpoint
    that acts on "the caller's own" data: it comes from a signature the platform
    issued, whereas a user_id in a path or body comes from the caller. Endpoints
    here must never accept the latter (see routers/erasure.py).
    """
    sub, _payload = await _authenticated_subject(credentials)
    return sub


async def verify_admin_role(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """FastAPI dependency — decodes the JWT and enforces role == 'admin'.

    Returns the user_id (sub claim) on success.
    Raises 401 if no/invalid token or token is revoked; 403 if valid but not admin.
    """
    sub, payload = await _authenticated_subject(credentials)

    roles: list[str] = payload.get("roles") or []
    if "admin" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )

    return sub


# Convenience type aliases — use in endpoint signatures as the annotated dep.
AdminDep = Annotated[str, Depends(verify_admin_role)]
AuthenticatedDep = Annotated[str, Depends(verify_authenticated_user)]
