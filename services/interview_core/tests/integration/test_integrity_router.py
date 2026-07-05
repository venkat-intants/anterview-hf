"""Integration tests for Phase B — POST /api/sessions/{id}/integrity-events.

In-process ASGI via httpx; DB session mocked so it runs fully offline. The mock
distinguishes the three queries the endpoint runs: select session, select events
(for re-scoring), update session.

Matrix:
  happy path            owned session + 2 events + consent → 200, score computed, stored=2
  ownership enforced    session.user_id != caller → 403
  not found             no session row → 404
  no token              missing Authorization → 401
  consent absent        owned session but no active consent → 403 (DPDP fail-closed)
  consent DB error      consent check raises → 403 (fail-closed, not 500)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import issue_access_token
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.main import app

_T0 = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


def _token(user_id: str) -> str:
    return str(
        issue_access_token(
            user_id=user_id,
            roles=["candidate"],
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    )


def _patch_db(
    *,
    session_user_id: str | None,
    event_rows: list[tuple],
    has_consent: bool = True,
) -> Any:
    """Yield a mock AsyncSession.

    - select(InterviewSession) → session row (or None) via scalar_one_or_none()
    - select(IntegrityEvent...) → ``event_rows`` via .all()
    - update(...) → ignored

    ``has_consent`` is injected by patching ``app.routers.integrity.has_active_consent``
    — this helper wires the DB mock only; callers must patch the consent guard
    separately (see _patch_consent).
    """
    session_row = None
    if session_user_id is not None:
        session_row = MagicMock()
        session_row.user_id = uuid.UUID(session_user_id)

    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = session_row

    events_result = MagicMock()
    events_result.all.return_value = event_rows

    update_result = MagicMock()

    def _execute_side_effect(stmt: Any, *a: Any, **k: Any) -> Any:
        sql = str(stmt).upper()
        if sql.startswith("UPDATE"):
            return update_result
        if "INTEGRITY_EVENTS" in sql:
            return events_result
        return session_result

    @asynccontextmanager
    async def _ctx() -> AsyncGenerator[AsyncSession, None]:
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(side_effect=_execute_side_effect)
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        yield db  # type: ignore[misc]

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        async with _ctx() as db:
            yield db

    return _override


@pytest_asyncio.fixture
async def client() -> AsyncClient:  # type: ignore[misc]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=10.0
    ) as ac:
        yield ac  # type: ignore[misc]


@pytest.mark.asyncio
async def test_post_integrity_happy_path(client: AsyncClient) -> None:
    """Owned session + active consent + events → 200, score computed."""
    uid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    # Re-query returns: 10s gaze_away (5 penalty) + tab_blur (3) = 8 → score 92.
    event_rows = [
        ("gaze_away", _T0, _T0 + timedelta(seconds=10)),
        ("tab_blur", _T0, None),
    ]
    override = _patch_db(session_user_id=uid, event_rows=event_rows)
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        with patch("app.routers.integrity.has_active_consent", return_value=True):
            resp = await client.post(
                f"/api/sessions/{sid}/integrity-events",
                json={
                    "events": [
                        {
                            "type": "gaze_away",
                            "started_at": _T0.isoformat(),
                            "ended_at": (_T0 + timedelta(seconds=10)).isoformat(),
                        },
                        {"type": "tab_blur", "started_at": _T0.isoformat()},
                    ]
                },
                headers={"Authorization": f"Bearer {_token(uid)}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["stored"] == 2
    assert data["integrity_score"] == 92
    assert data["summary"]["by_type"]["gaze_away"] == 1


@pytest.mark.asyncio
async def test_post_integrity_wrong_owner_forbidden(client: AsyncClient) -> None:
    """Session owned by a different user → 403."""
    caller = str(uuid.uuid4())
    other = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    override = _patch_db(session_user_id=other, event_rows=[])
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        # Consent would be fine, but ownership check happens first.
        with patch("app.routers.integrity.has_active_consent", return_value=True):
            resp = await client.post(
                f"/api/sessions/{sid}/integrity-events",
                json={"events": [{"type": "tab_blur", "started_at": _T0.isoformat()}]},
                headers={"Authorization": f"Bearer {_token(caller)}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_post_integrity_session_not_found(client: AsyncClient) -> None:
    """No session row → 404."""
    uid = str(uuid.uuid4())
    override = _patch_db(session_user_id=None, event_rows=[])
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        resp = await client.post(
            f"/api/sessions/{uuid.uuid4()}/integrity-events",
            json={"events": [{"type": "tab_blur", "started_at": _T0.isoformat()}]},
            headers={"Authorization": f"Bearer {_token(uid)}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_post_integrity_no_token(client: AsyncClient) -> None:
    """Missing Authorization → 401."""
    resp = await client.post(
        f"/api/sessions/{uuid.uuid4()}/integrity-events",
        json={"events": [{"type": "tab_blur", "started_at": _T0.isoformat()}]},
    )
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# DPDP consent gate — Finding 1 tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_integrity_consent_absent_is_forbidden(client: AsyncClient) -> None:
    """Owned session but NO active consent → 403 (DPDP fail-closed).

    Biometric/gaze events are sensitive under DPDP Act 2023.  The endpoint
    must reject the batch when the candidate has no active recording consent,
    regardless of session ownership.  Events must NOT be persisted.
    """
    uid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    override = _patch_db(session_user_id=uid, event_rows=[])
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        with patch(
            "app.routers.integrity.has_active_consent",
            return_value=False,  # consent revoked or never granted
        ):
            resp = await client.post(
                f"/api/sessions/{sid}/integrity-events",
                json={"events": [{"type": "gaze_away", "started_at": _T0.isoformat()}]},
                headers={"Authorization": f"Bearer {_token(uid)}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 403, resp.text
    # Detail must be informative — candidate should know consent is the issue.
    assert "consent" in resp.json()["detail"].lower(), (
        "403 detail must mention consent so the candidate knows what to fix"
    )


@pytest.mark.asyncio
async def test_post_integrity_consent_db_error_is_forbidden(client: AsyncClient) -> None:
    """Consent check raises a DB exception → 403 (fail-closed, NOT 500).

    A transient DB error while checking consent must be treated as 'consent
    unknown'.  DPDP requires fail-closed: refuse the batch rather than
    silently recording biometric data without confirmed consent.
    """
    uid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    override = _patch_db(session_user_id=uid, event_rows=[])
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        with patch(
            "app.routers.integrity.has_active_consent",
            side_effect=RuntimeError("connection pool exhausted"),
        ):
            resp = await client.post(
                f"/api/sessions/{sid}/integrity-events",
                json={"events": [{"type": "gaze_away", "started_at": _T0.isoformat()}]},
                headers={"Authorization": f"Bearer {_token(uid)}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 403, (
        f"Expected 403 (fail-closed) on consent DB error, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_post_integrity_consent_checked_before_persist(client: AsyncClient) -> None:
    """The consent gate must fire BEFORE any db.add() / db.flush() call.

    If events were inserted before the consent check, a revocation race window
    would exist.  Verify that the DB mock's ``add`` method is never called when
    consent is absent.
    """
    uid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    from app.database import get_db_session

    add_calls: list[Any] = []

    @asynccontextmanager
    async def _recording_ctx() -> AsyncGenerator[AsyncSession, None]:
        db = AsyncMock(spec=AsyncSession)

        session_row = MagicMock()
        session_row.user_id = uuid.UUID(uid)
        session_result = MagicMock()
        session_result.scalar_one_or_none.return_value = session_row

        db.execute = AsyncMock(return_value=session_result)
        db.add = MagicMock(side_effect=lambda row: add_calls.append(row))
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        yield db  # type: ignore[misc]

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        async with _recording_ctx() as db:
            yield db

    app.dependency_overrides[get_db_session] = _override
    try:
        with patch(
            "app.routers.integrity.has_active_consent",
            return_value=False,
        ):
            resp = await client.post(
                f"/api/sessions/{sid}/integrity-events",
                json={"events": [{"type": "gaze_away", "started_at": _T0.isoformat()}]},
                headers={"Authorization": f"Bearer {_token(uid)}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 403, resp.text
    assert add_calls == [], (
        "db.add() must NOT be called when consent is absent — "
        "events must be rejected before any DB write"
    )
