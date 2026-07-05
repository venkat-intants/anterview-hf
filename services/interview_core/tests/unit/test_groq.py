"""Unit tests for the Groq LLM adapter.

All tests are fully offline — the HTTP layer is mocked via
``unittest.mock``.  No real Groq API calls are made here.

Coverage:
  Construction:
    test_no_api_key_raises_at_construction

  generate — request shape:
    test_generate_posts_to_correct_url
    test_generate_sends_bearer_auth_header
    test_generate_request_includes_model
    test_generate_request_includes_system_message_first
    test_generate_maps_model_role_to_assistant
    test_generate_maps_user_role_unchanged
    test_generate_request_includes_max_tokens

  generate — response parsing:
    test_generate_returns_text_from_choices
    test_generate_returns_prompt_tokens
    test_generate_returns_candidates_tokens_from_completion_tokens
    test_generate_returns_thoughts_tokens_as_none
    test_generate_returns_finish_reason
    test_generate_strips_whitespace_from_text

  generate — error paths:
    test_generate_raises_llmerror_on_non_2xx
    test_generate_raises_llmerror_on_5xx
    test_generate_raises_llmerror_on_network_error
    test_generate_raises_llmerror_on_empty_choices
    test_generate_raises_llmerror_on_empty_content

  generate_stream — delegation:
    test_generate_stream_yields_full_text_as_single_chunk
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.llm.base import LLMError, LLMMessage, LLMResponse
from app.llm.groq import GroqAdapter

# ---------------------------------------------------------------------------
# Test constants / helpers
# ---------------------------------------------------------------------------

_API_KEY = "gsk_test_key_abc123"
_MODEL = "llama-3.3-70b-versatile"
_BASE_URL = "https://api.groq.com/openai/v1"
_MAX_TOKENS = 1024


def _make_adapter(
    api_key: str = _API_KEY,
    model: str = _MODEL,
    max_tokens: int = _MAX_TOKENS,
    base_url: str = _BASE_URL,
) -> GroqAdapter:
    """Build a GroqAdapter with test credentials."""
    return GroqAdapter(
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        base_url=base_url,
    )


def _make_mock_response(
    status_code: int,
    body: dict[str, Any] | str,
) -> httpx.Response:
    """Build a synthetic httpx.Response without hitting the network."""
    if isinstance(body, dict):
        content = json.dumps(body).encode()
        headers = {"content-type": "application/json"}
    else:
        content = body.encode()
        headers = {"content-type": "text/plain"}
    return httpx.Response(status_code=status_code, content=content, headers=headers)


def _make_success_body(
    text: str = "Tell me about yourself.",
    finish_reason: str = "stop",
    prompt_tokens: int = 42,
    completion_tokens: int = 15,
) -> dict[str, Any]:
    """Build a valid Groq/OpenAI chat-completions response body."""
    return {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "model": _MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _patch_httpx_post(response: httpx.Response) -> Any:
    """Patch httpx.AsyncClient.post to return ``response``."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=response)
    mock_cls = MagicMock(return_value=mock_client)
    return patch("httpx.AsyncClient", mock_cls), mock_client, mock_cls


# ---------------------------------------------------------------------------
# Construction tests
# ---------------------------------------------------------------------------


def test_no_api_key_raises_at_construction() -> None:
    """Empty api_key must raise ValueError at construction time with a clear message."""
    with pytest.raises(ValueError) as exc_info:
        GroqAdapter(api_key="", model=_MODEL, max_tokens=_MAX_TOKENS, base_url=_BASE_URL)

    assert "api_key is required" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# generate — request shape tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_posts_to_correct_url() -> None:
    """generate() must POST to {base_url}/chat/completions."""
    mock_response = _make_mock_response(200, _make_success_body())
    captured: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = url
        return mock_response

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = _capture_post

    with patch("httpx.AsyncClient", MagicMock(return_value=mock_client)):
        adapter = _make_adapter()
        await adapter.generate("You are an interviewer.", [LLMMessage.user("Hello.")])

    assert captured["url"] == f"{_BASE_URL}/chat/completions", (
        f"Expected POST to {_BASE_URL}/chat/completions, got {captured['url']!r}"
    )


@pytest.mark.asyncio
async def test_generate_sends_bearer_auth_header() -> None:
    """generate() must send Authorization: Bearer {api_key} header."""
    mock_response = _make_mock_response(200, _make_success_body())
    captured: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["headers"] = kwargs.get("headers", {})
        return mock_response

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = _capture_post

    with patch("httpx.AsyncClient", MagicMock(return_value=mock_client)):
        adapter = _make_adapter()
        await adapter.generate("You are an interviewer.", [LLMMessage.user("Hello.")])

    auth = captured["headers"].get("Authorization", "")
    assert auth == f"Bearer {_API_KEY}", (
        f"Expected 'Bearer {_API_KEY}', got {auth!r}"
    )


@pytest.mark.asyncio
async def test_generate_request_includes_model() -> None:
    """The request body must include the configured model name."""
    mock_response = _make_mock_response(200, _make_success_body())
    captured: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["body"] = kwargs.get("json", {})
        return mock_response

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = _capture_post

    with patch("httpx.AsyncClient", MagicMock(return_value=mock_client)):
        adapter = _make_adapter(model="llama-3.3-70b-versatile")
        await adapter.generate("System.", [LLMMessage.user("Hello.")])

    assert captured["body"].get("model") == "llama-3.3-70b-versatile", (
        f"Expected model='llama-3.3-70b-versatile', got {captured['body'].get('model')!r}"
    )


@pytest.mark.asyncio
async def test_generate_request_includes_system_message_first() -> None:
    """The messages array must start with a role='system' entry for the system_prompt."""
    mock_response = _make_mock_response(200, _make_success_body())
    captured: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["body"] = kwargs.get("json", {})
        return mock_response

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = _capture_post

    system_prompt = "You are a senior interviewer."

    with patch("httpx.AsyncClient", MagicMock(return_value=mock_client)):
        adapter = _make_adapter()
        await adapter.generate(system_prompt, [LLMMessage.user("Hello.")])

    messages = captured["body"].get("messages", [])
    assert messages, "messages list must not be empty"
    first = messages[0]
    assert first["role"] == "system", (
        f"Expected first message role='system', got {first['role']!r}"
    )
    assert first["content"] == system_prompt, (
        f"Expected system content={system_prompt!r}, got {first['content']!r}"
    )


@pytest.mark.asyncio
async def test_generate_maps_model_role_to_assistant() -> None:
    """LLMMessage with role='model' must be translated to OpenAI role='assistant'."""
    mock_response = _make_mock_response(200, _make_success_body())
    captured: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["body"] = kwargs.get("json", {})
        return mock_response

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = _capture_post

    history = [
        LLMMessage.model("Tell me about your experience."),
        LLMMessage.user("I have 5 years of Python experience."),
    ]

    with patch("httpx.AsyncClient", MagicMock(return_value=mock_client)):
        adapter = _make_adapter()
        await adapter.generate("System.", history)

    messages = captured["body"].get("messages", [])
    # messages[0] is the system prompt; messages[1] is the model turn
    model_turn = messages[1]
    assert model_turn["role"] == "assistant", (
        f"Expected 'model' role to be translated to 'assistant', got {model_turn['role']!r}"
    )
    assert model_turn["content"] == "Tell me about your experience."


@pytest.mark.asyncio
async def test_generate_maps_user_role_unchanged() -> None:
    """LLMMessage with role='user' must remain 'user' in the OpenAI request."""
    mock_response = _make_mock_response(200, _make_success_body())
    captured: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["body"] = kwargs.get("json", {})
        return mock_response

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = _capture_post

    history = [LLMMessage.user("I have 5 years of experience.")]

    with patch("httpx.AsyncClient", MagicMock(return_value=mock_client)):
        adapter = _make_adapter()
        await adapter.generate("System.", history)

    messages = captured["body"].get("messages", [])
    # messages[0] = system, messages[1] = user turn
    user_turn = messages[1]
    assert user_turn["role"] == "user", (
        f"Expected user role to remain 'user', got {user_turn['role']!r}"
    )
    assert user_turn["content"] == "I have 5 years of experience."


@pytest.mark.asyncio
async def test_generate_request_includes_max_tokens() -> None:
    """The request body must include max_tokens matching the adapter default."""
    mock_response = _make_mock_response(200, _make_success_body())
    captured: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["body"] = kwargs.get("json", {})
        return mock_response

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = _capture_post

    with patch("httpx.AsyncClient", MagicMock(return_value=mock_client)):
        adapter = _make_adapter(max_tokens=512)
        await adapter.generate("System.", [LLMMessage.user("Hello.")])

    assert captured["body"].get("max_tokens") == 512, (
        f"Expected max_tokens=512, got {captured['body'].get('max_tokens')!r}"
    )


@pytest.mark.asyncio
async def test_generate_per_call_max_tokens_override() -> None:
    """generate(max_tokens=N) must override the adapter default in the request."""
    mock_response = _make_mock_response(200, _make_success_body())
    captured: dict[str, Any] = {}

    async def _capture_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["body"] = kwargs.get("json", {})
        return mock_response

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = _capture_post

    with patch("httpx.AsyncClient", MagicMock(return_value=mock_client)):
        adapter = _make_adapter(max_tokens=512)
        # Override at call time — should win over the 512 default.
        await adapter.generate("System.", [LLMMessage.user("Hello.")], max_tokens=2048)

    assert captured["body"].get("max_tokens") == 2048, (
        f"Expected per-call override of max_tokens=2048, got {captured['body'].get('max_tokens')!r}"
    )


# ---------------------------------------------------------------------------
# generate — response parsing tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_returns_text_from_choices() -> None:
    """generate() must return LLMResponse.text from choices[0].message.content."""
    expected_text = "Tell me about your most recent project."
    mock_response = _make_mock_response(200, _make_success_body(text=expected_text))

    ctx, _, _ = _patch_httpx_post(mock_response)
    with ctx:
        adapter = _make_adapter()
        result = await adapter.generate("System.", [LLMMessage.user("Hello.")])

    assert isinstance(result, LLMResponse)
    assert result.text == expected_text


@pytest.mark.asyncio
async def test_generate_returns_prompt_tokens() -> None:
    """generate() must map usage.prompt_tokens -> LLMResponse.prompt_tokens."""
    mock_response = _make_mock_response(200, _make_success_body(prompt_tokens=88))

    ctx, _, _ = _patch_httpx_post(mock_response)
    with ctx:
        adapter = _make_adapter()
        result = await adapter.generate("System.", [LLMMessage.user("Hello.")])

    assert result.prompt_tokens == 88


@pytest.mark.asyncio
async def test_generate_returns_candidates_tokens_from_completion_tokens() -> None:
    """generate() must map usage.completion_tokens -> LLMResponse.candidates_tokens."""
    mock_response = _make_mock_response(200, _make_success_body(completion_tokens=33))

    ctx, _, _ = _patch_httpx_post(mock_response)
    with ctx:
        adapter = _make_adapter()
        result = await adapter.generate("System.", [LLMMessage.user("Hello.")])

    assert result.candidates_tokens == 33


@pytest.mark.asyncio
async def test_generate_returns_thoughts_tokens_as_none() -> None:
    """Groq does not expose hidden reasoning tokens; thoughts_tokens must be None."""
    mock_response = _make_mock_response(200, _make_success_body())

    ctx, _, _ = _patch_httpx_post(mock_response)
    with ctx:
        adapter = _make_adapter()
        result = await adapter.generate("System.", [LLMMessage.user("Hello.")])

    assert result.thoughts_tokens is None


@pytest.mark.asyncio
async def test_generate_returns_finish_reason() -> None:
    """generate() must map choices[0].finish_reason -> LLMResponse.finish_reason."""
    mock_response = _make_mock_response(200, _make_success_body(finish_reason="stop"))

    ctx, _, _ = _patch_httpx_post(mock_response)
    with ctx:
        adapter = _make_adapter()
        result = await adapter.generate("System.", [LLMMessage.user("Hello.")])

    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_generate_strips_whitespace_from_text() -> None:
    """generate() must strip leading/trailing whitespace from the response text."""
    mock_response = _make_mock_response(200, _make_success_body(text="  Hello!  "))

    ctx, _, _ = _patch_httpx_post(mock_response)
    with ctx:
        adapter = _make_adapter()
        result = await adapter.generate("System.", [LLMMessage.user("Hi.")])

    assert result.text == "Hello!"


# ---------------------------------------------------------------------------
# generate — error path tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_raises_llmerror_on_non_2xx() -> None:
    """HTTP 429 from Groq must raise LLMError with .status == 429."""
    mock_response = _make_mock_response(429, "Too Many Requests")

    ctx, _, _ = _patch_httpx_post(mock_response)
    with ctx:
        adapter = _make_adapter()
        with pytest.raises(LLMError) as exc_info:
            await adapter.generate("System.", [LLMMessage.user("Hello.")])

    assert exc_info.value.status == 429
    assert exc_info.value.body is not None


@pytest.mark.asyncio
async def test_generate_raises_llmerror_on_5xx() -> None:
    """HTTP 500 from Groq must raise LLMError with .status == 500."""
    mock_response = _make_mock_response(500, "Internal Server Error")

    ctx, _, _ = _patch_httpx_post(mock_response)
    with ctx:
        adapter = _make_adapter()
        with pytest.raises(LLMError) as exc_info:
            await adapter.generate("System.", [LLMMessage.user("Hello.")])

    assert exc_info.value.status == 500


@pytest.mark.asyncio
async def test_generate_raises_llmerror_on_401() -> None:
    """HTTP 401 (bad API key) must raise LLMError with .status == 401."""
    mock_response = _make_mock_response(
        401,
        {"error": {"message": "Invalid API Key.", "type": "invalid_request_error"}},
    )

    ctx, _, _ = _patch_httpx_post(mock_response)
    with ctx:
        adapter = _make_adapter()
        with pytest.raises(LLMError) as exc_info:
            await adapter.generate("System.", [LLMMessage.user("Hello.")])

    assert exc_info.value.status == 401


@pytest.mark.asyncio
async def test_generate_raises_llmerror_on_network_error() -> None:
    """httpx network errors (DNS, timeout) must be wrapped in LLMError(status=None)."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(
        side_effect=httpx.ConnectTimeout("Connection timed out")
    )

    with patch("httpx.AsyncClient", MagicMock(return_value=mock_client)):
        adapter = _make_adapter()
        with pytest.raises(LLMError) as exc_info:
            await adapter.generate("System.", [LLMMessage.user("Hello.")])

    assert exc_info.value.status is None
    assert "network:" in str(exc_info.value)


@pytest.mark.asyncio
async def test_generate_raises_llmerror_on_empty_choices() -> None:
    """A 200 response with an empty choices list must raise LLMError."""
    body: dict[str, Any] = {
        "id": "chatcmpl-xxx",
        "choices": [],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
    }
    mock_response = _make_mock_response(200, body)

    ctx, _, _ = _patch_httpx_post(mock_response)
    with ctx:
        adapter = _make_adapter()
        with pytest.raises(LLMError) as exc_info:
            await adapter.generate("System.", [LLMMessage.user("Hello.")])

    assert "empty response" in str(exc_info.value).lower()
    assert exc_info.value.status is None  # parsing error, not HTTP error


@pytest.mark.asyncio
async def test_generate_raises_llmerror_on_empty_content() -> None:
    """A 200 response where choices[0].message.content is '' must raise LLMError."""
    body = _make_success_body(text="")
    mock_response = _make_mock_response(200, body)

    ctx, _, _ = _patch_httpx_post(mock_response)
    with ctx:
        adapter = _make_adapter()
        with pytest.raises(LLMError) as exc_info:
            await adapter.generate("System.", [LLMMessage.user("Hello.")])

    assert "empty response" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# generate_stream — delegation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_stream_yields_full_text_as_single_chunk() -> None:
    """generate_stream() must yield the full response text as a single non-empty chunk.

    The GroqAdapter uses the single-chunk fallback path described in the
    LLMAdapter Protocol docstring — it delegates to generate() and yields
    the complete text once so the TTS sentence-splitter upstream still works.
    """
    expected_text = "What are your key strengths as a software engineer?"
    mock_response = _make_mock_response(200, _make_success_body(text=expected_text))

    ctx, _, _ = _patch_httpx_post(mock_response)
    with ctx:
        adapter = _make_adapter()
        chunks: list[str] = []
        async for chunk in adapter.generate_stream(
            "System.", [LLMMessage.user("Hello.")]
        ):
            chunks.append(chunk)

    assert len(chunks) == 1, f"Expected exactly 1 chunk, got {len(chunks)}"
    assert chunks[0] == expected_text
    # Protocol invariant: no empty strings yielded.
    assert all(c for c in chunks)
