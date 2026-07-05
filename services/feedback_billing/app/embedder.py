"""Gemini embeddings + match-reason generation for semantic resume search.

HR workflow — semantic candidate discovery. STATELESS: returns vectors / text;
the caller (data_gateway) persists embeddings and runs the pgvector query.

Two capabilities, both on the free, no-card Gemini API (same key as the scorer):
  * embed_texts()          — embed resumes (at ingest) or an HR query (at search).
  * generate_match_reason() — a one-line "why this candidate matches" explanation.

Embedding model: gemini-embedding-001 at outputDimensionality=3072 (its native
size, already L2-normalized) → drops straight into applicants.embedding
halfvec(3072). taskType is asymmetric: RETRIEVAL_DOCUMENT for resumes,
RETRIEVAL_QUERY for the search phrase — this materially improves retrieval.

PII: NEVER log resume text or embedding values.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from app.config import Settings

log = structlog.get_logger(__name__)

# gemini-embedding-001 accepts ~2048 input tokens; ~8000 chars is a safe ceiling
# (matches the resume scorer's truncation) and keeps batch payloads small.
_MAX_EMBED_CHARS: int = 8000
_RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS: int = 4
_BACKOFF_BASE_SECONDS: float = 1.0

# data_gateway passes "document" (resumes) or "query" (the HR search phrase).
_TASK_TYPES: dict[str, str] = {
    "document": "RETRIEVAL_DOCUMENT",
    "query": "RETRIEVAL_QUERY",
}


class EmbeddingError(Exception):
    """Raised when the embedding/explanation pipeline fails (Gemini error)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


async def _post_with_retry(
    url: str, body: dict[str, Any], *, timeout: float, headers: dict[str, str] | None = None
) -> httpx.Response:
    """POST with the same backoff/retry policy as the resume scorer."""
    response: httpx.Response | None = None
    last_error = "no attempt made"
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = await client.post(url, json=body, headers=headers)
            except httpx.RequestError as exc:
                response = None
                last_error = f"request error: {exc}"
            else:
                if response.status_code == 200:
                    return response
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code not in _RETRY_STATUSES:
                    break
            if attempt < _MAX_ATTEMPTS - 1:
                await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2**attempt))
    raise EmbeddingError(f"Gemini call failed after {_MAX_ATTEMPTS} attempt(s): {last_error}")


async def embed_texts(
    *,
    texts: list[str],
    task_type: str,
    settings: Settings,
) -> list[list[float]]:
    """Embed a batch of texts. Returns one 3072-float vector per input (same order).

    task_type: "document" (resumes) or "query" (the HR search phrase).
    Empty/whitespace inputs yield a zero vector (kept positional so the caller's
    index alignment never breaks).
    """
    if not texts:
        return []
    gemini_task = _TASK_TYPES.get(task_type, "RETRIEVAL_DOCUMENT")
    model = settings.embedding_model
    dims = settings.embedding_dimensions

    requests: list[dict[str, Any]] = []
    blank_positions: set[int] = set()
    for i, t in enumerate(texts):
        clean = (t or "").strip()[:_MAX_EMBED_CHARS]
        if not clean:
            blank_positions.add(i)
            clean = " "  # Gemini rejects empty content; result is discarded below.
        requests.append(
            {
                "model": f"models/{model}",
                "content": {"parts": [{"text": clean}]},
                "taskType": gemini_task,
                "outputDimensionality": dims,
            }
        )

    # Auth via x-goog-api-key header (not ?key=) so the key never lands in
    # request URLs / proxy access logs.
    url = f"{settings.gemini_api_base_url}/models/{model}:batchEmbedContents"
    headers = {"x-goog-api-key": settings.gemini_api_key}
    response = await _post_with_retry(url, {"requests": requests}, timeout=60.0, headers=headers)

    try:
        embeddings = response.json()["embeddings"]
    except (KeyError, ValueError) as exc:
        raise EmbeddingError(f"Failed to read Gemini embedding response: {exc}") from exc
    if len(embeddings) != len(texts):
        raise EmbeddingError(
            f"Gemini returned {len(embeddings)} embeddings for {len(texts)} inputs"
        )

    vectors: list[list[float]] = []
    for i, item in enumerate(embeddings):
        if i in blank_positions:
            vectors.append([0.0] * dims)
            continue
        values = item.get("values") or item.get("value")
        if not values or len(values) != dims:
            raise EmbeddingError(f"embedding {i} had {len(values or [])} dims, expected {dims}")
        vectors.append([float(v) for v in values])

    log.info("embedder.complete", count=len(vectors), task_type=task_type, model=model)
    return vectors


_REASON_PROMPT = """\
You are a recruiter explaining, in ONE short sentence (max 30 words), why a
candidate's resume matches an HR search. Be concrete and ground it in the resume;
if the match is weak, say so plainly. No preamble, no markdown — just the sentence.

HR is searching for: {{QUERY}}

Resume:
{{RESUME}}"""


async def generate_match_reason(
    *,
    resume_text: str,
    query: str,
    settings: Settings,
) -> str:
    """One-sentence 'why this candidate matched the query' (lazy, on demand)."""
    prompt = (
        _REASON_PROMPT.replace("{{QUERY}}", query.strip()[:300]).replace(
            "{{RESUME}}", (resume_text or "").strip()[:6000]
        )
    )
    url = f"{settings.gemini_api_base_url}/models/{settings.gemini_model}:generateContent"
    headers = {"x-goog-api-key": settings.gemini_api_key}
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 256},
    }
    response = await _post_with_retry(url, body, timeout=30.0, headers=headers)
    try:
        text: str = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, ValueError) as exc:
        raise EmbeddingError(f"Failed to read Gemini reason response: {exc}") from exc
    return text.strip()[:400]
