"""S5-004: DPDP right-to-erasure endpoint tests.

Tests:
  1. test_erasure_requires_admin_jwt       — no token → 401
  2. test_erasure_requires_admin_role      — user-role JWT → 403
  3. test_erasure_user_not_found           — admin JWT, unknown user_id → 404
  4. test_erasure_happy_path               — admin JWT, existing user → 202
  5. test_erasure_duplicate_request        — second erasure for same user → 409

All five tests use an isolated FastAPI app that directly mounts the erasure
router under /admin.  This makes the test suite runnable before the router
wiring step in main.py is complete.  The isolated app preserves the same
auth guard because the router defines AdminDep (verify_admin_role) on the
endpoint itself, not just at the prefix level.

All tests mock the DB session — no live PostgreSQL connection required.
PII note: no email / name / phone in any assertion or fixture.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from shared.auth.jwt import issue_access_token

from app.config import settings
from app.database import get_db_session

# Models imported for type context only — test fixtures use MagicMock, not live ORM instances
from app.routers.erasure import router as erasure_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(role: str, sub: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") -> str:
    """Issue a real-shaped platform token via shared.auth.jwt.issue_access_token.

    The platform uses a 'roles' LIST claim (not a singular 'role' string).
    """
    return issue_access_token(
        user_id=sub,
        roles=[role],
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )


_KNOWN_USER_ID = "11111111-2222-3333-4444-555555555555"
_UNKNOWN_USER_ID = "99999999-9999-9999-9999-999999999999"
_ADMIN_SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_ERASURE_URL = f"/admin/users/{_KNOWN_USER_ID}/dpdp/delete"


def _fake_user() -> MagicMock:
    """Return a mock User with the fields the endpoint accesses."""
    u = MagicMock(spec_set=["id", "deleted_at"])
    u.id = uuid.UUID(_KNOWN_USER_ID)
    u.deleted_at = None
    return u


def _fake_erasure_request() -> MagicMock:
    er = MagicMock(spec_set=["request_id", "user_id", "status"])
    er.request_id = uuid.uuid4()
    er.user_id = uuid.UUID(_KNOWN_USER_ID)
    er.status = "pending"
    return er


def _make_mock_session(
    *,
    user: MagicMock | None = None,
    existing_erasure: MagicMock | None = None,
) -> AsyncMock:
    """Build a mock AsyncSession for the given scenario.

    Call order from the endpoint:
      execute #0 → user lookup        (scalar_one_or_none → user)
      execute #1 → duplicate check    (scalar_one_or_none → existing_erasure)
      execute #2 → sessions SELECT    (scalars().all() → [])
      execute #3 → sessions UPDATE    (ignored — no sessions in test data)
    """
    session = AsyncMock()

    scalars_result = MagicMock()
    scalars_result.all.return_value = []

    call_results: list[Any] = [user, existing_erasure]
    call_index: dict[str, int] = {"i": 0}
    executed_sql: list[str] = []

    async def _execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:  # noqa: ANN401
        executed_sql.append(str(stmt))
        result = MagicMock()
        idx = call_index["i"]
        call_index["i"] += 1
        result.scalar_one_or_none.return_value = (
            call_results[idx] if idx < len(call_results) else None
        )
        result.scalars.return_value = scalars_result
        result.rowcount = 0
        return result

    session.execute = _execute
    session.executed_sql = executed_sql  # test introspection
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    return session


def _build_test_app(mock_session: AsyncMock) -> FastAPI:
    """Minimal FastAPI app with the erasure router and an overridden DB session.

    The erasure router defines AdminDep directly on each endpoint, so the
    JWT guard fires correctly without needing the main.py prefix-level
    admin_router dependency.
    """
    test_app = FastAPI()

    async def _get_db_override() -> AsyncMock:
        yield mock_session

    test_app.dependency_overrides[get_db_session] = _get_db_override
    test_app.include_router(erasure_router, prefix="/admin")
    return test_app


# ---------------------------------------------------------------------------
# Test 1 — no token → 401
# ---------------------------------------------------------------------------


def test_erasure_requires_admin_jwt() -> None:
    mock_session = _make_mock_session()
    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_ERASURE_URL)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test 2 — user-role JWT → 403
# ---------------------------------------------------------------------------


def test_erasure_requires_admin_role() -> None:
    mock_session = _make_mock_session()
    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    token = _make_token(role="candidate")
    resp = client.post(_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin role required"


# ---------------------------------------------------------------------------
# Test 3 — admin JWT, unknown user → 404
# ---------------------------------------------------------------------------


def test_erasure_user_not_found() -> None:
    token = _make_token(role="admin", sub=_ADMIN_SUB)
    unknown_url = f"/admin/users/{_UNKNOWN_USER_ID}/dpdp/delete"
    mock_session = _make_mock_session(user=None, existing_erasure=None)

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(unknown_url, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 404
    assert _UNKNOWN_USER_ID in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Test 4 — happy path → 202 with correct shape
# ---------------------------------------------------------------------------


def test_erasure_happy_path() -> None:
    token = _make_token(role="admin", sub=_ADMIN_SUB)
    user = _fake_user()
    mock_session = _make_mock_session(user=user, existing_erasure=None)

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(
        _ERASURE_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "User requested deletion"},
    )

    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert "request_id" in data
    assert data["user_id"] == _KNOWN_USER_ID
    assert "scheduled_completion" in data

    # scheduled_completion must be ~30 days from now (±1 hour tolerance)
    scheduled = datetime.fromisoformat(data["scheduled_completion"])
    delta = scheduled - datetime.now(UTC)
    assert timedelta(days=29, hours=23) < delta < timedelta(days=30, hours=1)

    # Proctoring/biometric integrity events are hard-deleted immediately.
    assert any(
        "DELETE FROM integrity_events" in sql for sql in mock_session.executed_sql
    ), "erasure must immediately purge integrity_events for the user's sessions"


# ---------------------------------------------------------------------------
# Test 5 — duplicate request → 409
# ---------------------------------------------------------------------------


def test_erasure_duplicate_request() -> None:
    token = _make_token(role="admin", sub=_ADMIN_SUB)
    user = _fake_user()
    existing = _fake_erasure_request()
    mock_session = _make_mock_session(user=user, existing_erasure=existing)

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(
        _ERASURE_URL,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 409
    assert _KNOWN_USER_ID in resp.json()["detail"]
