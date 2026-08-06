"""Integration tests for POST /internal/score — S5-006.

Tests:
  1. test_internal_score_returns_201  — valid body + mocked scorer → 201
  2. test_internal_score_requires_jwt — no auth → 401
  3. test_internal_score_duplicate_session — second call → 409
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.main import app
from app.scorer import ScoringError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jwt(sub: str = "interview_core", roles: list[str] | None = None) -> str:
    """Sign a valid short-lived JWT using the test jwt_secret."""
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": sub,
        "roles": roles or ["service"],
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": uuid.uuid4().hex,
    }
    return str(
        jose_jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    )


_VALID_BODY: dict[str, Any] = {
    "session_id": str(uuid.uuid4()),
    "job_title": "Junior Java Developer",
    "experience_level": "entry",
    "language": "en",
    "turns": [
        {"role": "ai", "text": "Tell me about yourself."},
        {"role": "user", "text": "I am a backend developer."},
    ],
}

_GOOD_SCORECARD_ID = str(uuid.uuid4())
_GOOD_SCORES: dict[str, int] = {
    "communication": 7,
    "technical": 6,
    "problem_solving": 8,
    "confidence": 7,
}
_GOOD_COMPOSITE = 7.05

# score_session now returns (scorecard_id, scores, composite).
_GOOD_SCORE_SESSION_RETURN = (_GOOD_SCORECARD_ID, _GOOD_SCORES, _GOOD_COMPOSITE)


def _mock_db_session(candidate_name: str | None = "Ravi Kumar") -> AsyncMock:
    """Build a minimal AsyncSession mock for dependency override.

    ``scalar`` answers the candidate-name lookup the endpoint runs before
    scoring; pass None to simulate a session whose user has no name on file.
    """
    mock_db = AsyncMock(spec=AsyncSession)
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.scalar = AsyncMock(return_value=candidate_name)
    return mock_db


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# test_internal_score_returns_201
# ---------------------------------------------------------------------------


def test_internal_score_returns_201(client: TestClient) -> None:
    """Valid body + mocked scorer → 201 with scorecard_id and composite_score."""
    token = _make_jwt()

    mock_db = _mock_db_session()

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db

    try:
        with patch(
            "app.routers.score.score_session",
            new_callable=AsyncMock,
            return_value=_GOOD_SCORE_SESSION_RETURN,
        ):
            resp = client.post(
                "/internal/score",
                json=_VALID_BODY,
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["scorecard_id"] == _GOOD_SCORECARD_ID
    assert body["composite_score"] == _GOOD_COMPOSITE
    assert body["scores"] == _GOOD_SCORES


# ---------------------------------------------------------------------------
# test_internal_score_accepts_jd_text
# ---------------------------------------------------------------------------


def test_internal_score_accepts_jd_text(client: TestClient) -> None:
    """Body with jd_text → 201; jd_text is forwarded to score_session."""
    token = _make_jwt()

    mock_db = _mock_db_session()

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db

    try:
        with patch(
            "app.routers.score.score_session",
            new_callable=AsyncMock,
            return_value=_GOOD_SCORE_SESSION_RETURN,
        ) as mock_score:
            resp = client.post(
                "/internal/score",
                json={
                    **_VALID_BODY,
                    "session_id": str(uuid.uuid4()),
                    "jd_text": "Must know Spring Boot and Kubernetes.",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 201, resp.text
    # jd_text must be passed through to the scorer.
    assert mock_score.await_args.kwargs["jd_text"] == (
        "Must know Spring Boot and Kubernetes."
    )


# ---------------------------------------------------------------------------
# PDF scorecard wiring — score_session only renders a PDF when it is handed a
# candidate name, and can only write report_pdf_key back with its own factory.
# ---------------------------------------------------------------------------


def test_internal_score_passes_candidate_name_for_pdf(client: TestClient) -> None:
    """The endpoint resolves the candidate name server-side and forwards it."""
    token = _make_jwt()
    mock_db = _mock_db_session(candidate_name="Ravi Kumar")

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db

    try:
        with patch(
            "app.routers.score.score_session",
            new_callable=AsyncMock,
            return_value=_GOOD_SCORE_SESSION_RETURN,
        ) as mock_score:
            resp = client.post(
                "/internal/score",
                json={**_VALID_BODY, "session_id": str(uuid.uuid4())},
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 201, resp.text
    kwargs = mock_score.await_args.kwargs
    assert kwargs["candidate_name"] == "Ravi Kumar"
    # The name must not be accepted from the wire — it is looked up in the DB.
    assert "candidate_name" not in _VALID_BODY
    mock_db.scalar.assert_awaited_once()
    assert "db_session_factory" in kwargs


def test_candidate_name_lookup_releases_the_transaction_before_scoring(
    client: TestClient,
) -> None:
    """The name lookup must not hold a connection across the Gemini call.

    The lookup is the first DB touch in this endpoint, and AsyncSession
    autobegins on the first statement and holds the pooled connection until
    commit/rollback. The next thing the request does is call Gemini with a 60s
    timeout plus retries — so without an explicit rollback each in-flight
    scoring parks a connection idle-in-transaction for a minute-plus, and
    database_pool_size defaults to 5.

    Asserting on ORDER, not just on the call: a rollback that happened after
    score_session returned would satisfy `assert_awaited` and still leave the
    pool pinned for the whole call, which is the entire defect.
    """
    token = _make_jwt()
    mock_db = _mock_db_session(candidate_name="Ravi Kumar")

    events: list[str] = []
    mock_db.rollback = AsyncMock(side_effect=lambda: events.append("rollback"))

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db

    async def _record_score(**_kwargs: object) -> tuple[str, dict, float]:
        events.append("score_session")
        return _GOOD_SCORE_SESSION_RETURN

    try:
        with patch("app.routers.score.score_session", new=_record_score):
            resp = client.post(
                "/internal/score",
                json={**_VALID_BODY, "session_id": str(uuid.uuid4())},
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 201, resp.text
    assert events == ["rollback", "score_session"], (
        f"expected the read transaction to be released BEFORE scoring, got {events}"
    )


def test_internal_score_survives_candidate_name_lookup_failure(
    client: TestClient,
) -> None:
    """A failed name lookup costs the PDF header, never the scorecard."""
    token = _make_jwt()
    mock_db = _mock_db_session()
    mock_db.scalar = AsyncMock(side_effect=RuntimeError("connection reset"))
    mock_db.rollback = AsyncMock()

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db

    try:
        with patch(
            "app.routers.score.score_session",
            new_callable=AsyncMock,
            return_value=_GOOD_SCORE_SESSION_RETURN,
        ) as mock_score:
            resp = client.post(
                "/internal/score",
                json={**_VALID_BODY, "session_id": str(uuid.uuid4())},
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 201, resp.text
    assert mock_score.await_args.kwargs["candidate_name"] == ""
    # The aborted statement must be rolled back or the INSERT that follows
    # would fail on the poisoned transaction.
    mock_db.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# test_internal_score_requires_jwt
# ---------------------------------------------------------------------------


def test_internal_score_requires_jwt(client: TestClient) -> None:
    """No Authorization header → 401."""
    resp = client.post("/internal/score", json=_VALID_BODY)
    assert resp.status_code == 401


def test_internal_score_rejects_invalid_jwt(client: TestClient) -> None:
    """Garbage token → 401."""
    resp = client.post(
        "/internal/score",
        json=_VALID_BODY,
        headers={"Authorization": "Bearer not.a.real.jwt"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# test_internal_score_duplicate_session
# ---------------------------------------------------------------------------


def test_internal_score_duplicate_session(client: TestClient) -> None:
    """Second call for same session_id → 409 Conflict."""
    token = _make_jwt()

    mock_db = _mock_db_session()

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db

    try:
        with patch(
            "app.routers.score.score_session",
            new_callable=AsyncMock,
            side_effect=IntegrityError(
                statement="INSERT INTO scorecards",
                params={},
                orig=Exception("duplicate key value violates unique constraint"),
            ),
        ):
            resp = client.post(
                "/internal/score",
                json={**_VALID_BODY, "session_id": str(uuid.uuid4())},
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# test_internal_score_gemini_failure_returns_502
# ---------------------------------------------------------------------------


def test_internal_score_gemini_failure_returns_502(client: TestClient) -> None:
    """ScoringError from scorer → 502 Bad Gateway."""
    token = _make_jwt()

    mock_db = _mock_db_session()

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db

    try:
        with patch(
            "app.routers.score.score_session",
            new_callable=AsyncMock,
            side_effect=ScoringError("Gemini returned HTTP 500"),
        ):
            resp = client.post(
                "/internal/score",
                json={**_VALID_BODY, "session_id": str(uuid.uuid4())},
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# BC-2: /internal/* service-role gate
# Only tokens with roles=["service"] may call /internal/score.
# ---------------------------------------------------------------------------


def test_internal_score_rejects_candidate_token(client: TestClient) -> None:
    """A candidate JWT (no 'service' role) → 403 Forbidden."""
    token = _make_jwt(sub="candidate123", roles=["candidate"])
    resp = client.post(
        "/internal/score",
        json=_VALID_BODY,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_internal_score_rejects_guest_token(client: TestClient) -> None:
    """A guest_candidate JWT → 403 Forbidden (stops denial-of-wallet attacks)."""
    token = _make_jwt(sub="guest123", roles=["guest_candidate"])
    resp = client.post(
        "/internal/score",
        json=_VALID_BODY,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_internal_score_rejects_hr_token(client: TestClient) -> None:
    """An HR manager JWT — valid user token but not a service token → 403."""
    token = _make_jwt(sub="hr123", roles=["hr_manager"])
    resp = client.post(
        "/internal/score",
        json=_VALID_BODY,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_internal_score_accepts_service_token(client: TestClient) -> None:
    """A service JWT (roles=['service'], sub='interview_core') → proceeds to scoring."""
    token = _make_jwt(sub="interview_core", roles=["service"])
    mock_db = _mock_db_session()

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db

    try:
        with patch(
            "app.routers.score.score_session",
            new_callable=AsyncMock,
            return_value=_GOOD_SCORE_SESSION_RETURN,
        ):
            resp = client.post(
                "/internal/score",
                json=_VALID_BODY,
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# BC-3: token-epoch check on /internal/score
# ---------------------------------------------------------------------------


def test_internal_score_rejects_revoked_service_token(client: TestClient) -> None:
    """A service token whose iat predates the epoch → 401 (token revoked)."""
    from datetime import timedelta
    from unittest.mock import patch as _patch  # noqa: PLC0415

    # Issue a token with iat = now - 10 min
    old_iat = datetime.now(tz=UTC) - timedelta(minutes=10)
    claims: dict[str, Any] = {
        "sub": "interview_core",
        "roles": ["service"],
        "iat": old_iat,
        "exp": datetime.now(tz=UTC) + timedelta(minutes=5),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": uuid.uuid4().hex,
    }
    from jose import jwt as jose_jwt  # noqa: PLC0415
    token = str(jose_jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm))

    # Simulate epoch set 5 minutes ago (after the iat), so the token is stale.
    stale_epoch = int((datetime.now(tz=UTC) - timedelta(minutes=5)).timestamp())

    async def _mock_redis_get(key: str) -> str | None:
        return str(stale_epoch)

    with _patch("app.routers.score.get_redis") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=_mock_redis_get)
        mock_get_redis.return_value = mock_redis

        resp = client.post(
            "/internal/score",
            json=_VALID_BODY,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 401
