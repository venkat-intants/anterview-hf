"""FastAPI dependency providers for data_gateway."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from shared.auth.base import AuthProvider, User
from shared.auth.jwt import verify_access_token
from shared.auth.local import USER_TOKEN_EPOCH_PREFIX

from app.config import settings
from app.redis_client import get_redis

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Auth provider singleton — set at startup by main.py
# ---------------------------------------------------------------------------
_auth_provider: AuthProvider | None = None

_bearer_scheme = HTTPBearer(auto_error=False)


def set_auth_provider(provider: AuthProvider) -> None:
    """Called once at startup to inject the provider."""
    global _auth_provider
    _auth_provider = provider


async def get_auth_provider_dep() -> AuthProvider:
    """FastAPI async dependency — returns the singleton AuthProvider."""
    if _auth_provider is None:
        raise RuntimeError("AuthProvider not initialised. Check startup hook.")
    return _auth_provider


# ---------------------------------------------------------------------------
# get_current_user dependency
# ---------------------------------------------------------------------------
_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing access token",
    headers={"WWW-Authenticate": "Bearer"},
)


async def _token_epoch(user_id: str) -> int | None:
    """Return the user's revocation epoch (Unix seconds), or None.

    Fails OPEN: any Redis error returns None (no revocation applied) so a cache
    hiccup can never lock every user out. Set by ``AuthProvider.logout_all``.
    """
    try:
        raw = await get_redis().get(USER_TOKEN_EPOCH_PREFIX + user_id)
        return int(raw) if raw is not None else None
    except Exception as exc:  # noqa: BLE001 — fail open on any Redis/parse error
        log.warning("auth.token_epoch.check_skipped", error_type=type(exc).__name__)
        return None


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
) -> User:
    """Extract Bearer token, verify JWT, return User.

    Raises HTTP 401 if token is missing, malformed, expired, or has been revoked
    by a "log out all devices" (its ``iat`` predates the user's token epoch).
    """
    if credentials is None:
        raise _UNAUTHORIZED

    try:
        payload = verify_access_token(
            credentials.credentials,
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
            expected_issuer=settings.jwt_issuer,
            expected_audience=settings.jwt_audience,
        )
    except JWTError as exc:
        log.warning("auth.jwt_verification_failed", error=str(exc))
        raise _UNAUTHORIZED from exc

    user_id: str | None = payload.get("sub")
    roles: list[str] = payload.get("roles", [])

    if not user_id:
        raise _UNAUTHORIZED

    # Revocation check: reject tokens issued before a "log out all devices".
    epoch = await _token_epoch(user_id)
    if epoch is not None:
        iat = payload.get("iat")
        if iat is not None and int(iat) < epoch:
            log.info("auth.token_revoked", user_id=user_id)
            raise _UNAUTHORIZED

    return User(
        user_id=user_id,
        full_name="",  # JWT carries only sub + roles; /auth/me fetches full profile
        email="",
        roles=roles,
    )


# ---------------------------------------------------------------------------
# Role-based access control (HR workflow — Phase 0)
# ---------------------------------------------------------------------------


def require_role(*allowed: str) -> Callable[[User], Awaitable[User]]:
    """Dependency factory: require the caller to hold at least one of *allowed* roles.

    Usage:
        SuperAdminDep = Annotated[User, Depends(require_role("super_admin"))]
        @router.post(..., dependencies=[Depends(require_role("super_admin"))])
    """

    async def _dep(user: Annotated[User, Depends(get_current_user)]) -> User:
        if not set(allowed) & set(user.roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions for this action.",
            )
        return user

    return _dep
