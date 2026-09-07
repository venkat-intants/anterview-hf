"""Groq adapters for the agent layer — the OpenAI-compatible sibling of ``llm.py``.

Same two shapes, same contract, same "return None when unconfigured" rule:

* ``build_groq_agent_llm`` — function calling. Groq speaks OpenAI's
  ``tools`` / ``tool_calls`` dialect, so the copilot loop works unchanged
  above this line.
* ``build_groq_panel_llm`` — plain structured JSON, no tools, because a panel
  specialist must see only the one signal it was handed.

Four things differ from Gemini, and all four have bitten somewhere before:

**Tool arguments arrive as a JSON STRING, not an object.** OpenAI's wire format
puts them in ``function.arguments`` as text. Forgetting to parse it hands every
tool an empty argument dict, which does not raise — the tool simply runs with
defaults and returns the wrong rows, and the copilot confidently reports them.

**Tool results are their own role.** OpenAI has a real ``tool`` role keyed by
``tool_call_id``; Gemini fakes it with a ``functionResponse`` part on a user
turn. Getting the pairing wrong makes the model re-request the same tool
forever, which presents as the agent looping to its step budget.

**Not every Groq model supports tools.** Availability is per-account and the
catalogue moves. A model without tool support answers in prose and never calls
anything — so a copilot that talks but never looks anything up is a
``GROQ_MODEL`` problem, not a prompt problem.

**No thinking budget to disable.** Gemini's 2.5 family needs ``thinkingConfig``
or hidden reasoning eats the output budget. Groq has no equivalent.

Tool SCHEMAS go over the wire unmodified here. Gemini accepts only a narrow
OpenAPI-ish subset and ``llm.py`` strips the rest; Groq takes standard JSON
Schema, so sanitising would remove constraints the model can actually use.
"""

from __future__ import annotations

import json
from typing import Any, Final

import httpx
import structlog
from shared.agents import AgentLLM, AgentMessage, AssistantStep, PanelLLM, ToolCall, ToolSpec

from app.config import settings

log = structlog.get_logger(__name__)

_AGENT_MAX_TOKENS: Final[int] = 2048
_PANEL_MAX_TOKENS: Final[int] = 1536
_TIMEOUT_SECONDS: Final[float] = 40.0
_RETRY_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS: Final[int] = 3


class _GroqClient:
    """Thin ``chat/completions`` wrapper with retry on transient statuses."""

    def __init__(self) -> None:
        self._url = f"{settings.groq_api_base_url.rstrip('/')}/chat/completions"
        # Bearer header, never a query parameter: the key must not reach proxy
        # access logs or exception text.
        self._headers = {"Authorization": f"Bearer {settings.groq_api_key}"}

    async def post(self, body: dict[str, Any]) -> dict[str, Any]:
        last_error = "no attempt made"
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    response = await client.post(self._url, json=body, headers=self._headers)
                except httpx.RequestError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                else:
                    if response.status_code == 200:
                        return dict(response.json())
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    # 400/401/404 mean a bad key, a bad payload, or a model this
                    # account cannot reach. Retrying burns latency the user is
                    # sitting through for a failure that will not change.
                    if response.status_code not in _RETRY_STATUSES:
                        break
                if attempt < _MAX_ATTEMPTS - 1:
                    log.warning("agents.groq.retry", attempt=attempt + 1, error=last_error)
        raise RuntimeError(f"groq call failed: {last_error}")


def _to_openai_messages(
    system_prompt: str, messages: list[AgentMessage]
) -> list[dict[str, Any]]:
    """Translate the neutral conversation into OpenAI's ``messages``.

    The system prompt is a message here rather than a separate field, which is
    the main structural difference from Gemini's ``systemInstruction``.
    """
    wire: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for message in messages:
        if message.role == "user":
            wire.append({"role": "user", "content": message.text})
        elif message.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            # Back to a string: this is what the API expects,
                            # and it is what our own id-keyed pairing below
                            # will be matched against on the next turn.
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ]
            wire.append(entry)
        else:  # tool results — one message each, keyed by the call id
            wire.extend(
                {
                    "role": "tool",
                    "tool_call_id": result.call_id,
                    "content": result.content if result.ok else (result.error or "failed"),
                }
                for result in message.tool_results
            )
    return wire


def _parse_step(data: dict[str, Any]) -> AssistantStep:
    """Pull text and tool calls out of a chat-completions response."""
    usage = data.get("usage") or {}
    step = AssistantStep(
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        output_tokens=int(usage.get("completion_tokens", 0) or 0),
    )

    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return step

    message = choices[0].get("message") or {}
    step.text = str(message.get("content") or "").strip()

    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        # The provider's own id is reused when present, because the tool
        # result must be sent back keyed by exactly that id. An empty one falls
        # through to ToolCall's generated default rather than becoming "",
        # which would pair every result to the same non-existent call.
        call_id = str(call.get("id") or "").strip()
        step.tool_calls.append(
            ToolCall(
                **({"id": call_id} if call_id else {}),
                name=str(name),
                arguments=_parse_arguments(function.get("arguments"), tool=str(name)),
            )
        )
    return step


def _parse_arguments(raw: Any, *, tool: str) -> dict[str, Any]:
    """Decode ``function.arguments``, which arrives as a JSON string.

    An empty dict on failure rather than an exception: the registry validates
    and bounds every argument anyway, and a malformed call should reach the
    model as one tool's readable error rather than ending the whole run. Logged
    because a model that cannot produce valid arguments for a tool usually
    means that tool's schema is too complex for the configured model.
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("agents.groq.bad_tool_arguments", tool=tool)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_groq_agent_llm() -> AgentLLM | None:
    """Function-calling adapter for the copilot loop, or None if unconfigured."""
    if not settings.groq_api_key:
        return None

    client = _GroqClient()

    async def call(
        system_prompt: str, messages: list[AgentMessage], tools: list[ToolSpec]
    ) -> AssistantStep:
        body: dict[str, Any] = {
            "model": settings.groq_model,
            "messages": _to_openai_messages(system_prompt, messages),
            "temperature": 0.2,
            "max_tokens": _AGENT_MAX_TOKENS,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters,
                    },
                }
                for spec in tools
            ]
            # "auto", not "required": the last turn of every run is a prose
            # answer with no tool call, and forcing a call would make the loop
            # unable to finish.
            body["tool_choice"] = "auto"
        return _parse_step(await client.post(body))

    return call


def build_groq_panel_llm() -> PanelLLM | None:
    """Structured-JSON adapter for panel specialists, or None if unconfigured.

    No tools by design: a specialist must see only the one signal it was given,
    and a tool would let it fetch the others.
    """
    if not settings.groq_api_key:
        return None

    client = _GroqClient()

    async def call(system_prompt: str, user_prompt: str) -> str:
        data = await client.post(
            {
                "model": settings.groq_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    # Groq refuses response_format=json_object unless the prompt
                    # mentions JSON. The panel prompts already ask for a JSON
                    # object, but appending it costs nothing and turns a 400 on
                    # a reworded prompt into a non-event.
                    {
                        "role": "user",
                        "content": user_prompt + "\n\nRespond with a single JSON object.",
                    },
                ],
                "temperature": 0.2,
                "max_tokens": _PANEL_MAX_TOKENS,
                "response_format": {"type": "json_object"},
            }
        )
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return ""
        return str((choices[0].get("message") or {}).get("content") or "")

    return call


__all__ = ["build_groq_agent_llm", "build_groq_panel_llm"]
