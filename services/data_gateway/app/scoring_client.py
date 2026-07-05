"""Client for feedback_billing's resume ATS scorer (HR workflow — Phase 1).

data_gateway owns applicants but the Gemini scorer lives in feedback_billing.
We mint a short internal JWT (data_gateway is the issuer; feedback_billing
validates it with the shared secret) and POST the resume text to
/internal/score-resume. PII: resume text is sent over the internal network only,
never logged here.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from shared.auth.jwt import issue_access_token

from app.config import settings

log = structlog.get_logger(__name__)


class ResumeScoreError(Exception):
    """Raised when the resume scorer cannot be reached or returns an error."""


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


async def score_resume_remote(
    *,
    resume_text: str,
    job_title: str,
    level: str,
    jd_text: str | None,
    acting_user_id: str,
) -> dict[str, Any]:
    """ATS-score a resume via feedback_billing. Raises ResumeScoreError on failure."""
    url = f"{settings.feedback_billing_url}/internal/score-resume"
    token = _internal_token(acting_user_id)
    try:
        async with httpx.AsyncClient(timeout=75.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "resume_text": resume_text,
                    "job_title": job_title,
                    "level": level,
                    "jd_text": jd_text or "",
                },
            )
    except httpx.RequestError as exc:
        raise ResumeScoreError(f"resume scorer unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise ResumeScoreError(
            f"resume scorer returned HTTP {resp.status_code}: {resp.text[:160]}"
        )
    result: dict[str, Any] = resp.json()
    return result
