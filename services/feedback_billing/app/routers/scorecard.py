"""GET /scorecards/{scorecard_id} — scorecard retrieval endpoint (S5-007).

Auth: JWT required (same _require_jwt pattern as score.py).
Returns scorecard data including a 30-day pre-signed S3 URL for the PDF.

Access control (IDOR fix):
  - The scorecard's owner (sessions.user_id == caller sub) may always read it.
  - An hr_manager / super_admin / platform_owner whose company_id matches the
    candidate's company_id may also read it.
  - Any other caller receives 404 (not 403 — do not leak existence).
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from pydantic import BaseModel, Field
from shared.auth.jwt import verify_access_token
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.config import settings as _app_settings
from app.database import get_db_session
from app.redis_client import get_redis

# Redis key prefix for per-user token revocation epochs.
# Kept in sync with shared.auth.local.USER_TOKEN_EPOCH_PREFIX — do not change.
_TOKEN_EPOCH_PREFIX = "auth_epoch:"

log = structlog.get_logger(__name__)

router = APIRouter(tags=["scorecards"])

_bearer_scheme = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Pydantic response model
# ---------------------------------------------------------------------------


class ScoreBreakdown(BaseModel):
    """Per-axis scores (0-10 each)."""

    communication: int = Field(..., ge=0, le=10)
    technical: int = Field(..., ge=0, le=10)
    problem_solving: int = Field(..., ge=0, le=10)
    confidence: int = Field(..., ge=0, le=10)


class AxisRationale(BaseModel):
    """Per-axis 'why this score' explanation. Empty strings for legacy rows."""

    communication: str = ""
    technical: str = ""
    problem_solving: str = ""
    confidence: str = ""


class ImprovementItem(BaseModel):
    """A single improvement recommendation."""

    area: str
    suggestion: str


class ScorecardResponse(BaseModel):
    """Response body for GET /scorecards/{scorecard_id}."""

    scorecard_id: str
    session_id: str
    composite_score: float
    scores: ScoreBreakdown
    rationale: AxisRationale = Field(
        default_factory=AxisRationale,
        description="Per-axis explanation of why each score was given.",
    )
    strengths: list[str]
    improvements: list[ImprovementItem]
    summary: str
    report_pdf_url: str | None = Field(
        default=None,
        description="30-day pre-signed S3 URL for the PDF report, or null if not yet generated.",
    )


# ---------------------------------------------------------------------------
# Auth dependency (shared pattern with score.py)
# ---------------------------------------------------------------------------

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing access token",
    headers={"WWW-Authenticate": "Bearer"},
)


def _get_settings() -> Settings:
    return _app_settings


async def _token_epoch_check(user_id: str, iat: Any) -> None:
    """Raise HTTP 401 if the token was issued before the user's revocation epoch.

    Fails OPEN: any Redis error is logged and silently ignored so a cache
    hiccup never locks every user out.
    """
    try:
        raw = await get_redis().get(_TOKEN_EPOCH_PREFIX + user_id)
        if raw is not None and iat is not None and int(iat) < int(raw):
            log.info("scorecard.auth.token_revoked", user_id=user_id)
            raise _UNAUTHORIZED
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — fail open on Redis/parse errors
        log.warning(
            "scorecard.auth.epoch_check_skipped",
            error_type=type(exc).__name__,
        )


async def _require_jwt(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
) -> dict[str, Any]:
    """Verify Bearer JWT; return decoded payload.

    Raises HTTP 401 on any auth failure, including tokens revoked by a
    "log out all devices" whose ``iat`` predates the user's token epoch.
    """
    if credentials is None:
        raise _UNAUTHORIZED

    try:
        payload = verify_access_token(
            credentials.credentials,
            secret=_app_settings.jwt_secret,
            algorithm=_app_settings.jwt_algorithm,
            expected_issuer=_app_settings.jwt_issuer,
            expected_audience=_app_settings.jwt_audience,
        )
    except JWTError as exc:
        log.warning("scorecard.auth.jwt_failed", error_type=type(exc).__name__)
        raise _UNAUTHORIZED from exc

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise _UNAUTHORIZED

    await _token_epoch_check(user_id, payload.get("iat"))

    return payload


# ---------------------------------------------------------------------------
# S3 pre-signed URL helper
# ---------------------------------------------------------------------------


async def _generate_presigned_url(
    s3_key: str,
    settings: Settings,
    expiry_seconds: int = 86400 * 30,
) -> str | None:
    """Generate a pre-signed GET URL for the given S3 key.

    Returns None if S3 is not configured or on any error.
    """
    if not settings.s3_access_key_id:
        return None

    try:
        import aioboto3  # local import — optional dep  # noqa: PLC0415

        session = aioboto3.Session(
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
        )
        endpoint = settings.s3_endpoint_url or None

        async with session.client("s3", endpoint_url=endpoint) as s3:
            url: str = await s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": settings.s3_scorecard_bucket,
                    "Key": s3_key,
                },
                ExpiresIn=expiry_seconds,
            )
        return url
    except Exception as exc:  # broad catch — non-raising pre-sign helper
        log.error(
            "scorecard.presign_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


# Roles that may read scorecards belonging to candidates in their company.
_PRIVILEGED_ROLES = frozenset({"hr_manager", "super_admin", "admin", "platform_owner"})


@router.get(
    "/scorecards/{scorecard_id}",
    response_model=ScorecardResponse,
    summary="Retrieve a scorecard by ID",
    description=(
        "Returns the scorecard data for the given scorecard_id, including a "
        "30-day pre-signed S3 URL for the PDF report if the PDF has been generated. "
        "JWT required. "
        "Only the scorecard owner or an HR/admin from the same company may read it."
    ),
)
async def get_scorecard(
    scorecard_id: str,
    jwt_payload: Annotated[dict[str, Any], Depends(_require_jwt)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    app_settings: Annotated[Settings, Depends(_get_settings)],
) -> ScorecardResponse:
    """Retrieve a scorecard row and return structured data with a pre-signed PDF URL.

    Access control (IDOR):
        - The candidate whose session produced this scorecard (sessions.user_id ==
          caller sub) may always read it.
        - An hr_manager / super_admin / platform_owner whose users.company_id matches
          the candidate's users.company_id may also read it.
        - Any other caller receives 404 — existence is not revealed.

    Returns:
        200 ScorecardResponse on success.
        401 if JWT is missing, invalid, or revoked.
        404 if no scorecard exists or the caller is not authorised to read it.
    """
    caller_sub: str = str(jwt_payload.get("sub") or "")
    caller_roles: list[str] = jwt_payload.get("roles") or []

    # Fetch the scorecard + the owning session's user_id in one query.
    result = await db.execute(
        sa_text(
            """
            SELECT sc.scorecard_id, sc.session_id, sc.scores, sc.composite_score,
                   sc.rationale, sc.strengths, sc.improvements, sc.summary,
                   sc.report_pdf_key,
                   s.user_id AS session_owner_id,
                   owner.company_id AS owner_company_id
            FROM scorecards sc
            JOIN sessions s ON s.id = sc.session_id
            JOIN users owner ON owner.id = s.user_id
            WHERE sc.scorecard_id = :scorecard_id
            """
        ),
        {"scorecard_id": scorecard_id},
    )
    row = result.mappings().first()

    if row is None:
        log.warning("scorecard.not_found", scorecard_id=scorecard_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scorecard {scorecard_id!r} not found.",
        )

    # ------------------------------------------------------------------
    # Ownership / company-scope check (IDOR fix)
    # Return 404 (not 403) to avoid leaking existence to unauthorized callers.
    # ------------------------------------------------------------------
    session_owner_id = str(row["session_owner_id"])
    owner_company_id = row["owner_company_id"]  # may be None for platform users

    caller_is_owner = caller_sub == session_owner_id
    if not caller_is_owner:
        # platform_owner is the global super-admin: unrestricted cross-company access.
        is_platform_owner = "platform_owner" in caller_roles

        if not is_platform_owner:
            # Scoped privileged users (HR / super_admin / admin) may read scorecards
            # only within their own company.
            caller_has_privilege = bool(
                set(caller_roles) & (_PRIVILEGED_ROLES - frozenset({"platform_owner"}))
            )
            caller_in_same_company = False
            if caller_has_privilege and owner_company_id is not None:
                # Verify the caller belongs to the same company as the candidate.
                caller_company_id = await db.scalar(
                    sa_text(
                        "SELECT company_id FROM users WHERE id = :uid AND deleted_at IS NULL"
                    ),
                    {"uid": caller_sub},
                )
                caller_in_same_company = (
                    caller_company_id is not None
                    and str(caller_company_id) == str(owner_company_id)
                )

            if not caller_in_same_company:
                log.warning(
                    "scorecard.access_denied",
                    scorecard_id=scorecard_id,
                    caller=caller_sub,
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Scorecard {scorecard_id!r} not found.",
                )

    # Build pre-signed URL if a PDF key is stored.
    pdf_url: str | None = None
    if row["report_pdf_key"]:
        pdf_url = await _generate_presigned_url(
            str(row["report_pdf_key"]),
            app_settings,
        )

    # The DB stores scores as JSONB; SQLAlchemy returns it as a dict already
    # when using asyncpg.  Guard with a json.loads fallback for test mocks
    # where the fixture provides JSON strings.

    def _as_dict(val: Any) -> dict[str, Any]:
        if isinstance(val, dict):
            return val
        return json.loads(val)  # type: ignore[no-any-return]

    def _as_list(val: Any) -> list[Any]:
        if isinstance(val, list):
            return val
        return json.loads(val)  # type: ignore[no-any-return]

    raw_scores = _as_dict(row["scores"])
    raw_rationale = _as_dict(row["rationale"]) if row["rationale"] is not None else {}
    strengths = _as_list(row["strengths"]) if row["strengths"] is not None else []
    improvements_raw = _as_list(row["improvements"]) if row["improvements"] is not None else []

    breakdown = ScoreBreakdown(
        communication=int(raw_scores.get("communication", 0)),
        technical=int(raw_scores.get("technical", 0)),
        problem_solving=int(raw_scores.get("problem_solving", 0)),
        confidence=int(raw_scores.get("confidence", 0)),
    )
    rationale = AxisRationale(
        communication=str(raw_rationale.get("communication", "")),
        technical=str(raw_rationale.get("technical", "")),
        problem_solving=str(raw_rationale.get("problem_solving", "")),
        confidence=str(raw_rationale.get("confidence", "")),
    )
    improvements = [
        ImprovementItem(
            area=str(i.get("area", "")),
            suggestion=str(i.get("suggestion", "")),
        )
        for i in improvements_raw
    ]

    log.info(
        "scorecard.fetched",
        scorecard_id=scorecard_id,
        has_pdf=pdf_url is not None,
    )

    return ScorecardResponse(
        scorecard_id=str(row["scorecard_id"]),
        session_id=str(row["session_id"]),
        composite_score=float(row["composite_score"]),
        scores=breakdown,
        rationale=rationale,
        strengths=[str(s) for s in strengths],
        improvements=improvements,
        summary=str(row["summary"]),
        report_pdf_url=pdf_url,
    )
