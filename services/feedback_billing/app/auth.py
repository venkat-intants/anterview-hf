"""One JWT dependency for every feedback_billing router.

Why this module exists: the same ~30-line auth dependency was pasted into
three routers, and the third copy drifted. ``scorecard_list.py`` verified the
signature but skipped the token-revocation epoch check that ``scorecard.py``
and ``score.py`` both perform — so ``GET /api/scorecards`` kept honouring
access tokens that "log out all devices", change-password, reset-password,
HR account deletion and DPDP erasure had all revoked. The epoch is the
platform's only immediate kill switch for an access token that has not yet
expired; a router that skips it is a hole in all five of those flows.

Copying the helper a fourth time is what caused the bug, so it lives here once
and the routers depend on it.

Note on the epoch prefix: it IS imported, from ``shared.auth.jwt`` (see the
re-export below). This paragraph used to say the opposite — that the value was
hand-copied because importing it would drag in bcrypt — which was true of the
*old* home, ``shared.auth.local``, and stopped being true when the constant
moved. Left uncorrected it costs a reader real time: they go hunting for the
other copies to keep in sync, and there are none (code review 2026-08-07,
SEC-6).

``shared.auth.jwt`` is bcrypt-free for exactly this reason — password hashing
lives in ``shared.auth.local``, so a service that only *verifies* tokens can
import the JWT half without pulling bcrypt into its requirements.txt, and
feedback_billing deliberately does not ship bcrypt. Anything that would put a
bcrypt import into ``shared.auth.jwt`` reintroduces the constraint this note
describes.

The constant is a wire format shared with data_gateway: it names the Redis key
``logout_all`` writes and this service reads, so the two must agree or
revocation silently stops working on one side.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from shared.auth.jwt import (
    USER_TOKEN_EPOCH_PREFIX,
    is_token_revoked,
    verify_access_token,
)

from app.config import settings as _app_settings
from app.redis_client import get_redis

log = structlog.get_logger(__name__)

# Re-exported from the canonical definition so the six copies of this literal
# that used to be "kept in sync" by comment are now actually one value. Name
# kept for the existing importers in this service.
TOKEN_EPOCH_PREFIX = USER_TOKEN_EPOCH_PREFIX

bearer_scheme = HTTPBearer(auto_error=False)

UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing access token",
    headers={"WWW-Authenticate": "Bearer"},
)


async def token_epoch_check(user_id: str, iat: Any, *, source: str) -> None:
    """Raise 401 if the token was issued before the user's revocation epoch.

    Fails OPEN by design: a Redis hiccup must not lock every user out of the
    platform. That is a deliberate availability trade-off — the epoch is a
    revocation accelerator, not the primary auth control, and tokens still
    expire on their own in 15 minutes.
    """
    # Comparison and fail-open live in shared/auth/jwt.py. The previous local
    # version wrapped the whole comparison in `try`, which meant it also needed
    # an `except HTTPException: raise` guard — without that, the 401 it had just
    # raised was swallowed by its own bare `except` and the request proceeded.
    # The shared helper returns a bool and raises nothing, so that hazard is
    # gone rather than replicated.
    if await is_token_revoked(get_redis, user_id, iat):
        log.info(f"{source}.auth.token_revoked", user_id=user_id)
        raise UNAUTHORIZED


async def require_jwt(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> dict[str, Any]:
    """Verify a user Bearer JWT and return the decoded payload.

    Raises 401 when the token is absent, malformed, expired, carries no ``sub``,
    carries a ``sub`` that is not a UUID, or predates the user's revocation
    epoch.

    The UUID check matters: ``sub`` is interpolated into queries that compare it
    against a uuid column, and a non-UUID string reaching the driver is a
    500 rather than the 401 it should be.
    """
    if credentials is None:
        raise UNAUTHORIZED

    try:
        payload = verify_access_token(
            credentials.credentials,
            secret=_app_settings.jwt_secret,
            algorithm=_app_settings.jwt_algorithm,
            expected_issuer=_app_settings.jwt_issuer,
            expected_audience=_app_settings.jwt_audience,
        )
    except JWTError as exc:
        log.warning("auth.jwt_failed", error_type=type(exc).__name__)
        raise UNAUTHORIZED from exc

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise UNAUTHORIZED

    try:
        uuid.UUID(str(user_id))
    except (ValueError, AttributeError, TypeError) as exc:
        log.warning("auth.sub_not_uuid")
        raise UNAUTHORIZED from exc

    await token_epoch_check(user_id, payload.get("iat"), source="scorecards")

    result: dict[str, Any] = dict(payload)
    return result
