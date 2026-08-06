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
