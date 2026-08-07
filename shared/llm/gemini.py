"""The one Gemini JSON caller, shared by every service that asks Gemini for a
structured object.

Why this module exists
----------------------
Three call sites in ``feedback_billing`` — the interview scorer, the resume/ATS
scorer and the exam + coding generator — each carried their own copy of the same
scaffolding: build the ``:generateContent`` URL, authenticate, ask for JSON
mode, disable thinking on 2.5 models, retry the transient statuses with
exponential backoff, dig the text out of the response envelope, parse it. Every
hardening since had to be applied three times: the ``thinkingConfig`` guard, the
move of the API key out of ``?key=`` and into a header, and the JSON-mode switch
each landed as three near-identical diffs.

The cost is not the duplication, it is the drift. The exam generator grew
brace-span extraction, ``finishReason`` capture and a gated ``json_repair``
fallback; the interview scorer — the path that produces the candidate's
scorecard, the highest-value output in the product — grew none of them. One
salvageable Gemini response therefore becomes a set of exam questions on one
path and a 502 with no scorecard on the other. One caller cannot drift from
itself.

What this module inherits from
------------------------------
It lifts the *strongest* of the three (the exam generator), not the intersection
of all three, so adopting it is an upgrade for every caller rather than an
averaging-down. The recovery ladder, in order:

1. strip a markdown code fence if the model wrapped its object in one;
2. if the text still does not start with ``{``, take the outermost ``{...}``
   span — this is what rescues a response with a stray sentence around the
   object, and is precisely what the scorer lacked;
3. drop a trailing comma before ``}``/``]`` (invalid JSON, emitted occasionally
   even in JSON mode);
4. as a last resort hand the text to ``json_repair`` — but only when the output
   was *not* truncated (see ``_repair_json``).

``finishReason`` is captured **before** the text is extracted, because the
failure that most needs explaining — a 2.5 model that spent its whole budget and
returned a candidate with no ``parts`` at all — has no text to attach a message
to. Carrying it means an operator reading the error can tell "raise
``maxOutputTokens``" from "the model returned prose", which the finding
(FB-2) called out as missing.

Errors stay the caller's
------------------------
``error_cls`` is a parameter rather than one unified exception type on purpose.
``ScoringError``, ``ResumeScoringError`` and ``ExamGenerationError`` are caught
by three separate endpoint handlers, each logging its own event
(``score.gemini_error`` / ``score.resume_error`` / ``score.generate_exam_error``)
and returning its own detail string, and ``data_gateway`` defines a further
``ExamGenerationError`` of its own for the same workflow. Collapsing them into
one shared type would be an API and observability change for three services
smuggled in as a refactor, so this module raises whatever the caller passes and
contributes only the diagnosis.

Primitives, not ``Settings``
----------------------------
The parameters are ``api_base_url`` / ``model`` / ``api_key`` and not a
``Settings`` object: ``shared/`` is imported by all four services and must not
know the shape of any one service's config class.

Dependency rule
---------------
``shared/`` is COPY'd into all four service images, so this module imports only
stdlib + httpx + structlog — all four already depend on httpx. ``json_repair``
is the exception and is imported *inside* the function, guarded, because only
feedback_billing and interview_core ship it; hoisting that import to module
level would break the other two at container start.
``shared/tests/test_shared_gemini.py`` asserts both halves of that mechanically.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Final

import httpx
import structlog

log = structlog.get_logger(__name__)

# Transient Gemini statuses worth retrying: 429 rate-limit plus gateway /
# overload errors (503 "high demand" is the common one on the free tier). A
# 400/403 is a real problem — a bad prompt or a bad key — and retrying it only
# multiplies the latency of a failure that was never going to succeed.
RETRY_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS: Final[int] = 4  # 1 initial + 3 retries
BACKOFF_BASE_SECONDS: Final[float] = 1.0  # exponential: 1s, 2s, 4s

# Gemini's own name for "I ran out of output budget mid-answer". Named because
# two decisions turn on it: the wording of the error, and whether json_repair is
# allowed to run at all.
TRUNCATED_FINISH_REASON: Final[str] = "MAX_TOKENS"

# Removes a trailing comma before a closing } or ]: matches ",  }" / ",\n]" and
# keeps just the bracket.
_TRAILING_COMMA_RE: Final[re.Pattern[str]] = re.compile(r",(\s*[}\]])")

# Error bodies are echoed into the message that is raised (and usually logged),
# so they are truncated: an unbounded provider body in a log line is a log-flood
# risk, and prompts on this platform carry transcript and resume text.
_ERROR_BODY_CHARS: Final[int] = 200


async def call_gemini_json(
    prompt: str,
    *,
    api_base_url: str,
    model: str,
    api_key: str,
    temperature: float,
    max_output_tokens: int,
    timeout: float,
    error_cls: type[Exception],
) -> dict[str, Any]:
    """POST *prompt* to Gemini in JSON mode with retry; return the parsed object.

    See the module docstring for why this lives in ``shared/`` and what recovery
    it performs. Raises *error_cls* — and only *error_cls* — on transport
    failure, a non-2xx response, an unreadable envelope, or output that cannot
    be recovered into a JSON object.

    Args:
        prompt: The fully rendered user prompt. Untrusted text must already be
            framed by the caller (``app.untrusted_input``); this module does not
            know which parts of a prompt came from a candidate.
        api_base_url: e.g. ``https://generativelanguage.googleapis.com/v1beta``.
        model: e.g. ``gemini-flash-lite-latest``. Also decides whether
            ``thinkingConfig`` is sent — see below.
        api_key: Sent as the ``x-goog-api-key`` header, never as ``?key=``.
        temperature: 0.2 for scoring (repeatable), higher for authoring.
        max_output_tokens: The whole budget goes to JSON, since thinking is
            disabled. Too small truncates the object mid-string; when that
            happens the raised error says so explicitly.
        timeout: Whole-request timeout in seconds, per attempt.
        error_cls: The caller's own exception type, constructed with a single
            message string.
    """
    # Auth via the x-goog-api-key header (not ?key=) so the key never lands in
    # request URLs, proxy access logs or exception text — see
    # feedback_billing/app/embedder.py, where this was first applied.
    url = f"{api_base_url}/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key}

    generation_config: dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens,
        # JSON mode: forces well-formed, fence-free output. Without it the
        # scorer regularly returned prose-wrapped or truncated JSON and 502'd,
        # so the candidate never got a scorecard. The recovery ladder below
        # still exists because "usually" is not "always".
        "responseMimeType": "application/json",
    }
    # Gemini 2.5 models are "thinking" models: hidden reasoning tokens count
    # against maxOutputTokens and on generation-heavy prompts consume nearly the
    # whole budget, truncating the JSON mid-string (seen live on the HF Space:
    # output died at char 41 → "Unterminated string"). Structured authoring and
    # scoring gain nothing from private reasoning, so spend the entire budget on
    # output. thinkingConfig is only accepted by the 2.5 family — older models
    # reject the field with HTTP 400 — hence the version guard.
    if "2.5" in model:
        generation_config["thinkingConfig"] = {"thinkingBudget": 0}

    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }

    response = await _post_with_retry(
        url,
        body=body,
        headers=headers,
        timeout=timeout,
        model=model,
        error_cls=error_cls,
    )

    try:
        payload: Any = response.json()
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        raise error_cls(f"Gemini returned a non-JSON body: {exc}") from exc

    raw_text, finish_reason = _extract_text(payload, error_cls=error_cls)
    cleaned = _clean(raw_text)

    try:
        parsed: Any = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        parsed = _repair_json(
            cleaned,
            parse_error=exc,
            finish_reason=finish_reason,
            model=model,
            error_cls=error_cls,
        )

    if not isinstance(parsed, dict):
        # Every caller does parsed.get(...) on the result. A bare JSON array
        # would reach them as an AttributeError from inside their own parsing
        # code, which reads like a bug in the caller rather than a bad response.
        raise error_cls(
            f"Gemini returned a JSON {type(parsed).__name__}, not an object"
            f"{_finish_hint(finish_reason)}"
        )
    return parsed


async def _post_with_retry(
    url: str,
    *,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    model: str,
    error_cls: type[Exception],
) -> httpx.Response:
    """POST with bounded exponential backoff; return the 200 response.

    Retries transient failures so a momentary Gemini hiccup does not cost a
    candidate their scorecard, and fails fast on everything else.
    """
    last_error = "no attempt made"

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await client.post(url, json=body, headers=headers)
            except httpx.RequestError as exc:
                last_error = f"request error: {exc}"
            else:
                if response.status_code == 200:
                    return response
                last_error = f"HTTP {response.status_code}: {response.text[:_ERROR_BODY_CHARS]}"
                if response.status_code not in RETRY_STATUSES:
                    break  # non-transient (e.g. 400/403) — do not retry
            if attempt < MAX_ATTEMPTS - 1:
                backoff = BACKOFF_BASE_SECONDS * (2**attempt)
                log.warning(
                    "shared.llm.gemini_retry",
                    # caller: the exception type is what distinguishes the call
                    # sites in the logs now that the code path is one module.
                    caller=error_cls.__name__,
                    model=model,
                    attempt=attempt + 1,
                    max_attempts=MAX_ATTEMPTS,
                    backoff_s=backoff,
                    error=last_error,
                )
                await asyncio.sleep(backoff)

    raise error_cls(f"Gemini call failed after {MAX_ATTEMPTS} attempt(s): {last_error}")


def _extract_text(payload: Any, *, error_cls: type[Exception]) -> tuple[str, str]:
    """Return ``(text, finish_reason)`` from a generateContent envelope.

    Written defensively rather than as ``payload["candidates"][0]["content"]
    ["parts"][0]["text"]`` because the two envelopes that most need explaining
    are exactly the ones that subscript chain turns into a bare ``KeyError``/
    ``IndexError``: a prompt blocked before generation (no candidates at all,
    only ``promptFeedback``), and a 2.5 model that spent its whole budget
    thinking and returned a candidate with no ``parts``.
    """
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        feedback = payload.get("promptFeedback") if isinstance(payload, dict) else None
        block_reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
        detail = f" (blockReason={block_reason})" if block_reason else ""
        raise error_cls(f"Gemini returned no candidates{detail}")

    candidate: dict[str, Any] = candidates[0]
    finish_reason = str(candidate.get("finishReason") or "")

    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    texts = [
        part["text"]
        for part in (parts if isinstance(parts, list) else [])
        # A "thought" part is the model's private reasoning, not output. It
        # should never appear (thinking is disabled on 2.5 above), but joining
        # one into the JSON would corrupt an otherwise parseable response, so
        # the filter is cheap insurance rather than a hypothetical.
        if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought")
    ]
    if not texts:
        raise error_cls(f"Gemini returned no text part{_finish_hint(finish_reason)}")
    return "".join(texts), finish_reason


def _clean(raw_text: str) -> str:
    """Apply the non-destructive half of the recovery ladder (steps 1-3)."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    if not cleaned.startswith("{"):
        # Tolerate prose the model wrapped around the object by parsing the
        # outermost {...} span. With responseMimeType=json this is usually a
        # no-op — and "usually" is exactly the gap the scorer fell into: the
        # exam generator has this step and the scorer never grew it.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    return _TRAILING_COMMA_RE.sub(r"\1", cleaned)


def _repair_json(
    cleaned: str,
    *,
    parse_error: json.JSONDecodeError,
    finish_reason: str,
    model: str,
    error_cls: type[Exception],
) -> dict[str, Any]:
    """Last rung of the ladder: ``json_repair``, or a diagnosed failure.

    Even in JSON mode Gemini intermittently emits invalid escapes or raw
    newlines inside strings — especially when the payload embeds source code
    (coding questions) or a quoted transcript line. json_repair salvages those;
    per-item validation downstream still drops anything structurally unusable.

    Deliberately NOT attempted on a truncated response: "repairing" a cut-off
    payload closes the braces and yields a half-empty object, which is worse
    than an error because it looks like a real result. A truncation is a budget
    problem and the error says so.
    """
    repaired: Any = None
    if finish_reason != TRUNCATED_FINISH_REASON:
        try:
            import json_repair

            repaired = json_repair.repair_json(cleaned, return_objects=True)
        except Exception:
            # Two failures collapse into one branch on purpose: json_repair is
            # not installed in every service image (see the dependency rule),
            # and it can also choke on the input. Both mean "no repair" and
            # neither should mask the original parse error.
            repaired = None

    if isinstance(repaired, dict) and repaired:
        log.warning(
            "shared.llm.gemini_json_repaired",
            caller=error_cls.__name__,
            model=model,
            # The parse error names an offset, never the payload — response text
            # can contain transcript/resume PII and must not be logged.
            parse_error=str(parse_error)[:120],
        )
        return repaired

    raise error_cls(
        f"Gemini response was not valid JSON{_finish_hint(finish_reason)}: {parse_error}"
    ) from parse_error


def _finish_hint(finish_reason: str) -> str:
    """Turn ``finishReason`` into the sentence an operator needs.

    The whole point: "raise maxOutputTokens" and "the model returned prose" are
    different tickets, and before this the error text could not tell them apart.
    """
    if finish_reason == TRUNCATED_FINISH_REASON:
        return (
            f" (finishReason={TRUNCATED_FINISH_REASON} — the output was cut off by"
            " maxOutputTokens; raise the budget rather than blaming the model)"
        )
    if finish_reason and finish_reason != "STOP":
        # SAFETY, RECITATION, OTHER... — not a budget problem, and not something
        # a retry fixes either.
        return f" (finishReason={finish_reason})"
    return ""
