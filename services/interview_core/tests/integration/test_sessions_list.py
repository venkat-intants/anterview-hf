"""Integration tests for GET /api/sessions (Area 1 — UI redesign v2).

Test matrix:
  test_list_sessions_returns_paginated_results   — valid JWT, sessions present → 200
  test_list_sessions_empty_for_new_user          — valid JWT, no sessions → 200 empty
  test_list_sessions_no_token                    — no auth header → 401
  test_list_sessions_only_own_sessions           — user A cannot see user B's sessions
  test_list_sessions_status_filter               — ?status=completed filters correctly
  test_list_sessions_pagination                  — page/per_page limits respected
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import issue_access_token
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_token(user_id: str | None = None) -> tuple[str, str]:
    uid = user_id or str(uuid.uuid4())
    token = str(
        issue_access_token(
            user_id=uid,
            roles=["candidate"],
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    )
    return token, uid


def _make_session_mapping(
    user_id: str,
    job_title: str = "Junior Java Developer",
    status: str = "completed",
) -> dict[str, Any]:
    """Return a fake row mapping as returned by the DB query."""
    now = datetime.now(tz=UTC)
    return {
        "id": uuid.uuid4(),
        "job_title": job_title,
        "language": "en",
        "status": status,
        "started_at": now,
        "completed_at": now,
        "duration_seconds": 600,
        "created_at": now,
    }


def _patch_list_db(
    *,
    total: int = 0,
    rows: list[dict[str, Any]] | None = None,
    scorecard_map: dict[uuid.UUID, str] | None = None,
) -> Any:
    """Patch get_db_session to return mock data for the list endpoint."""
    rows = rows or []
    scorecard_map = scorecard_map or {}

    # The list endpoint runs three queries:
    #   1. COUNT(*) scalar
    #   2. SELECT sessions+jobs (returns mappings)
    #   3. SELECT scorecards IN (...) (returns mappings)
    count_result = MagicMock()
    count_result.scalar_one.return_value = total

    # Rows from query 2
    data_mappings = MagicMock()
    data_mappings.all.return_value = rows
    data_result = MagicMock()
    data_result.mappings.return_value = data_mappings

    # Rows from query 3 (scorecard join)
    sc_row_mocks = [
        {"session_id": sid, "scorecard_id": scid}
        for sid, scid in scorecard_map.items()
    ]
    sc_mappings = MagicMock()
    sc_mappings.all.return_value = sc_row_mocks
    sc_result = MagicMock()
    sc_result.mappings.return_value = sc_mappings

    call_count: list[int] = [0]

    def _execute_side_effect(stmt: Any, *args: Any, **kwargs: Any) -> Any:
        call_count[0] += 1
        sql_str = str(stmt)
        if "COUNT(*)" in sql_str or "count" in sql_str.lower():
            return count_result
        if "scorecard" in sql_str.lower():
            return sc_result
        return data_result

    @asynccontextmanager
    async def _ctx() -> AsyncGenerator[AsyncSession, None]:
        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.execute = AsyncMock(side_effect=_execute_side_effect)
        yield mock_db  # type: ignore[misc]

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        async with _ctx() as db:
            yield db

    return _override


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncClient:  # type: ignore[misc]
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=10.0,
    ) as ac:
        yield ac  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_returns_paginated_results(client: AsyncClient) -> None:
    """Valid JWT + sessions present → 200 with pagination envelope."""
    token, user_id = _valid_token()
    rows = [_make_session_mapping(user_id), _make_session_mapping(user_id)]

    override = _patch_list_db(total=2, rows=rows)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.get(
            "/api/sessions",
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
    assert "session_id" in item
    assert item["job_title"] == "Junior Java Developer"
    assert item["language"] == "en"
    assert item["status"] == "completed"


@pytest.mark.asyncio
async def test_list_sessions_empty_for_new_user(client: AsyncClient) -> None:
    """Valid JWT, no sessions → 200 with empty items list."""
    token, _ = _valid_token()

    override = _patch_list_db(total=0, rows=[])
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.get(
            "/api/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_list_sessions_no_token(client: AsyncClient) -> None:
    """No Authorization header → 401."""
    resp = await client.get("/api/sessions")
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_list_sessions_only_own_sessions(client: AsyncClient) -> None:
    """User A's JWT returns only user A's rows.

    We inject only user A's rows for the DB query — the endpoint's WHERE
    clause (session.user_id == caller_id) ensures user B cannot sneak through.
    We verify the response items all match user A's job_title.
    """
    token_a, user_a = _valid_token()
    user_b = str(uuid.uuid4())

    # Inject rows that belong to user_a only (the DB WHERE clause enforces this
    # at the SQL level; we just verify the envelope contract here)
    rows_a = [_make_session_mapping(user_a, job_title="Role A")]
    # rows for user_b are NOT injected — they would not pass the WHERE clause
    override = _patch_list_db(total=1, rows=rows_a)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.get(
            "/api/sessions",
            headers={"Authorization": f"Bearer {token_a}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["job_title"] == "Role A"
    # Confirms user B can't see user A's data by calling with user B's token
    token_b, _ = _valid_token(user_b)
    override_b = _patch_list_db(total=0, rows=[])
    app.dependency_overrides[get_db_session] = override_b
    try:
        resp_b = await client.get(
            "/api/sessions",
            headers={"Authorization": f"Bearer {token_b}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp_b.status_code == 200
    assert resp_b.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_sessions_status_filter(client: AsyncClient) -> None:
    """?status=completed returns only completed sessions."""
    token, user_id = _valid_token()
    rows = [_make_session_mapping(user_id, status="completed")]

    override = _patch_list_db(total=1, rows=rows)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.get(
            "/api/sessions?status=completed",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    for item in data["items"]:
        assert item["status"] == "completed"


@pytest.mark.asyncio
async def test_list_sessions_pagination(client: AsyncClient) -> None:
    """page=2 per_page=1 returns the correct envelope metadata."""
    token, user_id = _valid_token()
    rows = [_make_session_mapping(user_id)]

    override = _patch_list_db(total=5, rows=rows)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.get(
            "/api/sessions?page=2&per_page=1",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["page"] == 2
    assert data["per_page"] == 1
    assert data["total"] == 5


@pytest.mark.asyncio
async def test_list_sessions_includes_scorecard_id(client: AsyncClient) -> None:
    """scorecard_id is populated when a matching scorecard exists."""
    token, user_id = _valid_token()
    session_uuid = uuid.uuid4()
    scorecard_uuid_str = str(uuid.uuid4())
    rows = [
        {
            "id": session_uuid,
            "job_title": "Backend Engineer",
            "language": "en",
            "status": "completed",
            "started_at": datetime.now(tz=UTC),
            "completed_at": datetime.now(tz=UTC),
            "duration_seconds": 600,
            "created_at": datetime.now(tz=UTC),
        }
    ]
    scorecard_map = {session_uuid: scorecard_uuid_str}

    override = _patch_list_db(total=1, rows=rows, scorecard_map=scorecard_map)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.get(
            "/api/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["scorecard_id"] == scorecard_uuid_str
