"""Strict (fail-closed) session revocation for account TAKEOVER paths.

Why this exists next to ``AuthProvider.logout_all``
---------------------------------------------------
``logout_all`` is best-effort by design: it logs Redis errors and returns, so a
password change can never 500 on a cache hiccup. That is the right trade for
"the user chose to sign out everywhere". It is the WRONG trade where an
unverified account is being taken over from whoever pre-registered the address
(``/apply/activate``, Google SSO): there the old owner's sessions are the very
thing being evicted, and "we tried" leaves a pre-registering attacker signed in
to the victim's account.

``logout_all`` also cannot be wrapped in ``try/except`` to detect failure --
it swallows the error itself. So the takeover paths call this instead. It
writes the per-user auth epoch directly and LETS THE ERROR PROPAGATE; the epoch
is what ``get_current_user`` and ``refresh()`` both check, so once it is written
every access token and refresh token issued before now is dead. The refresh-key
purge afterwards is tidy-up and stays best-effort.

Callers run it BEFORE ``db.commit()`` so a failure rolls the takeover back
(no password or identity change is persisted while the old sessions are alive).
If the commit then fails the old sessions have been revoked for nothing, which
is the safe direction.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import structlog
from shared.auth.jwt import USER_TOKEN_EPOCH_PREFIX

from app.config import settings
from app.redis_client import get_redis

log = structlog.get_logger(__name__)


class SessionRevocationError(RuntimeError):
    """The epoch could not be written; the old sessions are still live."""


async def revoke_all_sessions_strict(user_id: str, *, settle: bool = False) -> None:
    """Invalidate every session issued to *user_id* before now, or raise.

    Raises :class:`SessionRevocationError` if the epoch write fails for any
    reason (Redis down, client not initialised). Never returns having done
    nothing.

    ``settle=True`` is for a caller that mints a NEW session for the same user
    straight afterwards (Google sign-in). The epoch has one-second resolution
    and the revocation checks are inclusive (``iat <= epoch`` in
    ``shared.auth.jwt``, ``created_at <= epoch`` on refresh), so a session
    minted in the same second as the epoch would itself read as revoked and
    the new owner's first request would be a 401. With ``settle`` this returns
    only once the wall clock has left the last second an epoch was written
    in -- a wait of under a second, on a path taken once per taken-over account.
    """
    epoch = int(datetime.now(tz=UTC).timestamp())
    # Must outlive the REFRESH token (see AuthProvider.logout_all): the epoch is
    # the backstop for a refresh key the best-effort index missed.
    ttl = int(getattr(settings, "jwt_refresh_expiry_days", 7)) * 86400 + 60
    try:
        await get_redis().setex(USER_TOKEN_EPOCH_PREFIX + user_id, ttl, epoch)
    except Exception as exc:  # noqa: BLE001 -- converted to a typed, fail-closed error
        log.error("session_revocation.strict_failed", error_type=type(exc).__name__)
        raise SessionRevocationError("could not revoke the previous sessions") from exc

    # Refresh-key purge: best-effort. The epoch above already rejects them.
    try:
        from app.dependencies import get_auth_provider_dep  # noqa: PLC0415

        await (await get_auth_provider_dep()).logout_all(user_id)
    except Exception as exc:  # noqa: BLE001 -- purge is tidy-up; the epoch is the control
        log.warning("session_revocation.purge_failed", error_type=type(exc).__name__)

    if settle:
        # logout_all above re-writes the epoch with its own, later ``now``, so
        # settle against the clock as it is here, not against ``epoch``.
        now = time.time()
        await asyncio.sleep(max(0.0, (int(now) + 1) - now))
