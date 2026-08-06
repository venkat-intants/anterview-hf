"""FastAPI dependency providers for data_gateway."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from shared.auth.base import AuthProvider, User
from shared.auth.jwt import is_token_revoked, verify_access_token
from sqlalchemy import text as sa_text

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
    # Shared implementation — see shared/auth/jwt.py.
    if await is_token_revoked(get_redis, user_id, payload.get("iat")):
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


# ---------------------------------------------------------------------------
# Bootstrap-password gate
# ---------------------------------------------------------------------------


async def require_password_changed(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Reject a caller who still owes a bootstrap password change.

    ``must_change_password`` is set by admin_hr when it provisions a
    super_admin or hr_manager, and it was enforced NOWHERE on the server — the
    forced reset lived only in the React router (AuthContext / ShellLayout). So
    a freshly provisioned account could drive the entire API on its bootstrap
    password simply by talking to it directly instead of through the SPA:
    POST /admin/hr-managers, GET /hr/applicants, POST /hr/interviews. For a
    super_admin that is the whole tenant.

    Applied to the privileged routers rather than globally: it costs one
    indexed read per request, and a candidate holding this flag can do nothing
    interesting anyway. /auth/* is deliberately NOT gated — the caller has to
    be able to reach /auth/me, /auth/change-password and /auth/logout* in order
    to clear the flag.

    Fails OPEN on a database error. A gate that 500s every privileged request
    during a transient blip is worse than the narrow window it protects, and
    the flag is a bootstrap-hygiene control rather than the tenancy boundary —
    that is enforced separately on every one of these routes.
    """
    try:
        must_change = await _must_change_password(user.user_id)
    except Exception as exc:  # noqa: BLE001 — never 500 a request on this check
        log.warning(
            "auth.must_change_password.check_skipped",
            error_type=type(exc).__name__,
        )
        return user

    if must_change:
        log.info("auth.bootstrap_password_gate", user_id=user.user_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must set a new password before using this account.",
        )
    return user


def require_role_password_ok(*allowed: str) -> Callable[[User, User], Awaitable[User]]:
    """``require_role`` plus the bootstrap-password gate, as one dependency.

    Exists so the two cannot drift apart. The gate was previously defined and
    wired to NOTHING — a function with a docstring claiming it was applied to
    the privileged routers, which is worse than no gate at all, because the
    next reader stops looking. Composing it into the role check means a new
    privileged route inherits both by using the same chokepoint.
    """

    role_dep = require_role(*allowed)

    # Default-value Depends, NOT Annotated. This module has
    # `from __future__ import annotations`, so every annotation is a string —
    # and a string containing `Depends(require_role(*allowed))` refers to a
    # closure variable FastAPI cannot resolve at runtime, which fails at import
    # with a PydanticUserError rather than at call time.
    # B008 flags a function call in an argument default — it exists to catch
    # mutable/expensive defaults evaluated once at definition time. FastAPI's
    # Depends() is the documented exception: it is a marker object the framework
    # resolves per request, and here it is the only form that works.
    async def _dep(
        user: User = Depends(role_dep),  # noqa: B008
        _pw_ok: User = Depends(require_password_changed),  # noqa: B008
    ) -> User:
        return user

    return _dep


async def _must_change_password(user_id: str) -> bool:
    """One indexed read of the caller's bootstrap-password flag."""
    from app.database import get_session_factory  # noqa: PLC0415 — avoid cycle

    factory = get_session_factory()
    async with factory() as session:
        raw = await session.scalar(
            sa_text(
                "SELECT must_change_password FROM users "
                "WHERE id = CAST(:uid AS uuid) AND deleted_at IS NULL"
            ),
            {"uid": user_id},
        )
    return bool(raw)
