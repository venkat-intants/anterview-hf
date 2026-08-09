"""S5-004 / DPDP-8: DPDP right-to-erasure endpoint tests.

Operator path (POST /users/{user_id}/dpdp/delete, admin JWT):
  1. test_erasure_requires_admin_jwt       — no token → 401
  2. test_erasure_requires_admin_role      — user-role JWT → 403
  3. test_erasure_user_not_found           — admin JWT, unknown user_id → 404
  4. test_erasure_happy_path               — admin JWT, existing user → 202
  5. test_erasure_duplicate_request        — second erasure for same user → 409

Self-service path (POST /users/me/dpdp/delete, any authenticated user) —
added with DPDP-8; see the block comment above those tests.

Failure and side-effect paths shared by both endpoints (token revocation,
session soft-delete, the duplicate-request race, DB errors) — see the third
block. They are grouped there rather than duplicated per endpoint because both
endpoints run the same pipeline; what differs is only how each REPORTS the
outcome, and that difference is what those tests assert.

All tests use an isolated FastAPI app that directly mounts the erasure
router under /admin.  This makes the test suite runnable before the router
wiring step in main.py is complete.  The isolated app preserves the same
auth guards because the router declares them (AdminDep / AuthenticatedDep) on
each endpoint itself, not just at the prefix level.

All tests mock the DB session — no live PostgreSQL connection required.
PII note: no email / name / phone in any assertion or fixture.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shared.auth.jwt import issue_access_token
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.config import settings
from app.database import get_db_session
from app.routers import erasure as erasure_mod

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
    sessions: list[Any] | None = None,
    pending_after_rollback: MagicMock | None = None,
) -> AsyncMock:
    """Build a mock AsyncSession for the given scenario.

    Call order from the endpoint:
      execute #0 → user lookup        (scalar_one_or_none → user)
      execute #1 → duplicate check    (scalar_one_or_none → existing_erasure)
      execute #2 → sessions SELECT    (scalars().all() → `sessions`, default [])
      execute #3 → sessions UPDATE    (only when `sessions` is non-empty)

    ``pending_after_rollback`` answers the re-read that both endpoints do AFTER
    a rollback, when the partial unique index rejected a racing duplicate. It is
    keyed off rollback having happened rather than off a call index because the
    number of statements before it depends on `sessions` — an index would encode
    the pipeline's current length into every test that touches the race path.
    """
    session = AsyncMock()

    scalars_result = MagicMock()
    scalars_result.all.return_value = list(sessions or [])

    call_results: list[Any] = [user, existing_erasure]
    call_index: dict[str, int] = {"i": 0}
    executed_sql: list[str] = []
    rolled_back: dict[str, bool] = {"yes": False}

    async def _execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:  # noqa: ANN401
        executed_sql.append(str(stmt))
        result = MagicMock()
        idx = call_index["i"]
        call_index["i"] += 1
        if rolled_back["yes"]:
            result.scalar_one_or_none.return_value = pending_after_rollback
        else:
            result.scalar_one_or_none.return_value = (
                call_results[idx] if idx < len(call_results) else None
            )
        result.scalars.return_value = scalars_result
        result.rowcount = 0
        return result

    def _mark_rolled_back(*args: Any, **kwargs: Any) -> None:
        rolled_back["yes"] = True

    session.execute = _execute
    session.executed_sql = executed_sql  # test introspection
    session.commit = AsyncMock()
    session.rollback = AsyncMock(side_effect=_mark_rolled_back)
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


# ===========================================================================
# DPDP-8 — self-service erasure (POST /users/me/dpdp/delete)
#
# docs/DATA-FLOW.md promised the data principal a self-service route under
# DPDP §11 while the code had only the operator one. These tests pin the four
# properties that make the new endpoint safe to point a bid response at: it
# authenticates, it takes the subject from the token and nowhere else, it is
# idempotent, and it leaves an audit trail.
# ===========================================================================

_SELF_ERASURE_URL = "/admin/users/me/dpdp/delete"
_CANDIDATE_SUB = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"


def _fake_pending_request() -> MagicMock:
    """A pending ErasureRequest the self-service path can return as-is."""
    er = MagicMock(spec_set=["request_id", "user_id", "status", "scheduled_for"])
    er.request_id = uuid.uuid4()
    er.user_id = uuid.UUID(_CANDIDATE_SUB)
    er.status = "pending"
    er.scheduled_for = datetime.now(UTC) + timedelta(days=17)
    return er


def _fake_self_user() -> MagicMock:
    u = MagicMock(spec_set=["id", "deleted_at"])
    u.id = uuid.UUID(_CANDIDATE_SUB)
    u.deleted_at = None
    return u


def _added_of_type(session: AsyncMock, type_name: str) -> list[Any]:
    """Rows handed to session.add() whose class has the given name.

    Matched by class NAME rather than by identity so the assertion does not
    depend on which module object the router happened to import.
    """
    return [
        call.args[0]
        for call in session.add.call_args_list
        if type(call.args[0]).__name__ == type_name
    ]


def test_self_erasure_requires_a_token() -> None:
    """A statutory right of the data principal, not of the anonymous internet."""
    mock_session = _make_mock_session()
    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    assert client.post(_SELF_ERASURE_URL).status_code == 401


def test_self_erasure_accepts_a_non_admin_token() -> None:
    """The whole point: a candidate holds no admin role and never will."""
    token = _make_token(role="candidate", sub=_CANDIDATE_SUB)
    mock_session = _make_mock_session(user=_fake_self_user(), existing_erasure=None)

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_SELF_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["user_id"] == _CANDIDATE_SUB
    assert data["already_pending"] is False
    scheduled = datetime.fromisoformat(data["scheduled_completion"])
    delta = scheduled - datetime.now(UTC)
    assert timedelta(days=29, hours=23) < delta < timedelta(days=30, hours=1)


def test_self_erasure_ignores_any_user_id_in_the_body() -> None:
    """The tenancy rule: identity comes from the session, never the request.

    A body field named user_id is the shape an attacker would try first. The
    endpoint has no such parameter, so the value must be inert — this test fails
    loudly if one is ever added for convenience.
    """
    token = _make_token(role="candidate", sub=_CANDIDATE_SUB)
    mock_session = _make_mock_session(user=_fake_self_user(), existing_erasure=None)

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(
        _SELF_ERASURE_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "leaving", "user_id": _KNOWN_USER_ID},
    )

    assert resp.status_code == 202, resp.text
    assert resp.json()["user_id"] == _CANDIDATE_SUB
    erasure_rows = _added_of_type(mock_session, "ErasureRequest")
    assert len(erasure_rows) == 1
    assert str(erasure_rows[0].user_id) == _CANDIDATE_SUB
    # requested_by == user_id is what marks a row as self-requested in the DB.
    assert str(erasure_rows[0].requested_by) == _CANDIDATE_SUB


def test_self_erasure_is_idempotent_while_a_request_is_pending() -> None:
    """A second click returns the first request, it does not mint a second.

    202 rather than the admin path's 409 on purpose: an error page in front of a
    statutory right reads to the person exercising it as a refusal.
    """
    token = _make_token(role="candidate", sub=_CANDIDATE_SUB)
    existing = _fake_pending_request()
    mock_session = _make_mock_session(user=_fake_self_user(), existing_erasure=existing)

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_SELF_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["already_pending"] is True
    assert data["request_id"] == str(existing.request_id)
    # The originally scheduled date is the one that binds, not now + 30 days —
    # otherwise every retry would push the erasure further away.
    assert data["scheduled_completion"] == existing.scheduled_for.isoformat()
    assert not _added_of_type(mock_session, "ErasureRequest"), "a duplicate row was created"
    assert not mock_session.commit.called, "an idempotent replay must not write"


def test_self_erasure_unknown_user_is_404() -> None:
    """A live token whose user row is gone — refuse rather than write nothing."""
    token = _make_token(role="candidate", sub=_CANDIDATE_SUB)
    mock_session = _make_mock_session(user=None, existing_erasure=None)

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_SELF_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 404


def test_self_erasure_purges_integrity_events_and_withdraws_consent() -> None:
    """The self path runs the SAME pipeline as the admin path, not a copy of it."""
    token = _make_token(role="candidate", sub=_CANDIDATE_SUB)
    mock_session = _make_mock_session(user=_fake_self_user(), existing_erasure=None)

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_SELF_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 202, resp.text
    sql = mock_session.executed_sql
    assert any("DELETE FROM integrity_events" in s for s in sql), (
        "biometric-derived proctoring signals must not survive the grace window"
    )
    assert any("UPDATE dpdp_consent_ledger" in s and "revoked_at" in s for s in sql), (
        "a retained ledger row with revoked_at NULL claims a consent that was withdrawn"
    )


def test_self_erasure_audit_entry_says_who_asked() -> None:
    """DPDP §11 evidence: the request was the data principal's own."""
    token = _make_token(role="candidate", sub=_CANDIDATE_SUB)
    mock_session = _make_mock_session(user=_fake_self_user(), existing_erasure=None)

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_SELF_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 202, resp.text

    audit_rows = _added_of_type(mock_session, "AuditLog")
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row.action == "dpdp_erasure_requested"
    assert row.actor_type == "user"
    assert str(row.actor_id) == _CANDIDATE_SUB
    assert row.details["channel"] == "self_service"


def test_admin_erasure_audit_entry_records_the_operator_channel() -> None:
    """Both paths write one audit row; the channel is how they stay separable."""
    token = _make_token(role="admin", sub=_ADMIN_SUB)
    mock_session = _make_mock_session(user=_fake_user(), existing_erasure=None)

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 202, resp.text

    audit_rows = _added_of_type(mock_session, "AuditLog")
    assert len(audit_rows) == 1
    assert audit_rows[0].actor_type == "admin"
    assert audit_rows[0].details["channel"] == "admin"
    assert str(audit_rows[0].actor_id) == _ADMIN_SUB


# ===========================================================================
# Shared side effects and failure paths
#
# Everything above asserts the two endpoints' happy paths. What follows covers
# the parts of the pipeline that only run when something goes right in a way the
# happy path never reaches (the user actually HAS sessions, Redis is actually
# reachable) or wrong (a racing duplicate, a dead database). Those branches are
# where a DPDP erasure silently does less than it reports: leaving live sessions
# behind, leaving valid tokens behind, or returning 202 for a write that rolled
# back.
# ===========================================================================


def _fake_redis() -> MagicMock:
    """A Redis stand-in whose calls can be asserted on. No server involved."""
    redis = MagicMock(spec_set=["setex", "smembers", "delete"])
    redis.setex = AsyncMock()
    # bytes on purpose: redis-py returns bytes unless decode_responses is set,
    # and the router decodes them before passing them back to delete().
    redis.smembers = AsyncMock(return_value={b"refresh:tok-a", b"refresh:tok-b"})
    redis.delete = AsyncMock()
    return redis


def test_erasure_soft_deletes_the_users_sessions() -> None:
    """The sessions branch only runs when the user HAS sessions.

    Every other test in this file supplies none, so the UPDATE that carries out
    half of the soft-delete was never executed by the suite — a change that
    dropped it would have left the erasure reporting success while the
    candidate's interview sessions stayed live.
    """
    token = _make_token(role="admin", sub=_ADMIN_SUB)
    mock_session = _make_mock_session(
        user=_fake_user(),
        existing_erasure=None,
        sessions=[MagicMock(), MagicMock()],
    )

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 202, resp.text
    assert any(
        "UPDATE sessions SET deleted_at" in sql for sql in mock_session.executed_sql
    ), "the user's sessions must be soft-deleted alongside the account"


def test_erasure_revokes_every_live_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Soft-deleting the row does not invalidate an already-issued JWT.

    Without this step the erased user keeps full access for the remaining life of
    their access token and can rotate their refresh token for its whole TTL, so
    the assertion is on the Redis writes themselves rather than on the 202.
    """
    redis = _fake_redis()
    monkeypatch.setattr(erasure_mod, "get_redis", lambda: redis)

    token = _make_token(role="admin", sub=_ADMIN_SUB)
    mock_session = _make_mock_session(user=_fake_user(), existing_erasure=None)

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 202, resp.text

    epoch_key, ttl, stamp = redis.setex.await_args.args
    assert epoch_key == f"auth_epoch:{_KNOWN_USER_ID}"
    # The epoch must outlive the grace window; the row is hard-deleted by then.
    assert ttl == 30 * 86400
    assert isinstance(stamp, int)

    deleted = [set(call.args) for call in redis.delete.await_args_list]
    assert {"refresh:tok-a", "refresh:tok-b"} in deleted, "refresh tokens survived"
    assert {f"user_sessions:{_KNOWN_USER_ID}"} in deleted, "session index survived"


def test_erasure_survives_a_redis_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Revocation is best-effort BECAUSE the soft-delete is already committed.

    Raising here would return 500 for a request that did succeed, and the caller
    would retry into a 409 — the worst of both. Losing the revocation is the
    lesser harm and is logged.
    """

    def _boom() -> MagicMock:
        raise RuntimeError("redis is down")

    monkeypatch.setattr(erasure_mod, "get_redis", _boom)

    token = _make_token(role="admin", sub=_ADMIN_SUB)
    mock_session = _make_mock_session(user=_fake_user(), existing_erasure=None)

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 202, resp.text
    assert mock_session.commit.called


def test_self_erasure_rejects_a_token_whose_subject_is_not_a_user() -> None:
    """A service token carries a name, not a user UUID.

    401 rather than 500: the credential is the problem, and the caller cannot fix
    it by changing the request. Without the guard the sub would reach uuid.UUID()
    and surface as an unhandled 500 on a statutory endpoint.
    """
    token = _make_token(role="service", sub="interview-core-worker")
    mock_session = _make_mock_session()

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_SELF_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert not mock_session.commit.called


def test_self_erasure_race_returns_the_winning_request() -> None:
    """Two clicks that both pass the pre-check: the DB index decides, not us.

    The loser must not see an error — it asked for the same thing the winner got
    — so it is handed the winner's request id and the ORIGINAL scheduled date.
    """
    token = _make_token(role="candidate", sub=_CANDIDATE_SUB)
    winner = _fake_pending_request()
    mock_session = _make_mock_session(
        user=_fake_self_user(),
        existing_erasure=None,
        pending_after_rollback=winner,
    )
    mock_session.commit = AsyncMock(
        side_effect=IntegrityError("INSERT INTO erasure_requests", {}, Exception("duplicate"))
    )

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_SELF_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["already_pending"] is True
    assert data["request_id"] == str(winner.request_id)
    assert data["scheduled_completion"] == winner.scheduled_for.isoformat()
    assert mock_session.rollback.called


def test_self_erasure_integrity_error_with_no_winner_is_a_500() -> None:
    """A constraint fired but no pending request exists — not the race we know.

    Reporting 202 here would tell the data principal their erasure is scheduled
    when nothing was written, which is the one failure mode this endpoint must
    never have.
    """
    token = _make_token(role="candidate", sub=_CANDIDATE_SUB)
    mock_session = _make_mock_session(
        user=_fake_self_user(),
        existing_erasure=None,
        pending_after_rollback=None,
    )
    mock_session.commit = AsyncMock(
        side_effect=IntegrityError("INSERT INTO erasure_requests", {}, Exception("other"))
    )

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_SELF_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 500
    assert mock_session.rollback.called


def test_self_erasure_db_error_is_generic_and_leaks_no_driver_text() -> None:
    """CWE-209: asyncpg puts the failing SQL and its bound VALUES into str(exc),
    and on this endpoint those values are a user id and, one frame up, PII."""
    leak = "candidate-email-in-the-driver-message"
    token = _make_token(role="candidate", sub=_CANDIDATE_SUB)
    mock_session = _make_mock_session(user=_fake_self_user(), existing_erasure=None)
    mock_session.commit = AsyncMock(side_effect=SQLAlchemyError(leak))

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_SELF_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 500
    assert leak not in resp.text
    assert mock_session.rollback.called


def test_admin_erasure_race_returns_409_not_202() -> None:
    """The deliberate divergence from the self path: an operator's duplicate is
    an operator mistake and is surfaced loudly."""
    token = _make_token(role="admin", sub=_ADMIN_SUB)
    mock_session = _make_mock_session(user=_fake_user(), existing_erasure=None)
    mock_session.commit = AsyncMock(
        side_effect=IntegrityError("INSERT INTO erasure_requests", {}, Exception("duplicate"))
    )

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 409
    assert _KNOWN_USER_ID in resp.json()["detail"]
    assert mock_session.rollback.called


def test_admin_erasure_db_error_is_generic_and_leaks_no_driver_text() -> None:
    leak = "candidate-email-in-the-driver-message"
    token = _make_token(role="admin", sub=_ADMIN_SUB)
    mock_session = _make_mock_session(user=_fake_user(), existing_erasure=None)
    mock_session.commit = AsyncMock(side_effect=SQLAlchemyError(leak))

    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(_ERASURE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 500
    assert leak not in resp.text
    assert mock_session.rollback.called


# ---------------------------------------------------------------------------
# Self-service erasure must reject NARROW, single-purpose credentials
# ---------------------------------------------------------------------------
#
# Found by the security review of this change. The self-service endpoint was
# gated on AuthenticatedDep — "any valid, non-revoked token". That correctly
# stops a caller naming SOMEONE ELSE (identity comes from the signature, and
# there is no user_id parameter), but it does not stop a narrow token acting for
# its own subject.
#
# The interview-invite flow mints `guest_candidate` with a REAL user UUID as
# `sub` and a `session_id` claim, signed with the one `jwt_secret` every service
# shares — so it verified cleanly in admin_ops. The route is mounted at the app
# root and both Caddyfiles proxy /users/*/dpdp/* here, so the whole chain was
# publicly reachable: forwarded invite link -> redeem -> 15-minute guest token
# -> irreversible erasure of that candidate's account AND the hiring company's
# ATS record for them. No step-up auth, no undo.


def _session_bound_token(role: str, sub: str = _CANDIDATE_SUB) -> str:
    """A credential scoped to one interview room, as interview_take.py mints it."""
    return issue_access_token(
        user_id=sub,
        roles=[role],
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        extra_claims={"session_id": "77777777-8888-9999-aaaa-bbbbbbbbbbbb"},
    )


def test_self_erasure_rejects_a_guest_interview_token() -> None:
    """A guest_candidate token must not be able to erase the account it names."""
    mock_session = _make_mock_session(user=_fake_self_user())
    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(
        _SELF_ERASURE_URL,
        headers={"Authorization": f"Bearer {_session_bound_token('guest_candidate')}"},
    )
    assert resp.status_code == 403, resp.text
    # 403 not 401: the token IS valid. Saying "invalid token" would send an
    # operator hunting the wrong problem.
    assert "sign" in resp.json()["detail"].lower()


def test_self_erasure_rejects_any_session_bound_token() -> None:
    """Belt and braces: a session_id claim marks a room-scoped credential.

    Pinned separately from the role check so that adding a future scoped role
    without updating _NON_ACCOUNT_ROLES still fails closed.
    """
    mock_session = _make_mock_session(user=_fake_self_user())
    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(
        _SELF_ERASURE_URL,
        headers={"Authorization": f"Bearer {_session_bound_token('candidate')}"},
    )
    assert resp.status_code == 403, resp.text


# A service-token case is deliberately NOT repeated here: a real service
# token's `sub` is a service NAME, so it is rejected by the UUID guard, and
# test_self_erasure_rejects_a_token_whose_subject_is_not_a_user already pins
# that with the realistic token shape. `service` is correspondingly absent
# from _NON_ACCOUNT_ROLES — see the comment there.


def test_self_erasure_still_works_for_a_real_candidate() -> None:
    """The fix must not break the statutory DPDP §11 path it guards."""
    mock_session = _make_mock_session(user=_fake_self_user(), existing_erasure=None)
    client = TestClient(_build_test_app(mock_session), raise_server_exceptions=False)
    resp = client.post(
        _SELF_ERASURE_URL,
        headers={"Authorization": f"Bearer {_make_token(role='candidate', sub=_CANDIDATE_SUB)}"},
    )
    assert resp.status_code == 202, resp.text
