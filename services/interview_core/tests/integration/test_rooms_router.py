"""Integration tests for POST /api/rooms/{session_id}/token — security-audit
findings 1 + 2 (2026-08).

rooms.py had no test file before this change even though it is the
highest-risk surface in this service (it mints the LiveKit join token that
starts a real interview). In-process ASGI via httpx; DB session mocked so it
runs fully offline, mirroring the technique in test_integrity_router.py: the
mock distinguishes queries by substring-matching the compiled SQL.

Matrix (security-audit list):
  wrong owner                        session.user_id != caller        -> 403
  guest token for another session    guest session_id claim mismatch  -> 403
  guest token with no session_id     claim absent entirely            -> 403
  missing consent                    owned, not finished, no consent  -> 403
  completed session                  session.status == 'completed'    -> 409

Plus a few adjacent cases exercised the same way as the rest of the suite:
  session not found                                                   -> 404
  no token                                                            -> 401
  failed session                     session.status == 'failed'       -> 409
  scorecard exists (status still 'in_progress')                       -> 409
  happy path                         owned, unfinished, consented     -> 200
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


def _token(user_id: str, *, roles: list[str] | None = None, session_id: str | None = None) -> str:
    """Issue a JWT. Pass session_id to mint a guest token bound to it,
    mirroring data_gateway's ``_issue_guest_token``."""
    extra_claims = {"session_id": session_id} if session_id is not None else None
    return str(
        issue_access_token(
            user_id=user_id,
            roles=roles or ["candidate"],
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
            extra_claims=extra_claims,
        )
    )


def _patch_db(
    *,
    session_user_id: str | None,
    session_status: str = "created",
    scorecard_exists: bool = False,
    has_consent: bool = True,
    job_title: str = "Junior Java Developer",
) -> Any:
    """Yield a mock AsyncSession wired for ``create_room_token``.

    Distinguishes the (up to) four queries the endpoint can run by
    substring-matching the compiled SQL, same technique as
    test_integrity_router.py's ``_patch_db``:
      - session lookup            -> default branch (no other substring hits)
      - scorecard existence check -> "SCORECARDS" in the raw SQL text
      - worker Job lookup         -> "FROM JOBS" in the compiled ORM select
      - DPDP consent check        -> "DPDP_CONSENT_LEDGER" in the raw SQL text
    """
    session_row = None
    if session_user_id is not None:
        session_row = MagicMock()
        session_row.id = uuid.uuid4()
        session_row.user_id = uuid.UUID(session_user_id)
        session_row.job_id = uuid.uuid4()
        session_row.status = session_status
        session_row.language = "en"

    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = session_row

    scorecard_result = MagicMock()
    scorecard_result.scalar_one_or_none.return_value = 1 if scorecard_exists else None

    job_row = MagicMock()
    job_row.title = job_title
    job_result = MagicMock()
    job_result.scalar_one_or_none.return_value = job_row

    consent_result = MagicMock()
    consent_result.scalar_one_or_none.return_value = 1 if has_consent else None

    def _execute_side_effect(stmt: Any, *a: Any, **k: Any) -> Any:
        sql = str(stmt).upper()
        if "DPDP_CONSENT_LEDGER" in sql:
            return consent_result
        if "SCORECARDS" in sql:
            return scorecard_result
        if "FROM JOBS" in sql:
            return job_result
        return session_result

    @asynccontextmanager
    async def _ctx() -> AsyncGenerator[AsyncSession, None]:
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(side_effect=_execute_side_effect)
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


async def _post_token(client: AsyncClient, session_id: str, token: str, *, override: Any) -> Any:
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        return await client.post(
            f"/api/rooms/{session_id}/token",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)


# ---------------------------------------------------------------------------
# Finding 2 — guest-session binding (shared GuestBoundUserDep)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guest_token_for_another_session_is_forbidden(client: AsyncClient) -> None:
    """A guest token minted for session B must not mint a token for session A.

    Guests are reused across invites (one users row can own several
    sessions), so an ownership check alone would pass here — the binding
    dependency must reject it before the ownership check is even reached.
    """
    uid = str(uuid.uuid4())
    session_a = str(uuid.uuid4())
    session_b = str(uuid.uuid4())
    guest_token = _token(uid, roles=["guest_candidate"], session_id=session_b)

    # Session A IS owned by this same guest user_id — proves the rejection is
    # NOT just an ownership failure but the binding check firing first.
    override = _patch_db(session_user_id=uid)
    resp = await _post_token(client, session_a, guest_token, override=override)

    assert resp.status_code == 403, resp.text
    assert "session" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_guest_token_with_no_session_id_claim_is_forbidden(client: AsyncClient) -> None:
    """A guest token minted with NO session_id claim at all must be rejected."""
    uid = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    # No session_id kwarg -> no session_id claim in the JWT.
    guest_token = _token(uid, roles=["guest_candidate"])

    override = _patch_db(session_user_id=uid)
    resp = await _post_token(client, session_id, guest_token, override=override)

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_guest_token_for_its_own_session_is_allowed_past_the_binding_check(
    client: AsyncClient,
) -> None:
    """Sanity check: a guest token bound to THIS session_id is not rejected by
    the binding dependency (it should proceed to the normal checks below)."""
    uid = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    guest_token = _token(uid, roles=["guest_candidate"], session_id=session_id)

    override = _patch_db(session_user_id=uid, session_status="created", has_consent=True)
    resp = await _post_token(client, session_id, guest_token, override=override)

    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Finding 1 — session-finished guard (409)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_session_is_rejected_with_409(client: AsyncClient) -> None:
    """A COMPLETED session must never be re-entered — 409, not a fresh token.

    Regression target: before this fix, create_room_token never looked at
    session.status, so a still-valid access token could re-POST for a
    completed (and already-scored) session and get a brand new LiveKit
    token, letting the worker run a second interview on it.
    """
    uid = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    token = _token(uid)

    override = _patch_db(session_user_id=uid, session_status="completed")
    resp = await _post_token(client, session_id, token, override=override)

    assert resp.status_code == 409, resp.text
    assert "finish" in resp.json()["detail"].lower() or "already" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_failed_session_is_rejected_with_409(client: AsyncClient) -> None:
    """A FAILED session is equally not re-enterable via this endpoint."""
    uid = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    token = _token(uid)

    override = _patch_db(session_user_id=uid, session_status="failed")
    resp = await _post_token(client, session_id, token, override=override)

    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_session_with_existing_scorecard_is_rejected_with_409(client: AsyncClient) -> None:
    """A scorecard on file blocks re-entry even if status was never flipped —
    the durable backstop for a status write that raced or never landed."""
    uid = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    token = _token(uid)

    override = _patch_db(session_user_id=uid, session_status="in_progress", scorecard_exists=True)
    resp = await _post_token(client, session_id, token, override=override)

    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_abandoned_session_is_still_reenterable(client: AsyncClient) -> None:
    """'abandoned' must NOT be treated as finished — a mid-interview drop
    must stay resumable, matching data_gateway's redeem-path semantics."""
    uid = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    token = _token(uid)

    override = _patch_db(session_user_id=uid, session_status="abandoned")
    resp = await _post_token(client, session_id, token, override=override)

    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Pre-existing gates (ownership, consent, not-found, no-token) — still enforced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_owner_is_forbidden(client: AsyncClient) -> None:
    """Session owned by a different user -> 403."""
    caller = str(uuid.uuid4())
    other = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    token = _token(caller)

    override = _patch_db(session_user_id=other)
    resp = await _post_token(client, session_id, token, override=override)

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_missing_consent_is_forbidden(client: AsyncClient) -> None:
    """Owned, unfinished session but no active DPDP consent -> 403."""
    uid = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    token = _token(uid)

    override = _patch_db(session_user_id=uid, session_status="created", has_consent=False)
    resp = await _post_token(client, session_id, token, override=override)

    assert resp.status_code == 403, resp.text
    assert "consent" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_session_not_found_is_404(client: AsyncClient) -> None:
    uid = str(uuid.uuid4())
    token = _token(uid)

    override = _patch_db(session_user_id=None)
    resp = await _post_token(client, str(uuid.uuid4()), token, override=override)

    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_no_token_is_401(client: AsyncClient) -> None:
    resp = await client.post(f"/api/rooms/{uuid.uuid4()}/token")
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_happy_path_returns_token(client: AsyncClient) -> None:
    """Owned, unfinished, consented session -> 200 with a join token."""
    uid = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    token = _token(uid)

    override = _patch_db(session_user_id=uid, session_status="created", has_consent=True)
    resp = await _post_token(client, session_id, token, override=override)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["room_name"] == session_id
    assert data["token"]
    assert data["url"] == settings.livekit_url
