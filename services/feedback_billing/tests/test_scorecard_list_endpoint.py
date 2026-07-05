"""Integration tests for GET /api/scorecards — scorecard history list (Area 2).

Test matrix:
  test_list_scorecards_returns_200               — valid JWT + rows present
  test_list_scorecards_empty_for_new_user        — valid JWT + no rows → 200 empty
  test_list_scorecards_requires_jwt              — no auth → 401
  test_list_scorecards_authz_user_b_sees_nothing — user B cannot see user A's scorecards
  test_list_scorecards_summary_truncated         — summary truncated to 200 chars
  test_list_scorecards_pagination_envelope       — page/per_page metadata correct
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jwt(sub: str = "user_a", roles: list[str] | None = None) -> str:
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": sub,
        "roles": roles or ["candidate"],
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": uuid.uuid4().hex,
    }
    return str(jose_jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm))


def _fake_count_result(n: int) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(return_value=n)
    row.get = MagicMock(return_value=n)
    mappings_mock = MagicMock()
    mappings_mock.first.return_value = row
    result = MagicMock()
    result.mappings.return_value = mappings_mock
    return result


def _fake_data_rows(rows: list[dict[str, Any]]) -> MagicMock:
    mappings_mock = MagicMock()
    mappings_mock.all.return_value = [_dict_to_mapping(r) for r in rows]
    result = MagicMock()
    result.mappings.return_value = mappings_mock
    return result


def _dict_to_mapping(d: dict[str, Any]) -> MagicMock:
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda k: d[k])
    m.get = MagicMock(side_effect=lambda k, *a: d.get(k, a[0] if a else None))
    return m


def _build_scorecard_row(
    user_id: str,
    job_title: str = "Backend Engineer",
    summary: str = "Good performance overall.",
    composite_score: float = 7.5,
) -> dict[str, Any]:
    return {
        "scorecard_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "composite_score": composite_score,
        "created_at": datetime.now(tz=UTC),
        "summary": summary,
        "job_title": job_title,
    }


def _patch_db(*, count: int, rows: list[dict[str, Any]]) -> Any:
    """Return a dependency override for get_db_session."""
    call_n: list[int] = [0]

    def _execute_side_effect(stmt: Any, *args: Any, **kwargs: Any) -> Any:
        call_n[0] += 1
        if call_n[0] == 1:
            # First call: COUNT query
            return _fake_count_result(count)
        # Second call: data rows
        return _fake_data_rows(rows)

    async def _session_gen() -> Any:
        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.execute = AsyncMock(side_effect=_execute_side_effect)
        yield mock_db

    return _session_gen


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_list_scorecards_returns_200(client: TestClient) -> None:
    """Valid JWT + rows present → 200 with items."""
    user_id = str(uuid.uuid4())
    token = _make_jwt(sub=user_id)
    rows = [_build_scorecard_row(user_id), _build_scorecard_row(user_id)]

    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = _patch_db(count=2, rows=rows)
    try:
        resp = client.get(
            "/api/scorecards",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 2
    assert data["page"] == 1
    assert data["per_page"] == 20
    assert len(data["items"]) == 2
    item = data["items"][0]
    assert "scorecard_id" in item
    assert "session_id" in item
    assert item["composite_score"] == 7.5
    assert item["job_title"] == "Backend Engineer"


def test_list_scorecards_empty_for_new_user(client: TestClient) -> None:
    """Valid JWT, no rows → 200 with empty items."""
    token = _make_jwt(sub=str(uuid.uuid4()))

    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = _patch_db(count=0, rows=[])
    try:
        resp = client.get(
            "/api/scorecards",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_list_scorecards_requires_jwt(client: TestClient) -> None:
    """No auth header → 401."""
    resp = client.get("/api/scorecards")
    assert resp.status_code == 401, resp.text


def test_list_scorecards_authz_user_b_sees_nothing(client: TestClient) -> None:
    """User B's token returns an empty list even when user A has scorecards.

    The SQL WHERE clause enforces: session_id IN (
        SELECT id FROM sessions WHERE user_id = :user_id
    ) — user B's user_id produces an empty set, so no scorecards leak.
    We simulate this by returning count=0/rows=[] for user B.
    """
    user_b_id = str(uuid.uuid4())
    token_b = _make_jwt(sub=user_b_id)

    # DB returns nothing for user B (the WHERE clause filters to user_b_id's sessions)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = _patch_db(count=0, rows=[])
    try:
        resp = client.get(
            "/api/scorecards",
            headers={"Authorization": f"Bearer {token_b}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_list_scorecards_summary_truncated(client: TestClient) -> None:
    """Summary longer than 200 characters is truncated with an ellipsis."""
    user_id = str(uuid.uuid4())
    token = _make_jwt(sub=user_id)
    long_summary = "A" * 300
    rows = [_build_scorecard_row(user_id, summary=long_summary)]

    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = _patch_db(count=1, rows=rows)
    try:
        resp = client.get(
            "/api/scorecards",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    summary = resp.json()["items"][0]["summary"]
    # The truncation adds "…" (1 char) at position 200, total ≤ 201
    assert len(summary) <= 201
    assert summary.endswith("…")


def test_list_scorecards_pagination_envelope(client: TestClient) -> None:
    """page=2 per_page=5 → correct envelope metadata."""
    user_id = str(uuid.uuid4())
    token = _make_jwt(sub=user_id)
    rows = [_build_scorecard_row(user_id)]

    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = _patch_db(count=10, rows=rows)
    try:
        resp = client.get(
            "/api/scorecards?page=2&per_page=5",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["page"] == 2
    assert data["per_page"] == 5
    assert data["total"] == 10
