"""LLM adapter package (S2-005).

Provides a provider-neutral ``LLMAdapter`` protocol consumed by the
LangGraph nodes plus the concrete Gemini implementation used in Sprint 2.

Future providers (Anthropic, Bedrock) drop in here as additional
``app/llm/<provider>.py`` modules. ``build_default_adapter()`` is the
single place that reads ``settings.llm_provider`` and decides which class
to construct, so the rest of the codebase stays provider-agnostic.
"""

from __future__ import annotations

from app.config import settings
from app.llm.base import LLMAdapter, LLMError, LLMMessage, LLMResponse
from app.llm.gemini import GeminiAdapter
from app.llm.groq import GroqAdapter

__all__ = [
    "GeminiAdapter",
    "GroqAdapter",
    "LLMAdapter",
    "LLMError",
    "LLMMessage",
    "LLMResponse",
    "build_default_adapter",
]


def build_default_adapter() -> LLMAdapter:
    """Construct the adapter selected by ``settings.llm_provider``.

    Centralises the provider switch so nodes / app bootstrap never need an
    ``if/elif`` chain. Add a new ``elif`` branch here when wiring Anthropic
    or Bedrock; nothing else changes.
    """
    provider = settings.llm_provider.lower()
    if provider == "gemini":
        return GeminiAdapter(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            max_tokens=settings.gemini_max_tokens,
            base_url=settings.gemini_api_base_url,
        )
    if provider == "groq":
        return GroqAdapter(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            max_tokens=settings.groq_max_tokens,
            base_url=settings.groq_api_base_url,
        )
    # Anthropic / Bedrock land in Sprint 3+ — see app.health for the call
    # patterns. Until then any other value is a config error worth halting on.
    raise ValueError(
        f"Unsupported LLM_PROVIDER: {settings.llm_provider!r} "
        "(supported: 'gemini', 'groq')"
    )
