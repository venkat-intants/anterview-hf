"""Client for feedback_billing's Gemini embeddings + match-reason (semantic search).

data_gateway owns the applicants table and runs the pgvector query, but the
Gemini embedding model lives in feedback_billing (the embeddings/scoring owner).
Same internal-JWT pattern as scoring_client: mint a short token signed with the
shared secret and POST over the internal network.

PII: resume text + the HR query travel on the internal network only, never logged.
Both helpers are best-effort by design — embedding/explanation outages must never
block resume upload or the applicant list.
"""

from __future__ import annotations

import httpx
import structlog
from shared.auth.jwt import SERVICE_TOKEN_TTL_SECONDS, issue_access_token

from app.config import settings

log = structlog.get_logger(__name__)


class EmbeddingError(Exception):
    """Raised when the embedding service cannot be reached or returns an error."""


# The service identity this process presents to feedback_billing. It must be
# in feedback_billing's _ALLOWED_SERVICE_SUBS.
_SERVICE_SUB = "data_gateway"


def _internal_token(acting_user_id: str) -> str:
    """Mint the service token for a feedback_billing /internal/* call.

    `sub` is the SERVICE, not the human who triggered the call. It used to be
    the acting HR user's UUID, which made a service identity and a user
    identity indistinguishable on the wire — both signed with the same
    jwt_secret, differing only by a roles claim. feedback_billing pins the
    allowed subjects, and a per-user UUID cannot be pinned.

    The human is carried in `act_sub` purely for attribution in the callee's
    logs. It is NOT authoritative and must never be used for an authorisation
    decision — the caller controls it, and the caller is the one being
    authorised.
    """
    return issue_access_token(
        user_id=_SERVICE_SUB,
        roles=["service"],
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        extra_claims={"act_sub": acting_user_id},
        # 60s, not the 15-minute user default. A service token's `sub` is a
        # service name, so logout_all (which only ever writes
        # auth_epoch:<user-uuid>) cannot revoke it — its lifetime is its only
        # containment. interview_core's minter always used 60s; these three
        # silently used 900s because that was issue_access_token's default and
        # there was no parameter to say otherwise.
        ttl_seconds=SERVICE_TOKEN_TTL_SECONDS,
    )


async def embed_texts_remote(
    *,
    texts: list[str],
    task_type: str,
    acting_user_id: str,
) -> list[list[float]]:
    """Embed texts via feedback_billing. task_type: 'document' | 'query'.

    Returns one vector per input (same order). Raises EmbeddingError on failure.
    """
    if not texts:
        return []
    url = f"{settings.feedback_billing_url}/internal/embed"
    token = _internal_token(acting_user_id)
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"texts": texts, "task_type": task_type},
            )
    except httpx.RequestError as exc:
        raise EmbeddingError(f"embedding service unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise EmbeddingError(f"embedding service returned HTTP {resp.status_code}: {resp.text[:160]}")
    vectors: list[list[float]] = resp.json().get("embeddings", [])
    return vectors


async def embed_one_remote(*, text: str, task_type: str, acting_user_id: str) -> list[float]:
    """Convenience: embed a single text. Returns [] if the input is empty."""
    if not text or not text.strip():
        return []
    out = await embed_texts_remote(texts=[text], task_type=task_type, acting_user_id=acting_user_id)
    return out[0] if out else []


async def why_match_remote(*, resume_text: str, query: str, acting_user_id: str) -> str:
    """One-sentence 'why this candidate matched' via feedback_billing."""
    url = f"{settings.feedback_billing_url}/internal/why-match"
    token = _internal_token(acting_user_id)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"resume_text": resume_text, "query": query},
            )
    except httpx.RequestError as exc:
        raise EmbeddingError(f"match-reason service unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise EmbeddingError(f"match-reason service returned HTTP {resp.status_code}: {resp.text[:160]}")
    reason: str = resp.json().get("reason", "")
    return reason


def to_pgvector_literal(vec: list[float]) -> str:
    """Format a vector as a pgvector text literal: '[0.1,0.2,...]'.

    Bound as a text param and cast with ``CAST(:p AS halfvec)`` in the query.
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"
