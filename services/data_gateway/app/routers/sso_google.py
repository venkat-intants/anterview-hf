"""Google OAuth 2.0 SSO endpoints — S5-003b.

Implements the Google OAuth 2.0 authorization-code flow.

Contract:
  GET  /auth/sso/google/initiate?return_url={url}
         → 302 redirect to accounts.google.com authorize endpoint
         → 503 {"detail": "GOOGLE_OAUTH_NOT_CONFIGURED"} if client_id not set

  GET  /auth/sso/google/callback?code={code}&state={state}
         → 200 {"access_token": str, "token_type": "bearer", "user_id": str}
         → 503 {"detail": "GOOGLE_OAUTH_NOT_CONFIGURED"} if client_id not set
         → 400 {"detail": "INVALID_OR_EXPIRED_STATE"} on CSRF state mismatch

B-035: SSO routes are gated on Google being CONFIGURED (client_id set), not on
AUTH_PROVIDER. The auth factory has no bootable "google" provider, so the
platform runs AUTH_PROVIDER=local and offers Google SSO alongside local login.
         → 502 {"detail": "GOOGLE_TOKEN_EXCHANGE_FAILED"} on token endpoint error
         → 502 {"detail": "GOOGLE_USERINFO_FAILED"} on userinfo fetch error
         → 500 on unexpected errors

CSRF protection: A random state token is stored in Redis as
  oauth:google:state:{state_token} → return_url  (10-minute TTL)
On callback the key is retrieved and atomically deleted; absence → 400.

DB note: The ``users`` table has no ``google_id`` column in the current schema
(migration 20260527_0001).  Until a follow-up migration adds the column, the
upsert uses ``email`` as the conflict target and stores the google sub into a
no-op UPDATE so the user row is refreshed on each login.
Follow-up: add ``google_id TEXT UNIQUE`` to the users table and a unique index,
then switch the ON CONFLICT clause to ``index_elements=["google_id"]`` here.

PII note: user email and name from Google are NEVER written to logs.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from shared.auth.jwt import issue_access_token
from shared.auth.local import mint_refresh_session
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.models import User
from app.redis_client import get_redis

# Reuse the canonical, tested DPDP helpers (PII-safe IP/UA hashing with the
# trusted-proxy gate) so a Google-signin consent row is recorded identically to
# the POST /consent endpoint — single source of truth for the anti-spoofing logic.
from app.routers.consent import (
    _extract_client_ip,
    _extract_user_agent,
    _hash_value,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth/sso/google", tags=["sso"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_STATE_TTL_SECONDS = 600  # 10 minutes
_STATE_KEY_PREFIX = "oauth:google:state:"

# ---------------------------------------------------------------------------
# Dependency shortcuts
# ---------------------------------------------------------------------------

DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
# Redis is injected as a FastAPI dependency so tests can override it cleanly.
# redis.asyncio.Redis is not subscriptable at runtime on redis-py 5.x,
# so we use Any as the type argument and suppress the mypy ignore.
RedisDep = Annotated[Any, Depends(get_redis)]


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class SsoTokenResponse(BaseModel):
    """Successful SSO response — matches the shape used by the local auth flow."""

    access_token: str
    token_type: str = "bearer"
    user_id: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_google_configured() -> None:
    """Raise HTTP 503 if google_oauth_client_id is empty."""
    if not settings.google_oauth_client_id.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GOOGLE_OAUTH_NOT_CONFIGURED",
        )


def _build_authorize_url(state: str) -> str:
    """Build the full Google OAuth2 authorization URL.

    Extracted as a pure function so the unit test can verify the URL
    structure without any I/O.
    """
    params: dict[str, str] = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
    }
    return f"{_GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"


def _state_redis_key(state_token: str) -> str:
    """Return the Redis key for the given OAuth state token."""
    return f"{_STATE_KEY_PREFIX}{state_token}"


def _set_session_cookies(response: Response, raw_refresh: str) -> str:
    """Set the httpOnly refresh + JS-readable CSRF cookies on *response*.

    Mirrors ``app.routers.auth._set_auth_cookies`` so a Google session persists
    beyond the 15-minute access token and is silently refreshed via /auth/refresh
    exactly like a local-password session. Returns the CSRF token (for logging).
    """
    max_age = settings.jwt_refresh_expiry_days * 86400
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=settings.auth_refresh_cookie_name,
        value=raw_refresh,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        domain=settings.auth_cookie_domain,
        path=settings.auth_cookie_path,
        max_age=max_age,
    )
    response.set_cookie(
        key=settings.auth_csrf_cookie_name,
        value=csrf_token,
        httponly=False,  # JS must read + echo it as X-CSRF-Token on refresh.
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        domain=settings.auth_cookie_domain,
        path=settings.auth_cookie_path,
        max_age=max_age,
    )
    return csrf_token


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/initiate",
    status_code=status.HTTP_302_FOUND,
    summary="Initiate Google OAuth 2.0 SSO flow",
    description=(
        "Redirects the browser to the Google OAuth2 authorize endpoint. "
        "Only available when AUTH_PROVIDER=google. "
        "Returns 404 otherwise; 503 if google_oauth_client_id is not configured. "
        "A cryptographically random state token is stored in Redis (10-minute TTL) "
        "for CSRF protection."
    ),
    response_class=RedirectResponse,
)
async def initiate(
    redis: RedisDep,
    return_url: Annotated[
        str,
        Query(description="URL to redirect the user to after successful authentication"),
    ] = "",
    consent: Annotated[
        bool,
        Query(description="True when the candidate ticked the DPDP consent box before sign-in"),
    ] = False,
    consent_version: Annotated[
        int,
        Query(ge=1, description="Version of the consent text the candidate agreed to"),
    ] = 1,
) -> RedirectResponse:
    """Begin the Google OAuth 2.0 authorization-code flow.

    Steps
    -----
    1. Guard: AUTH_PROVIDER must be 'google'.
    2. Guard: google_oauth_client_id must be configured.
    3. Generate a random ``state`` nonce (URL-safe, 32 bytes).
    4. Store ``state → return_url`` in Redis with 10-minute TTL.
    5. Build the Google authorize URL and return 302.
    """
    # B-035: SSO availability is gated on Google being *configured*, NOT on
    # AUTH_PROVIDER. The auth factory has no bootable "google" provider (it
    # raises NotImplementedError), so the platform runs AUTH_PROVIDER=local
    # while still offering Google SSO alongside local password login. The route
    # 503s when Google credentials are absent.
    _require_google_configured()

    state = secrets.token_urlsafe(32)

    # Carry the candidate's consent intent (and return_url) in the state payload so
    # the callback — which sees the candidate's real browser IP — records the DPDP
    # consent row atomically with account creation. JSON so it stays parseable.
    state_payload = json.dumps(
        {
            "return_url": return_url or "",
            "consent": bool(consent),
            "consent_version": int(consent_version),
        }
    )
    await redis.set(
        _state_redis_key(state),
        state_payload,
        ex=_STATE_TTL_SECONDS,
    )

    authorize_url = _build_authorize_url(state)
    log.info("google.sso.initiate", state_prefix=state[:8], consent=bool(consent))
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_302_FOUND)


@router.get(
    "/callback",
    status_code=status.HTTP_200_OK,
    response_model=SsoTokenResponse,
    summary="Handle Google OAuth2 callback and issue Intants JWT",
    description=(
        "Validates the CSRF state token, exchanges the authorization code for "
        "a Google access token, fetches the user's profile from Google, upserts "
        "the user in the Intants DB, and returns an Intants JWT. "
        "Returns 404 if AUTH_PROVIDER != google; 400 on invalid/expired state; "
        "502 on Google API errors."
    ),
)
async def callback(
    code: Annotated[str, Query(description="Authorization code from Google")],
    state: Annotated[str, Query(description="CSRF state token")],
    db: DbSessionDep,
    redis: RedisDep,
    request: Request,
    response: Response,
) -> SsoTokenResponse:
    """Complete the Google OAuth 2.0 flow and create/update the Intants user.

    Steps
    -----
    1. Guard: AUTH_PROVIDER must be 'google'.
    2. Validate ``state`` against Redis; delete the key atomically.
    3. Exchange ``code`` for a Google access token via POST to token endpoint.
    4. Fetch user info (sub, email, name) from Google userinfo endpoint.
    5. Upsert user in DB (ON CONFLICT email DO UPDATE).
    6. Issue Intants JWT (same claim shape as LocalAuthProvider).
    7. Return {"access_token": …, "token_type": "bearer", "user_id": …}.

    Error handling
    --------------
    - Missing/expired state → 400 INVALID_OR_EXPIRED_STATE
    - Google token exchange non-2xx → 502 GOOGLE_TOKEN_EXCHANGE_FAILED
    - Google userinfo non-2xx → 502 GOOGLE_USERINFO_FAILED
    - httpx.HTTPError on network failure → 502
    - Unexpected errors → 500
    """
    # B-035: gate on Google being configured (see initiate). Decoupled from
    # AUTH_PROVIDER so SSO works alongside local login.
    _require_google_configured()

    # ------------------------------------------------------------------
    # Step 2: Validate CSRF state token via Redis (get-then-delete)
    # ------------------------------------------------------------------
    state_key = _state_redis_key(state)
    stored_value: str | None = await redis.get(state_key)
    if stored_value is None:
        log.warning("google.sso.callback.invalid_state", state_prefix=state[:8])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_OR_EXPIRED_STATE",
        )
    # Atomically delete so the state token cannot be replayed.
    await redis.delete(state_key)

    # Recover the candidate's consent intent from the state payload (JSON written
    # by initiate). Tolerate a legacy plain-string state (return_url only) — in
    # that case no consent was captured, so none is recorded here and the
    # candidate hits the standard consent gate before any interview.
    consent_requested = False
    consent_version = 1
    try:
        parsed_state = json.loads(stored_value)
        if isinstance(parsed_state, dict):
            consent_requested = bool(parsed_state.get("consent", False))
            consent_version = int(parsed_state.get("consent_version", 1))
    except (ValueError, TypeError):
        pass

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            # ------------------------------------------------------------------
            # Step 3: Exchange code for tokens
            # ------------------------------------------------------------------
            try:
                token_resp = await http.post(
                    _GOOGLE_TOKEN_URL,
                    data={
                        "client_id": settings.google_oauth_client_id,
                        "client_secret": settings.google_oauth_client_secret,
                        "redirect_uri": settings.google_oauth_redirect_uri,
                        "code": code,
                        "grant_type": "authorization_code",
                    },
                )
            except httpx.HTTPError as exc:
                log.warning("google.sso.token_exchange.http_error", error=str(exc))
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GOOGLE_TOKEN_EXCHANGE_FAILED",
                ) from exc

            if token_resp.status_code >= 400:
                log.warning(
                    "google.sso.token_exchange.error",
                    status_code=token_resp.status_code,
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GOOGLE_TOKEN_EXCHANGE_FAILED",
                )

            token_payload = token_resp.json()
            access_token_google: str = token_payload.get("access_token", "")

            # ------------------------------------------------------------------
            # Step 4: Fetch user info
            # ------------------------------------------------------------------
            try:
                userinfo_resp = await http.get(
                    _GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token_google}"},
                )
            except httpx.HTTPError as exc:
                log.warning("google.sso.userinfo.http_error", error=str(exc))
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GOOGLE_USERINFO_FAILED",
                ) from exc

            if userinfo_resp.status_code >= 400:
                log.warning(
                    "google.sso.userinfo.error",
                    status_code=userinfo_resp.status_code,
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GOOGLE_USERINFO_FAILED",
                )

            userinfo = userinfo_resp.json()
            # google_sub is the stable Google user identifier.
            google_sub: str = userinfo.get("sub", "")
            email: str = userinfo.get("email", "")
            full_name: str | None = userinfo.get("name") or None

            if not email:
                # Google must return an email when the 'email' scope is granted.
                log.error("google.sso.userinfo.missing_email")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GOOGLE_USERINFO_FAILED",
                )

    except HTTPException:
        raise
    except Exception as exc:
        log.exception("google.sso.callback.unexpected_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="INTERNAL_ERROR",
        ) from exc

    # ------------------------------------------------------------------
    # Candidate-only gate (BEFORE any write or session mint).
    #
    # Google sign-in is for CANDIDATES only — HR/admin accounts are provisioned
    # internally and sign in with a password. If this Google email already maps to
    # an account holding any privileged role, refuse: issuing a session here would
    # let /auth/refresh re-derive that account's full privileged roles from the DB
    # (the candidate access token lasts 15 min, but refresh reads live roles),
    # silently escalating past the candidate-only boundary. Reject outright so no
    # access token, refresh token, or cookie is ever issued to a privileged email.
    # ------------------------------------------------------------------
    privileged_row = await db.execute(
        text(
            "SELECT 1 FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE u.email = :email AND u.deleted_at IS NULL "
            "AND r.name IN ('hr_manager', 'super_admin', 'platform_owner', 'admin') "
            "LIMIT 1"
        ),
        {"email": email},
    )
    if privileged_row.fetchone() is not None:
        log.warning("google.sso.callback.privileged_account_rejected")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="GOOGLE_SIGNIN_CANDIDATES_ONLY",
        )

    # ------------------------------------------------------------------
    # Step 5: Upsert user in DB
    #
    # NOTE: The ``users`` table does not yet have a ``google_id`` column.
    # The follow-up migration (add google_id TEXT UNIQUE to users) is
    # tracked as a separate PR.  Until then:
    #   - Conflict target: unique index on ``email``
    #   - google_sub is recorded in the structlog context only (not in DB)
    #   - ON CONFLICT DO UPDATE refreshes full_name and updated_at
    # After the migration, switch to:
    #   index_elements=["google_id"]
    #   and store google_sub in the google_id column.
    # ------------------------------------------------------------------
    now_utc = datetime.now(UTC)
    new_user_id: uuid.UUID = uuid.uuid4()

    stmt = (
        pg_insert(User)
        .values(
            id=new_user_id,
            email=email,
            password_hash=None,
            full_name=full_name,
            phone=None,
            preferred_language="en",
            naipunyam_id=None,
            is_active=True,
            created_at=now_utc,
            updated_at=now_utc,
        )
        .on_conflict_do_update(
            index_elements=["email"],
            set_={
                "full_name": full_name,
                "updated_at": now_utc,
                "is_active": True,
            },
        )
        .returning(User.id)
    )

    result = await db.execute(stmt)
    row = result.fetchone()
    if row is None:
        log.error("google.sso.upsert.no_row_returned")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="INTERNAL_ERROR",
        )
    final_user_id: uuid.UUID = row[0]

    # Grant the 'candidate' role in the DB. The JWT below hardcodes ['candidate'],
    # but /auth/me and token refresh read roles from user_roles — without this row
    # a Google user would appear role-less after the access token expires. Google
    # sign-in is CANDIDATE-ONLY: the WHERE NOT EXISTS guard means an existing
    # HR/admin account (same email) is never granted candidate, and ON CONFLICT
    # keeps repeat logins idempotent.
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :uid, (SELECT id FROM roles WHERE name = 'candidate') "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
            "  WHERE ur.user_id = :uid "
            "  AND r.name IN ('hr_manager', 'super_admin', 'platform_owner', 'admin')"
            ") ON CONFLICT DO NOTHING"
        ),
        {"uid": final_user_id},
    )

    # DPDP §7: record the candidate's consent ATOMICALLY with the PII-storing
    # user upsert when they ticked the consent box before sign-in. Same ledger
    # taxonomy + idempotency + evidence shape as POST /consent and the interview
    # invite flow (interview_voice_recording / interview). IP + User-Agent are
    # sha256-hashed with the server salt — raw PII is never stored or logged.
    if consent_requested:
        evidence = {
            "source": "google_sso",
            "version": consent_version,
            "ip_hash": _hash_value(_extract_client_ip(request)),
            "user_agent_hash": _hash_value(_extract_user_agent(request)),
            "consented_at_iso": now_utc.isoformat(),
        }
        await db.execute(
            text(
                "INSERT INTO dpdp_consent_ledger "
                "(id, user_id, consent_type, granted, granted_at, purpose, evidence) "
                "SELECT :id, :uid, 'interview_voice_recording', TRUE, :now, 'interview', "
                "CAST(:ev AS jsonb) "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM dpdp_consent_ledger WHERE user_id = :uid "
                "  AND consent_type = 'interview_voice_recording' AND purpose = 'interview' "
                "  AND granted = TRUE AND revoked_at IS NULL"
                ") ON CONFLICT DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "uid": final_user_id,
                "now": now_utc,
                "ev": json.dumps(evidence),
            },
        )

    await db.commit()

    log.info(
        "google.sso.callback.ok",
        google_sub_prefix=google_sub[:8] if google_sub else "empty",
        intants_user_id=str(final_user_id),
    )

    # ------------------------------------------------------------------
    # Step 6: Issue Intants JWT (candidate-only)
    # ------------------------------------------------------------------
    intants_jwt = issue_access_token(
        user_id=str(final_user_id),
        roles=["candidate"],
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )

    # ------------------------------------------------------------------
    # Step 7: Mint a refresh token + set auth cookies so the Google session
    # persists past the 15-min access token (silently refreshed via /auth/refresh,
    # exactly like local login). sso.ts sends this exchange with
    # credentials:'include' so the Set-Cookie headers are stored by the browser.
    #
    # AUTH-01 fix: mint_refresh_session writes "<user_id>:<created_at_unix>" and
    # adds the key to the user_sessions:<uid> index so logout_all, admin
    # delete, and password-reset all revoke this SSO session correctly.
    # ------------------------------------------------------------------
    refresh_ttl = settings.jwt_refresh_expiry_days * 86400
    raw_refresh = await mint_refresh_session(redis, str(final_user_id), refresh_ttl)
    _set_session_cookies(response, raw_refresh)

    return SsoTokenResponse(
        access_token=intants_jwt,
        token_type="bearer",
        user_id=str(final_user_id),
    )
