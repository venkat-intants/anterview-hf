"""Client for feedback_billing's AI exam-question generator (HR MCQ authoring).

data_gateway owns exams but the Gemini generator lives in feedback_billing. We
mint a short internal JWT (data_gateway is the issuer; feedback_billing validates
it with the shared secret) and POST the generation params to
/internal/generate-exam. The generated questions are returned for the HR endpoint
to validate + persist. PII: nothing sensitive is sent — only topic/role text.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from shared.auth.jwt import issue_access_token

from app.config import settings

log = structlog.get_logger(__name__)


class ExamGenerationError(Exception):
    """Raised when the AI generator cannot be reached or returns an error."""


# The service identity this process presents to feedback_billing. It must be
# in feedback_billing's _ALLOWED_SERVICE_SUBS.
_SERVICE_SUB = "data_gateway"


def _internal_token(acting_user_id: str) -> str:
    """Mint the service token for a feedback_billing /internal/* call.

    `sub` is the SERVICE, not the human who triggered the call. It used to be
    the acting HR user's UUID, which made a service identity and a user
    identity indistinguishable on the wire — both signed with the same
    jwt_secret, differing only by a roles claim. feedback_billing pins the
    allowed subjects, and a per-user UUID cannot be pinned.

    The human is carried in `act_sub` purely for attribution in the callee's
    logs. It is NOT authoritative and must never be used for an authorisation
    decision — the caller controls it, and the caller is the one being
    authorised.
    """
    return issue_access_token(
        user_id=_SERVICE_SUB,
        roles=["service"],
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        extra_claims={"act_sub": acting_user_id},
    )


async def generate_exam_questions_remote(
    *,
    topic: str,
    num_questions: int,
    difficulty: str,
    language: str,
    acting_user_id: str,
    job_title: str = "",
    experience_level: str = "mid",
) -> list[dict[str, Any]]:
    """Generate MCQs via feedback_billing. Raises ExamGenerationError on failure.

    Returns a list of {"prompt", "options", "correct_index"} dicts.
    """
    url = f"{settings.feedback_billing_url}/internal/generate-exam"
    token = _internal_token(acting_user_id)
    try:
        async with httpx.AsyncClient(timeout=100.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "topic": topic,
                    "num_questions": num_questions,
                    "difficulty": difficulty,
                    "language": language,
                    # Role context — lets the generator spread questions across
                    # the competencies this role is assessed on, matching the
                    # interview. Empty job_title = topic-only, as before.
                    "job_title": job_title,
                    "experience_level": experience_level,
                },
            )
    except httpx.RequestError as exc:
        raise ExamGenerationError(f"exam generator unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise ExamGenerationError(
            f"exam generator returned HTTP {resp.status_code}: {resp.text[:160]}"
        )
    data: dict[str, Any] = resp.json()
    questions: list[dict[str, Any]] = data.get("questions") or []
    return questions


async def generate_coding_questions_remote(
    *,
    topic: str,
    num_questions: int,
    difficulty: str,
    language: str,
    allowed_languages: list[str],
    acting_user_id: str,
    job_title: str = "",
    experience_level: str = "mid",
) -> list[dict[str, Any]]:
    """Generate coding problems via feedback_billing. Raises ExamGenerationError.

    Returns a list of {"prompt", "reference_solution", "test_cases"} dicts where
    test_cases items are {"stdin", "expected_output", "is_sample", "weight"}.
    """
    url = f"{settings.feedback_billing_url}/internal/generate-coding"
    token = _internal_token(acting_user_id)
    try:
        # Coding generation emits far more tokens than MCQs — allow extra time.
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "topic": topic,
                    "num_questions": num_questions,
                    "difficulty": difficulty,
                    "language": language,
                    "allowed_languages": allowed_languages,
                    "job_title": job_title,
                    "experience_level": experience_level,
                },
            )
    except httpx.RequestError as exc:
        raise ExamGenerationError(f"coding generator unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise ExamGenerationError(
            f"coding generator returned HTTP {resp.status_code}: {resp.text[:160]}"
        )
    data: dict[str, Any] = resp.json()
    questions: list[dict[str, Any]] = data.get("questions") or []
    return questions
