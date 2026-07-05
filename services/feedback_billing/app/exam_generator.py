"""AI exam-question generation — HR workflow (MCQ authoring).

Generates multiple-choice questions for an HR exam using Gemini (same provider +
retry + JSON-mode pattern as the resume/interview scorers). STATELESS: returns
the generated questions; the caller (data_gateway HR endpoints) validates and
persists them.

Each question has exactly 4 options and one correct answer. Day-1 languages
(EN / HI / TE) are supported via the ``language`` parameter — the prompt asks
Gemini to write the prompt + options in that language.
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

EXAM_GENERATOR_VERSION: str = "1.0"

# Strips a trailing comma before a closing } or ] (invalid JSON Gemini sometimes emits).
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")

_RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS: int = 4
_BACKOFF_BASE_SECONDS: float = 1.0

_OPTIONS_PER_QUESTION: int = 4
_MAX_QUESTIONS: int = 30  # hard cap — guards token budget + abuse
_VALID_DIFFICULTIES: frozenset[str] = frozenset({"easy", "medium", "hard", "mixed"})
# BCP-47 -> human name for the prompt. Day-1 languages plus a graceful default.
_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi (हिन्दी)",
    "te": "Telugu (తెలుగు)",
}

_PROMPT_TEMPLATE: str = """\
You are an expert exam author creating MULTIPLE-CHOICE questions (MCQs) for a
hiring / skills assessment. Write clear, unambiguous questions that test real
understanding — not trivia or trick questions.

## Assessment
Topic / role : {{TOPIC}}
Difficulty   : {{DIFFICULTY}}
Language     : {{LANGUAGE}}
How many     : {{COUNT}} questions

## Rules
- Write EVERY question prompt and ALL options in {{LANGUAGE}}.
- Each question MUST have EXACTLY 4 options.
- EXACTLY ONE option is correct. "correct_index" is its 0-based position (0-3).
- Vary the position of the correct answer across questions (do not always use 0).
- Options must be plausible and mutually exclusive; no "All of the above" /
  "None of the above"; no duplicate options.
- Keep each prompt self-contained (no "as shown above" / external references).
- Match the requested difficulty. For "mixed", spread across easy/medium/hard.

## Output STRICT JSON (no markdown, no code fences)
{
  "questions": [
    {
      "prompt": "<the question text>",
      "options": ["<opt 0>", "<opt 1>", "<opt 2>", "<opt 3>"],
      "correct_index": <int 0-3>
    }
  ]
}

Return EXACTLY {{COUNT}} questions. Output ONLY the JSON object."""


class ExamGenerationError(Exception):
    """Raised when the generation pipeline fails (Gemini error or bad JSON)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _normalise_question(raw: Any) -> dict[str, Any] | None:
    """Validate + clean one raw question dict. Returns None to drop a bad row."""
    if not isinstance(raw, dict):
        return None
    prompt = str(raw.get("prompt", "")).strip()
    options_raw = raw.get("options")
    if not prompt or not isinstance(options_raw, list):
        return None
    options = [str(o).strip() for o in options_raw]
    options = [o for o in options if o]
    if len(options) != _OPTIONS_PER_QUESTION:
        return None
    if len(set(options)) != _OPTIONS_PER_QUESTION:  # reject duplicate options
        return None
    try:
        correct_index = int(raw.get("correct_index"))
    except (TypeError, ValueError):
        return None
    if not (0 <= correct_index < _OPTIONS_PER_QUESTION):
        return None
    return {"prompt": prompt[:2000], "options": options, "correct_index": correct_index}


async def generate_exam_questions(
    *,
    topic: str,
    num_questions: int,
    difficulty: str = "medium",
    language: str = "en",
    settings: Settings,
) -> list[dict[str, Any]]:
    """Generate MCQs for *topic*. Returns a list of validated question dicts.

    Each dict: {"prompt": str, "options": [4 strings], "correct_index": int 0-3}.
    Raises ExamGenerationError on Gemini failure / unparseable output / zero valid
    questions.
    """
    count = max(1, min(_MAX_QUESTIONS, int(num_questions)))
    difficulty = difficulty if difficulty in _VALID_DIFFICULTIES else "medium"
    language_name = _LANGUAGE_NAMES.get(language.lower(), "English")

    prompt = (
        _PROMPT_TEMPLATE.replace("{{TOPIC}}", topic[:300])
        .replace("{{DIFFICULTY}}", difficulty)
        .replace("{{LANGUAGE}}", language_name)
        .replace("{{COUNT}}", str(count))
    )

    url = (
        f"{settings.gemini_api_base_url}"
        f"/models/{settings.gemini_model}:generateContent"
        f"?key={settings.gemini_api_key}"
    )
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,  # some variety across generations
            # Generous budget — 4 options * N questions of JSON, plus the model's
            # private reasoning tokens. Scales with the question count.
            "maxOutputTokens": min(8192, 1024 + count * 256),
            "responseMimeType": "application/json",
        },
    }

    response = None
    last_error = "no attempt made"
    async with httpx.AsyncClient(timeout=90.0) as client:
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
        raise ExamGenerationError(
            f"Gemini call failed after {_MAX_ATTEMPTS} attempt(s): {last_error}"
        )

    try:
        raw_text: str = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise ExamGenerationError(f"Failed to read Gemini response: {exc}") from exc

    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", cleaned)

    try:
        parsed: dict[str, Any] = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ExamGenerationError(f"Gemini response was not valid JSON: {exc}") from exc

    raw_questions = parsed.get("questions")
    if not isinstance(raw_questions, list):
        raise ExamGenerationError("Gemini response had no 'questions' array.")

    questions = [q for q in (_normalise_question(r) for r in raw_questions) if q is not None]
    if not questions:
        raise ExamGenerationError("Gemini returned no usable questions.")

    log.info(
        "exam_generator.complete",
        topic=topic[:80],
        requested=count,
        produced=len(questions),
        difficulty=difficulty,
        language=language,
        model=settings.gemini_model,
    )
    return questions[:count]
