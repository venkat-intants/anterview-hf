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


def _internal_token(acting_user_id: str) -> str:
    # feedback_billing's /internal/* gate (_require_service_jwt) requires the
    # "service" role — a plain user/guest token is rejected 403. Mint with it.
    return issue_access_token(
        user_id=acting_user_id,
        roles=["service"],
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )


async def generate_exam_questions_remote(
    *,
    topic: str,
    num_questions: int,
    difficulty: str,
    language: str,
    acting_user_id: str,
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
