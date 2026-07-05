"""Gemini REST adapter — primary LLM provider for Sprint 2.

We hit the Gemini ``generateContent`` REST endpoint directly via ``httpx``
rather than the ``google-generativeai`` SDK. Rationale:

  - One fewer transitive dep (faster CI installs, smaller container image).
  - Same call pattern as ``app.health._check_gemini`` — one less wire
    format to know.
  - Streaming / multi-turn / tool-use are all straightforward in REST; the
    SDK adds little value for our usage and obscures error bodies.

Error taxonomy — every failure surfaces as ``LLMError``:

  - HTTP non-2xx       -> ``LLMError(status=<code>, body=<snippet>)``
  - finishReason=MAX_TOKENS AND no visible parts -> ``LLMError("MAX_TOKENS...")``
  - empty candidates / empty parts -> ``LLMError("empty response")``
  - ``httpx.HTTPError`` / timeout -> ``LLMError("network: ...")``

NOTE: gemini-2.5-flash is a thinking model. It can spend most of the output
budget on private reasoning tokens (``thoughtsTokenCount``) and still
return visible text. We only flag MAX_TOKENS as fatal when the visible
parts list is empty — otherwise the response is usable even if truncated.
"""

from __future__ import annotations

import json as _json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

from app.llm.base import LLMAdapter, LLMError, LLMMessage, LLMResponse

log = structlog.get_logger(__name__)

# How long we'll wait for a single generation. Sprint 2 sticks with 30s —
# matches the Day-1 compat-check script. Sprint 3 will lower this once the
# voice budget (700ms LLM) is enforced.
DEFAULT_TIMEOUT_SECONDS: float = 30.0


class GeminiAdapter(LLMAdapter):
    """REST adapter for Google Gemini ``generateContent``."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        base_url: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            # Fail loud at construction — far better than discovering at
            # the first turn that the env var was empty.
            raise ValueError("GeminiAdapter: api_key is required")
        self._api_key = api_key
        self._model = model
        self._default_max_tokens = max_tokens
        self._base_url = base_url.rstrip("/")
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
        """Call ``models/{model}:generateContent`` and return our neutral shape."""
        budget = max_tokens if max_tokens is not None else self._default_max_tokens
        payload = self._build_payload(system_prompt, messages, budget)
        url = (
            f"{self._base_url}/models/"
            f"{self._model}:generateContent?key={self._api_key}"
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            # Network-layer failure (DNS, connect, read timeout). The model
            # never saw the request — caller can retry safely if it wants.
            raise LLMError(f"network: {type(exc).__name__}: {exc}") from exc

        if response.status_code != 200:
            # 4xx/5xx — auth issue, quota, malformed payload, etc. Body is
            # the most useful signal; keep it but cap length.
            raise LLMError(
                f"gemini http {response.status_code}",
                status=response.status_code,
                body=response.text,
            )

        return self._parse_response(response.json())

    async def generate_stream(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Call ``models/{model}:streamGenerateContent`` and yield text deltas.

        Gemini's streaming endpoint returns a JSON array-of-objects wrapped in
        a chunked HTTP response.  Each object has the same shape as a single
        ``generateContent`` response (``candidates[0].content.parts``).  We
        extract the text from each chunk as it arrives and yield it so that the
        sentence-splitter upstream can start firing TTS for completed sentences
        while the model is still generating.

        Error handling mirrors ``generate``:
          - Network errors       -> ``LLMError("network: ...")``
          - HTTP non-2xx         -> ``LLMError("gemini http <code>")``
          - No text in any chunk -> ``LLMError("empty stream response")``

        PII rule: NEVER log individual chunk text.  Log only chunk counts and
        total character length at DEBUG level.

        Yields:
            Non-empty text deltas in arrival order.
        """
        budget = max_tokens if max_tokens is not None else self._default_max_tokens
        payload = self._build_payload(system_prompt, messages, budget)
        url = (
            f"{self._base_url}/models/"
            f"{self._model}:streamGenerateContent"
            f"?key={self._api_key}&alt=sse"
        )

        try:
            async with (
                httpx.AsyncClient(timeout=self._timeout_seconds) as client,
                client.stream("POST", url, json=payload) as response,
            ):
                    if response.status_code != 200:
                        body = await response.aread()
                        raise LLMError(
                            f"gemini http {response.status_code}",
                            status=response.status_code,
                            body=body.decode("utf-8", errors="replace"),
                        )

                    chunk_count = 0
                    total_chars = 0

                    async for line in response.aiter_lines():
                        # Gemini SSE: lines are prefixed with "data: ".
                        # Blank lines are SSE heartbeats — skip them.
                        if not line.startswith("data: "):
                            continue
                        raw_json = line[len("data: "):]
                        if not raw_json.strip():
                            continue

                        try:
                            chunk_data: dict[str, Any] = _json.loads(raw_json)
                        except _json.JSONDecodeError:
                            # Malformed chunk — skip rather than abort; the
                            # overall response may still be complete.
                            log.warning(
                                "llm.gemini.stream.malformed_chunk",
                                chunk_preview=raw_json[:80],
                            )
                            continue

                        candidates = chunk_data.get("candidates") or []
                        if not candidates:
                            continue

                        parts = (
                            candidates[0]
                            .get("content", {})
                            .get("parts") or []
                        )
                        chunk_text = "".join(
                            p.get("text", "") for p in parts if isinstance(p, dict)
                        )
                        if chunk_text:
                            chunk_count += 1
                            total_chars += len(chunk_text)
                            yield chunk_text

                    if total_chars == 0:
                        raise LLMError("empty stream response: no text in any chunk")

                    log.debug(
                        "llm.gemini.stream.done",
                        chunk_count=chunk_count,
                        total_chars=total_chars,
                    )

        except httpx.HTTPError as exc:
            raise LLMError(f"network: {type(exc).__name__}: {exc}") from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _build_payload(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        max_tokens: int,
    ) -> dict[str, Any]:
        """Translate our neutral shape into the Gemini wire format.

        Gemini's schema:
          - ``systemInstruction`` carries the persona / global rules.
          - ``contents`` is the conversation turn list. Each turn has a
            ``role`` (``user`` or ``model``) and a ``parts`` array. We only
            ever send a single text part per turn.

        S4-003 note on cache-block split: ``render_interviewer_system_prompt``
        now concatenates the base system prompt with a per-persona delta
        block (separator ``\\n\\n[PERSONA: <id>]\\n``). The Anthropic
        adapter will split these into two ``cache_control`` blocks so the
        big ~600-token base block stays cache-warm across all four
        personas. Gemini does NOT expose per-part ``cache_control`` on
        ``systemInstruction`` today, so this adapter sends the concatenated
        string as a single part — no-op. When Gemini exposes block-level
        caching, mirror the Anthropic split here.
        """
        contents: list[dict[str, Any]] = [
            {"role": m.role, "parts": [{"text": m.text}]} for m in messages
        ]
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if system_prompt:
            # Omit the key entirely if no system prompt was supplied —
            # Gemini accepts that and we avoid sending an empty parts list.
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        return payload

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        """Extract visible text + token counts, raising ``LLMError`` on any miss."""
        candidates = data.get("candidates") or []
        if not candidates:
            # Either the prompt was blocked by safety or the API returned a
            # malformed body. Either way the graph can't proceed.
            raise LLMError("empty response: no candidates", body=str(data)[:500])

        candidate = candidates[0]
        finish_reason = str(candidate.get("finishReason") or "UNKNOWN")

        parts = candidate.get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))

        usage = data.get("usageMetadata", {}) or {}
        prompt_tokens = int(usage.get("promptTokenCount") or 0)
        candidates_tokens = int(usage.get("candidatesTokenCount") or 0)
        raw_thoughts = usage.get("thoughtsTokenCount")
        thoughts_tokens = int(raw_thoughts) if raw_thoughts is not None else None

        if finish_reason == "MAX_TOKENS" and not text:
            # Thinking model burned the whole budget on hidden reasoning.
            # Distinguished from "MAX_TOKENS but text was produced" — that
            # one is degraded-but-usable; this one is fatal.
            raise LLMError(
                "MAX_TOKENS — increase max_tokens budget "
                f"(prompt={prompt_tokens}, thoughts={thoughts_tokens})"
            )

        if not text:
            raise LLMError(
                f"empty response: finishReason={finish_reason}",
                body=str(data)[:500],
            )

        log.debug(
            "llm.gemini.generate.ok",
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            candidates_tokens=candidates_tokens,
            thoughts_tokens=thoughts_tokens,
            text_chars=len(text),
        )

        return LLMResponse(
            text=text.strip(),
            prompt_tokens=prompt_tokens,
            candidates_tokens=candidates_tokens,
            thoughts_tokens=thoughts_tokens,
            finish_reason=finish_reason,
        )
