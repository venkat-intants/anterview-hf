"""Gemini adapters for the agent layer.

Two shapes are needed and they are genuinely different calls:

* ``build_agent_llm`` — function-calling. The copilot loop needs the model to
  emit structured tool invocations, so tool specs go over the wire as
  ``functionDeclarations`` and come back as ``functionCall`` parts.
* ``build_panel_llm`` — plain structured JSON, no tools. The panel specialists
  read one document and return an assessment; giving them tools would let a
  specialist go looking at signals it is supposed to be blind to.

Both return ``None`` when no key is configured, which every caller treats as
"assistant unavailable" rather than an error — a deployment without a Gemini
key must still serve the rest of data_gateway normally.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog
from shared.agents import AgentLLM, AgentMessage, AssistantStep, PanelLLM, ToolCall, ToolSpec

from app.config import settings

log = structlog.get_logger(__name__)

# Agent turns are short (a sentence or two plus tool calls), but the final
# answer can be a comparison table across several candidates.
_AGENT_MAX_TOKENS: int = 2048
# Specialists emit a small JSON object; the ceiling is generous enough that a
# verbose model does not get truncated mid-string, which would fail the parse.
_PANEL_MAX_TOKENS: int = 1536

_TIMEOUT_SECONDS: float = 40.0
_RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS: int = 3


class _GeminiClient:
    """Thin ``generateContent`` wrapper with retry on transient statuses."""

    def __init__(self) -> None:
        self._url = (
            f"{settings.gemini_api_base_url.rstrip('/')}"
            f"/models/{settings.gemini_model}:generateContent"
            f"?key={settings.gemini_api_key}"
        )

    async def post(self, body: dict[str, Any]) -> dict[str, Any]:
        last_error = "no attempt made"
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    response = await client.post(self._url, json=body)
                except httpx.RequestError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                else:
                    if response.status_code == 200:
                        return dict(response.json())
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    # 400/403 mean a bad key or a malformed payload — retrying
                    # just burns latency the user is sitting through.
                    if response.status_code not in _RETRY_STATUSES:
                        break
                if attempt < _MAX_ATTEMPTS - 1:
                    log.warning("agents.gemini.retry", attempt=attempt + 1, error=last_error)
        raise RuntimeError(f"gemini call failed: {last_error}")


def _generation_config(max_tokens: int, *, json_mode: bool) -> dict[str, Any]:
    config: dict[str, Any] = {"temperature": 0.2, "maxOutputTokens": max_tokens}
    if json_mode:
        config["responseMimeType"] = "application/json"
    # On 2.5 "thinking" models hidden reasoning counts against maxOutputTokens
    # and truncates the payload mid-string. Disable it so the whole budget is
    # output. Guarded: pre-2.5 models 400 on thinkingConfig.
    if "2.5" in settings.gemini_model:
        config["thinkingConfig"] = {"thinkingBudget": 0}
    return config


def _to_gemini_contents(messages: list[AgentMessage]) -> list[dict[str, Any]]:
    """Translate the neutral conversation into Gemini's ``contents``.

    Gemini expects tool output as a ``functionResponse`` part on a USER turn
    (not a dedicated tool role), paired by function NAME with the preceding
    ``functionCall``. Getting this wrong makes the model silently ignore tool
    results and re-request the same tool forever — which presents as the agent
    looping until it hits the step budget.
    """
    contents: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "user":
            contents.append({"role": "user", "parts": [{"text": message.text}]})
        elif message.role == "assistant":
            parts: list[dict[str, Any]] = []
            if message.text:
                parts.append({"text": message.text})
            parts.extend(
                {"functionCall": {"name": call.name, "args": call.arguments}}
                for call in message.tool_calls
            )
            contents.append({"role": "model", "parts": parts or [{"text": ""}]})
        else:  # tool results
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": result.name,
                                # Wrapped in an object: Gemini requires the
                                # response to be a struct, and our tools return
                                # JSON text.
                                "response": {
                                    "ok": result.ok,
                                    "content": result.content
                                    if result.ok
                                    else (result.error or "failed"),
                                },
                            }
                        }
                        for result in message.tool_results
                    ],
                }
            )
    return contents


def _sanitise_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip JSON Schema keywords Gemini's function-calling subset rejects.

    Gemini accepts a narrow OpenAPI-ish subset. Sending a plain-but-valid
    schema with ``additionalProperties`` or ``$schema`` returns an opaque 400
    that is painful to trace back to the offending tool, so unsupported keys
    are dropped here rather than policed at every tool definition.
    """
    allowed = {
        "type",
        "description",
        "properties",
        "required",
        "items",
        "enum",
        "format",
        "nullable",
    }
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in allowed:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: _sanitise_schema(v) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            out[key] = _sanitise_schema(value)
        else:
            out[key] = value
    return out


def _parse_step(data: dict[str, Any]) -> AssistantStep:
    """Pull text and function calls out of a Gemini response."""
    usage = data.get("usageMetadata") or {}
    step = AssistantStep(
        prompt_tokens=int(usage.get("promptTokenCount", 0) or 0),
        output_tokens=int(usage.get("candidatesTokenCount", 0) or 0),
    )

    candidates = data.get("candidates") or []
    if not candidates:
        return step

    texts: list[str] = []
    for part in (candidates[0].get("content") or {}).get("parts") or []:
        if "text" in part and part["text"]:
            texts.append(str(part["text"]))
        call = part.get("functionCall")
        if call and call.get("name"):
            args = call.get("args")
            step.tool_calls.append(
                ToolCall(
                    name=str(call["name"]),
                    arguments=dict(args) if isinstance(args, dict) else {},
                )
            )
    step.text = "\n".join(texts).strip()
    return step


def build_agent_llm() -> AgentLLM | None:
    """Function-calling adapter for the copilot loop, or None if unconfigured."""
    if not settings.gemini_api_key:
        return None

    client = _GeminiClient()

    async def call(
        system_prompt: str, messages: list[AgentMessage], tools: list[ToolSpec]
    ) -> AssistantStep:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": _to_gemini_contents(messages),
            # JSON mode is OFF here: it is incompatible with function calling
            # and the final answer is prose for a human anyway.
            "generationConfig": _generation_config(_AGENT_MAX_TOKENS, json_mode=False),
        }
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": spec.name,
                            "description": spec.description,
                            "parameters": _sanitise_schema(spec.parameters),
                        }
                        for spec in tools
                    ]
                }
            ]
        return _parse_step(await client.post(body))

    return call


def build_panel_llm() -> PanelLLM | None:
    """Structured-JSON adapter for panel specialists, or None if unconfigured.

    No tools by design: a specialist must see only the one signal it was given,
    and a tool would let it fetch the others.
    """
    if not settings.gemini_api_key:
        return None

    client = _GeminiClient()

    async def call(system_prompt: str, user_prompt: str) -> str:
        data = await client.post(
            {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": _generation_config(_PANEL_MAX_TOKENS, json_mode=True),
            }
        )
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(str(p.get("text", "")) for p in parts)

    return call


def describe_availability() -> dict[str, Any]:
    """Health/debug payload — never echoes the key itself."""
    return {
        "configured": bool(settings.gemini_api_key),
        "model": settings.gemini_model if settings.gemini_api_key else None,
    }


__all__ = [
    "build_agent_llm",
    "build_panel_llm",
    "describe_availability",
    "json",  # re-exported for tests that assert payload shape
]
