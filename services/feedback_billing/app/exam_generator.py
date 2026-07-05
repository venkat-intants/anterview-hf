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

    parsed = await _call_gemini_json(
        prompt, settings, max_output_tokens=min(8192, 2048 + count * 256)
    )

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


async def _call_gemini_json(
    prompt: str, settings: Settings, *, max_output_tokens: int
) -> dict[str, Any]:
    """POST *prompt* to Gemini in JSON mode with retry; return the parsed object.

    Shared by the MCQ and coding generators. Raises ExamGenerationError on
    transport failure, unreadable response, or invalid JSON.
    """
    url = (
        f"{settings.gemini_api_base_url}"
        f"/models/{settings.gemini_model}:generateContent"
        f"?key={settings.gemini_api_key}"
    )
    generation_config: dict[str, Any] = {
        "temperature": 0.7,  # some variety across generations
        # Pure JSON output budget (thinking is disabled below on 2.5 models).
        "maxOutputTokens": max_output_tokens,
        "responseMimeType": "application/json",
    }
    # Gemini 2.5 models are "thinking" models: their hidden reasoning tokens
    # count against maxOutputTokens and on generation-heavy prompts can consume
    # nearly the whole budget, truncating the JSON mid-string (seen live on the
    # HF Space: output died at char 41 → "Unterminated string"). Structured
    # authoring gains nothing from private reasoning, so spend the entire budget
    # on output. thinkingConfig is only accepted by 2.5-family models — older
    # models reject the field with HTTP 400, hence the version guard.
    if "2.5" in settings.gemini_model:
        generation_config["thinkingConfig"] = {"thinkingBudget": 0}
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
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
        candidate: dict[str, Any] = response.json()["candidates"][0]
        raw_text: str = candidate["content"]["parts"][0]["text"]
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise ExamGenerationError(f"Failed to read Gemini response: {exc}") from exc
    # Surfaced in parse errors: MAX_TOKENS here means the JSON was truncated by
    # the output budget — raise the budget rather than blaming the model output.
    finish_reason = str(candidate.get("finishReason", ""))

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
        # Even in JSON mode Gemini intermittently emits invalid escapes / raw
        # newlines inside strings — especially when the payload embeds source
        # code (coding questions). json_repair salvages those; per-question
        # validation downstream still drops anything structurally unusable.
        # Deliberately NOT attempted on MAX_TOKENS truncation: "repairing" a
        # cut-off payload fabricates half-empty questions — report it instead.
        repaired = None
        if finish_reason != "MAX_TOKENS":
            try:
                import json_repair

                repaired = json_repair.repair_json(cleaned, return_objects=True)
            except Exception:  # noqa: BLE001 — fall through to the original error
                repaired = None
        if isinstance(repaired, dict) and repaired:
            log.warning("exam_generator.json_repaired", parse_error=str(exc)[:120])
            return repaired
        hint = (
            f" (finishReason={finish_reason} — output was truncated)"
            if finish_reason == "MAX_TOKENS"
            else ""
        )
        raise ExamGenerationError(
            f"Gemini response was not valid JSON{hint}: {exc}"
        ) from exc
    return parsed


# ---------------------------------------------------------------------------
# Coding-question generation (HR workflow — coding-round authoring)
# ---------------------------------------------------------------------------

# Per-generation cap: coding problems are token-heavy (statement + reference
# solution + test cases ≈ 1.5-2.5k output tokens each), so this is deliberately
# lower than the MCQ cap. data_gateway also caps questions per exam at 20.
_MAX_CODING_QUESTIONS: int = 8
_MAX_TEST_CASES: int = 20
_MAX_CASE_TEXT: int = 20_000

_CODING_PROMPT_TEMPLATE: str = """\
You are an expert competitive-programming problem setter creating CODING
problems for a hiring / skills assessment. Problems are auto-graded by running
the candidate's program with a test case's stdin and comparing the program's
stdout to the expected output (trailing whitespace and trailing newlines are
ignored).

## Assessment
Topic / role : {{TOPIC}}
Difficulty   : {{DIFFICULTY}}
Statement language : {{LANGUAGE}}
How many     : {{COUNT}} problems
Candidates may solve in ANY of: {{ALLOWED_LANGUAGES}}

## Rules
- Write the problem STATEMENT in {{LANGUAGE}} (keep technical terms in English).
- Every problem MUST be solvable in every allowed language using ONLY the
  standard library, reading from stdin and writing to stdout.
- The statement MUST fully specify: the task, the input format, the output
  format, and explicit constraints (input sizes / value ranges).
- Expected outputs MUST be deterministic — exactly one correct output per
  input. No floating-point tolerance tricks, no multiple valid answers.
- Give each problem 5 to 8 test cases:
  * EXACTLY 2 with "is_sample": true — simple, explained in the statement.
  * The rest with "is_sample": false — hidden; cover edge cases (minimum /
    maximum constraints, empty-ish inputs, tricky values). Give harder hidden
    cases "weight" 2, simple ones 1.
- "stdin" and "expected_output" are the LITERAL text piped to / expected from
  the program (use \\n for newlines inside the JSON strings).
- "reference_solution" is a correct, clean solution in {{REF_LANGUAGE}} that
  reads stdin and writes stdout. It MUST produce exactly the expected_output
  for every test case.
- Match the requested difficulty. For "mixed", spread across easy/medium/hard.

## Output STRICT JSON (no markdown, no code fences)
{
  "questions": [
    {
      "prompt": "<full problem statement incl. input/output format and constraints>",
      "reference_solution": "<{{REF_LANGUAGE}} source code>",
      "test_cases": [
        {"stdin": "<input>", "expected_output": "<output>", "is_sample": true, "weight": 1}
      ]
    }
  ]
}

Return EXACTLY {{COUNT}} problems. Output ONLY the JSON object."""


def _normalise_test_case(raw: Any) -> dict[str, Any] | None:
    """Validate + clean one raw test-case dict. Returns None to drop a bad row."""
    if not isinstance(raw, dict):
        return None
    stdin = raw.get("stdin", "")
    expected = raw.get("expected_output", "")
    if not isinstance(stdin, str) or not isinstance(expected, str):
        return None
    if not expected.strip():  # a case with no expected output cannot grade
        return None
    try:
        weight = int(raw.get("weight", 1))
    except (TypeError, ValueError):
        weight = 1
    return {
        "stdin": stdin[:_MAX_CASE_TEXT],
        "expected_output": expected[:_MAX_CASE_TEXT],
        "is_sample": bool(raw.get("is_sample", False)),
        "weight": min(100, max(1, weight)),
    }


def _normalise_coding_question(raw: Any) -> dict[str, Any] | None:
    """Validate + clean one raw coding-question dict. Returns None to drop it.

    Grading requires at least one sample case (shown to the candidate for
    'Run') and at least one hidden case (otherwise the visible samples ARE the
    grade and can be hard-coded against).
    """
    if not isinstance(raw, dict):
        return None
    prompt = str(raw.get("prompt", "")).strip()
    if not prompt:
        return None
    cases_raw = raw.get("test_cases")
    if not isinstance(cases_raw, list):
        return None
    cases = [c for c in (_normalise_test_case(r) for r in cases_raw) if c is not None]
    cases = cases[:_MAX_TEST_CASES]
    if not any(c["is_sample"] for c in cases):
        return None
    if not any(not c["is_sample"] for c in cases):
        return None
    reference = raw.get("reference_solution")
    reference_solution = str(reference).strip()[:40_000] if reference else None
    return {
        "prompt": prompt[:20_000],
        "reference_solution": reference_solution or None,
        "test_cases": cases,
    }


async def generate_coding_questions(
    *,
    topic: str,
    num_questions: int,
    difficulty: str = "medium",
    language: str = "en",
    allowed_languages: list[str],
    settings: Settings,
) -> list[dict[str, Any]]:
    """Generate stdin/stdout coding problems for *topic*.

    Each dict: {"prompt": str, "reference_solution": str | None,
    "test_cases": [{"stdin", "expected_output", "is_sample", "weight"}]}
    with >=1 sample and >=1 hidden case. Raises ExamGenerationError on Gemini
    failure / unparseable output / zero valid questions.
    """
    count = max(1, min(_MAX_CODING_QUESTIONS, int(num_questions)))
    difficulty = difficulty if difficulty in _VALID_DIFFICULTIES else "medium"
    language_name = _LANGUAGE_NAMES.get(language.lower(), "English")
    langs = ", ".join(allowed_languages) or "python"
    # Reference solution language: python when allowed (most reviewable),
    # else the first allowed language.
    ref_language = "python" if "python" in allowed_languages else (
        allowed_languages[0] if allowed_languages else "python"
    )

    prompt = (
        _CODING_PROMPT_TEMPLATE.replace("{{TOPIC}}", topic[:300])
        .replace("{{DIFFICULTY}}", difficulty)
        .replace("{{LANGUAGE}}", language_name)
        .replace("{{COUNT}}", str(count))
        .replace("{{ALLOWED_LANGUAGES}}", langs)
        .replace("{{REF_LANGUAGE}}", ref_language)
    )

    # Coding problems are far heavier than MCQs (statement + solution + cases);
    # thinking is disabled so the whole budget is output. Gemini 2.5-flash
    # supports up to 65k output tokens.
    parsed = await _call_gemini_json(
        prompt, settings, max_output_tokens=min(32_768, 4096 + count * 2048)
    )

    raw_questions = parsed.get("questions")
    if not isinstance(raw_questions, list):
        raise ExamGenerationError("Gemini response had no 'questions' array.")

    questions = [
        q for q in (_normalise_coding_question(r) for r in raw_questions) if q is not None
    ]
    if not questions:
        raise ExamGenerationError("Gemini returned no usable coding questions.")

    log.info(
        "exam_generator.coding_complete",
        topic=topic[:80],
        requested=count,
        produced=len(questions),
        difficulty=difficulty,
        language=language,
        model=settings.gemini_model,
    )
    return questions[:count]
