"""Resume ATS scoring — HR workflow Phase 1.

Scores an applicant's resume against a role using Gemini (same provider + retry
+ JSON-mode pattern as the interview scorer). STATELESS: returns the result; the
caller (data_gateway HR endpoints) persists it on the applicant row.

PII: NEVER log resume text. Only log job_title + overall score.
"""

from __future__ import annotations

from typing import Any

import structlog
from shared.llm import call_gemini_json

from app.config import Settings
from app.untrusted_input import frame_untrusted, frame_untrusted_inline, scan_untrusted

log = structlog.get_logger(__name__)

RESUME_SCORER_VERSION: str = "1.0"

# Transport, retry and JSON recovery live in shared.llm.gemini — this module
# owns only the prompt, the output contract and the clamping below.

# Generous budget so the structured JSON body is never truncated.
_MAX_OUTPUT_TOKENS: int = 4096
# ATS scores feed an HR shortlist, so they must be repeatable run to run.
_TEMPERATURE: float = 0.2
_TIMEOUT_SECONDS: float = 60.0

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
    # Both inputs are written outside this organisation: the resume by the
    # candidate being scored, the JD by whoever uploaded it. Truncate first, then
    # scan and frame, so what we inspect is exactly what the model will see —
    # scanning the full text while sending a truncated copy (or vice versa) makes
    # the finding describe a different string than the one that was scored.
    resume_excerpt = resume_text[:8000]
    jd_excerpt = jd_text[:1200]
    injection_markers = scan_untrusted(
        {
            "resume": resume_excerpt,
            "jd": jd_excerpt,
            # job_title and level are HR-controlled and length-capped at the API
            # boundary (routers/score.py), so they are the lowest-risk strings
            # in this prompt — but they ARE substituted into it, and they used
            # to reach this call as log context only. A `job_title=` keyword
            # sitting next to a scan reads exactly like a scanned field; it was
            # not one, which is the sort of gap that survives review.
            "job_title": job_title,
            "level": level,
        },
        event="resume_scorer.injection_markers_detected",
        # Still log context too: the marker list says WHICH field tripped, this
        # says which posting it was. A job title is not candidate PII (this
        # module already logs it on the success path).
        job_title=job_title,
    )

    jd_block = (
        "Job description (calibrate skill/experience expectations against this):\n"
        + frame_untrusted(jd_excerpt, label="JOB DESCRIPTION")
        if jd_text
        else ""
    )
    # Title/level get the inline treatment, not the block treatment — see
    # frame_untrusted_inline for why a "## Role" header block is the one place
    # where full BEGIN/END delimiters would cost more than they buy.
    prompt = (
        _PROMPT_TEMPLATE.replace("{{JOB_TITLE}}", frame_untrusted_inline(job_title))
        .replace("{{LEVEL}}", frame_untrusted_inline(level))
        .replace("{{JD_BLOCK}}", jd_block)
        .replace(
            "{{RESUME_TEXT}}", frame_untrusted(resume_excerpt, label="RESUME")
        )
    )

    # One shared caller for every Gemini JSON request in this service
    # (shared.llm.gemini): header auth (never ?key=, so the key stays out of
    # request URLs and proxy logs), the retry ladder, and a JSON-recovery ladder
    # strictly wider than the one this module used to carry — it adds
    # finishReason diagnosis and a json_repair rung on top of the fence /
    # brace-span / trailing-comma handling that was here.
    parsed: dict[str, Any] = await call_gemini_json(
        prompt,
        api_base_url=settings.gemini_api_base_url,
        model=settings.gemini_model,
        api_key=settings.gemini_api_key,
        temperature=_TEMPERATURE,
        max_output_tokens=_MAX_OUTPUT_TOKENS,
        timeout=_TIMEOUT_SECONDS,
        # data_gateway's HR endpoints catch ResumeScoringError by name — the
        # exception types stay per-module, only the plumbing is shared.
        error_cls=ResumeScoringError,
    )

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
        # Travels with the score so HR sees "this CV contained instructions
        # aimed at the scorer" next to the number it may have influenced.
        # Empty list on the normal path — callers can persist it unconditionally.
        "injection_markers": injection_markers,
    }
    log.info(
        "resume_scorer.complete",
        job_title=job_title,
        overall=overall,
        model=settings.gemini_model,
        injection_marker_count=len(injection_markers),
    )
    return result
