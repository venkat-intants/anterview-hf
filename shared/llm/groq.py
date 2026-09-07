"""The Groq JSON caller — the OpenAI-compatible sibling of ``gemini.py``.

Same contract, same recovery ladder (both import it from ``_recovery``), same
``error_cls`` parameter so each caller keeps its own exception type. What
differs is only what has to: the wire format and the vocabulary.

    POST {base_url}/chat/completions
    Authorization: Bearer {api_key}
    {"model", "messages":[{"role":"user","content":...}],
     "temperature", "max_tokens", "response_format":{"type":"json_object"}}

    -> choices[0].message.content  +  choices[0].finish_reason

Three differences from Gemini worth knowing before reading the code:

* **JSON mode is a hard mode, not a hint.** Groq rejects the request outright
  if ``response_format`` is ``json_object`` and the word "json" appears nowhere
  in the prompt. That is a 400 on a prompt that would have worked fine on
  Gemini, so the instruction is appended here rather than left to each caller
  to remember — three call sites remembering a provider quirk is how the
  original duplication started.

* **There is no thinking budget to disable.** Gemini's 2.5 family spends hidden
  reasoning tokens out of ``maxOutputTokens`` and needs ``thinkingConfig`` to
  stop truncating JSON mid-string. Groq's chat models have no equivalent, so
  the whole budget is output and there is nothing to guard.

* **Truncation is called ``length``, not ``MAX_TOKENS``.** Normalised before it
  leaves this module — see ``_recovery.TRUNCATED_FINISH_REASON`` for why that
  one string matters more than it looks.

There is deliberately no embeddings function here. Groq serves no embeddings
API, so resume vectors stay on whatever ``EMBEDDING_MODEL`` names regardless of
``LLM_PROVIDER``; semantic applicant search therefore still needs a Gemini key.
Saying so here is cheaper than someone discovering it when search goes quiet.
"""

from __future__ import annotations

from typing import Any, Final

import structlog

from shared.llm._recovery import (
    ERROR_BODY_CHARS,
    TRUNCATED_FINISH_REASON,
    finish_hint,
    parse_json_object,
    post_with_retry,
)

log = structlog.get_logger(__name__)

_PROVIDER: Final[str] = "Groq"

# OpenAI's name for "cut off by max_tokens". Mapped onto the shared constant so
# the truncation guard in repair_json fires for both providers.
_OPENAI_TRUNCATED: Final[str] = "length"

# The knob an operator raises when output is truncated. Named in the error.
_BUDGET_PARAM: Final[str] = "max_tokens"

# Groq refuses response_format=json_object unless the prompt mentions JSON.
# Appended rather than prepended so it cannot displace a caller's system framing
# of untrusted text, which must stay at the top where the model reads it first.
_JSON_NUDGE: Final[str] = "\n\nRespond with a single valid JSON object and nothing else."


async def call_groq_json(
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
    """POST *prompt* to Groq in JSON mode with retry; return the parsed object.

    Signature-compatible with :func:`shared.llm.gemini.call_gemini_json` on
    purpose — ``call_llm_json`` dispatches between them on a provider string,
    and a divergent signature would push that difference out to every call site.

    Raises *error_cls* — and only *error_cls* — on transport failure, a non-2xx
    response, an unreadable envelope, or output that cannot be recovered into a
    JSON object.
    """
    # Fail fast on a missing key. Without this the empty value builds the
    # header `Authorization: Bearer `, which httpx rejects as an illegal header
    # value — after four retry attempts, and reported to the user as
    # "Illegal header value b'Bearer '". That reads like a bug in the transport
    # rather than a blank environment variable, which is the one thing the
    # message needed to say.
    if not (api_key or "").strip():
        raise error_cls(
            "No GROQ_API_KEY is configured — set one, or set LLM_PROVIDER=gemini."
        )

    url = f"{api_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}

    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt + _JSON_NUDGE}],
        "temperature": temperature,
        "max_tokens": max_output_tokens,
        "response_format": {"type": "json_object"},
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
        raise error_cls(f"Groq returned a non-JSON body: {exc}") from exc

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
    """Return ``(text, normalised_finish_reason)`` from a chat-completions body.

    Written defensively rather than as ``payload["choices"][0]["message"]
    ["content"]`` for the same reason the Gemini side is: the envelopes that
    most need explaining are exactly the ones that subscript chain turns into a
    bare ``KeyError``/``IndexError``. On Groq that is a request refused by the
    content filter (an ``error`` object and no choices) and a completion cut
    off before the model emitted anything at all.
    """
    if not isinstance(payload, dict):
        raise error_cls("Groq returned an unreadable body")

    # A 200 carrying an error object: rare, but it exists, and reading it is
    # the difference between a useful message and "no choices".
    error = payload.get("error")
    if isinstance(error, dict) and error.get("message"):
        raise error_cls(f"Groq error: {str(error['message'])[:ERROR_BODY_CHARS]}")

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise error_cls("Groq returned no choices")

    choice: dict[str, Any] = choices[0]
    raw_finish = str(choice.get("finish_reason") or "")
    finish_reason = (
        TRUNCATED_FINISH_REASON if raw_finish == _OPENAI_TRUNCATED else raw_finish
    )

    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise error_cls(
            f"Groq returned no message content{finish_hint(finish_reason, _BUDGET_PARAM)}"
        )
    return content, finish_reason
