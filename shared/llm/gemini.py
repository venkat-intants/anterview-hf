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

What lives here, and what does not
----------------------------------
Only the Gemini-specific half: building the ``:generateContent`` request,
reading its envelope, and naming its truncation marker. Retry, backoff and the
JSON recovery ladder moved to ``shared/llm/_recovery.py`` when Groq became a
first-class provider, so both providers share one copy of the part that was
worth centralising. That module's docstring carries the ladder's reasoning.

``finishReason`` is still captured **before** the text is extracted, because the
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
is the exception and is imported *inside* the function in ``_recovery``,
guarded, because only feedback_billing and interview_core ship it; hoisting that
import to module level would break the other two at container start.
``shared/tests/test_shared_gemini.py`` asserts both halves of that mechanically.
"""

from __future__ import annotations

from typing import Any, Final

import structlog

from shared.llm._recovery import (
    BACKOFF_BASE_SECONDS,
    MAX_ATTEMPTS,
    RETRY_STATUSES,
    TRUNCATED_FINISH_REASON,
    finish_hint,
    parse_json_object,
    post_with_retry,
)

log = structlog.get_logger(__name__)

_PROVIDER: Final[str] = "Gemini"
# The knob an operator raises when output is truncated. Named in the error.
_BUDGET_PARAM: Final[str] = "maxOutputTokens"

# Re-exported so the existing importers of these names from this module keep
# working — three services and the shared test suite reference them.
__all__ = [
    "BACKOFF_BASE_SECONDS",
    "MAX_ATTEMPTS",
    "RETRY_STATUSES",
    "TRUNCATED_FINISH_REASON",
    "call_gemini_json",
]




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
    # Same reason as the Groq guard: an empty key here reaches Google as a
    # header with no value and comes back as an opaque 400 after four retries,
    # when the real answer is that nothing was configured.
    if not (api_key or "").strip():
        raise error_cls(
            "No GEMINI_API_KEY is configured — set one, or set LLM_PROVIDER=groq."
        )

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

    response = await post_with_retry(
        url,
        body=body,
        headers=headers,
        timeout=timeout,
        model=model,
        provider=_PROVIDER,
        error_cls=error_cls,
    )

    try:
        payload: Any = response.json()
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        raise error_cls(f"Gemini returned a non-JSON body: {exc}") from exc

    raw_text, finish_reason = _extract_text(payload, error_cls=error_cls)
    return parse_json_object(
        raw_text,
        finish_reason=finish_reason,
        model=model,
        provider=_PROVIDER,
        budget_param=_BUDGET_PARAM,
        error_cls=error_cls,
    )


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
        raise error_cls(f"Gemini returned no text part{finish_hint(finish_reason, _BUDGET_PARAM)}")
    return "".join(texts), finish_reason
