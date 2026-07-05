"""Unit tests for semantic resume search — the embedding client + the
best-effort embed hook. feedback_billing HTTP is mocked; the DB is an AsyncMock.

The critical guarantee under test: embedding is BEST-EFFORT — an embedding-service
outage must never raise out of the ingest path.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest


# ---------------------------------------------------------------------------
# to_pgvector_literal — the format the hybrid SQL casts to halfvec
# ---------------------------------------------------------------------------
def test_to_pgvector_literal_formats_and_handles_empty() -> None:
    from app.embedding_client import to_pgvector_literal

    assert to_pgvector_literal([]) == "[]"
    assert to_pgvector_literal([0.5, -1.0, 2.0]) == "[0.5,-1.0,2.0]"


# ---------------------------------------------------------------------------
# embedding_client — remote calls to feedback_billing (httpx mocked)
# ---------------------------------------------------------------------------
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


@pytest.mark.asyncio
async def test_embed_one_remote_empty_text_short_circuits() -> None:
    from app.embedding_client import embed_one_remote

    # Empty/whitespace text returns [] without minting a token or any HTTP.
    assert await embed_one_remote(text="   ", task_type="document", acting_user_id="u") == []


@pytest.mark.asyncio
async def test_embed_texts_remote_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.embedding_client import embed_texts_remote

    monkeypatch.setattr(
        "app.embedding_client.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(_FakeResp(200, {"embeddings": [[0.1, 0.2]]})),
    )
    out = await embed_texts_remote(texts=["x"], task_type="query", acting_user_id="u")
    assert out == [[0.1, 0.2]]


@pytest.mark.asyncio
async def test_embed_texts_remote_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.embedding_client import EmbeddingError, embed_texts_remote

    monkeypatch.setattr(
        "app.embedding_client.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(_FakeResp(502, {"detail": "down"})),
    )
    with pytest.raises(EmbeddingError):
        await embed_texts_remote(texts=["x"], task_type="query", acting_user_id="u")


# ---------------------------------------------------------------------------
# _embed_applicant — best-effort guarantee (never raises out of ingest)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_embed_applicant_swallows_service_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.embedding_client import EmbeddingError
    from app.routers import hr_applicants as hra

    async def _boom(**_: Any) -> list[float]:
        raise EmbeddingError("embedding service down")

    monkeypatch.setattr(hra, "embed_one_remote", _boom)

    db = AsyncMock()
    applicant = SimpleNamespace(
        id=uuid.uuid4(), company_id=uuid.uuid4(), resume_text="a real resume"
    )
    # Must NOT raise — the applicant has already been committed by the caller.
    await hra._embed_applicant(db, applicant, uuid.uuid4())


@pytest.mark.asyncio
async def test_embed_applicant_noop_on_empty_resume() -> None:
    from app.routers import hr_applicants as hra

    db = AsyncMock()
    applicant = SimpleNamespace(id=uuid.uuid4(), company_id=uuid.uuid4(), resume_text="   ")
    await hra._embed_applicant(db, applicant, uuid.uuid4())
    db.execute.assert_not_called()  # no embedding write for an empty resume
