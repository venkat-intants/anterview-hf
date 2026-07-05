"""LocalAuthProvider — email + bcrypt + JWT + Redis refresh tokens.

bcrypt is CPU-bound (sync). All bcrypt calls are wrapped in asyncio.to_thread()
to avoid blocking the event loop.

## logout_all TOCTOU fix (2026-07-01)

The previous implementation set the access-token epoch AFTER purging refresh
tokens.  A concurrent POST /auth/refresh that ran between the session-set purge
and the epoch write could mint a brand-new access+refresh pair whose iat was
older than the (not-yet-written) epoch, allowing the tokens to survive revocation.

Fix — two-part atomic defence:

1. ``logout_all`` sets the epoch BEFORE touching the session set, so any
   concurrent ``refresh()`` that executes after the epoch write will see the
   epoch and can compare it against the session's creation timestamp.

2. Each refresh-token Redis value now stores ``<user_id>:<created_at_unix>``
   instead of plain ``<user_id>``.  ``refresh()`` parses ``created_at`` and
   checks: if an epoch exists AND ``created_at <= epoch``, the session
   predates the revocation and the request is rejected *before* new tokens
   are issued.  This closes the window even when logout_all completes between
   the Redis GET and the Redis DELETE in refresh().

Redis error handling policy (unchanged):
- Epoch READ errors in refresh() → fail-open (token allowed through).
  A Redis outage must not lock users out.
- Epoch WRITE errors in logout_all → logged as WARNING (best-effort, as before).

## Single shared mint path (AUTH-01 fix, 2026-07-01)

``mint_refresh_session`` is a module-level async helper that encapsulates ALL
refresh-token minting logic: generate raw token, hash it, write
``<user_id>:<created_at_unix>`` to Redis, add the key to the session index.
Both ``LocalAuthProvider._issue_tokens`` and the SSO routers (Google,
Naipunyam) call this single function so that every token — regardless of how
the user authenticated — is stored in the revocable tracked format and is
covered by ``logout_all`` and the admin delete/password-reset revocation paths.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import Any

import bcrypt
import structlog
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth.base import AuthProvider, AuthTokens, User
from shared.auth.jwt import (
    ACCESS_TOKEN_TTL_SECONDS,
    generate_refresh_token,
    hash_refresh_token,
    issue_access_token,
)

log = structlog.get_logger(__name__)

# Redis key prefix for refresh tokens
_RT_PREFIX = "refresh:"

# Reverse index: SET of a user's live refresh-token keys. Lets "log out all
# devices" (and DPDP §9 right-to-erasure) purge every session for a user without
# an O(N) Redis SCAN over the whole keyspace.
_SESSIONS_PREFIX = "user_sessions:"

# Per-user access-token "epoch" (a Unix timestamp). Any access token whose ``iat``
# predates this value is treated as revoked by get_current_user. Set by
# ``logout_all`` (and password reset) to invalidate every outstanding access token
# for the user *immediately*, rather than waiting out the 15-minute TTL.
# Imported by data_gateway's get_current_user — keep the name stable.
USER_TOKEN_EPOCH_PREFIX = "auth_epoch:"

# Separator used inside the Redis refresh-token value: "<user_id>:<created_at_unix>"
_RT_VALUE_SEP = ":"

# ---------------------------------------------------------------------------
# Timing-oracle mitigation (Security finding: HIGH — user enumeration)
#
# When a login attempt uses an unknown email we must still spend ~250ms doing
# a bcrypt comparison so that an attacker cannot distinguish "email not found"
# from "wrong password" by measuring response time.  We pre-compute a dummy
# hash once at import time (gensalt is expensive; hashpw is the cheap half),
# then call checkpw against it on the unknown-email path via asyncio.to_thread.
# ---------------------------------------------------------------------------
_DUMMY_BCRYPT_HASH: bytes = bcrypt.hashpw(
    b"dummy_password_for_timing_oracle_mitigation", bcrypt.gensalt(12)
)


def _hash_email(email: str) -> str:
    """Return first 16 hex chars of SHA-256(email) for safe log correlation.

    Never log raw email addresses — DPDP §8 treats email as PII.
    """
    return hashlib.sha256(email.encode()).hexdigest()[:16]


async def mint_refresh_session(
    redis: Redis,
    user_id: str,
    ttl_seconds: int,
) -> str:
    """Generate a refresh token, persist it in the tracked format, and index it.

    This is the **single canonical path** for creating any refresh token in the
    Intants platform.  Both ``LocalAuthProvider._issue_tokens`` and the SSO
    routers (Google, Naipunyam) call this function so the revocation guarantees
    provided by ``logout_all`` apply uniformly to *every* session, regardless
    of how the user signed in.

    Redis value written:
        ``refresh:{sha256(raw_token)}``  →  ``<user_id>:<created_at_unix>``

    Session index updated (best-effort):
        ``user_sessions:<user_id>``  ←  SADD the refresh key

    Returns the raw (unhashed) token suitable for delivery to the client.
    The caller must never store the raw token server-side — only the hash lives
    in Redis.
    """
    raw_refresh = generate_refresh_token()
    refresh_key = _RT_PREFIX + hash_refresh_token(raw_refresh)
    created_at = int(datetime.now(tz=UTC).timestamp())
    redis_value = f"{user_id}{_RT_VALUE_SEP}{created_at}"
    await redis.setex(refresh_key, ttl_seconds, redis_value)

    # Best-effort session index — a failure here must never block the login.
    try:
        sess_key = _SESSIONS_PREFIX + user_id
        await redis.sadd(sess_key, refresh_key)
        await redis.expire(sess_key, ttl_seconds)
    except Exception:  # noqa: BLE001 — index is an optimisation, never fatal
        log.warning("auth.session_index.track_failed", user_id=user_id)

    return raw_refresh


class LocalAuthProvider(AuthProvider):
    """Concrete AuthProvider that stores credentials in Postgres and tokens in Redis."""

    def __init__(
        self,
        db_session_factory: Callable[[], AsyncGenerator[AsyncSession, None]],
        redis_client: Redis,
        settings: Any,
    ) -> None:
        self._db_factory = db_session_factory
        self._redis = redis_client
        self._settings = settings

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _issue_tokens(
        self,
        user_id: str,
        roles: list[str],
    ) -> AuthTokens:
        """Issue access + refresh token pair, store refresh token in Redis.

        Delegates refresh-token minting to the module-level ``mint_refresh_session``
        so the tracked ``<user_id>:<created_at_unix>`` format and session-index
        update are guaranteed for every token, regardless of code path.
        """
        access = issue_access_token(
            user_id=user_id,
            roles=roles,
            secret=self._settings.jwt_secret,
            algorithm=self._settings.jwt_algorithm,
            issuer=getattr(self._settings, "jwt_issuer", "intants-data-gateway"),
            audience=getattr(self._settings, "jwt_audience", "intants-services"),
        )
        ttl_seconds = self._settings.jwt_refresh_expiry_days * 86400
        raw_refresh = await mint_refresh_session(self._redis, user_id, ttl_seconds)

        log.debug("auth.tokens_issued", user_id=user_id, roles=roles)
        return AuthTokens(
            access_token=access,
            refresh_token=raw_refresh,
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            user_id=user_id,
            roles=roles,
        )

    async def _track_session(self, user_id: str, refresh_key: str, ttl_seconds: int) -> None:
        """Add *refresh_key* to the user's session index (best-effort).

        Best-effort by design: a failure here must never block login. The worst
        case is that ``logout_all`` misses this one refresh key — which still
        expires on its own TTL, and the access-token epoch bump still logs the
        device out within the 15-minute access-token window.
        """
        try:
            sess_key = _SESSIONS_PREFIX + user_id
            await self._redis.sadd(sess_key, refresh_key)
            # Keep the index at least as long as the longest-lived member.
            await self._redis.expire(sess_key, ttl_seconds)
        except Exception:  # noqa: BLE001 — index is an optimisation, never fatal
            log.warning("auth.session_index.track_failed", user_id=user_id)

    async def _untrack_session(self, user_id: str, refresh_key: str) -> None:
        """Remove *refresh_key* from the user's session index (best-effort)."""
        try:
            await self._redis.srem(_SESSIONS_PREFIX + user_id, refresh_key)
        except Exception:  # noqa: BLE001 — stale members are harmless (they expire)
            log.warning("auth.session_index.untrack_failed", user_id=user_id)

    @staticmethod
    def _parse_rt_value(raw: str) -> tuple[str, int | None]:
        """Parse a refresh-token Redis value into ``(user_id, created_at_unix)``.

        Supports both the legacy format (plain ``user_id`` string — no separator)
        and the current format (``user_id:created_at_unix``).  Legacy tokens that
        lack a creation timestamp return ``None`` for ``created_at``, which causes
        the epoch check to be skipped (fail-open for rollout compatibility).
        """
        sep_index = raw.find(_RT_VALUE_SEP)
        if sep_index == -1:
            # Legacy format — no creation timestamp.
            return raw, None
        user_id_part = raw[:sep_index]
        ts_part = raw[sep_index + len(_RT_VALUE_SEP):]
        try:
            created_at = int(ts_part)
        except ValueError:
            # Malformed — treat as legacy.
            return user_id_part, None
        return user_id_part, created_at

    async def _is_session_revoked_by_epoch(
        self, user_id: str, created_at: int | None
    ) -> bool:
        """Return True if *created_at* predates the user's revocation epoch.

        Fail-open on Redis errors: a Redis outage must not lock valid users out.
        When ``created_at`` is ``None`` (legacy token without timestamp) the check
        is skipped and returns False — the old access-token epoch check at
        get_current_user time still applies.
        """
        if created_at is None:
            return False
        try:
            epoch_raw: str | None = await self._redis.get(
                USER_TOKEN_EPOCH_PREFIX + user_id
            )
        except Exception:  # noqa: BLE001 — fail-open: Redis errors must not block reads
            log.warning("auth.refresh.epoch_read_failed", user_id=user_id)
            return False
        if epoch_raw is None:
            return False
        try:
            epoch = int(epoch_raw)
        except ValueError:
            return False
        # Revoked if the session was created at or before the logout_all epoch.
        return created_at <= epoch

    async def _get_roles_for_user(self, session: AsyncSession, user_id: str) -> list[str]:
        result = await session.execute(
            text(
                "SELECT r.name FROM roles r "
                "JOIN user_roles ur ON r.id = ur.role_id "
                "WHERE ur.user_id = :uid"
            ),
            {"uid": user_id},
        )
        return [str(row[0]) for row in result.fetchall()]

    async def _hash_password(self, password: str) -> str:
        rounds: int = self._settings.password_hash_rounds
        return await asyncio.to_thread(
            lambda: bcrypt.hashpw(
                password.encode(), bcrypt.gensalt(rounds=rounds)
            ).decode()
        )

    async def _verify_password(self, plain: str, hashed: str) -> bool:
        result: bool = await asyncio.to_thread(
            lambda: bcrypt.checkpw(plain.encode(), hashed.encode())
        )
        return result

    # ------------------------------------------------------------------
    # AuthProvider implementation
    # ------------------------------------------------------------------

    async def register(self, email: str, password: str, full_name: str) -> AuthTokens:
        """Create new user, assign 'candidate' role, return tokens."""
        password_hash = await self._hash_password(password)
        user_id = str(uuid.uuid4())

        async for session in self._db_factory():
            try:
                await session.execute(
                    text(
                        "INSERT INTO users (id, email, password_hash, full_name) "
                        "VALUES (:id, :email, :pw, :fn)"
                    ),
                    {
                        "id": user_id,
                        "email": email,
                        "pw": password_hash,
                        "fn": full_name,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO user_roles (user_id, role_id) "
                        "VALUES (:uid, (SELECT id FROM roles WHERE name = 'candidate'))"
                    ),
                    {"uid": user_id},
                )
                await session.commit()
                # Log email_hash only — never raw PII (DPDP §8)
                log.info("auth.register", user_id=user_id, email_hash=_hash_email(email))
                return await self._issue_tokens(user_id, ["candidate"])
            except IntegrityError as exc:
                await session.rollback()
                log.warning(
                    "auth.register.duplicate_email", email_hash=_hash_email(email)
                )
                raise ValueError("email already exists") from exc
        # Should never reach here — db_factory always yields exactly one session
        raise RuntimeError("db_factory yielded no session")  # pragma: no cover

    async def authenticate(self, email: str, password: str) -> AuthTokens:
        """Verify email/password, return tokens."""
        row: Any = None
        async for session in self._db_factory():
            result = await session.execute(
                text(
                    "SELECT id, password_hash FROM users "
                    "WHERE email = :email AND deleted_at IS NULL AND is_active = true"
                ),
                {"email": email},
            )
            row = result.fetchone()

        if row is None:
            # Timing-oracle mitigation: run a dummy bcrypt comparison so that
            # unknown-email responses take the same ~250 ms as known-email
            # wrong-password responses.  Without this, response-time difference
            # leaks whether an email is registered (user-enumeration attack).
            await asyncio.to_thread(
                lambda: bcrypt.checkpw(b"dummy", _DUMMY_BCRYPT_HASH)
            )
            log.warning("auth.login.not_found", email_hash=_hash_email(email))
            raise ValueError("invalid credentials")

        user_id_str = str(row[0])
        stored_hash: str | None = row[1]

        if stored_hash is None or not await self._verify_password(password, stored_hash):
            log.warning("auth.login.bad_password", user_id=user_id_str)
            raise ValueError("invalid credentials")

        roles: list[str] = []
        async for session in self._db_factory():
            roles = await self._get_roles_for_user(session, user_id_str)

        log.info("auth.login", user_id=user_id_str)
        return await self._issue_tokens(user_id_str, roles)

    async def refresh(self, refresh_token: str) -> AuthTokens:
        """Validate refresh token, rotate it, return new token pair.

        Race-safety against logout_all (TOCTOU fix):
        After reading the stored value, we check the per-user epoch BEFORE
        issuing new tokens.  Because logout_all now writes the epoch BEFORE
        purging sessions, any token that existed prior to the logout_all call
        will have a ``created_at`` timestamp that is <= the epoch, and is
        therefore rejected here regardless of whether logout_all has finished
        deleting it from Redis yet.
        """
        key = _RT_PREFIX + hash_refresh_token(refresh_token)
        raw_value: str | None = await self._redis.get(key)

        if raw_value is None:
            log.warning("auth.refresh.invalid_token")
            raise ValueError("invalid or expired refresh token")

        user_id, created_at = self._parse_rt_value(str(raw_value))

        # Epoch check: reject sessions that predate a logout_all call.
        # This is the primary guard against the TOCTOU race: even if logout_all
        # hasn't deleted this specific key yet (it set the epoch first), we refuse
        # to mint new tokens for a pre-revocation session.
        if await self._is_session_revoked_by_epoch(user_id, created_at):
            log.warning(
                "auth.refresh.rejected_by_epoch",
                user_id=user_id,
                session_created_at=created_at,
            )
            # Best-effort cleanup: remove the stale key that logout_all may not
            # have deleted yet (e.g., it was added by a concurrent _issue_tokens
            # call that completed after logout_all read smembers).
            await self._redis.delete(key)
            await self._untrack_session(user_id, key)
            raise ValueError("invalid or expired refresh token")

        # Liveness gate + roles, in one DB session, BEFORE rotating the token.
        # Without the liveness check a soft-deleted (DPDP erasure) or suspended
        # account could keep rotating tokens for the full refresh-token TTL —
        # authenticate()/get_user() filter these out, but refresh() did not.
        # Doing this before the Redis rotation means a transient DB error can't
        # destroy a legitimate user's still-valid refresh token.
        roles: list[str] = []
        async for session in self._db_factory():
            live_result = await session.execute(
                text(
                    "SELECT 1 FROM users "
                    "WHERE id = :uid AND deleted_at IS NULL AND is_active = true"
                ),
                {"uid": user_id},
            )
            if live_result.scalar() is None:
                log.warning("auth.refresh.rejected_inactive", user_id=user_id)
                raise ValueError("invalid or expired refresh token")
            roles = await self._get_roles_for_user(session, user_id)

        # Rotate: delete old token immediately + drop it from the session index
        # (the new token is re-added by _issue_tokens below).
        await self._redis.delete(key)
        await self._untrack_session(user_id, key)

        log.info("auth.refresh", user_id=user_id)
        return await self._issue_tokens(user_id, roles)

    async def logout(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        current_user_id: str | None = None,
    ) -> None:
        """Invalidate refresh token from Redis (access token expires naturally).

        When ``current_user_id`` is provided, the Redis key is looked up first
        to confirm ownership.  If the stored user_id does not match the caller's
        user_id the delete is skipped to prevent a user from invalidating another
        user's session (confused-deputy / cross-user logout attack).
        """
        if refresh_token is not None:
            key = _RT_PREFIX + hash_refresh_token(refresh_token)
            if current_user_id is not None:
                stored_raw: str | None = await self._redis.get(key)
                if stored_raw is None:
                    # Token already expired or revoked — treat as no-op.
                    log.info("auth.logout.token_not_found", user_id=current_user_id)
                    return
                # Parse new "<user_id>:<created_at>" format; ignore created_at here.
                stored_uid, _ = self._parse_rt_value(str(stored_raw))
                if stored_uid != current_user_id:
                    log.warning(
                        "auth.logout.cross_user_rejected",
                        caller_user_id=current_user_id,
                        token_owner_user_id=stored_uid,
                    )
                    return  # Do NOT delete another user's token.
            deleted = await self._redis.delete(key)
            if current_user_id is not None:
                await self._untrack_session(current_user_id, key)
            log.info("auth.logout", refresh_deleted=bool(deleted))
        # access_token: no per-jti denylist — 15 min TTL is the mitigation for a
        # single logout. "Log out ALL devices" (logout_all) additionally bumps the
        # user's token epoch so outstanding access tokens are rejected immediately.

    async def logout_all(self, user_id: str) -> int:
        """Revoke every session for *user_id*: all refresh tokens + all access tokens.

        ## Ordering guarantee (TOCTOU fix)

        The epoch is written FIRST, before the session-set is read or any
        refresh keys are deleted.  This ensures that a concurrent ``refresh()``
        call — which reads the stored epoch after it validates the incoming
        token — will always see the epoch and reject a pre-revocation session.

        Execution order:
          1. Write ``auth_epoch:<uid>`` = now  ← must be first
          2. Read ``user_sessions:<uid>`` (smembers)
          3. Delete every refresh-token key in the set
          4. Delete the session-index key itself

        If the epoch write fails (Redis error) we still attempt the session
        purge — a partial revocation (refresh tokens gone, access tokens
        still alive for up to 15 min) is better than no revocation.

        Refresh tokens: every key in the ``user_sessions:<uid>`` index is deleted
        (no Redis SCAN needed), then the index itself is dropped.

        Access tokens: the per-user ``auth_epoch:<uid>`` is set to *now*, so
        get_current_user rejects any access token whose ``iat`` predates it. The
        epoch key only needs to outlive the 15-minute access-token window — after
        that all pre-cutoff access tokens have expired on their own.

        Returns the number of refresh sessions revoked. Best-effort throughout:
        Redis errors are logged, never raised, so a logout-all attempt cannot
        itself 500.
        """
        epoch = int(datetime.now(tz=UTC).timestamp())

        # Step 1 — write epoch FIRST so concurrent refresh() calls see it.
        try:
            await self._redis.setex(
                USER_TOKEN_EPOCH_PREFIX + user_id,
                ACCESS_TOKEN_TTL_SECONDS + 60,  # outlive the access-token window
                epoch,
            )
        except Exception:  # noqa: BLE001 — epoch bump is best-effort
            log.warning("auth.logout_all.epoch_set_failed", user_id=user_id)

        # Step 2-4 — purge all refresh tokens (best-effort).
        revoked = 0
        sess_key = _SESSIONS_PREFIX + user_id
        try:
            members = await self._redis.smembers(sess_key)
            keys = [
                m.decode() if isinstance(m, bytes | bytearray) else str(m) for m in members
            ]
            if keys:
                await self._redis.delete(*keys)
                revoked = len(keys)
            await self._redis.delete(sess_key)
        except Exception:  # noqa: BLE001 — refresh purge is best-effort
            log.warning("auth.logout_all.refresh_purge_failed", user_id=user_id)

        log.info("auth.logout_all", user_id=user_id, sessions_revoked=revoked)
        return revoked

    async def get_user(self, user_id: str) -> User:
        """Return user profile by ID."""
        row: Any = None
        roles: list[str] = []
        async for session in self._db_factory():
            result = await session.execute(
                text(
                    "SELECT id, full_name, email FROM users "
                    "WHERE id = :uid AND deleted_at IS NULL AND is_active = true"
                ),
                {"uid": user_id},
            )
            row = result.fetchone()
            if row is None:
                raise ValueError(f"user {user_id!r} not found")
            roles = await self._get_roles_for_user(session, user_id)

        if row is None:  # db_factory yielded nothing (should not happen)
            raise ValueError(f"user {user_id!r} not found")  # pragma: no cover

        return User(
            user_id=str(row[0]),
            full_name=str(row[1]) if row[1] is not None else "",
            email=str(row[2]),
            roles=roles,
        )
