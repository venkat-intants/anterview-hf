"""Integration tests for GET /api/avatars and the avatar_id field on POST /api/sessions.

Strategy:
  - interview_core ASGI runs in-process via httpx ASGITransport (same pattern as
    test_sessions_router.py).
  - JWTs are generated locally via shared.auth.jwt.issue_access_token.
  - DB session is mocked for the sessions endpoint tests.
  - The avatars endpoint has no DB dependency — only auth is needed.

Test matrix:

GET /api/avatars:
  test_list_avatars_requires_auth            — no token → 401
  test_list_avatars_returns_catalog          — valid token → 200, correct shape
  test_list_avatars_no_server_side_fields    — replica_id and voice must NOT appear

POST /api/sessions (avatar_id field):
  test_create_session_default_avatar_when_omitted    — no avatar_id → presenter_id="anna"
  test_create_session_explicit_valid_avatar          — avatar_id="lucas" → presenter_id="lucas"
  test_create_session_unknown_avatar_id_422          — unknown avatar_id → 422
  test_create_session_null_avatar_id_defaults        — avatar_id=None → presenter_id="anna"
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import issue_access_token
from sqlalchemy.ext.asyncio import AsyncSession

from app.avatars import AVATARS, DEFAULT_AVATAR_ID, valid_avatar_ids
from app.config import settings
from app.main import app
from app.models import Job

# ---------------------------------------------------------------------------
# Helpers — mirrors test_sessions_router.py pattern
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


def _build_fake_job(*, is_active: bool = True) -> MagicMock:
    fake_job = MagicMock(spec=Job)
    fake_job.id = uuid.uuid4()
    fake_job.title = "Backend Engineer"
    fake_job.is_active = is_active
    return fake_job


def _patch_db_session(
    *,
    job: MagicMock | None,
    consent_present: bool = True,
    captured_sessions: list[Any] | None = None,
) -> Any:
    """Patch get_db_session to return a mock AsyncSession.

    Optionally appends the InterviewSession passed to db.add() into
    ``captured_sessions`` so tests can inspect presenter_id after the call.
    """
    job_result = MagicMock()
    job_result.scalar_one_or_none.return_value = job

    consent_result = MagicMock()
    consent_result.scalar_one_or_none.return_value = 1 if consent_present else None

    def _execute_side_effect(stmt: Any, *args: Any, **kwargs: Any) -> Any:
        sql_str = str(stmt)
        if "dpdp_consent_ledger" in sql_str:
            return consent_result
        return job_result

    @asynccontextmanager
    async def _ctx() -> AsyncGenerator[AsyncSession, None]:
        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.execute = AsyncMock(side_effect=_execute_side_effect)

        def _capture_add(obj: Any) -> None:
            if captured_sessions is not None:
                captured_sessions.append(obj)

        mock_db.add = MagicMock(side_effect=_capture_add)
        mock_db.commit = AsyncMock()
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
    """In-process ASGI client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=10.0,
    ) as ac:
        yield ac  # type: ignore[misc]


# ---------------------------------------------------------------------------
# GET /api/avatars tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_avatars_requires_auth(client: AsyncClient) -> None:
    """No Authorization header → 401."""
    resp = await client.get("/api/avatars")
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_list_avatars_returns_catalog(client: AsyncClient) -> None:
    """Valid token → 200 with a well-formed catalog."""
    token, _ = _valid_token()
    resp = await client.get(
        "/api/avatars",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Top-level key must be "avatars"
    assert "avatars" in data
    avatars = data["avatars"]
    assert isinstance(avatars, list)
    assert len(avatars) == len(AVATARS)

    # Every item must have the four public fields
    ids_returned: set[str] = set()
    for item in avatars:
        assert "id" in item and isinstance(item["id"], str)
        assert "name" in item and isinstance(item["name"], str)
        assert "gender" in item and item["gender"] in ("male", "female")
        assert "thumbnail_url" in item and item["thumbnail_url"].startswith("https://")
        ids_returned.add(item["id"])

    # All catalog ids must be present
    assert ids_returned == valid_avatar_ids()


@pytest.mark.asyncio
async def test_list_avatars_no_server_side_fields(client: AsyncClient) -> None:
    """replica_id and voice must NOT be present in any avatar item."""
    token, _ = _valid_token()
    resp = await client.get(
        "/api/avatars",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    for item in resp.json()["avatars"]:
        assert "replica_id" not in item, "replica_id must never be exposed to client"
        assert "voice" not in item, "voice must never be exposed to client"


@pytest.mark.asyncio
async def test_list_avatars_order_stable(client: AsyncClient) -> None:
    """The catalog order must match AVATARS list order (deterministic for FE)."""
    token, _ = _valid_token()
    resp = await client.get(
        "/api/avatars",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    returned_ids = [item["id"] for item in resp.json()["avatars"]]
    expected_ids = [av.id for av in AVATARS]
    assert returned_ids == expected_ids


# ---------------------------------------------------------------------------
# POST /api/sessions — avatar_id field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_default_avatar_when_omitted(client: AsyncClient) -> None:
    """Omitting avatar_id → presenter_id stored as DEFAULT_AVATAR_ID ("anna")."""
    token, _ = _valid_token()
    fake_job = _build_fake_job()
    captured: list[Any] = []

    override = _patch_db_session(job=fake_job, captured_sessions=captured)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.post(
            "/api/sessions",
            json={"job_id": str(fake_job.id), "language": "en"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 201, resp.text
    assert len(captured) == 1
    session_obj = captured[0]
    assert session_obj.presenter_id == DEFAULT_AVATAR_ID, (
        f"Expected presenter_id={DEFAULT_AVATAR_ID!r}, got {session_obj.presenter_id!r}"
    )


@pytest.mark.asyncio
async def test_create_session_explicit_valid_avatar(client: AsyncClient) -> None:
    """Providing avatar_id='lucas' → presenter_id stored as 'lucas'."""
    token, _ = _valid_token()
    fake_job = _build_fake_job()
    captured: list[Any] = []

    override = _patch_db_session(job=fake_job, captured_sessions=captured)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.post(
            "/api/sessions",
            json={"job_id": str(fake_job.id), "language": "en", "avatar_id": "lucas"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 201, resp.text
    assert len(captured) == 1
    assert captured[0].presenter_id == "lucas"


@pytest.mark.asyncio
async def test_create_session_gloria_avatar(client: AsyncClient) -> None:
    """Providing avatar_id='gloria' → presenter_id stored as 'gloria'."""
    token, _ = _valid_token()
    fake_job = _build_fake_job()
    captured: list[Any] = []

    override = _patch_db_session(job=fake_job, captured_sessions=captured)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.post(
            "/api/sessions",
            json={"job_id": str(fake_job.id), "language": "hi", "avatar_id": "gloria"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 201, resp.text
    assert captured[0].presenter_id == "gloria"


@pytest.mark.asyncio
async def test_create_session_unknown_avatar_id_422(client: AsyncClient) -> None:
    """Unknown avatar_id → 422 Unprocessable Entity."""
    token, _ = _valid_token()
    fake_job = _build_fake_job()

    override = _patch_db_session(job=fake_job)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.post(
            "/api/sessions",
            json={"job_id": str(fake_job.id), "language": "en", "avatar_id": "does-not-exist"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 422, resp.text
    assert "does-not-exist" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_session_null_avatar_id_defaults(client: AsyncClient) -> None:
    """Explicitly sending avatar_id=null → treated as omitted → default presenter_id."""
    token, _ = _valid_token()
    fake_job = _build_fake_job()
    captured: list[Any] = []

    override = _patch_db_session(job=fake_job, captured_sessions=captured)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.post(
            "/api/sessions",
            json={"job_id": str(fake_job.id), "language": "te", "avatar_id": None},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 201, resp.text
    assert captured[0].presenter_id == DEFAULT_AVATAR_ID
