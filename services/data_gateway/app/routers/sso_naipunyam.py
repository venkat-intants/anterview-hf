"""Naipunyam SSO endpoints — S5-003a.

Implements OAuth2 authorization-code flow for Naipunyam (APSSDC portal).

Contract:
  GET  /auth/sso/naipunyam/initiate?return_url={url}
         → 302 redirect to Naipunyam OAuth authorize endpoint
         → 404 if AUTH_PROVIDER != "naipunyam"
         → 503 {"detail": "NAIPUNYAM_NOT_CONFIGURED"} if base_url not set

  POST /auth/sso/naipunyam/callback
         Body: {"code": str, "state": str}
         → 200 {"access_token": str, "token_type": "bearer", "user_id": str}
         → 404 if AUTH_PROVIDER != "naipunyam"
         → 503 {"detail": "NAIPUNYAM_UNAVAILABLE"} on circuit-open or httpx error

Login-CSRF defence (2026-08-06): ``state`` is verified server-side. ``initiate``
writes a JSON payload to Redis under ``oauth:naipunyam:state:<state>`` holding the
SHA-256 of a ``state_binding`` secret plus a PKCE verifier, and sets the binding
secret as an httpOnly ``SameSite=Lax`` cookie. ``callback`` looks the state up,
deletes it (one-shot, no replay), and compares the presented cookie's SHA-256
with ``hmac.compare_digest`` BEFORE the token exchange and before any session is
minted. A callback that does not come from the browser that started the flow
costs nothing and reveals nothing. Same pattern as ``sso_google.py`` — read that
module's ``initiate`` docstring for the attack this blocks.

Candidate-only: an IdP email mapping to an account with ``hr_manager``,
``super_admin``, ``platform_owner`` or ``admin`` is rejected with 403 before any
write, because ``/auth/refresh`` re-derives live roles from the DB and would
silently escalate the session past the candidate boundary.

AUTH-01 fix (2026-07-01): the callback now mints a proper tracked refresh token
via ``mint_refresh_session`` (same function used by LocalAuthProvider and the
Google SSO router) so that Naipunyam sessions are covered by logout_all/epoch
revocation, admin deletion, and password-reset invalidation.  The raw refresh
token is set as an httpOnly cookie exactly like local and Google sessions.

PII note: user profile data from Naipunyam is NEVER written to logs.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
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
from sqlalchemy import bindparam as sa_bindparam
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.models import User
from app.naipunyam.circuit_breaker import CircuitOpenError
from app.naipunyam.client import NaipunyamClient, NaipunyamError
from app.redis_client import get_redis

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth/sso/naipunyam", tags=["sso"])

# ---------------------------------------------------------------------------
# Dependency shortcuts
# ---------------------------------------------------------------------------
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
RedisDep = Annotated[Any, Depends(get_redis)]

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class SsoCallbackBody(BaseModel):
    """Body for POST /auth/sso/naipunyam/callback."""

    code: str
    state: str


class SsoTokenResponse(BaseModel):
    """Successful SSO response — mirrors local auth token shape.

    A refresh token is minted and delivered as an httpOnly cookie (not in this
    JSON body) so the Naipunyam session persists past the 15-minute access
    token and is revocable by logout_all / admin delete (AUTH-01).
    """

    access_token: str
    token_type: str = "bearer"
    user_id: str


# ---------------------------------------------------------------------------
# Login-CSRF state machinery (mirrors sso_google.py)
# ---------------------------------------------------------------------------

# How long a started SSO flow stays completable. Long enough for a slow IdP
# login, short enough that a leaked state is not useful later.
_STATE_TTL_SECONDS = 600  # 10 minutes
_STATE_KEY_PREFIX = "oauth:naipunyam:state:"

# httpOnly cookie binding a state token to the browser that started the flow.
# Without it `state` is a bearer value the attacker generates for themselves,
# which is exactly the login-CSRF this module used to be open to.
_STATE_COOKIE_NAME = "naipunyam_oauth_state"

# Roles that must never receive a session from this flow. Naipunyam SSO signs in
# CANDIDATES; staff accounts are provisioned internally and use a password.
_PRIVILEGED_ROLES = ("hr_manager", "super_admin", "platform_owner", "admin")


def _state_redis_key(state_token: str) -> str:
    """Return the Redis key for the given OAuth state token."""
    return f"{_STATE_KEY_PREFIX}{state_token}"


def _pkce_challenge(verifier: str) -> str:
    """S256 code challenge for *verifier* (RFC 7636 §4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_naipunyam_provider() -> None:
    """Raise HTTP 404 if AUTH_PROVIDER is not naipunyam.

    Callers outside this module that need Naipunyam SSO must only be reachable
    when the platform is configured for it.  Returning 404 (rather than 403 or
    400) avoids leaking information about alternate auth flows to scanners.
    """
    if settings.auth_provider.lower() != "naipunyam":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not Found",
        )


def _require_naipunyam_configured() -> None:
    """Raise HTTP 503 if the Naipunyam base URL is not configured."""
    if not settings.naipunyam_api_base_url.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NAIPUNYAM_NOT_CONFIGURED",
        )


def _set_session_cookies(response: Response, raw_refresh: str) -> None:
    """Set the httpOnly refresh + JS-readable CSRF cookies on *response*.

    Mirrors the Google SSO and local auth cookie setup so a Naipunyam session
    is silently refreshed via /auth/refresh exactly like any other session.
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
        httponly=False,  # JS must read + echo as X-CSRF-Token on refresh.
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        domain=settings.auth_cookie_domain,
        path=settings.auth_cookie_path,
        max_age=max_age,
    )


def _make_client() -> NaipunyamClient:
    """Construct a NaipunyamClient from current settings."""
    return NaipunyamClient(
        base_url=settings.naipunyam_api_base_url,
        client_id=settings.naipunyam_client_id,
        client_secret=settings.naipunyam_client_secret,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/initiate",
    status_code=status.HTTP_302_FOUND,
    summary="Initiate Naipunyam OAuth2 SSO flow",
    description=(
        "Redirects the browser to the Naipunyam OAuth2 authorize endpoint. "
        "Only available when AUTH_PROVIDER=naipunyam. "
        "Returns 404 otherwise; 503 if the base URL is not configured."
    ),
    response_class=RedirectResponse,
)
async def initiate(
    redis: RedisDep,
    return_url: Annotated[
        str,
        Query(description="URL to redirect to after successful authentication"),
    ] = "",
) -> RedirectResponse:
    """Begin Naipunyam OAuth2 authorization-code flow.

    1. Guard: AUTH_PROVIDER must be naipunyam.
    2. Guard: naipunyam_api_base_url must be configured.
    3. Generate ``state``, a ``state_binding`` secret, and a PKCE verifier;
       store the binding's SHA-256 and the verifier in Redis under ``state``.
    4. Set ``state_binding`` as an httpOnly SameSite=Lax cookie.
    5. Build the authorize URL (carrying the S256 challenge) and redirect.

    Steps 3-4 are what make ``state`` non-forgeable. The value in the URL is
    public by construction — anyone who can start a flow gets one — so on its
    own it proves nothing about who is completing the flow. The binding cookie
    is the half the attacker cannot obtain: it is httpOnly (script on the page
    cannot read it) and only its SHA-256 reaches Redis (a Redis read does not
    disclose it either).
    """
    _require_naipunyam_provider()
    _require_naipunyam_configured()

    state = secrets.token_urlsafe(32)
    state_binding = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)

    await redis.set(
        _state_redis_key(state),
        json.dumps(
            {
                "return_url": return_url or "",
                "binding": hashlib.sha256(state_binding.encode()).hexdigest(),
                "code_verifier": code_verifier,
            }
        ),
        ex=_STATE_TTL_SECONDS,
    )

    redirect_uri = settings.naipunyam_saml_acs_url or (
        f"{settings.naipunyam_api_base_url.rstrip('/')}/auth/sso/naipunyam/callback"
    )

    params: dict[str, str] = {
        "client_id": settings.naipunyam_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        # PKCE. Even for a confidential client this binds the authorization
        # code to the browser that started the flow, so a code captured in
        # transit cannot be redeemed without the matching verifier.
        "code_challenge": _pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    if return_url:
        params["return_url"] = return_url

    authorize_url = (
        f"{settings.naipunyam_api_base_url.rstrip('/')}/oauth/authorize"
        f"?{urlencode(params)}"
    )

    # Log a prefix, never the nonce itself: a full state in the log stream is a
    # credential for anyone who can read logs.
    log.info("naipunyam.sso.initiate", state_prefix=state[:8])
    redirect = RedirectResponse(url=authorize_url, status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        key=_STATE_COOKIE_NAME,
        value=state_binding,
        httponly=True,
        secure=settings.auth_cookie_secure,
        # Lax, not Strict: the callback is triggered by a cross-site top-level
        # navigation back from the Naipunyam portal, and Strict would withhold
        # the cookie exactly then, breaking every legitimate sign-in.
        samesite="lax",
        domain=settings.auth_cookie_domain,
        path="/",
        max_age=_STATE_TTL_SECONDS,
    )
    return redirect


@router.post(
    "/callback",
    status_code=status.HTTP_200_OK,
    response_model=SsoTokenResponse,
    summary="Handle Naipunyam OAuth2 callback and issue Intants JWT",
    description=(
        "Exchanges the authorization code for a Naipunyam token, fetches the "
        "user profile, upserts the user in the Intants DB, and returns an "
        "Intants JWT. Returns 404 if AUTH_PROVIDER != naipunyam; 503 on "
        "Naipunyam service errors."
    ),
)
async def callback(
    body: SsoCallbackBody,
    db: DbSessionDep,
    redis: RedisDep,
    request: Request,
    response: Response,
) -> SsoTokenResponse:
    """Complete the Naipunyam OAuth2 flow and create/update the Intants user.

    Steps
    -----
    1. Guard: AUTH_PROVIDER must be naipunyam.
    2. Guard: naipunyam_api_base_url must be configured.
    2a. Look up ``state`` in Redis and delete it (one-shot — no replay).
    2b. Verify the state belongs to THIS browser via the binding cookie.
    3. Exchange ``code`` + PKCE verifier for a Naipunyam bearer token.
    4. Extract the user's UID from the token response.
    5. Fetch the full profile via GET /v1/users/{uid}/profile.
    5a. Reject the sign-in if the email maps to a privileged account.
    6. Upsert the user row (INSERT … ON CONFLICT naipunyam_id DO UPDATE).
    7. Issue an Intants JWT (same claim shape as LocalAuthProvider).
    8. Mint a tracked refresh token via mint_refresh_session + set cookies.
    9. Return {"access_token": …, "token_type": "bearer", "user_id": …}.

    Steps 2a-2b run BEFORE the token exchange on purpose: a forged callback is
    then rejected without ever contacting the IdP, so it costs us nothing and
    tells the attacker nothing.

    Error handling
    --------------
    - Unknown / expired / unbound state → 400 INVALID_OR_EXPIRED_STATE
    - Privileged account → 403 NAIPUNYAM_SIGNIN_CANDIDATES_ONLY
    - CircuitOpenError or httpx.HTTPError → 503 NAIPUNYAM_UNAVAILABLE
    - NaipunyamError (non-2xx from IdP) → 503 NAIPUNYAM_UNAVAILABLE
    """
    _require_naipunyam_provider()
    _require_naipunyam_configured()

    # ------------------------------------------------------------------
    # Step 2a: the state must be one WE issued and have not yet consumed
    # ------------------------------------------------------------------
    state_key = _state_redis_key(body.state)
    stored_value = await redis.get(state_key)
    if stored_value is None:
        log.warning(
            "naipunyam.sso.callback.unknown_state",
            state_prefix=body.state[:8],
        )
        response.delete_cookie(_STATE_COOKIE_NAME, path="/")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_OR_EXPIRED_STATE",
        )
    # Atomically drop it so the same state cannot authorise a second callback.
    await redis.delete(state_key)

    if isinstance(stored_value, bytes):
        stored_value = stored_value.decode()

    # ------------------------------------------------------------------
    # Step 2b: the state must belong to THIS browser (login-CSRF defence)
    # ------------------------------------------------------------------
    expected_binding = ""
    pkce_verifier = ""
    with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError):
        parsed = json.loads(stored_value)
        if isinstance(parsed, dict):
            expected_binding = str(parsed.get("binding") or "")
            pkce_verifier = str(parsed.get("code_verifier") or "")

    binding_cookie = request.cookies.get(_STATE_COOKIE_NAME, "")
    presented = (
        hashlib.sha256(binding_cookie.encode()).hexdigest() if binding_cookie else ""
    )
    # No fallback for a missing binding. Unlike the Google flow — which had live
    # sessions mid-flight when its fix shipped and needed a compat branch — this
    # provider has never been enabled, so there is no legacy state to tolerate.
    # An unbound state is a forged state; treat it as one.
    if not expected_binding or not presented or not hmac.compare_digest(
        presented, expected_binding
    ):
        log.warning(
            "naipunyam.sso.callback.state_not_bound_to_browser",
            state_prefix=body.state[:8],
            had_cookie=bool(binding_cookie),
        )
        response.delete_cookie(_STATE_COOKIE_NAME, path="/")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_OR_EXPIRED_STATE",
        )

    # One-shot: the binding must not survive to authorise a second callback.
    response.delete_cookie(_STATE_COOKIE_NAME, path="/")

    client = _make_client()
    try:
        # ------------------------------------------------------------------
        # Step 3: exchange code for token (authorization_code grant)
        # ------------------------------------------------------------------
        token_url = f"{settings.naipunyam_api_base_url.rstrip('/')}/oauth/token"
        exchange_data: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": body.code,
            "client_id": settings.naipunyam_client_id,
            "client_secret": settings.naipunyam_client_secret,
        }
        if pkce_verifier:
            exchange_data["code_verifier"] = pkce_verifier
        try:
            token_resp = await client._http.post(token_url, data=exchange_data)
        except httpx.HTTPError as exc:
            log.warning("naipunyam.sso.token_exchange.http_error", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="NAIPUNYAM_UNAVAILABLE",
            ) from exc

        if token_resp.status_code >= 400:
            log.warning(
                "naipunyam.sso.token_exchange.error",
                status_code=token_resp.status_code,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="NAIPUNYAM_UNAVAILABLE",
            )

        token_payload = token_resp.json()
        naipunyam_uid: str = token_payload.get("sub") or token_payload.get("uid", "")

        if not naipunyam_uid:
            log.error("naipunyam.sso.callback.missing_uid")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="NAIPUNYAM_UNAVAILABLE",
            )

        # ------------------------------------------------------------------
        # Step 4 & 5: fetch profile
        # ------------------------------------------------------------------
        try:
            profile = await client.get_profile(naipunyam_uid)
        except (CircuitOpenError, NaipunyamError, httpx.HTTPError) as exc:
            log.warning(
                "naipunyam.sso.profile_fetch.error",
                uid=naipunyam_uid,
                error=type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="NAIPUNYAM_UNAVAILABLE",
            ) from exc

        # ------------------------------------------------------------------
        # Step 6: upsert user
        # ------------------------------------------------------------------
        now_utc = datetime.now(UTC)
        intants_user_id: uuid.UUID = uuid.uuid4()

        # Use PostgreSQL INSERT … ON CONFLICT to atomically upsert.
        # email is required; derive a placeholder if the profile omits it.
        email = profile.email or f"{naipunyam_uid}@naipunyam.invalid"

        # ------------------------------------------------------------------
        # Step 5a: candidate-only gate (BEFORE any write or session mint).
        #
        # Naipunyam SSO signs in CANDIDATES. Staff accounts are provisioned
        # internally and sign in with a password. If this IdP email already maps
        # to an account holding a privileged role, refuse: the access token we
        # would mint says roles=["candidate"], but /auth/refresh re-derives roles
        # from the DB, so 15 minutes later the same session silently holds the
        # account's real privileges. Reject outright so no access token, refresh
        # token or cookie is ever issued against a privileged email.
        # ------------------------------------------------------------------
        privileged_row = await db.execute(
            text(
                "SELECT 1 FROM users u "
                "JOIN user_roles ur ON ur.user_id = u.id "
                "JOIN roles r ON r.id = ur.role_id "
                "WHERE u.email = :email AND u.deleted_at IS NULL "
                "AND r.name IN :roles "
                "LIMIT 1"
            ).bindparams(sa_bindparam("roles", expanding=True)),
            {"email": email, "roles": list(_PRIVILEGED_ROLES)},
        )
        if privileged_row.fetchone() is not None:
            log.warning("naipunyam.sso.callback.privileged_account_rejected")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="NAIPUNYAM_SIGNIN_CANDIDATES_ONLY",
            )

        stmt = (
            pg_insert(User)
            .values(
                id=intants_user_id,
                email=email,
                password_hash=None,
                full_name=profile.name or None,
                phone=profile.phone or None,
                preferred_language=profile.preferred_language or "en",
                naipunyam_id=naipunyam_uid,
                is_active=True,
                created_at=now_utc,
                updated_at=now_utc,
            )
            .on_conflict_do_update(
                index_elements=["naipunyam_id"],
                set_={
                    "full_name": profile.name or None,
                    "phone": profile.phone or None,
                    "preferred_language": profile.preferred_language or "en",
                    "updated_at": now_utc,
                    "is_active": True,
                },
            )
            .returning(User.id)
        )

        result = await db.execute(stmt)
        row = result.fetchone()
        if row is None:
            log.error("naipunyam.sso.upsert.no_row_returned", uid=naipunyam_uid)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="NAIPUNYAM_UNAVAILABLE",
            )
        final_user_id: uuid.UUID = row[0]
        await db.commit()

        log.info(
            "naipunyam.sso.callback.ok",
            naipunyam_uid=naipunyam_uid,
            intants_user_id=str(final_user_id),
        )

        # ------------------------------------------------------------------
        # Step 7: issue Intants JWT
        # ------------------------------------------------------------------
        access_token = issue_access_token(
            user_id=str(final_user_id),
            roles=["candidate"],
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )

        # ------------------------------------------------------------------
        # Step 8: mint tracked refresh token + set auth cookies (AUTH-01)
        #
        # mint_refresh_session writes "<user_id>:<created_at_unix>" and adds
        # the key to the user_sessions:<uid> index, making this Naipunyam
        # session fully revocable by logout_all / admin delete / epoch check.
        # ------------------------------------------------------------------
        refresh_ttl = settings.jwt_refresh_expiry_days * 86400
        raw_refresh = await mint_refresh_session(redis, str(final_user_id), refresh_ttl)
        _set_session_cookies(response, raw_refresh)

        return SsoTokenResponse(
            access_token=access_token,
            token_type="bearer",
            user_id=str(final_user_id),
        )

    except HTTPException:
        raise
    except (CircuitOpenError, NaipunyamError) as exc:
        log.warning("naipunyam.sso.callback.circuit_or_api_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NAIPUNYAM_UNAVAILABLE",
        ) from exc
    except httpx.HTTPError as exc:
        log.warning("naipunyam.sso.callback.http_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NAIPUNYAM_UNAVAILABLE",
        ) from exc
    finally:
        await client.aclose()
