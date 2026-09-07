"""The Groq adapter for the copilot loop.

Function calling is where a provider swap goes wrong quietly. Every failure
below produces a plausible-looking conversation rather than an exception, which
is why each one is pinned rather than left to integration:

* arguments arrive as a JSON **string**. Not parsing it hands every tool an
  empty dict — the tool does not raise, it runs with defaults and returns the
  wrong rows, and the copilot reports them confidently.
* tool results are keyed by ``tool_call_id``. Mis-pairing them makes the model
  re-request the same tool until the step budget runs out, which looks like the
  agent "thinking" rather than like a bug.
* ``tool_choice`` must be ``auto``. Forcing a call would make the final,
  answer-only turn impossible and the loop could never terminate.
* ``describe_availability`` must read the ACTIVE provider's key. Reading
  Gemini's on a Groq deployment reports an assistant that is not there, and the
  console gates purely on that flag.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from shared.agents import AgentMessage, ToolCall, ToolResult, ToolSpec


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, recorder: list[dict[str, Any]], script: list[Any]) -> None:
        self._recorder = recorder
        self._script = script

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(self, url: str, json: Any = None, headers: Any = None) -> Any:  # noqa: A002
        self._recorder.append({"url": url, "body": json, "headers": headers})
        return self._script.pop(0)


def _patch(monkeypatch: pytest.MonkeyPatch, *script: Any) -> list[dict[str, Any]]:
    recorder: list[dict[str, Any]] = []
    calls = list(script)

    class _FakeHttpx:
        import httpx as _real

        RequestError = _real.RequestError

        def AsyncClient(self, **kwargs: Any) -> _FakeClient:  # noqa: N802
            return _FakeClient(recorder, calls)

    monkeypatch.setattr("app.agents.llm_groq.httpx", _FakeHttpx())
    return recorder


def _configure(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    from app.config import settings

    defaults = {
        "llm_provider": "groq",
        "groq_api_key": "gsk-test",
        "groq_model": "openai/gpt-oss-120b",
        "groq_api_base_url": "https://api.groq.test/openai/v1",
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        monkeypatch.setattr(settings, key, value, raising=False)


def _reply(content: str = "", tool_calls: list[dict[str, Any]] | None = None) -> _FakeResponse:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return _FakeResponse(
        200,
        {
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        },
    )


_SPEC = ToolSpec(
    name="list_applicants",
    description="List applicants",
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer"}},
        "additionalProperties": False,
    },
    effect="read",
    data_class="candidate_pii",
    allowed_roles=("hr_manager",),
)


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_system_prompt_is_a_message_not_a_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini takes systemInstruction; OpenAI-shaped APIs take a system role."""
    _configure(monkeypatch)
    recorder = _patch(monkeypatch, _reply("hello"))
    from app.agents.llm_groq import build_groq_agent_llm

    call = build_groq_agent_llm()
    assert call is not None
    await call("you are the hiring copilot", [AgentMessage(role="user", text="hi")], [])

    messages = recorder[0]["body"]["messages"]
    assert messages[0] == {"role": "system", "content": "you are the hiring copilot"}
    assert messages[1] == {"role": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_tools_go_over_the_wire_unsanitised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Groq takes standard JSON Schema. Stripping keywords the way the Gemini
    adapter must would drop constraints the model can actually use."""
    _configure(monkeypatch)
    recorder = _patch(monkeypatch, _reply("ok"))
    from app.agents.llm_groq import build_groq_agent_llm

    call = build_groq_agent_llm()
    assert call is not None
    await call("sys", [AgentMessage(role="user", text="hi")], [_SPEC])

    tool = recorder[0]["body"]["tools"][0]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "list_applicants"
    assert tool["function"]["parameters"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_tool_choice_is_auto_so_the_loop_can_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last turn of every run is prose with no tool call. Forcing a call
    would make that turn impossible and the loop could never terminate."""
    _configure(monkeypatch)
    recorder = _patch(monkeypatch, _reply("ok"))
    from app.agents.llm_groq import build_groq_agent_llm

    call = build_groq_agent_llm()
    assert call is not None
    await call("sys", [AgentMessage(role="user", text="hi")], [_SPEC])
    assert recorder[0]["body"]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_no_tools_means_no_tools_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    recorder = _patch(monkeypatch, _reply("ok"))
    from app.agents.llm_groq import build_groq_agent_llm

    call = build_groq_agent_llm()
    assert call is not None
    await call("sys", [AgentMessage(role="user", text="hi")], [])
    assert "tools" not in recorder[0]["body"]


# ---------------------------------------------------------------------------
# Tool calls — the silent-failure surface
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_arguments_arrive_as_a_string_and_are_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one that fails silently: an unparsed argument string becomes an empty
    dict, the tool runs with defaults, and the copilot reports the wrong rows
    without anything raising."""
    _configure(monkeypatch)
    _patch(
        monkeypatch,
        _reply(
            tool_calls=[
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {
                        "name": "list_applicants",
                        "arguments": '{"limit": 5, "status": "shortlisted"}',
                    },
                }
            ]
        ),
    )
    from app.agents.llm_groq import build_groq_agent_llm

    call = build_groq_agent_llm()
    assert call is not None
    step = await call("sys", [AgentMessage(role="user", text="hi")], [_SPEC])

    assert len(step.tool_calls) == 1
    assert step.tool_calls[0].name == "list_applicants"
    assert step.tool_calls[0].arguments == {"limit": 5, "status": "shortlisted"}


@pytest.mark.asyncio
async def test_the_providers_call_id_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """The result has to be sent back keyed by exactly this id."""
    _configure(monkeypatch)
    _patch(
        monkeypatch,
        _reply(
            tool_calls=[
                {
                    "id": "call_abc",
                    "function": {"name": "list_applicants", "arguments": "{}"},
                }
            ]
        ),
    )
    from app.agents.llm_groq import build_groq_agent_llm

    call = build_groq_agent_llm()
    assert call is not None
    step = await call("sys", [AgentMessage(role="user", text="hi")], [_SPEC])
    assert step.tool_calls[0].id == "call_abc"


@pytest.mark.asyncio
async def test_a_missing_call_id_still_gets_a_usable_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty id would pair every result to the same non-existent call."""
    _configure(monkeypatch)
    _patch(
        monkeypatch,
        _reply(tool_calls=[{"function": {"name": "list_applicants", "arguments": "{}"}}]),
    )
    from app.agents.llm_groq import build_groq_agent_llm

    call = build_groq_agent_llm()
    assert call is not None
    step = await call("sys", [AgentMessage(role="user", text="hi")], [_SPEC])
    assert step.tool_calls[0].id


@pytest.mark.asyncio
async def test_malformed_arguments_degrade_to_empty_rather_than_killing_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registry validates and bounds every argument anyway, so a bad call
    should reach the model as one tool's readable error, not end the turn."""
    _configure(monkeypatch)
    _patch(
        monkeypatch,
        _reply(
            tool_calls=[
                {"id": "c1", "function": {"name": "list_applicants", "arguments": "{not json"}}
            ]
        ),
    )
    from app.agents.llm_groq import build_groq_agent_llm

    call = build_groq_agent_llm()
    assert call is not None
    step = await call("sys", [AgentMessage(role="user", text="hi")], [_SPEC])
    assert step.tool_calls[0].arguments == {}


@pytest.mark.asyncio
async def test_tool_results_are_sent_back_keyed_by_call_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mis-pairing makes the model re-request the same tool until the step
    budget runs out, which reads as the agent thinking rather than as a bug."""
    _configure(monkeypatch)
    recorder = _patch(monkeypatch, _reply("done"))
    from app.agents.llm_groq import build_groq_agent_llm

    history = [
        AgentMessage(role="user", text="who should I interview?"),
        AgentMessage(
            role="assistant",
            text="",
            tool_calls=[ToolCall(id="call_abc", name="list_applicants", arguments={"limit": 5})],
        ),
        AgentMessage(
            role="tool",
            tool_results=[
                ToolResult(call_id="call_abc", name="list_applicants", ok=True, content='{"n":3}')
            ],
        ),
    ]
    call = build_groq_agent_llm()
    assert call is not None
    await call("sys", history, [_SPEC])

    messages = recorder[0]["body"]["messages"]
    assistant = next(m for m in messages if m["role"] == "assistant")
    tool_msg = next(m for m in messages if m["role"] == "tool")

    assert assistant["tool_calls"][0]["id"] == "call_abc"
    # Round-tripped as a string, which is what the API expects.
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"limit": 5}'
    assert tool_msg["tool_call_id"] == "call_abc"
    assert tool_msg["content"] == '{"n":3}'


@pytest.mark.asyncio
async def test_a_failed_tool_reports_its_error_to_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    recorder = _patch(monkeypatch, _reply("done"))
    from app.agents.llm_groq import build_groq_agent_llm

    history = [
        AgentMessage(role="user", text="x"),
        AgentMessage(
            role="tool",
            tool_results=[
                ToolResult(call_id="c1", name="t", ok=False, error="permission denied")
            ],
        ),
    ]
    call = build_groq_agent_llm()
    assert call is not None
    await call("sys", history, [])
    tool_msg = next(m for m in recorder[0]["body"]["messages"] if m["role"] == "tool")
    assert tool_msg["content"] == "permission denied"


@pytest.mark.asyncio
async def test_token_usage_is_carried_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run's budget stops at max_total_tokens; a zero here makes it
    unbounded."""
    _configure(monkeypatch)
    _patch(monkeypatch, _reply("hi"))
    from app.agents.llm_groq import build_groq_agent_llm

    call = build_groq_agent_llm()
    assert call is not None
    step = await call("sys", [AgentMessage(role="user", text="hi")], [])
    assert (step.prompt_tokens, step.output_tokens) == (120, 30)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
def test_no_groq_key_means_no_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, groq_api_key="")
    from app.agents.llm_groq import build_groq_agent_llm, build_groq_panel_llm

    assert build_groq_agent_llm() is None
    assert build_groq_panel_llm() is None


def test_the_factory_follows_the_provider_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """One switch moves the copilot, the panel and the status endpoint together
    — they must never disagree about which model is live."""
    from app import agents
    from app.agents import llm as llm_module

    _configure(monkeypatch, llm_provider="groq", gemini_api_key="")
    assert llm_module.build_agent_llm() is not None
    assert llm_module.build_panel_llm() is not None
    assert agents  # imported for the side-effectful package, keeps linters quiet

    _configure(monkeypatch, llm_provider="gemini", gemini_api_key="", groq_api_key="gsk-test")
    # Gemini selected with no Gemini key: unavailable, even though a Groq key
    # is present. Falling back would silently bill the wrong provider.
    assert llm_module.build_agent_llm() is None


def test_availability_reports_the_active_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """The console gates purely on ``configured``. Reading Gemini's key on a
    Groq deployment would offer an assistant whose every message fails."""
    from app.agents.llm import describe_availability

    _configure(monkeypatch, llm_provider="groq", gemini_api_key="", groq_api_key="gsk-test")
    out = describe_availability()
    assert out["provider"] == "groq"
    assert out["configured"] is True
    assert out["model"] == "openai/gpt-oss-120b"

    _configure(monkeypatch, llm_provider="groq", groq_api_key="", gemini_api_key="gem-key")
    out = describe_availability()
    assert out["configured"] is False
    assert out["model"] is None
