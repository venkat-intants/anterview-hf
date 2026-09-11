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
from shared.auth.jwt import SERVICE_TOKEN_TTL_SECONDS, issue_access_token

from app.config import settings
from app.remote import describe_unreachable

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
        # 60s, not the 15-minute user default. A service token's `sub` is a
        # service name, so logout_all (which only ever writes
        # auth_epoch:<user-uuid>) cannot revoke it — its lifetime is its only
        # containment. interview_core's minter always used 60s; these three
        # silently used 900s because that was issue_access_token's default and
        # there was no parameter to say otherwise.
        ttl_seconds=SERVICE_TOKEN_TTL_SECONDS,
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
        raise ResumeScoreError(
            describe_unreachable(exc, what="Resume scoring", url=url)
        ) from exc
    if resp.status_code != 200:
        raise ResumeScoreError(
            f"resume scorer returned HTTP {resp.status_code}: {resp.text[:160]}"
        )
    result: dict[str, Any] = resp.json()
    return result


class InterviewScoreError(Exception):
    """Raised when an interview re-score or PDF render failed for THIS item."""


class ScoringServiceUnavailableError(InterviewScoreError):
    """feedback_billing is unreachable, or cannot do this kind of work at all
    (storage not configured). A condition of the service, not of the item —
    so the reconciler stops the pass rather than charging an attempt to every
    row it would have tried. Charging them would park every interview and every
    scorecard for good after an outage of an hour or two."""


# /internal/score makes a Gemini call with its own timeout and retries, so the
# reconciler waits far longer than the live worker's 15s. A timeout here does
# not mean the scorecard was lost: the scorer keeps going, and if it lands, the
# next pass finds the session scored and moves on.
_INTERVIEW_SCORE_TIMEOUT = 180.0
_PDF_RENDER_TIMEOUT = 60.0


async def score_interview_remote(payload: dict[str, Any]) -> str:
    """Score a finished interview via feedback_billing.

    Returns "created", or "exists" when a scorecard for the session was already
    written (HTTP 409 — the UNIQUE(session_id) guard, which is what makes a
    retry safe). Raises InterviewScoreError on anything else. The transcript in
    ``payload`` is PII and is never logged here.
    """
    url = f"{settings.feedback_billing_url}/internal/score"
    token = _internal_token("reconciler")
    try:
        async with httpx.AsyncClient(timeout=_INTERVIEW_SCORE_TIMEOUT) as client:
            resp = await client.post(
                url, headers={"Authorization": f"Bearer {token}"}, json=payload
            )
    except httpx.RequestError as exc:
        raise ScoringServiceUnavailableError(
            describe_unreachable(exc, what="Interview scoring", url=url)
        ) from exc
    if resp.status_code == 201:
        return "created"
    if resp.status_code == 409:
        return "exists"
    if resp.status_code in (401, 403, 503):
        # Auth misconfiguration or a service that is up but not serving: no
        # attempt on any session would behave differently.
        raise ScoringServiceUnavailableError(
            f"interview scorer returned HTTP {resp.status_code}: {resp.text[:160]}"
        )
    raise InterviewScoreError(
        f"interview scorer returned HTTP {resp.status_code}: {resp.text[:160]}"
    )


async def render_scorecard_pdf_remote(scorecard_id: str) -> dict[str, Any]:
    """Ask feedback_billing to render a scorecard's missing PDF.

    Returns the response body — ``{"status": "rendered" | "already_present" |
    "not_applicable", "reason": ...}``. Raises InterviewScoreError on a
    transport failure or any non-200.
    """
    url = f"{settings.feedback_billing_url}/internal/scorecards/{scorecard_id}/pdf"
    token = _internal_token("reconciler")
    try:
        async with httpx.AsyncClient(timeout=_PDF_RENDER_TIMEOUT) as client:
            resp = await client.post(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.RequestError as exc:
        raise ScoringServiceUnavailableError(
            describe_unreachable(exc, what="Scorecard PDF", url=url)
        ) from exc
    if resp.status_code in (401, 403, 503):
        # 503 is "scorecard storage is not configured" — true of every
        # scorecard until someone configures it, and then true of none.
        raise ScoringServiceUnavailableError(
            f"scorecard PDF render returned HTTP {resp.status_code}: {resp.text[:160]}"
        )
    if resp.status_code != 200:
        raise InterviewScoreError(
            f"scorecard PDF render returned HTTP {resp.status_code}: {resp.text[:160]}"
        )
    result: dict[str, Any] = resp.json()
    return result
