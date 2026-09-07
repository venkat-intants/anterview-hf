"""The provider-neutral half of asking a model for a JSON object.

Split out of ``gemini.py`` when Groq became a first-class provider, and the
split runs exactly along the line where provider knowledge stops:

    provider-specific   build the request · read the envelope · name the
                        truncation marker
    provider-neutral    retry · backoff · the JSON recovery ladder · the
                        diagnosis in the error message

Everything in the second column is here, in one copy, because it is the part
that was worth centralising in the first place. ``gemini.py``'s own docstring
records what happened when three call sites each carried their own copy: the
exam generator grew brace-span extraction and a guarded ``json_repair``
fallback, the interview scorer — the path that produces a candidate's scorecard
— grew neither, and one salvageable response therefore became questions on one
path and a 502 with no scorecard on the other. Giving Groq its own copy of that
ladder would have recreated the same drift with a second provider's name on it.

The recovery ladder, in order:

1. strip a markdown code fence if the model wrapped its object in one;
2. if the text still does not start with ``{``, take the outermost ``{...}``
   span — this rescues a response with a stray sentence around the object;
3. drop a trailing comma before ``}``/``]`` (invalid JSON, emitted occasionally
   even in JSON mode);
4. as a last resort hand the text to ``json_repair`` — but only when the output
   was *not* truncated (see :func:`repair_json`).

Dependency rule: stdlib + httpx + structlog, same as the module it came from.
``json_repair`` is imported inside the function, guarded, because only
feedback_billing and interview_core ship it.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Final

import httpx
import structlog

log = structlog.get_logger(__name__)

# Transient statuses worth retrying: 429 rate-limit plus gateway / overload
# errors. Identical for both providers — Groq returns 429 under free-tier
# pressure and 503 when a model is cold, exactly as Gemini does. A 400/403 is a
# real problem (bad prompt, bad key) and retrying only multiplies the latency of
# a failure that was never going to succeed.
RETRY_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS: Final[int] = 4  # 1 initial + 3 retries
BACKOFF_BASE_SECONDS: Final[float] = 1.0  # exponential: 1s, 2s, 4s

# The NORMALISED name for "I ran out of output budget mid-answer". Each provider
# maps its own vocabulary onto this before handing a finish reason over —
# Gemini says ``MAX_TOKENS``, OpenAI-compatible APIs (Groq) say ``length``.
#
# Normalising matters more than it looks: two decisions turn on this string, and
# one of them is whether ``json_repair`` is allowed to run at all. A Groq
# truncation left as ``length`` would not match, repair would run on a cut-off
# payload, and the caller would receive a half-empty object that parses cleanly
# and reads like a real result.
TRUNCATED_FINISH_REASON: Final[str] = "MAX_TOKENS"

# Removes a trailing comma before a closing } or ]: matches ",  }" / ",\n]" and
# keeps just the bracket.
_TRAILING_COMMA_RE: Final[re.Pattern[str]] = re.compile(r",(\s*[}\]])")

# Error bodies are echoed into the message that is raised (and usually logged),
# so they are truncated: an unbounded provider body in a log line is a log-flood
# risk, and prompts on this platform carry transcript and resume text.
ERROR_BODY_CHARS: Final[int] = 200


async def post_with_retry(
    url: str,
    *,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    model: str,
    provider: str,
    error_cls: type[Exception],
) -> httpx.Response:
    """POST with bounded exponential backoff; return the 200 response.

    Retries transient failures so a momentary provider hiccup does not cost a
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
                last_error = f"HTTP {response.status_code}: {response.text[:ERROR_BODY_CHARS]}"
                if response.status_code not in RETRY_STATUSES:
                    break  # non-transient (e.g. 400/403) — do not retry
            if attempt < MAX_ATTEMPTS - 1:
                backoff = BACKOFF_BASE_SECONDS * (2**attempt)
                log.warning(
                    "shared.llm.retry",
                    provider=provider,
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

    raise error_cls(
        f"{provider} call failed after {MAX_ATTEMPTS} attempt(s): {last_error}"
    )


def clean_json_text(raw_text: str) -> str:
    """Apply the non-destructive half of the recovery ladder (steps 1-3)."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    if not cleaned.startswith("{"):
        # Tolerate prose the model wrapped around the object by parsing the
        # outermost {...} span. With JSON mode on this is usually a no-op — and
        # "usually" is exactly the gap the scorer fell into: the exam generator
        # had this step and the scorer never grew it.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    return _TRAILING_COMMA_RE.sub(r"\1", cleaned)


def repair_json(
    cleaned: str,
    *,
    parse_error: json.JSONDecodeError,
    finish_reason: str,
    model: str,
    provider: str,
    budget_param: str,
    error_cls: type[Exception],
) -> dict[str, Any]:
    """Last rung of the ladder: ``json_repair``, or a diagnosed failure.

    Even in JSON mode models intermittently emit invalid escapes or raw
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
            "shared.llm.json_repaired",
            provider=provider,
            caller=error_cls.__name__,
            model=model,
            # The parse error names an offset, never the payload — response text
            # can contain transcript/resume PII and must not be logged.
            parse_error=str(parse_error)[:120],
        )
        return repaired

    raise error_cls(
        f"{provider} response was not valid JSON"
        f"{finish_hint(finish_reason, budget_param)}: {parse_error}"
    ) from parse_error


def finish_hint(finish_reason: str, budget_param: str = "the output budget") -> str:
    """Turn a normalised finish reason into the sentence an operator needs.

    The whole point: "raise the token budget" and "the model returned prose"
    are different tickets, and before this the error text could not tell them
    apart.

    ``budget_param`` is the provider's actual knob — ``maxOutputTokens`` on
    Gemini, ``max_tokens`` on Groq. It is a parameter rather than generic
    wording because the message's whole value is telling an operator which
    setting to change, and "raise the output budget" sends them looking for a
    field that does not exist under that name in either API.
    """
    if finish_reason == TRUNCATED_FINISH_REASON:
        return (
            f" (finishReason={TRUNCATED_FINISH_REASON} — the output was cut off by"
            f" {budget_param}; raise the budget rather than blaming the model)"
        )
    if finish_reason and finish_reason not in ("STOP", "stop"):
        # SAFETY, RECITATION, content_filter, tool_calls... — not a budget
        # problem, and not something a retry fixes either.
        return f" (finishReason={finish_reason})"
    return ""


def parse_json_object(
    raw_text: str,
    *,
    finish_reason: str,
    model: str,
    provider: str,
    budget_param: str,
    error_cls: type[Exception],
) -> dict[str, Any]:
    """Run the whole ladder over one response body and return the object.

    The single entry point both providers use once they have extracted text and
    a normalised finish reason from their own envelope.
    """
    cleaned = clean_json_text(raw_text)
    try:
        parsed: Any = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        parsed = repair_json(
            cleaned,
            parse_error=exc,
            finish_reason=finish_reason,
            model=model,
            provider=provider,
            budget_param=budget_param,
            error_cls=error_cls,
        )

    if not isinstance(parsed, dict):
        # Every caller does parsed.get(...) on the result. A bare JSON array
        # would reach them as an AttributeError from inside their own parsing
        # code, which reads like a bug in the caller rather than a bad response.
        raise error_cls(
            f"{provider} returned a JSON {type(parsed).__name__}, not an object"
            f"{finish_hint(finish_reason, budget_param)}"
        )
    return parsed
