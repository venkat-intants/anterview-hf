"""GET /scorecards/{scorecard_id} — scorecard retrieval endpoint (S5-007).

Auth: JWT required (shared app.auth.require_jwt — see that module's docstring).
Returns scorecard data including a 15-minute pre-signed S3 URL for the PDF.

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
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UNAUTHORIZED, require_jwt
from app.config import Settings
from app.config import settings as _app_settings
from app.database import get_db_session

log = structlog.get_logger(__name__)

router = APIRouter(tags=["scorecards"])

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


class AxisFeedbackEntry(BaseModel):
    """Concrete mistakes + actionable practice steps for one axis."""

    went_wrong: list[str] = Field(default_factory=list)
    how_to_improve: list[str] = Field(default_factory=list)


class AxisFeedback(BaseModel):
    """Per-axis went_wrong / how_to_improve bullets. Empty for legacy rows."""

    communication: AxisFeedbackEntry = Field(default_factory=AxisFeedbackEntry)
    technical: AxisFeedbackEntry = Field(default_factory=AxisFeedbackEntry)
    problem_solving: AxisFeedbackEntry = Field(default_factory=AxisFeedbackEntry)
    confidence: AxisFeedbackEntry = Field(default_factory=AxisFeedbackEntry)


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
    axis_feedback: AxisFeedback = Field(
        default_factory=AxisFeedback,
        description="Per-axis 'what went wrong' and 'how to improve' bullets.",
    )
    strengths: list[str]
    improvements: list[ImprovementItem]
    summary: str
    report_pdf_url: str | None = Field(
        default=None,
        description="15-minute pre-signed S3 URL for the PDF report, or null if not yet generated.",
    )


# ---------------------------------------------------------------------------
# Auth dependency — imported, not reimplemented (see app/auth.py)
# ---------------------------------------------------------------------------

# A local copy is exactly how this router ended up as the fourth pasted
# JWT verifier in this service; app/auth.py exists precisely to stop that
# (see its docstring). Keep the module-local names so the rest of this file
# and its tests are unaffected by the refactor.
_UNAUTHORIZED = UNAUTHORIZED
_require_jwt = require_jwt


def _get_settings() -> Settings:
    return _app_settings


# ---------------------------------------------------------------------------
# S3 pre-signed URL helper
# ---------------------------------------------------------------------------


async def _generate_presigned_url(
    s3_key: str,
    settings: Settings,
    expiry_seconds: int = 3600,
) -> str | None:
    """Generate a pre-signed GET URL for the given S3 key.

    One hour. This was 30 DAYS, which is the thing being fixed: a pre-signed
    URL is an unauthenticated bearer capability, so it honours neither the
    15-minute access token nor the auth_epoch kill switch, and it points at a
    PDF carrying the candidate's name, scores and the AI narrative. A month-long
    window survives logout-all, password change, HR account deletion and the
    candidate closing their account.

    Not 15 minutes, though the exposure argument favours it: the frontend
    renders this straight into an <a href> on the scorecard page
    (web/src/pages/Scorecard.tsx), so the link has to still work when someone
    reads their results for a while and then clicks Download — otherwise they
    get a raw S3 AccessDenied XML page with no explanation. An hour covers a
    realistic page view and is still a 720x reduction. Shortening further needs
    the page to re-fetch the scorecard on click first.

    The platform's revocation
    model (short-lived tokens + the auth_epoch kill switch) means a pre-signed
    URL that outlives it is an unauthenticated bearer capability honouring
    neither: the PDF it points to carries the candidate's name, scores and the
    AI narrative. The frontend fetches this endpoint and opens the URL
    immediately, so 15 minutes costs it nothing.

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
        "15-minute pre-signed S3 URL for the PDF report if the PDF has been generated. "
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

    # axis_feedback is stored NESTED inside the rationale JSONB (no separate
    # column). Legacy rows simply lack the key → empty lists everywhere.
    raw_feedback = raw_rationale.get("axis_feedback") or {}

    def _feedback_entry(axis: str) -> AxisFeedbackEntry:
        entry = raw_feedback.get(axis)
        if not isinstance(entry, dict):
            return AxisFeedbackEntry()
        went = entry.get("went_wrong")
        improve = entry.get("how_to_improve")
        return AxisFeedbackEntry(
            went_wrong=[str(b) for b in went] if isinstance(went, list) else [],
            how_to_improve=[str(b) for b in improve] if isinstance(improve, list) else [],
        )

    axis_feedback = AxisFeedback(
        communication=_feedback_entry("communication"),
        technical=_feedback_entry("technical"),
        problem_solving=_feedback_entry("problem_solving"),
        confidence=_feedback_entry("confidence"),
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
        axis_feedback=axis_feedback,
        strengths=[str(s) for s in strengths],
        improvements=improvements,
        summary=str(row["summary"]),
        report_pdf_url=pdf_url,
    )
