"""Unit tests for the Gemini embedder (semantic resume search).

Gemini HTTP is mocked so these run offline (no network, no key). Mirrors the
mocking style of test_resume_scorer.py.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.embedder import (
    _REASON_PROMPT,
    EmbeddingError,
    embed_texts,
    generate_match_reason,
)

_DIMS = 8
_SETTINGS = SimpleNamespace(
    gemini_api_base_url="https://example.test/v1beta",
    gemini_model="gemini-flash-lite-latest",
    gemini_api_key="test-key",
    # Provider-resolved names, read by the call sites now that Groq is
    # selectable. The gemini_* ones stay so nothing reading them directly
    # changes behaviour.
    llm_provider="gemini",
    llm_api_base_url="https://example.test/v1beta",
    llm_model="gemini-flash-lite-latest",
    llm_api_key="test-key",
    embedding_model="gemini-embedding-001",
    embedding_dimensions=_DIMS,
)


class _FakeResp:
    def __init__(self, status_code: int, data: dict[str, Any]) -> None:
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data)

    def json(self) -> dict[str, Any]:
        return self._data


class _FakeClient:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def post(self, *_: Any, **__: Any) -> _FakeResp:
        return self._resp


def _patch(monkeypatch: pytest.MonkeyPatch, resp: _FakeResp) -> None:
    monkeypatch.setattr("app.embedder.httpx.AsyncClient", lambda *a, **k: _FakeClient(resp))


def _batch(vectors: list[list[float]]) -> dict[str, Any]:
    return {"embeddings": [{"values": v} for v in vectors]}


@pytest.mark.asyncio
async def test_embed_texts_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeResp(200, _batch([[0.1] * _DIMS, [0.2] * _DIMS])))
    out = await embed_texts(texts=["alpha", "beta"], task_type="document", settings=_SETTINGS)
    assert len(out) == 2
    assert out[0] == pytest.approx([0.1] * _DIMS)
    assert out[1] == pytest.approx([0.2] * _DIMS)


@pytest.mark.asyncio
async def test_embed_texts_blank_input_becomes_zero_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even though the API returns a vector for the blank slot, the blank position
    # is overwritten with zeros so callers never get a misleading embedding.
    _patch(monkeypatch, _FakeResp(200, _batch([[0.3] * _DIMS, [0.9] * _DIMS])))
    out = await embed_texts(texts=["real text", "   "], task_type="document", settings=_SETTINGS)
    assert out[0] == pytest.approx([0.3] * _DIMS)
    assert out[1] == [0.0] * _DIMS


@pytest.mark.asyncio
async def test_embed_texts_empty_list_short_circuits() -> None:
    # No client is constructed when there is nothing to embed.
    assert await embed_texts(texts=[], task_type="query", settings=_SETTINGS) == []


@pytest.mark.asyncio
async def test_embed_texts_count_mismatch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeResp(200, _batch([[0.1] * _DIMS])))  # 1 vec for 2 inputs
    with pytest.raises(EmbeddingError):
        await embed_texts(texts=["a", "b"], task_type="document", settings=_SETTINGS)


@pytest.mark.asyncio
async def test_embed_texts_wrong_dims_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeResp(200, _batch([[0.1] * (_DIMS - 1)])))
    with pytest.raises(EmbeddingError):
        await embed_texts(texts=["a"], task_type="document", settings=_SETTINGS)


@pytest.mark.asyncio
async def test_embed_texts_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # 400 is NOT a retry status → fails fast (no backoff sleeps).
    _patch(monkeypatch, _FakeResp(400, {"error": "bad request"}))
    with pytest.raises(EmbeddingError):
        await embed_texts(texts=["a"], task_type="document", settings=_SETTINGS)


@pytest.mark.asyncio
async def test_generate_match_reason_trims(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason now comes back wrapped in a JSON object.

    It moved onto the shared provider dispatcher so it can be served by Groq
    like the rest of the service, and that caller is JSON-only — so the
    transport is patched on ``shared.llm._recovery`` rather than on
    ``app.embedder``, which no longer makes this call itself.
    """
    payload = {
        "candidates": [
            {"content": {"parts": [{"text": '{"reason": "  Strong Kubernetes match.  "}'}]}}
        ]
    }
    monkeypatch.setattr(
        "shared.llm._recovery.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(_FakeResp(200, payload)),
    )
    reason = await generate_match_reason(
        resume_text="resume", query="container orchestration", settings=_SETTINGS
    )
    assert reason == "Strong Kubernetes match."


@pytest.mark.asyncio
async def test_generate_match_reason_rejects_an_empty_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty string would render as a blank explanation next to a search
    hit, which reads as "no reason to match" rather than "we could not say"."""
    monkeypatch.setattr(
        "shared.llm._recovery.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(
            _FakeResp(200, {"candidates": [{"content": {"parts": [{"text": '{"reason": ""}'}]}}]})
        ),
    )
    with pytest.raises(EmbeddingError):
        await generate_match_reason(resume_text="r", query="q", settings=_SETTINGS)


# ---------------------------------------------------------------------------
# The prompt and the parser have to agree
# ---------------------------------------------------------------------------
def _contract_from_prompt() -> dict[str, object]:
    """The JSON example the prompt shows the model, parsed.

    Deriving the fixture from the prompt is the whole point. The two tests
    above hand the parser ``{"reason": ...}`` — a key the prompt did not ask
    for — so they passed for months while production returned 502, because
    each asserted the reader and *assumed* the writer. Reading the contract out
    of the prompt is what couples the two halves.
    """
    example = _REASON_PROMPT[_REASON_PROMPT.rindex("{") : _REASON_PROMPT.rindex("}") + 1]
    parsed: dict[str, object] = json.loads(example)
    return parsed


def test_the_prompt_states_a_parseable_contract() -> None:
    """call_llm_json asks the provider for a JSON object, so a prompt that ends
    "just the sentence" is asking for one thing over a transport that demands
    another. The model obeys the transport and picks its own key."""
    assert _contract_from_prompt()


def test_the_prompt_names_the_key_the_code_reads() -> None:
    """The failure this closes: gpt-oss-120b answered under "match", the reader
    looked up "reason", and a good explanation surfaced to HR as a 502."""
    assert set(_contract_from_prompt()) == {"reason"}


@pytest.mark.asyncio
async def test_an_answer_shaped_like_the_prompt_asked_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end over the contract: a model that does exactly what the prompt
    says must not be rejected. Fixture keys come from the prompt, not from a
    literal typed here — that is what makes this fail if the two drift."""
    answer = json.dumps({k: "Ships Python payment APIs." for k in _contract_from_prompt()})
    monkeypatch.setattr(
        "shared.llm._recovery.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(
            _FakeResp(200, {"candidates": [{"content": {"parts": [{"text": answer}]}}]})
        ),
    )
    reason = await generate_match_reason(
        resume_text="resume", query="python backend", settings=_SETTINGS
    )
    assert reason == "Ships Python payment APIs."


def test_the_prompt_does_not_also_ask_for_bare_prose() -> None:
    """Both instructions at once is how this broke: the model has to pick, and
    the one it picks is not the one the code reads."""
    assert "just the sentence" not in _REASON_PROMPT
