"""Integration tests for S2-007 — POST /api/sessions.

Strategy:
- interview_core ASGI runs in-process via httpx ASGITransport.
- JWTs are generated locally via the shared helper; no live data_gateway.
- DB session factory is mocked so tests run fully offline.

Test matrix:
  test_create_session_happy_path     valid JWT + active job → 201, session_id returned, row "added"
  test_create_session_no_token       missing Authorization header → 401
  test_create_session_unknown_job    valid JWT, job_id has no row → 404
  test_create_session_inactive_job   valid JWT, job exists but is_active=False → 400
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

from app.config import settings
from app.main import app
from app.models import Job

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_token(user_id: str | None = None) -> tuple[str, str]:
    """Issue a fresh JWT; return (token, user_id_used)."""
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
    fake_job.title = "Junior Java Developer"
    fake_job.is_active = is_active
    return fake_job


def _patch_db_session(
    *,
    job: MagicMock | None,
    consent_present: bool = True,
) -> Any:
    """Patch app.routers.sessions.get_db_session to yield a mock AsyncSession.

    The mock distinguishes between two queries the router runs:
      1. ``select(Job)`` — returns ``job`` (or None).
      2. ``text("SELECT 1 FROM dpdp_consent_ledger ...")`` — returns 1 if
         ``consent_present`` is True, else None. This makes the consent
         gate exercisable in tests instead of trivially passing because
         the mock returns the job for every query.

    add() and commit() are recorded but no-op.
    """
    # Result for the Job lookup
    job_result = MagicMock()
    job_result.scalar_one_or_none.return_value = job

    # Result for the consent guard SELECT 1
    consent_result = MagicMock()
    consent_result.scalar_one_or_none.return_value = 1 if consent_present else None

    def _execute_side_effect(stmt: Any, *args: Any, **kwargs: Any) -> Any:
        # The consent guard uses sqlalchemy.text("SELECT 1 ...");
        # TextClause stringifies to that literal SQL. The job lookup
        # uses select(Job) which has no "dpdp_consent_ledger" in its SQL.
        sql_str = str(stmt)
        if "dpdp_consent_ledger" in sql_str:
            return consent_result
        return job_result

    @asynccontextmanager
    async def _ctx() -> AsyncGenerator[AsyncSession, None]:
        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.execute = AsyncMock(side_effect=_execute_side_effect)
        mock_db.add = MagicMock()
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
    """In-process ASGI client for interview_core."""
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
async def test_create_session_happy_path(client: AsyncClient) -> None:
    """Valid JWT + active job → 201 with session_id, job_title, language."""
    token, user_id = _valid_token()
    fake_job = _build_fake_job(is_active=True)
    body = {"job_id": str(fake_job.id), "language": "en"}

    override = _patch_db_session(job=fake_job)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.post(
            "/api/sessions",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "session_id" in data
    # response session_id must be a UUID string
    uuid.UUID(data["session_id"])
    assert data["job_title"] == "Junior Java Developer"
    assert data["language"] == "en"
    # echo of user_id back is not part of contract, just confirms auth worked
    _ = user_id


@pytest.mark.asyncio
async def test_create_session_no_token(client: AsyncClient) -> None:
    """No Authorization header → 401."""
    body = {"job_id": str(uuid.uuid4()), "language": "en"}
    resp = await client.post("/api/sessions", json=body)
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_create_session_unknown_job(client: AsyncClient) -> None:
    """Valid JWT but job_id not found → 404."""
    token, _ = _valid_token()
    body = {"job_id": str(uuid.uuid4()), "language": "en"}

    override = _patch_db_session(job=None)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.post(
            "/api/sessions",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 404, resp.text
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_session_inactive_job(client: AsyncClient) -> None:
    """Valid JWT, job exists but is_active=False → 400."""
    token, _ = _valid_token()
    fake_job = _build_fake_job(is_active=False)
    body = {"job_id": str(fake_job.id), "language": "en"}

    override = _patch_db_session(job=fake_job)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.post(
            "/api/sessions",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 400, resp.text
    assert "not currently active" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_session_no_consent_returns_403(client: AsyncClient) -> None:
    """Valid JWT, active job, but NO active consent row → 403.

    This is the security-auditor CRITICAL gate for S3-011: without
    server-side enforcement, the React modal alone is bypassable. The
    `interview_core` consent_guard.has_active_consent() must run BEFORE
    the session row is created.
    """
    token, _ = _valid_token()
    fake_job = _build_fake_job(is_active=True)
    body = {"job_id": str(fake_job.id), "language": "en"}

    override = _patch_db_session(job=fake_job, consent_present=False)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.post(
            "/api/sessions",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 403, resp.text
    assert "consent" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_session_invalid_language(client: AsyncClient) -> None:
    """Language outside en/hi/te → 422 (Pydantic Literal validation)."""
    token, _ = _valid_token()
    body = {"job_id": str(uuid.uuid4()), "language": "fr"}

    # DB dep is resolved before body validation in FastAPI's dep graph, so
    # override it even though we expect 422 (not a DB-touching code path).
    override = _patch_db_session(job=None)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.post(
            "/api/sessions",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 422, resp.text
