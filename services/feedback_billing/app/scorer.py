"""End-of-session scoring — S5-006.

Renders the scorer prompt via Jinja2, calls Gemini at temperature 0.2,
parses the JSON output, writes a scorecards row, returns the scorecard_id.

PII rules:
  - NEVER log transcript text or individual turn text.
  - Only log session_id, scorecard_id, composite_score, model.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from jinja2 import BaseLoader, Environment
from shared.intelligence import (
    RoleProfile,
    axis_weights,
    render_competency_output_spec,
    render_scoring_rubric_block,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCORER_VERSION: str = "1.0"

# Removes a trailing comma before a closing } or ] (invalid JSON Gemini sometimes
# emits): matches ",  }" / ",\n]" etc. and keeps just the bracket.
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")

# Transient Gemini HTTP statuses worth retrying: 429 rate-limit, plus gateway /
# overload errors (503 "high demand" is the common one on the free tier). A 4xx
# like 400/403 is a real problem (bad prompt / key) and is NOT retried.
_GEMINI_RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_GEMINI_MAX_ATTEMPTS: int = 4  # 1 initial + 3 retries
_GEMINI_BACKOFF_BASE_SECONDS: float = 1.0  # exponential: 1s, 2s, 4s

# Axis weights for composite score (LLD §10).
_WEIGHTS: dict[str, float] = {
    "communication": 0.30,
    "technical": 0.30,
    "problem_solving": 0.25,
    "confidence": 0.15,
}

# Language display names for the prompt (APSSDC Day-1 languages).
# Unknown codes fall back to "English" per spec.
_LANG_NAMES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi (Hinglish)",
    "te": "Telugu (Tenglish)",
}

# ---------------------------------------------------------------------------
# Prompt template (LLD §7.5 — Jinja2)
# ---------------------------------------------------------------------------

SCORER_PROMPT_TEMPLATE: str = """\
You are an expert assessor scoring a mock job interview transcript for APSSDC.

## Inputs
Job          : {{ job_title }}
Experience   : {{ experience_level }}
Language     : {{ lang_name }}

## Scoring axes (each 0-10, calibrated to tier)
1. Communication       — clarity, structure, fluency in {{ lang_name }}
2. Technical Knowledge — depth, correctness, NOS-aligned competency
3. Problem Solving     — reasoning quality, structured thinking, examples
4. Confidence          — composure, conviction, voice steadiness

## Calibration anchors
- 0-3  : Clear weakness; cannot perform at this tier.
- 4-5  : Below tier expectations; significant gaps.
- 6-7  : Meets tier expectations.
- 8-9  : Exceeds tier expectations.
- 10   : Exceptional performance.

## Output (STRICT JSON, no markdown, no code fences)
{
  "scores": {
    "communication":   <int 0-10>,
    "technical":       <int 0-10>,
    "problem_solving": <int 0-10>,
    "confidence":      <int 0-10>
  },
  "rationale": {
    "communication":   "<why this exact score>",
    "technical":       "<why this exact score>",
    "problem_solving": "<why this exact score>",
    "confidence":      "<why this exact score>"
  },
  "strengths":    [<string>, <string>, <string>],
  "improvements": [
    {"area": <string>, "suggestion": <string>},
    {"area": <string>, "suggestion": <string>},
    {"area": <string>, "suggestion": <string>}
  ],
  "summary": "<2-3 sentences — overall verdict, calibrated to tier>"
}

Rules:
- All output text in {{ lang_name }}.
- "improvements" must be actionable, not generic.
- "rationale": for EACH of the four axes write 3-5 sentences explaining WHY that
  exact score was given. You MUST: (a) cite specific evidence from the
  transcript — paraphrase what the candidate actually said or did (never quote
  PII); (b) name the calibration band it falls in (e.g. "meets tier
  expectations (6-7)"); (c) state concretely what the candidate would need to
  demonstrate to score higher. Ground every claim in the transcript — do NOT
  invent details that were not said. If the candidate barely spoke on an axis,
  say so explicitly and explain how that limited the score.

## Transcript
{% for turn in turns %}
[{{ turn.role | upper }}] {{ turn.text }}
{% endfor %}"""

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class ScoringError(Exception):
    """Raised when the scoring pipeline fails (Gemini error or JSON parse failure)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Jinja2 environment (sandboxed loader — no filesystem access needed)
# ---------------------------------------------------------------------------

_jinja_env: Environment = Environment(loader=BaseLoader(), autoescape=False)


def _render_prompt(
    *,
    job_title: str,
    experience_level: str,
    lang_name: str,
    turns: list[dict[str, str]],
) -> str:
    """Render the Jinja2 scorer prompt with the provided context."""
    template = _jinja_env.from_string(SCORER_PROMPT_TEMPLATE)
    return template.render(
        job_title=job_title,
        experience_level=experience_level,
        lang_name=lang_name,
        turns=turns,
    )


# The transcript is the LAST section of the rendered prompt, so role context
# has to be spliced in ABOVE it — appending would put the rubric after the
# transcript, where it reads as commentary on the conversation rather than as
# instructions governing it.
_TRANSCRIPT_MARKER: str = "## Transcript"


def _splice_role_context(rendered: str, block: str) -> str:
    """Insert ``block`` immediately before the transcript section."""
    idx = rendered.find(_TRANSCRIPT_MARKER)
    if idx == -1:  # template changed shape — appending still beats dropping it
        return f"{rendered}\n\n{block}"
    return f"{rendered[:idx]}{block}\n\n{rendered[idx:]}"


# ---------------------------------------------------------------------------
# Composite score formula (LLD §10)
# ---------------------------------------------------------------------------


def _compute_composite(
    scores: dict[str, int], weights: dict[str, float] | None = None
) -> float:
    """Return the weighted composite score, rounded to 2 decimal places.

    ``weights`` defaults to the fixed LLD §10 blend. When a role profile is
    available the caller passes per-role weights instead (see
    ``shared.intelligence.render.axis_weights``) — the four AXES are unchanged
    (they are persisted, charted and typed in the frontend), only their
    relative importance moves. A hands-on trade role should not have its
    composite driven 30% by how articulate the candidate was.
    """
    active = weights if weights is not None else _WEIGHTS
    return round(sum(active[k] * scores[k] for k in _WEIGHTS), 2)


def _clamp(value: int, lo: int = 0, hi: int = 10) -> int:
    """Clamp an integer to [lo, hi]."""
    return max(lo, min(hi, value))


def _extract_competency_breakdown(
    raw: dict[str, Any], profile: RoleProfile
) -> dict[str, dict[str, Any]]:
    """Pull the per-competency scores out of the model's response.

    Best-effort by design: this is supplementary evidence for a human reviewer,
    not a scoring input. Only competencies that are actually in the profile are
    kept (the model occasionally invents one), and a malformed entry is skipped
    rather than failing the scorecard.
    """
    breakdown: dict[str, dict[str, Any]] = {}
    for comp in profile.competencies:
        entry = raw.get(comp.id)
        if not isinstance(entry, dict):
            continue
        try:
            score = _clamp(int(entry.get("score", 0)))
        except (TypeError, ValueError):
            continue
        breakdown[comp.id] = {
            "name": comp.name,
            "weight": comp.weight,
            "score": score,
            "evidence": str(entry.get("evidence", ""))[:800],
        }
    return breakdown


# ---------------------------------------------------------------------------
# Public scoring function
# ---------------------------------------------------------------------------


async def score_session(
    *,
    session_id: str,
    job_title: str,
    experience_level: str,
    language: str,  # "en" | "hi" | "te"
    turns: list[dict[str, Any]],  # [{"role": "ai"|"user", "text": str}, ...]
    db_session: AsyncSession,
    settings: Settings,
    jd_text: str = "",
    candidate_name: str = "",
    db_session_factory: Any = None,
    role_profile: RoleProfile | None = None,
) -> tuple[str, dict[str, int], float]:
    """Score a completed interview session and persist the scorecard row.

    Args:
        session_id: UUID of the completed session.
        job_title: Job title for prompt calibration.
        experience_level: Experience tier (e.g. 'entry', 'mid', 'senior').
        language: BCP-47 language code ('en' | 'hi' | 'te').
        turns: Ordered list of conversation turns.
        db_session: Active async DB session for the INSERT.
        settings: Application settings (Gemini, S3 credentials, etc.).
        jd_text: Parsed job description text. Optional. When non-empty, a
                 capped (1200-char) JD section is appended to the prompt so
                 the scorer can calibrate technical-depth expectations against
                 the actual role requirements rather than just the title.
        candidate_name: Candidate's full name — used only in the PDF header.
                        Optional; PDF is skipped if not provided.
        db_session_factory: async_sessionmaker passed to the fire-and-forget
                            PDF task so it can open a fresh session for the
                            report_pdf_key UPDATE. Pass get_session_factory()
                            from the calling endpoint.
        role_profile: the role model the interview was conducted against
                      (shared.intelligence). When supplied it (a) tells the
                      scorer what the four axes MEAN for this role via
                      behaviourally-anchored competency descriptions, (b)
                      reweights the composite to the role, and (c) adds a
                      per-competency breakdown to ``rationale``. None
                      reproduces the pre-intelligence-layer behaviour exactly.

    Returns:
        Tuple of (scorecard_id, scores, composite_score) where:
          - scorecard_id: UUID string of the new scorecard row
          - scores: dict mapping axis names to int scores (0-10, clamped)
          - composite_score: weighted average (rounded to 2 dp)

    Raises:
        ScoringError: if Gemini returns non-200 or the JSON is unparseable.

    PII rules:
        NEVER log transcript text. Only log session_id, scorecard_id,
        composite_score, model.
    """
    # ---- 1. Render prompt -------------------------------------------------
    lang_name = _LANG_NAMES.get(language, "English")
    # Filter turns to non-empty text only; map role "ai" → "interviewer".
    safe_turns = [
        {"role": t["role"], "text": t["text"]}
        for t in turns
        if t.get("text", "").strip()
    ]
    rendered = _render_prompt(
        job_title=job_title,
        experience_level=experience_level,
        lang_name=lang_name,
        turns=safe_turns,
    )
    # Conditionally append the JD section (capped to 1200 chars to stay in
    # budget). Done outside the Jinja2 template to keep the template clean and
    # avoid {% if %} whitespace artefacts.
    if jd_text:
        rendered = _splice_role_context(
            rendered,
            "## Job Description (use to calibrate technical depth expectations)\n"
            + jd_text[:1200],
        )

    # Role model — spliced above the transcript so it governs the scoring
    # rather than reading as commentary on it. This is what stops "technical: 7"
    # meaning a generic (in practice, software-flavoured) notion of technical
    # skill for a welder or a staff nurse.
    active_weights = axis_weights(role_profile)
    if role_profile is not None:
        rendered = _splice_role_context(rendered, render_scoring_rubric_block(role_profile))
        rendered = _splice_role_context(
            rendered,
            "## Additional output\n" + render_competency_output_spec(role_profile),
        )

    # ---- 2. Call Gemini ---------------------------------------------------
    url = (
        f"{settings.gemini_api_base_url}"
        f"/models/{settings.gemini_model}:generateContent"
        f"?key={settings.gemini_api_key}"
    )
    generation_config: dict[str, Any] = {
        "temperature": 0.2,
        # Raised from 2048 to fit the larger output (scores + per-axis
        # rationale + strengths + improvements + summary). With JSON mode +
        # thinking disabled the whole budget goes to the JSON; a too-small
        # cap truncates it mid-string and the parse fails.
        "maxOutputTokens": 4096,
        # JSON mode (B-041) — forces well-formed, fence-free JSON. Without it
        # the scorer truncated/malformed its JSON and 502'd, so the candidate
        # never got a scorecard.
        "responseMimeType": "application/json",
    }
    # On Gemini 2.5 "thinking" models, hidden reasoning tokens count against
    # maxOutputTokens and can truncate the JSON mid-string. Disable thinking so
    # the whole budget is output. Guarded: pre-2.5 models 400 on thinkingConfig.
    if "2.5" in settings.gemini_model:
        generation_config["thinkingConfig"] = {"thinkingBudget": 0}
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": rendered}]}],
        "generationConfig": generation_config,
    }

    # Retry on transient failures (notably 503 "high demand" on the free tier)
    # with exponential backoff, so a momentary Gemini hiccup does not cost the
    # candidate their scorecard. Non-transient errors (bad key/prompt) fail fast.
    response = None
    last_error = "no attempt made"
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(_GEMINI_MAX_ATTEMPTS):
            try:
                response = await client.post(url, json=body)
            except httpx.RequestError as exc:
                response = None
                last_error = f"request error: {exc}"
            else:
                if response.status_code == 200:
                    break
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                if response.status_code not in _GEMINI_RETRY_STATUSES:
                    break  # non-transient (e.g. 400/403) — do not retry
            if attempt < _GEMINI_MAX_ATTEMPTS - 1:
                backoff = _GEMINI_BACKOFF_BASE_SECONDS * (2**attempt)
                log.warning(
                    "score.gemini_retry",
                    attempt=attempt + 1,
                    max_attempts=_GEMINI_MAX_ATTEMPTS,
                    backoff_s=backoff,
                    error=last_error,
                )
                await asyncio.sleep(backoff)

    if response is None or response.status_code != 200:
        raise ScoringError(
            f"Gemini call failed after {_GEMINI_MAX_ATTEMPTS} attempt(s): {last_error}"
        )

    # ---- 3. Parse response -----------------------------------------------
    try:
        resp_data: dict[str, Any] = response.json()
        raw_text: str = resp_data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise ScoringError(f"Failed to extract text from Gemini response: {exc}") from exc

    # Strip markdown fences if the model wrapped its JSON.
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        # Remove opening ```json or ``` fence, then closing ```, then whitespace.
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    # Gemini occasionally emits a trailing comma before a closing } or ] which is
    # invalid JSON — strip it so a recoverable response doesn't 502 the scorer.
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", cleaned)

    try:
        parsed: dict[str, Any] = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ScoringError(f"Gemini response was not valid JSON: {exc}") from exc

    # ---- 4. Validate and clamp scores ------------------------------------
    raw_scores: dict[str, Any] = parsed.get("scores", {})
    required_axes = list(_WEIGHTS.keys())
    for axis in required_axes:
        if axis not in raw_scores:
            raise ScoringError(f"Gemini response missing score axis: {axis!r}")

    scores: dict[str, int] = {
        axis: _clamp(int(raw_scores[axis])) for axis in required_axes
    }

    strengths: list[Any] = parsed.get("strengths", [])
    improvements: list[Any] = parsed.get("improvements", [])
    summary: str = parsed.get("summary", "")

    if not summary:
        raise ScoringError("Gemini response missing 'summary' field")

    # Per-axis rationale ("why this score"). Best-effort: a model that omits it
    # (or omits an axis) must not fail scoring — missing axes become "".
    raw_rationale: dict[str, Any] = parsed.get("rationale", {}) or {}
    rationale: dict[str, str] = {
        axis: str(raw_rationale.get(axis, "")) for axis in required_axes
    }

    # Per-competency breakdown — stored INSIDE the existing rationale JSONB
    # rather than as a new column. The four canonical axes stay exactly as they
    # were (the admin analytics SQL aggregates them by name and the frontend
    # types them), so this is purely additive: no migration, no breakage for a
    # scorecard written before the intelligence layer.
    if role_profile is not None:
        rationale["_role_profile_id"] = role_profile.profile_id
        rationale["_domain_family"] = role_profile.domain_family
        raw_comps = parsed.get("competencies")
        if isinstance(raw_comps, dict):
            breakdown = _extract_competency_breakdown(raw_comps, role_profile)
            if breakdown:
                rationale["_competencies"] = json.dumps(breakdown, ensure_ascii=False)

    # ---- 5. Compute composite score --------------------------------------
    composite = _compute_composite(scores, active_weights)

    # ---- 6. Persist scorecard row ----------------------------------------
    # Import here to avoid circular — the Scorecard model lives in data_gateway
    # but feedback_billing uses its own shared DB session pointing at the same DB.
    # We do a raw INSERT via SQLAlchemy core to avoid an ORM model dependency
    # on data_gateway (different service boundary). Using the shared JSONB column
    # via text() would require raw SQL — instead we use a lightweight dataclass
    # approach with SQLAlchemy insert().
    from sqlalchemy import text as sa_text  # noqa: PLC0415

    scorecard_id = str(uuid.uuid4())
    created_at = datetime.now(tz=UTC)

    await db_session.execute(
        sa_text(
            """
            INSERT INTO scorecards
                (scorecard_id, session_id, scores, composite_score,
                 rationale, strengths, improvements, summary, lang,
                 scorer_model, scorer_version, created_at)
            VALUES
                (:scorecard_id, :session_id, CAST(:scores AS jsonb), :composite_score,
                 CAST(:rationale AS jsonb), CAST(:strengths AS jsonb),
                 CAST(:improvements AS jsonb), :summary, :lang,
                 :scorer_model, :scorer_version, :created_at)
            """
        ),
        {
            "scorecard_id": scorecard_id,
            "session_id": session_id,
            "scores": json.dumps(scores),
            "composite_score": composite,
            "rationale": json.dumps(rationale),
            "strengths": json.dumps(strengths),
            "improvements": json.dumps(improvements),
            "summary": summary,
            "lang": language,
            "scorer_model": settings.gemini_model,
            "scorer_version": SCORER_VERSION,
            "created_at": created_at,
        },
    )
    await db_session.commit()

    log.info(
        "scorer.complete",
        session_id=session_id,
        scorecard_id=scorecard_id,
        composite_score=composite,
        model=settings.gemini_model,
        # Rubric provenance — lets any score be traced back to the exact role
        # model that produced it (the audit story for a government bid).
        role_profile_id=role_profile.profile_id if role_profile else None,
        domain_family=role_profile.domain_family if role_profile else None,
        profile_source=role_profile.source if role_profile else None,
        # NEVER log transcript text — PII.
    )

    # ---- 7. Fire-and-forget PDF generation ----------------------------------
    # Only attempt if we have a candidate name and S3 credentials are configured.
    if candidate_name and settings.s3_access_key_id:
        from app.pdf_render import (
            render_scorecard_pdf,  # local import — avoids circular  # noqa: PLC0415
        )

        asyncio.create_task(
            render_scorecard_pdf(
                scorecard_id,
                session_id,
                candidate_name,
                job_title,
                language,
                scores,
                composite,
                [str(s) for s in strengths],
                [
                    {"area": str(i.get("area", "")), "suggestion": str(i.get("suggestion", ""))}
                    for i in improvements
                ],
                summary,
                settings=settings,
                db_session_factory=db_session_factory,
            )
        )

    return scorecard_id, scores, composite
