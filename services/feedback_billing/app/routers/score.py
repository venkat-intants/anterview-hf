"""POST /internal/score — end-of-session scoring endpoint (S5-006).

Auth: JWT required (HS256, same jwt_secret as other services).
      No admin role required — this is an internal service-to-service call.

Idempotency: the scorecards table has a UNIQUE constraint on session_id.
             A second call for the same session_id returns HTTP 409.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from pydantic import BaseModel, Field, field_validator
from shared.auth.jwt import verify_access_token
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.config import settings as _app_settings
from app.database import get_db_session
from app.redis_client import get_redis
from app.scorer import ScoringError, score_session

# Redis key prefix for per-user token revocation epochs.
# Kept in sync with shared.auth.local.USER_TOKEN_EPOCH_PREFIX — do not change.
_TOKEN_EPOCH_PREFIX = "auth_epoch:"


def _get_settings() -> Settings:
    return _app_settings

log = structlog.get_logger(__name__)

router = APIRouter(tags=["internal"])

_bearer_scheme = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

_VALID_ROLES = {"ai", "user", "interviewer", "candidate"}


class TurnIn(BaseModel):
    """A single conversation turn in the transcript."""

    role: str = Field(..., description="Speaker role: 'ai' or 'user'")
    text: str = Field(..., description="Turn text content")

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in _VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(_VALID_ROLES)}")
        return v


class ScoreRequest(BaseModel):
    """Request body for POST /internal/score."""

    session_id: str = Field(..., description="UUID of the completed session")
    job_title: str = Field(..., min_length=1, description="Job title for calibration")
    experience_level: str = Field(
        ..., min_length=1, description="Experience tier, e.g. 'entry', 'mid', 'senior'"
    )
    language: str = Field(
        default="en",
        description="BCP-47 language code: 'en', 'hi', or 'te'",
    )
    jd_text: str = Field(
        default="", description="Parsed JD text — optional, improves scoring accuracy"
    )
    turns: list[TurnIn] = Field(
        ..., description="Ordered list of conversation turns"
    )


class ScoreResponse(BaseModel):
    """Response body for POST /internal/score (201 Created)."""

    scorecard_id: str
    composite_score: float
    scores: dict[str, int]


# ---------------------------------------------------------------------------
# JWT dependency
# ---------------------------------------------------------------------------

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing access token",
    headers={"WWW-Authenticate": "Bearer"},
)

_FORBIDDEN = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Service role required for internal endpoints.",
)

# The set of sub claims that are permitted to call /internal/* endpoints.
# Extend this tuple if a second internal service needs to call scoring.
_ALLOWED_SERVICE_SUBS = frozenset({"interview_core"})


async def _token_epoch_check(user_id: str, iat: Any) -> None:
    """Raise HTTP 401 if the token's iat predates the user's revocation epoch.

    Fails OPEN: any Redis error is logged and silently ignored so a cache
    hiccup never blocks legitimate internal service calls.
    """
    try:
        raw = await get_redis().get(_TOKEN_EPOCH_PREFIX + user_id)
        if raw is not None and iat is not None and int(iat) < int(raw):
            log.info("score.auth.token_revoked", user_id=user_id)
            raise _UNAUTHORIZED
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — fail open on Redis/parse errors
        log.warning("score.auth.epoch_check_skipped", error_type=type(exc).__name__)


async def _require_service_jwt(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
) -> dict[str, Any]:
    """Verify Bearer JWT and enforce the ``service`` role for /internal/* endpoints.

    Legitimate caller: the interview worker, which mints a JWT with
    ``roles=["service"]`` and ``sub="interview_core"``.

    Raises:
        HTTP 401 — token absent, malformed, expired, or revoked.
        HTTP 403 — token is valid but the caller is not a service account
                   (prevents normal user / guest tokens from reaching Gemini).
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
        log.warning("score.auth.jwt_failed", error_type=type(exc).__name__)
        raise _UNAUTHORIZED from exc

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise _UNAUTHORIZED

    roles: list[str] = payload.get("roles") or []
    if "service" not in roles:
        log.warning(
            "score.auth.non_service_rejected",
            sub=user_id,
            roles=roles,
        )
        raise _FORBIDDEN

    # Token-epoch check: reject service tokens that predate a logout_all
    # (e.g. after a compromised service credential is rotated).
    await _token_epoch_check(user_id, payload.get("iat"))

    return payload


# ---------------------------------------------------------------------------
# Keep the old name as an alias so existing callers outside this module
# (e.g. tests that patch "app.routers.score._require_jwt") still work.
# New code should use _require_service_jwt directly.
# ---------------------------------------------------------------------------
_require_jwt = _require_service_jwt


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/score",
    status_code=status.HTTP_201_CREATED,
    response_model=ScoreResponse,
    summary="Score a completed interview session",
    description=(
        "Renders the APSSDC scorer prompt, calls Gemini at temperature 0.2, "
        "and persists a scorecards row. Returns the scorecard_id and composite_score. "
        "Idempotency: a second call for the same session_id returns 409."
    ),
)
async def internal_score(
    body: ScoreRequest,
    _jwt_payload: Annotated[dict[str, Any], Depends(_require_jwt)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    app_settings: Annotated[Settings, Depends(_get_settings)],
) -> ScoreResponse:
    """Score a session and persist the scorecard.

    Returns:
        201 ScoreResponse on success.
        400 if the request body is invalid (handled by FastAPI Pydantic validation).
        401 if the JWT is missing or invalid.
        409 if a scorecard already exists for this session_id.
        502 if Gemini fails (ScoringError).
    """
    turns_dicts = [{"role": t.role, "text": t.text} for t in body.turns]

    try:
        scorecard_id, scores, composite = await score_session(
            session_id=body.session_id,
            job_title=body.job_title,
            experience_level=body.experience_level,
            language=body.language,
            jd_text=body.jd_text,
            turns=turns_dicts,
            db_session=db,
            settings=app_settings,
        )
    except ScoringError as exc:
        log.error(
            "score.gemini_error",
            session_id=body.session_id,
            error=exc.message,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Scoring failed: {exc.message}",
        ) from exc
    except IntegrityError as exc:
        # UNIQUE constraint on session_id — duplicate submission.
        log.warning(
            "score.duplicate_session",
            session_id=body.session_id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scorecard already exists for session_id {body.session_id!r}",
        ) from exc

    return ScoreResponse(
        scorecard_id=scorecard_id,
        composite_score=composite,
        scores=scores,
    )


# ---------------------------------------------------------------------------
# Resume ATS scoring (HR workflow Phase 1)
# ---------------------------------------------------------------------------


class ResumeScoreRequest(BaseModel):
    """Request body for POST /score-resume — ATS-score a resume against a role."""

    resume_text: str = Field(..., min_length=1, description="Extracted resume text")
    job_title: str = Field(default="General Role", min_length=1)
    level: str = Field(default="mid", description="entry | mid | senior")
    jd_text: str = Field(default="", description="Job description — optional, improves accuracy")


class ResumeScoreResponse(BaseModel):
    candidate_name: str = ""
    candidate_email: str = ""
    overall: int
    breakdown: dict[str, int]
    strengths: list[str]
    concerns: list[str]
    recommendation: str
    summary: str


@router.post(
    "/score-resume",
    status_code=status.HTTP_200_OK,
    response_model=ResumeScoreResponse,
    summary="ATS-score an applicant resume against a role (stateless)",
)
async def internal_score_resume(
    body: ResumeScoreRequest,
    _jwt_payload: Annotated[dict[str, Any], Depends(_require_jwt)],
    app_settings: Annotated[Settings, Depends(_get_settings)],
) -> ResumeScoreResponse:
    """ATS-score a resume. Stateless — returns the score for the caller to persist."""
    from app.resume_scorer import ResumeScoringError, score_resume

    try:
        result = await score_resume(
            resume_text=body.resume_text,
            job_title=body.job_title,
            level=body.level,
            jd_text=body.jd_text,
            settings=app_settings,
        )
    except ResumeScoringError as exc:
        log.error("score.resume_error", error=exc.message)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Resume scoring failed: {exc.message}",
        ) from exc

    return ResumeScoreResponse(
        candidate_name=result.get("candidate_name", ""),
        candidate_email=result.get("candidate_email", ""),
        overall=result["overall"],
        breakdown=result["breakdown"],
        strengths=result["strengths"],
        concerns=result["concerns"],
        recommendation=result["recommendation"],
        summary=result["summary"],
    )


# ---------------------------------------------------------------------------
# AI exam-question generation (HR workflow — MCQ authoring)
# ---------------------------------------------------------------------------


class GenerateExamRequest(BaseModel):
    """Request body for POST /generate-exam — generate MCQs for a topic/role."""

    topic: str = Field(..., min_length=1, max_length=300, description="Subject or role")
    num_questions: int = Field(default=5, ge=1, le=30)
    difficulty: str = Field(default="medium", description="easy | medium | hard | mixed")
    language: str = Field(default="en", description="BCP-47: 'en', 'hi', or 'te'")


class GeneratedQuestion(BaseModel):
    prompt: str
    options: list[str]
    correct_index: int


class GenerateExamResponse(BaseModel):
    questions: list[GeneratedQuestion]


@router.post(
    "/generate-exam",
    status_code=status.HTTP_200_OK,
    response_model=GenerateExamResponse,
    summary="Generate MCQ exam questions for a topic via Gemini (stateless)",
)
async def internal_generate_exam(
    body: GenerateExamRequest,
    _jwt_payload: Annotated[dict[str, Any], Depends(_require_jwt)],
    app_settings: Annotated[Settings, Depends(_get_settings)],
) -> GenerateExamResponse:
    """Generate MCQs. Stateless — returns the questions for the caller to persist."""
    from app.exam_generator import ExamGenerationError, generate_exam_questions

    try:
        questions = await generate_exam_questions(
            topic=body.topic,
            num_questions=body.num_questions,
            difficulty=body.difficulty,
            language=body.language,
            settings=app_settings,
        )
    except ExamGenerationError as exc:
        log.error("score.generate_exam_error", error=exc.message)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Exam generation failed: {exc.message}",
        ) from exc

    return GenerateExamResponse(
        questions=[GeneratedQuestion(**q) for q in questions]
    )


# ---------------------------------------------------------------------------
# Semantic resume search (HR workflow) — embeddings + match explanation
# ---------------------------------------------------------------------------


class EmbedRequest(BaseModel):
    """Request body for POST /internal/embed — embed resumes or an HR query."""

    texts: list[str] = Field(..., min_length=1, max_length=64, description="Texts to embed")
    task_type: str = Field(
        default="document", description="'document' (resumes) | 'query' (search phrase)"
    )


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]


@router.post(
    "/embed",
    status_code=status.HTTP_200_OK,
    response_model=EmbedResponse,
    summary="Embed texts with Gemini for semantic resume search (stateless)",
)
async def internal_embed(
    body: EmbedRequest,
    _jwt_payload: Annotated[dict[str, Any], Depends(_require_jwt)],
    app_settings: Annotated[Settings, Depends(_get_settings)],
) -> EmbedResponse:
    """Return one embedding vector per input text (same order). Caller persists/queries."""
    from app.embedder import EmbeddingError, embed_texts

    try:
        vectors = await embed_texts(
            texts=body.texts, task_type=body.task_type, settings=app_settings
        )
    except EmbeddingError as exc:
        log.error("score.embed_error", error=exc.message)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Embedding failed: {exc.message}",
        ) from exc
    return EmbedResponse(embeddings=vectors)


class WhyMatchRequest(BaseModel):
    """Request body for POST /internal/why-match — explain a resume↔query match."""

    resume_text: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1, max_length=300)


class WhyMatchResponse(BaseModel):
    reason: str


@router.post(
    "/why-match",
    status_code=status.HTTP_200_OK,
    response_model=WhyMatchResponse,
    summary="One-sentence explanation of why a resume matches an HR query (stateless)",
)
async def internal_why_match(
    body: WhyMatchRequest,
    _jwt_payload: Annotated[dict[str, Any], Depends(_require_jwt)],
    app_settings: Annotated[Settings, Depends(_get_settings)],
) -> WhyMatchResponse:
    from app.embedder import EmbeddingError, generate_match_reason

    try:
        reason = await generate_match_reason(
            resume_text=body.resume_text, query=body.query, settings=app_settings
        )
    except EmbeddingError as exc:
        log.error("score.why_match_error", error=exc.message)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Match explanation failed: {exc.message}",
        ) from exc
    return WhyMatchResponse(reason=reason)
