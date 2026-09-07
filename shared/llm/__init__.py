"""LLM adapters shared by all four services.

Two providers behind one function. :func:`call_llm_json` takes a provider name
and dispatches; :mod:`shared.llm._recovery` holds everything that is not
provider-specific, so retry, backoff and the JSON recovery ladder exist in one
copy rather than one per provider.

Typical use::

    from shared.llm import call_llm_json

    parsed = await call_llm_json(
        rendered_prompt,
        provider=settings.llm_provider,     # "gemini" | "groq"
        api_base_url=settings.llm_api_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        temperature=0.2,
        max_output_tokens=6144,
        timeout=60.0,
        error_cls=ScoringError,   # each caller keeps its own exception type
    )

``call_gemini_json`` and ``call_groq_json`` remain importable and are what the
dispatcher calls; reach for one of them directly only when a call site must
pin a provider regardless of configuration.

WHAT SWITCHING PROVIDERS DOES NOT MOVE: embeddings. Groq serves no embeddings
API, so resume vectors keep coming from whatever ``EMBEDDING_MODEL`` names and
semantic applicant search still needs a Gemini key even when
``LLM_PROVIDER=groq``. That is a property of the provider, not an oversight
here — see the note in ``groq.py``.
"""

from typing import Any

from shared.llm._recovery import (
    BACKOFF_BASE_SECONDS,
    MAX_ATTEMPTS,
    RETRY_STATUSES,
    TRUNCATED_FINISH_REASON,
)
from shared.llm.gemini import call_gemini_json
from shared.llm.groq import call_groq_json

__all__ = [
    "BACKOFF_BASE_SECONDS",
    "MAX_ATTEMPTS",
    "RETRY_STATUSES",
    "TRUNCATED_FINISH_REASON",
    "call_gemini_json",
    "call_groq_json",
    "call_llm_json",
]

# Provider name -> caller. Both entries have the identical signature, which is
# what lets the dispatcher be a dictionary lookup instead of a branch that
# each new provider has to be threaded through.
_CALLERS = {
    "gemini": call_gemini_json,
    "groq": call_groq_json,
}


async def call_llm_json(
    prompt: str,
    *,
    provider: str,
    api_base_url: str,
    model: str,
    api_key: str,
    temperature: float,
    max_output_tokens: int,
    timeout: float,
    error_cls: type[Exception],
) -> dict[str, Any]:
    """Ask the configured provider for a JSON object.

    Raises *error_cls* for an unknown provider rather than silently falling
    back to a default. A typo in ``LLM_PROVIDER`` that quietly resolved to
    Gemini would send traffic — and cost — to a provider the operator believed
    they had switched away from, and nothing in the response would say so.
    """
    caller = _CALLERS.get((provider or "").strip().lower())
    if caller is None:
        raise error_cls(
            f"Unknown LLM_PROVIDER {provider!r}; expected one of {sorted(_CALLERS)}"
        )
    return await caller(
        prompt,
        api_base_url=api_base_url,
        model=model,
        api_key=api_key,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        timeout=timeout,
        error_cls=error_cls,
    )
