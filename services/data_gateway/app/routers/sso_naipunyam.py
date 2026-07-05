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

The stub currently skips server-side ``state`` verification (a nonce stored in
session/Redis).  Full CSRF protection will be added in S5-003b once the Redis
session layer is wired for SSO flows.

AUTH-01 fix (2026-07-01): the callback now mints a proper tracked refresh token
via ``mint_refresh_session`` (same function used by LocalAuthProvider and the
Google SSO router) so that Naipunyam sessions are covered by logout_all/epoch
revocation, admin deletion, and password-reset invalidation.  The raw refresh
token is set as an httpOnly cookie exactly like local and Google sessions.

PII note: user profile data from Naipunyam is NEVER written to logs.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from shared.auth.jwt import issue_access_token
from shared.auth.local import mint_refresh_session
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
    return_url: Annotated[
        str,
        Query(description="URL to redirect to after successful authentication"),
    ] = "",
) -> RedirectResponse:
    """Begin Naipunyam OAuth2 authorization-code flow.

    1. Guard: AUTH_PROVIDER must be naipunyam.
    2. Guard: naipunyam_api_base_url must be configured.
    3. Generate a random ``state`` nonce (CSRF protection).
    4. Build the authorize URL and redirect.

    The ``state`` nonce is currently embedded in the redirect URL only; full
    server-side state validation (Redis nonce store) is deferred to S5-003b.
    """
    _require_naipunyam_provider()
    _require_naipunyam_configured()

    state = secrets.token_urlsafe(32)
    redirect_uri = settings.naipunyam_saml_acs_url or (
        f"{settings.naipunyam_api_base_url.rstrip('/')}/auth/sso/naipunyam/callback"
    )

    params: dict[str, str] = {
        "client_id": settings.naipunyam_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    if return_url:
        params["return_url"] = return_url

    authorize_url = (
        f"{settings.naipunyam_api_base_url.rstrip('/')}/oauth/authorize"
        f"?{urlencode(params)}"
    )

    log.info("naipunyam.sso.initiate", state=state)
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_302_FOUND)


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
    response: Response,
) -> SsoTokenResponse:
    """Complete the Naipunyam OAuth2 flow and create/update the Intants user.

    Steps
    -----
    1. Guard: AUTH_PROVIDER must be naipunyam.
    2. Guard: naipunyam_api_base_url must be configured.
    3. Exchange ``code`` for a Naipunyam bearer token (authorization_code grant).
    4. Extract the user's UID from the token response.
    5. Fetch the full profile via GET /v1/users/{uid}/profile.
    6. Upsert the user row (INSERT … ON CONFLICT naipunyam_id DO UPDATE).
    7. Issue an Intants JWT (same claim shape as LocalAuthProvider).
    8. Mint a tracked refresh token via mint_refresh_session + set cookies.
    9. Return {"access_token": …, "token_type": "bearer", "user_id": …}.

    Error handling
    --------------
    - CircuitOpenError or httpx.HTTPError → 503 NAIPUNYAM_UNAVAILABLE
    - NaipunyamError (non-2xx from IdP) → 503 NAIPUNYAM_UNAVAILABLE
    """
    _require_naipunyam_provider()
    _require_naipunyam_configured()

    client = _make_client()
    try:
        # ------------------------------------------------------------------
        # Step 3: exchange code for token (authorization_code grant)
        # ------------------------------------------------------------------
        token_url = f"{settings.naipunyam_api_base_url.rstrip('/')}/oauth/token"
        try:
            token_resp = await client._http.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": body.code,
                    "client_id": settings.naipunyam_client_id,
                    "client_secret": settings.naipunyam_client_secret,
                },
            )
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
