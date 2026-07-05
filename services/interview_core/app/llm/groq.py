"""Groq REST adapter — fast OpenAI-compatible LLM provider.

Groq exposes an OpenAI-compatible ``/chat/completions`` endpoint and
consistently returns p50 latencies of ~300 ms on ``llama-3.3-70b-versatile``,
making it an excellent swap-in when Gemini's free-tier rate limits bite during
demos.

Wire format is standard OpenAI Chat Completions v1:
  - ``POST {base_url}/chat/completions``
  - ``Authorization: Bearer {api_key}``
  - Request body: ``{"model", "messages", "max_tokens", "temperature"}``
  - Response: ``choices[0].message.content`` + ``usage.*``

Role mapping — our internal messages use Gemini vocabulary (``user`` /
``model``).  The OpenAI wire format uses ``user`` / ``assistant``.  We
translate at the boundary here; callers (nodes.py) need not know:

    LLMMessage.role="model"  ->  OpenAI role "assistant"
    LLMMessage.role="user"   ->  OpenAI role "user"  (unchanged)

Error taxonomy — every failure surfaces as ``LLMError`` (same as
GeminiAdapter so callers have a single exception type):

  - HTTP non-2xx        -> ``LLMError(status=<code>, body=<snippet>)``
  - Empty content       -> ``LLMError("empty response: ...")``
  - httpx network error -> ``LLMError("network: ...")``

PII rule: NEVER log prompt text or response text.  Only log token counts,
latency, and finish_reason.  The only log event emitted here is
``llm.groq.generate.ok`` at DEBUG level.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

from app.llm.base import LLMAdapter, LLMError, LLMMessage, LLMResponse

log = structlog.get_logger(__name__)

# Match Gemini adapter's 30-second ceiling.  Groq is much faster in practice
# (~300 ms) but we keep a generous upper bound for rare queue spikes.
DEFAULT_TIMEOUT_SECONDS: float = 30.0

# Groq uses OpenAI role names.  Translate from our internal Gemini vocabulary.
_ROLE_MAP: dict[str, str] = {
    "user": "user",
    "model": "assistant",
}


class GroqAdapter(LLMAdapter):
    """OpenAI-compatible adapter for the Groq inference API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        base_url: str,
        temperature: float = 0.7,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            # Fail loud at construction — far better than discovering at
            # the first turn that the env var was empty.
            raise ValueError("GroqAdapter: api_key is required")
        self._api_key = api_key
        self._model = model
        self._default_max_tokens = max_tokens
        self._base_url = base_url.rstrip("/")
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Call ``POST /chat/completions`` and return our neutral shape."""
        budget = max_tokens if max_tokens is not None else self._default_max_tokens
        payload = self._build_payload(system_prompt, messages, budget)
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            # Network-layer failure (DNS, connect, read timeout). The model
            # never saw the request — caller can retry safely if it wants.
            raise LLMError(f"network: {type(exc).__name__}: {exc}") from exc

        if response.status_code != 200:
            # 4xx/5xx — auth issue, quota, malformed payload, etc. Body is
            # the most useful signal; keep it but cap length.
            raise LLMError(
                f"groq http {response.status_code}",
                status=response.status_code,
                body=response.text,
            )

        return self._parse_response(response.json())

    async def generate_stream(  # type: ignore[override,misc]
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield the full response as a single chunk.

        Groq supports native SSE streaming but the LangGraph turn loop only
        needs chunked output for the TTS sentence-splitter which already
        works with a single-chunk path.  We delegate to ``generate()`` and
        yield the complete text once, satisfying the Protocol's contract for
        adapters that do not implement true token-by-token streaming.

        ``# type: ignore[override]`` suppresses the mypy false-positive that
        arises because the Protocol declares ``generate_stream`` as
        ``async def -> AsyncIterator[str]``.  mypy treats that as
        ``Coroutine[..., AsyncIterator[str]]`` and then flags any concrete
        ``async def`` that uses ``yield`` (which is an ``AsyncGenerator``,
        a structural subtype of ``AsyncIterator``).  The same ignore is
        warranted on GeminiAdapter for the same reason.  The correct long-term
        fix is to declare the Protocol method without ``async`` (plain ``def``
        returning ``AsyncIterator``); that is a base.py change tracked
        separately to avoid disrupting the Gemini adapter tests.

        A native SSE streaming implementation can be added later if the TTS
        pipeline requires sub-sentence latency from the Groq path.
        """
        response = await self.generate(system_prompt, messages, max_tokens)
        # Protocol says: empty strings must NOT be yielded.
        # generate() already raises LLMError if text is empty, so this is safe.
        yield response.text

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        max_tokens: int,
    ) -> dict[str, Any]:
        """Translate our neutral shape into the OpenAI Chat Completions format.

        The OpenAI schema expects a flat ``messages`` array where the system
        prompt is the first element with role ``"system"``, followed by the
        conversation turns with roles ``"user"`` / ``"assistant"``.

        Our internal messages use Gemini vocabulary (``user`` / ``model``).
        We translate ``model`` → ``assistant`` here via ``_ROLE_MAP``.
        """
        openai_messages: list[dict[str, str]] = []

        if system_prompt:
            openai_messages.append({"role": "system", "content": system_prompt})

        for m in messages:
            openai_messages.append(
                {
                    "role": _ROLE_MAP.get(m.role, "user"),
                    "content": m.text,
                }
            )

        return {
            "model": self._model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "temperature": self._temperature,
        }

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        """Extract visible text + token counts, raising ``LLMError`` on any miss.

        Groq returns standard OpenAI response shape:
          ``choices[0].message.content`` -> text
          ``usage.prompt_tokens``        -> prompt_tokens
          ``usage.completion_tokens``    -> candidates_tokens
          ``choices[0].finish_reason``   -> finish_reason
        """
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("empty response: no choices", body=str(data)[:500])

        choice = choices[0]
        finish_reason = str(choice.get("finish_reason") or "UNKNOWN")
        text = (choice.get("message") or {}).get("content") or ""

        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        candidates_tokens = int(usage.get("completion_tokens") or 0)

        if not text:
            raise LLMError(
                f"empty response: finish_reason={finish_reason}",
                body=str(data)[:500],
            )

        log.debug(
            "llm.groq.generate.ok",
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            candidates_tokens=candidates_tokens,
            thoughts_tokens=None,
            text_chars=len(text),
        )

        return LLMResponse(
            text=text.strip(),
            prompt_tokens=prompt_tokens,
            candidates_tokens=candidates_tokens,
            thoughts_tokens=None,  # Groq does not expose hidden reasoning tokens
            finish_reason=finish_reason,
        )
