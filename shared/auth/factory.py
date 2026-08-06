"""AuthProvider factory — returns the right implementation for AUTH_PROVIDER env var."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth.base import AuthProvider


def get_auth_provider(
    settings: Any,
    db_session_factory: Callable[[], AsyncGenerator[AsyncSession, None]],
    redis_client: Redis,
) -> AuthProvider:
    """Factory: instantiate the AuthProvider requested by settings.auth_provider."""
    provider = settings.auth_provider.lower()

    if provider == "local":
        # LOAD-BEARING lazy import — do not hoist to module scope.
        #
        # shared/auth/local.py imports bcrypt at module level and calls
        # bcrypt.hashpw at import time (the timing-oracle dummy hash). Only
        # data_gateway ships bcrypt; interview_core, feedback_billing and
        # admin_ops do not. Hoisting this import makes `import shared.auth`
        # raise ModuleNotFoundError in three of the four service images, at
        # container start, in production.
        #
        # Those three services never instantiate LocalAuthProvider — they only
        # verify tokens — so paying the import cost lazily is exactly right.
        from shared.auth.local import LocalAuthProvider  # noqa: PLC0415

        return LocalAuthProvider(
            db_session_factory=db_session_factory,
            redis_client=redis_client,
            settings=settings,
        )

    if provider in {"google", "keycloak", "naipunyam"}:
        raise NotImplementedError(
            f"AUTH_PROVIDER={provider!r} is not yet implemented. "
            "Set AUTH_PROVIDER=local for Sprint 1."
        )

    raise ValueError(f"Unknown AUTH_PROVIDER: {provider!r}")
