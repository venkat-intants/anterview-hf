"""Unit tests for the Gemini embedder (semantic resume search).

Gemini HTTP is mocked so these run offline (no network, no key). Mirrors the
mocking style of test_resume_scorer.py.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.embedder import EmbeddingError, embed_texts, generate_match_reason

_DIMS = 8
_SETTINGS = SimpleNamespace(
    gemini_api_base_url="https://example.test/v1beta",
    gemini_model="gemini-flash-lite-latest",
    gemini_api_key="test-key",
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
    payload = {"candidates": [{"content": {"parts": [{"text": "  Strong Kubernetes match.  "}]}}]}
    _patch(monkeypatch, _FakeResp(200, payload))
    reason = await generate_match_reason(
        resume_text="resume", query="container orchestration", settings=_SETTINGS
    )
    assert reason == "Strong Kubernetes match."
