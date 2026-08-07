"""LLM adapters shared by all four services.

Today that is one adapter: :func:`call_gemini_json`, the single Gemini
``:generateContent`` caller. It exists because the retry / auth / JSON-mode /
JSON-recovery scaffolding had been written once per call site and had already
drifted — the candidate-facing scorer had the weakest recovery of the three
copies. See ``shared/llm/gemini.py`` for the full reasoning.

Typical use::

    from shared.llm import call_gemini_json

    parsed = await call_gemini_json(
        rendered_prompt,
        api_base_url=settings.gemini_api_base_url,
        model=settings.gemini_model,
        api_key=settings.gemini_api_key,
        temperature=0.2,
        max_output_tokens=6144,
        timeout=60.0,
        error_cls=ScoringError,   # each caller keeps its own exception type
    )
"""

from shared.llm.gemini import (
    BACKOFF_BASE_SECONDS,
    MAX_ATTEMPTS,
    RETRY_STATUSES,
    TRUNCATED_FINISH_REASON,
    call_gemini_json,
)

__all__ = [
    "BACKOFF_BASE_SECONDS",
    "MAX_ATTEMPTS",
    "RETRY_STATUSES",
    "TRUNCATED_FINISH_REASON",
    "call_gemini_json",
]
