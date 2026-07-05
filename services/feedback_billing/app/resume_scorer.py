"""Resume ATS scoring — HR workflow Phase 1.

Scores an applicant's resume against a role using Gemini (same provider + retry
+ JSON-mode pattern as the interview scorer). STATELESS: returns the result; the
caller (data_gateway HR endpoints) persists it on the applicant row.

PII: NEVER log resume text. Only log job_title + overall score.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
import structlog

from app.config import Settings

log = structlog.get_logger(__name__)

RESUME_SCORER_VERSION: str = "1.0"

# Strips a trailing comma before a closing } or ] (invalid JSON Gemini sometimes emits).
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")

_RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS: int = 4
_BACKOFF_BASE_SECONDS: float = 1.0
_AXES: tuple[str, ...] = (
    "skills_match",
    "experience_relevance",
    "education_fit",
    "role_alignment",
)
_RECOMMENDATIONS: frozenset[str] = frozenset({"strong_fit", "moderate_fit", "weak_fit"})

# Placeholders use {{NAME}} markers (substituted via str.replace) so the literal
# JSON braces in the template need no escaping.
_PROMPT_TEMPLATE: str = """\
You are an expert technical recruiter / ATS scoring a candidate's RESUME against
a specific role. Judge how well the resume fits the role. Be objective and ground
EVERY judgement in the resume text — never invent experience that is not present.

## Role
Title : {{JOB_TITLE}}
Level : {{LEVEL}}
{{JD_BLOCK}}

## Sub-scores (each 0-100)
- skills_match         : do the candidate's skills match the role's requirements?
- experience_relevance : is their work experience relevant + sufficient for the level?
- education_fit        : education / certifications appropriate for the role?
- role_alignment       : overall trajectory and intent alignment with this role.

## Output STRICT JSON (no markdown, no code fences)
{
  "candidate_name": "<the candidate's full name from the resume, or empty string>",
  "candidate_email": "<the candidate's email from the resume, or empty string>",
  "overall": <int 0-100>,
  "breakdown": {
    "skills_match": <int 0-100>,
    "experience_relevance": <int 0-100>,
    "education_fit": <int 0-100>,
    "role_alignment": <int 0-100>
  },
  "strengths": [<string>, <string>, <string>],
  "concerns": [<string>, <string>, <string>],
  "recommendation": "strong_fit" | "moderate_fit" | "weak_fit",
  "summary": "<2-3 sentence verdict grounded in the resume>"
}

Rules:
- Extract candidate_name and candidate_email verbatim from the resume (usually
  the header). Use an empty string if absent — never invent them.
- "overall" is a HOLISTIC fit score (not merely the average of sub-scores).
- Ground all claims in the resume; if key info is missing, say so and let it
  lower the relevant sub-score.
- "concerns" = real gaps vs THIS role (missing skills, thin/irrelevant
  experience, etc.) — not generic filler.
- Be calibrated: a generic or unrelated resume scores low; a strong, on-target
  resume scores high.

## Resume
{{RESUME_TEXT}}"""


class ResumeScoringError(Exception):
    """Raised when the resume scoring pipeline fails (Gemini error or bad JSON)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _clamp(value: Any) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = 0
    return max(0, min(100, v))


async def score_resume(
    *,
    resume_text: str,
    job_title: str,
    level: str = "mid",
    jd_text: str = "",
    settings: Settings,
) -> dict[str, Any]:
    """ATS-score *resume_text* against a role. Returns a dict; never persists."""
    jd_block = (
        f"Job description (calibrate skill/experience expectations against this):\n{jd_text[:1200]}"
        if jd_text
        else ""
    )
    prompt = (
        _PROMPT_TEMPLATE.replace("{{JOB_TITLE}}", job_title)
        .replace("{{LEVEL}}", level)
        .replace("{{JD_BLOCK}}", jd_block)
        .replace("{{RESUME_TEXT}}", resume_text[:8000])
    )

    url = (
        f"{settings.gemini_api_base_url}"
        f"/models/{settings.gemini_model}:generateContent"
        f"?key={settings.gemini_api_key}"
    )
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            # Generous budget so the structured JSON body is never truncated
            # (thinking-capable models spend part of this on reasoning).
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
        },
    }

    response = None
    last_error = "no attempt made"
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = await client.post(url, json=body)
            except httpx.RequestError as exc:
                response = None
                last_error = f"request error: {exc}"
            else:
                if response.status_code == 200:
                    break
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code not in _RETRY_STATUSES:
                    break
            if attempt < _MAX_ATTEMPTS - 1:
                await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2**attempt))

    if response is None or response.status_code != 200:
        raise ResumeScoringError(
            f"Gemini call failed after {_MAX_ATTEMPTS} attempt(s): {last_error}"
        )

    try:
        raw_text: str = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise ResumeScoringError(f"Failed to read Gemini response: {exc}") from exc

    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    # Tolerate any prose the model wraps around the object: parse the outermost
    # {...} span. With responseMimeType=json this is usually a no-op, but it
    # prevents brittle failures when the model prepends a stray token.
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    # Tolerate a trailing comma before a closing } or ] (invalid JSON).
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", cleaned)
    try:
        parsed: dict[str, Any] = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ResumeScoringError(f"Gemini response was not valid JSON: {exc}") from exc

    raw_breakdown: dict[str, Any] = parsed.get("breakdown", {}) or {}
    breakdown = {axis: _clamp(raw_breakdown.get(axis, 0)) for axis in _AXES}
    overall = _clamp(parsed.get("overall", 0))
    recommendation = str(parsed.get("recommendation", "moderate_fit"))
    if recommendation not in _RECOMMENDATIONS:
        recommendation = "moderate_fit"

    result = {
        "candidate_name": str(parsed.get("candidate_name", "")).strip(),
        "candidate_email": str(parsed.get("candidate_email", "")).strip(),
        "overall": overall,
        "breakdown": breakdown,
        "strengths": [str(s) for s in (parsed.get("strengths") or [])][:5],
        "concerns": [str(c) for c in (parsed.get("concerns") or [])][:5],
        "recommendation": recommendation,
        "summary": str(parsed.get("summary", "")),
        "scorer_version": RESUME_SCORER_VERSION,
    }
    log.info("resume_scorer.complete", job_title=job_title, overall=overall, model=settings.gemini_model)
    return result
