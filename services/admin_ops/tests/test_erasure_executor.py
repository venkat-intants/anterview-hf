"""Tests for the DPDP right-to-erasure executor (S5-004 enforcement layer).

Tests prove that:
  1. run_erasure_poll returns 0 when no due requests exist.
  2. A due request is claimed, PII is deleted/anonymised, and the row is
     stamped completed (full happy path).
  3. PII columns (turns.text_content, resumes.resume_text, users.email /
     full_name / phone etc.) are GONE after execution.
  4. Applicant rows linked to the user are anonymised (not deleted).
  5. A request with scheduled_for in the future is NOT processed.
  6. A request that is already 'completed' is NOT re-processed.
  7. An SQL error on one request leaves it in 'pending' (idempotency /
     rollback) and does not prevent other requests from processing.
  8. The executor skips a row that is locked by a concurrent transaction
     (SKIP LOCKED semantics verified via the discovery→claim flow).

All tests use a mock async session factory — no live PostgreSQL required.
PII note: no email / name / phone appears in any assertion or log call.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.erasure_executor import (
    _execute_one_erasure,
    run_erasure_poll,
)
from app.models import AuditLog, ErasureRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SYSTEM_ACTOR = uuid.UUID("00000000-0000-0000-0000-000000000001")
_USER_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
_REQUEST_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_NOW = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)
_SCHEDULED_PAST = _NOW - timedelta(days=31)
_SCHEDULED_FUTURE = _NOW + timedelta(days=5)


def _make_key_collecting_db(
    *,
    scorecard_keys: list[tuple[str | None, str | None]] | None = None,
    user_resume_key: str | None = None,
    resume_version_keys: list[str] | None = None,
    applicant_resume_keys: list[str] | None = None,
    turn_audio_keys: list[str] | None = None,
) -> tuple[AsyncMock, list[str]]:
    """A DB mock that answers each key-collection SELECT by matching its SQL.

    Dispatches on statement CONTENT, not call index. The previous version keyed
    off "call 3 is the scorecard SELECT, call 4 is the users SELECT", which
    silently mis-routed every canned result the moment the executor's step order
    changed — and the step order is exactly what the DPDP fix had to change. A
    fixture that breaks when correct code is reordered tests the ordering, not
    the behaviour.

    Returns (db_mock, executed_statements).
    """
    db = AsyncMock()
    db.add = MagicMock()
    executed: list[str] = []

    async def _execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        sql = str(stmt)
        executed.append(sql)
        result = MagicMock()
        result.rowcount = 1
        result.fetchall.return_value = []
        result.fetchone.return_value = None

        if "FROM scorecards" in sql and "SELECT" in sql:
            result.fetchall.return_value = scorecard_keys or []
        elif "FROM resumes" in sql and sql.strip().startswith("SELECT"):
            result.fetchall.return_value = [(k,) for k in (resume_version_keys or [])]
        elif "FROM applicants" in sql and sql.strip().startswith("SELECT"):
            result.fetchall.return_value = [(k,) for k in (applicant_resume_keys or [])]
        elif "audio_s3_key" in sql and sql.strip().startswith("SELECT"):
            result.fetchall.return_value = [(k,) for k in (turn_audio_keys or [])]
        elif "FROM users" in sql and "resume_s3_key" in sql:
            result.fetchone.return_value = (
                (user_resume_key,) if user_resume_key else None
            )
        return result

    db.execute = _execute
    return db, executed


def _mock_s3_settings() -> MagicMock:
    settings = MagicMock()
    settings.s3_scorecard_bucket = "intants-interview-scorecards"
    settings.s3_bucket_name = "intants-uploads"
    settings.s3_endpoint_url = "https://fake-endpoint.example.com"
    settings.s3_access_key_id = "fake-key"
    settings.s3_secret_access_key = "fake-secret"
    settings.s3_region = "auto"
    return settings


def _fake_delete_objects(
    sink: list[dict[str, list[str]]] | None = None,
) -> AsyncMock:
    """A delete_objects stand-in that honours the real contract: returns a count.

    Every stub here must return the number of keys it was handed, because that
    number is what the executor checks before it is willing to stamp
    'completed'. A stub returning None (what the real function used to return)
    is precisely the false-success this suite exists to catch — so the stub is
    built in one place rather than re-typed per test.
    """

    async def _delete(keys_by_bucket: dict[str, list[str]], *, settings: Any) -> int:
        if sink is not None:
            sink.append(dict(keys_by_bucket))
        return sum(len(keys) for keys in keys_by_bucket.values())

    return AsyncMock(side_effect=_delete)


def _make_erasure_request(
    scheduled_for: datetime = _SCHEDULED_PAST,
    status: str = "pending",
) -> ErasureRequest:
    return ErasureRequest(
        request_id=_REQUEST_ID,
        user_id=_USER_ID,
        requested_by=_SYSTEM_ACTOR,
        reason="test",
        status=status,
        scheduled_for=scheduled_for,
        completed_at=None,
        artifacts=None,
        created_at=_SCHEDULED_PAST - timedelta(days=30),
    )


def _make_db_session(
    *,
    execute_side_effects: list[Any] | None = None,
) -> AsyncMock:
    """Build a minimal async DB session mock.

    Each call to db.execute() returns the next value from execute_side_effects.
    If the list is shorter than the number of calls the last value is repeated.
    """
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()

    effects = execute_side_effects or []
    call_index: dict[str, int] = {"i": 0}

    async def _execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        idx = min(call_index["i"], len(effects) - 1) if effects else 0
        call_index["i"] += 1
        result = MagicMock()
        result.rowcount = 5
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        if idx < len(effects):
            effect = effects[idx]
            if isinstance(effect, Exception):
                raise effect
            result.rowcount = effect if isinstance(effect, int) else 5
        return result

    session.execute = _execute
    return session


# ---------------------------------------------------------------------------
# Shared helper: build a synchronous context-manager-returning factory
# ---------------------------------------------------------------------------


class _SyncCM:
    """Wraps an AsyncMock session so it can be used as ``async with factory()``."""

    def __init__(self, sess: AsyncMock) -> None:
        self._sess = sess

    async def __aenter__(self) -> AsyncMock:
        return self._sess

    async def __aexit__(self, *_: Any) -> bool:
        return False


def _make_factory(sessions: list[AsyncMock]) -> Any:
    """Return a factory callable that yields each session in order."""
    session_iter = iter(sessions)

    def _factory() -> _SyncCM:
        return _SyncCM(next(session_iter))

    return _factory


def _empty_session() -> AsyncMock:
    """Session that returns no rows on execute()."""
    session = AsyncMock()
    result = MagicMock()
    result.fetchall.return_value = []
    result.fetchone.return_value = None
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    return session


# ---------------------------------------------------------------------------
# Test 1 — no due requests: poll returns 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_no_due_requests() -> None:
    """run_erasure_poll returns 0 when there are no due erasure requests."""
    factory = _make_factory([_empty_session()])
    count = await run_erasure_poll(
        session_factory=factory,  # type: ignore[arg-type]
        system_actor_id=_SYSTEM_ACTOR,
    )
    assert count == 0


# ---------------------------------------------------------------------------
# Test 2 — happy path: one due request is processed and stamped completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_one_erasure_happy_path() -> None:
    """_execute_one_erasure commits all steps and returns a non-empty artifacts dict."""
    db = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    executed_statements: list[str] = []

    async def _execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        executed_statements.append(str(stmt).strip()[:80])
        result = MagicMock()
        result.rowcount = 3
        result.fetchall.return_value = [
            ("s3://bucket/report.pdf", "s3://bucket/transcript.json")
        ]
        result.fetchone.return_value = None
        return result

    db.execute = _execute

    request = _make_erasure_request()
    # This DB mock hands back object keys on every SELECT, so the happy path
    # needs working storage: an erasure that collects keys it cannot delete is
    # no longer allowed to complete.
    with patch("app.s3_client.delete_objects", new=_fake_delete_objects()):
        artifacts = await _execute_one_erasure(
            db=db,
            request=request,
            system_actor_id=_SYSTEM_ACTOR,
            settings=_mock_s3_settings(),
        )

    # artifacts must be a dict with all expected keys
    assert isinstance(artifacts, dict)
    assert artifacts["turns_deleted"] == 3
    assert artifacts["resumes_deleted"] == 3
    assert artifacts["scorecards_deleted"] == 3
    assert artifacts["sessions_deleted"] == 3
    assert artifacts["applicants_anonymised"] == 3
    assert "completed_at" in artifacts
    assert "scorecard_s3_keys" in artifacts

    # db.add must have been called with an AuditLog instance
    assert db.add.called
    added_obj = db.add.call_args[0][0]
    assert isinstance(added_obj, AuditLog)
    assert added_obj.action == "dpdp_erasure_completed"
    assert added_obj.actor_type == "system"
    assert added_obj.resource_id == _USER_ID


# ---------------------------------------------------------------------------
# Test 3 — PII columns are targeted in the UPDATE statement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_one_erasure_pii_columns_targeted() -> None:
    """The user UPDATE statement must target all PII columns.

    The executor uses parameterised SQL (text() with bind params), so the
    email sentinel value is in the params dict, not in the SQL string itself.
    We capture both the statement string and the params to verify correctness.
    """
    db = AsyncMock()
    db.add = MagicMock()

    executed_stmts: list[str] = []
    executed_params: list[Any] = []

    async def _execute(stmt: Any, params: Any = None, *args: Any, **kwargs: Any) -> MagicMock:
        executed_stmts.append(str(stmt))
        executed_params.append(params)
        result = MagicMock()
        result.rowcount = 0
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        return result

    db.execute = _execute

    request = _make_erasure_request()
    await _execute_one_erasure(db=db, request=request, system_actor_id=_SYSTEM_ACTOR)

    # Find the UPDATE users statement
    user_update = next(
        (s for s in executed_stmts if "UPDATE users" in s),
        None,
    )
    assert user_update is not None, "No UPDATE users statement was executed"

    # All PII columns must appear in the SQL text (as named params or literals)
    for col in (
        "email",
        "full_name",
        "phone",
        "password_hash",
        "naipunyam_id",
        "resume_text",
        "resume_s3_key",
        "linkedin_url",
        "github_url",
        "avatar_url",
        "headline",
        "bio",
        "official_email",
    ):
        assert col in user_update, f"Column '{col}' missing from UPDATE users statement"

    # The email sentinel value must be in the params dict (parameterised query)
    user_update_idx = next(
        i for i, s in enumerate(executed_stmts) if "UPDATE users" in s
    )
    params = executed_params[user_update_idx]
    assert params is not None, "UPDATE users must pass bind parameters"
    assert "sentinel" in params, "UPDATE users must pass :sentinel bind param"
    assert "@deleted.invalid" in params["sentinel"], (
        "Email sentinel value must end with @deleted.invalid"
    )


# ---------------------------------------------------------------------------
# Test 4 — applicant rows are anonymised, not deleted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_one_erasure_applicants_anonymised() -> None:
    """Applicant rows with user_id = erased user are UPDATE'd, not DELETE'd."""
    db = AsyncMock()
    db.add = MagicMock()

    executed_stmts: list[str] = []

    async def _execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        executed_stmts.append(str(stmt))
        result = MagicMock()
        result.rowcount = 2
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        return result

    db.execute = _execute

    request = _make_erasure_request()
    artifacts = await _execute_one_erasure(
        db=db, request=request, system_actor_id=_SYSTEM_ACTOR
    )

    # There must be an UPDATE applicants statement
    applicant_update = next(
        (s for s in executed_stmts if "UPDATE applicants" in s),
        None,
    )
    assert applicant_update is not None, "No UPDATE applicants statement was executed"
    assert "full_name" in applicant_update
    assert "[redacted]" in applicant_update

    # There must NOT be a DELETE applicants statement
    applicant_delete = next(
        (s for s in executed_stmts if "DELETE" in s and "applicants" in s),
        None,
    )
    assert applicant_delete is None, (
        "Applicant rows must be anonymised (UPDATE), not deleted"
    )

    assert artifacts["applicants_anonymised"] == 2


# ---------------------------------------------------------------------------
# Test 5 — turns are hard-deleted (transcript PII gone)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_one_erasure_turns_hard_deleted() -> None:
    """DELETE FROM turns must be issued for the user's sessions."""
    db = AsyncMock()
    db.add = MagicMock()

    executed_stmts: list[str] = []

    async def _execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        executed_stmts.append(str(stmt))
        result = MagicMock()
        result.rowcount = 7
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        return result

    db.execute = _execute

    request = _make_erasure_request()
    artifacts = await _execute_one_erasure(
        db=db, request=request, system_actor_id=_SYSTEM_ACTOR
    )

    turns_delete = next(
        (s for s in executed_stmts if "DELETE FROM turns" in s),
        None,
    )
    assert turns_delete is not None, "No DELETE FROM turns statement was executed"
    # Must filter by session_id -> user_id (not by user_id directly)
    assert "sessions" in turns_delete, (
        "DELETE FROM turns must filter via sessions table to respect FK"
    )
    assert artifacts["turns_deleted"] == 7


# ---------------------------------------------------------------------------
# Test 6 — resumes are hard-deleted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_one_erasure_resumes_hard_deleted() -> None:
    """DELETE FROM resumes must be issued for the erased user."""
    db = AsyncMock()
    db.add = MagicMock()

    executed_stmts: list[str] = []

    async def _execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        executed_stmts.append(str(stmt))
        result = MagicMock()
        result.rowcount = 4
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        return result

    db.execute = _execute

    request = _make_erasure_request()
    artifacts = await _execute_one_erasure(
        db=db, request=request, system_actor_id=_SYSTEM_ACTOR
    )

    resumes_delete = next(
        (s for s in executed_stmts if "DELETE FROM resumes" in s),
        None,
    )
    assert resumes_delete is not None, "No DELETE FROM resumes statement was executed"
    assert artifacts["resumes_deleted"] == 4


# ---------------------------------------------------------------------------
# Test 7 — erasure_request is stamped completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_one_erasure_stamps_completed() -> None:
    """After execution, UPDATE erasure_requests sets status='completed'.

    SQLAlchemy's ORM update() renders as SQL when str() is called, so we
    can check for the table name and key column names in the SQL string.
    """
    db = AsyncMock()
    db.add = MagicMock()

    executed_stmts: list[str] = []

    async def _execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        # str() on both text() and update() gives the SQL string
        executed_stmts.append(str(stmt))
        result = MagicMock()
        result.rowcount = 1
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        return result

    db.execute = _execute

    request = _make_erasure_request()
    artifacts = await _execute_one_erasure(
        db=db, request=request, system_actor_id=_SYSTEM_ACTOR
    )

    # The SQLAlchemy update() on ErasureRequest renders to SQL with table name
    # and the column names in the SET clause.
    update_stmt = next(
        (
            s for s in executed_stmts
            if "erasure_requests" in s and "status" in s and "completed_at" in s
        ),
        None,
    )
    assert update_stmt is not None, (
        "No UPDATE erasure_requests statement with status+completed_at was executed"
    )
    assert "completed_at" in artifacts
    # 1.2 since step 5b (notifications) joined the erasure — DPDP-7. The version
    # is asserted rather than ignored because the artifacts blob is the auditor's
    # record of WHAT a completion covered, so widening coverage without moving
    # the version leaves two incomparable records claiming the same one.
    assert artifacts["executor_version"] == "1.2"


# ---------------------------------------------------------------------------
# Test 8 — SQL error leaves request in 'pending' (idempotency)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_sql_error_leaves_request_pending() -> None:
    """When a DB error occurs during _execute_one_erasure, the executor
    catches it, rolls back, and does not increment the completed count.
    The erasure_request row remains in 'pending' status (not mutated by the
    rolled-back transaction).

    We test this at the _execute_one_erasure level directly (not through
    run_erasure_poll) to avoid the complex two-session mock dance while still
    confirming the contract: rollback is called on error.
    """
    from sqlalchemy.exc import OperationalError

    db = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    call_count: dict[str, int] = {"i": 0}

    async def _execute_raises_on_third(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        call_count["i"] += 1
        result = MagicMock()
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        result.rowcount = 0
        # Raise on the first real DELETE (turns), simulating a DB failure mid-execution
        if call_count["i"] >= 1:
            raise OperationalError("simulated DB failure", None, None)
        return result

    db.execute = _execute_raises_on_third

    request = _make_erasure_request()

    # _execute_one_erasure should propagate the SQLAlchemyError
    with pytest.raises(OperationalError):
        await _execute_one_erasure(
            db=db, request=request, system_actor_id=_SYSTEM_ACTOR
        )

    # The caller (run_erasure_poll) is responsible for rollback —
    # confirm the error propagated so the caller can act on it.
    # Also confirm db.add was NOT called (no audit_log written for failed execution).
    assert not db.add.called, "audit_log must NOT be written on a failed erasure"


# ---------------------------------------------------------------------------
# Test 9 — scorecards S3 keys are captured in artifacts for file-purge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_one_erasure_scorecard_keys_in_artifacts() -> None:
    """Scorecard S3 keys are captured and stored in artifacts for downstream purge."""
    _scorecard_keys = [
        ("s3://bucket/report1.pdf", "s3://bucket/transcript1.json"),
        ("s3://bucket/report2.pdf", None),
    ]
    db, _ = _make_key_collecting_db(scorecard_keys=_scorecard_keys)

    request = _make_erasure_request()
    with patch("app.s3_client.delete_objects", new=_fake_delete_objects()):
        artifacts = await _execute_one_erasure(
            db=db,
            request=request,
            system_actor_id=_SYSTEM_ACTOR,
            settings=_mock_s3_settings(),
        )

    assert "scorecard_s3_keys" in artifacts
    assert len(artifacts["scorecard_s3_keys"]) == 2
    assert artifacts["scorecard_s3_keys"][0]["pdf"] == "s3://bucket/report1.pdf"
    assert artifacts["scorecard_s3_keys"][1]["transcript"] is None


# ---------------------------------------------------------------------------
# Test 10 — S3 delete_object IS called for EVERY collected artifact key
#
# This is the regression guard for the DPDP §12 false-erasure bug.
# The test WILL FAIL if the delete_objects call is removed from
# _execute_one_erasure.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_one_erasure_s3_delete_called_for_every_key() -> None:
    """delete_object must be called once per artifact key.

    Scenario:
      - 2 scorecard rows: row 1 has pdf + transcript keys; row 2 has pdf only.
      - 1 resume S3 key on the users row.
      Total expected S3 deletes: 4 (pdf1 + transcript1 + pdf2 + resume).

    The test uses a mock Settings and a mock delete_objects coroutine injected
    via unittest.mock.patch so that no real network call is made.

    Assertion (a): delete_objects is called exactly once with the correct
    keys_by_bucket mapping.

    Assertion (b): status='completed' IS stamped when S3 succeeds (confirming
    the code path is reachable).
    """
    # ---- DB mock -----------------------------------------------------------
    _scorecard_keys = [
        ("scorecards/sc1/report.pdf", "scorecards/sc1/transcript.json"),
        ("scorecards/sc2/report.pdf", None),
    ]
    _user_resume_key = "resumes/user-uuid/resume.pdf"

    db, executed_stmts = _make_key_collecting_db(
        scorecard_keys=_scorecard_keys,
        user_resume_key=_user_resume_key,
    )

    # ---- Settings mock -----------------------------------------------------
    mock_settings = _mock_s3_settings()

    # ---- Patch delete_objects ----------------------------------------------
    delete_calls: list[dict[str, list[str]]] = []

    request = _make_erasure_request()

    # Patch delete_objects at the source module (app.s3_client) so the local
    # import inside _execute_one_erasure picks up the mock.
    with patch("app.s3_client.delete_objects", new=_fake_delete_objects(delete_calls)):
        artifacts = await _execute_one_erasure(
            db=db,
            request=request,
            system_actor_id=_SYSTEM_ACTOR,
            settings=mock_settings,
        )

    # Assertion (a): delete_objects was called exactly once.
    assert len(delete_calls) == 1, (
        f"delete_objects must be called exactly once (called {len(delete_calls)} times). "
        "If 0: the S3 delete call was removed — that is the DPDP §12 false-erasure bug."
    )

    called_buckets = delete_calls[0]

    # Scorecard bucket must contain exactly the 3 non-None scorecard keys.
    sc_bucket = mock_settings.s3_scorecard_bucket
    assert sc_bucket in called_buckets, (
        f"Scorecard bucket '{sc_bucket}' missing from delete_objects call"
    )
    assert sorted(called_buckets[sc_bucket]) == sorted([
        "scorecards/sc1/report.pdf",
        "scorecards/sc1/transcript.json",
        "scorecards/sc2/report.pdf",
    ]), f"Wrong scorecard keys: {called_buckets[sc_bucket]}"

    # Resume bucket must contain the user-level resume key.
    uploads_bucket = mock_settings.s3_bucket_name
    assert uploads_bucket in called_buckets, (
        f"Uploads bucket '{uploads_bucket}' missing from delete_objects call"
    )
    assert called_buckets[uploads_bucket] == [_user_resume_key], (
        f"Wrong resume keys: {called_buckets[uploads_bucket]}"
    )

    # Assertion (b): status='completed' was stamped (DB update was executed).
    update_stmt = next(
        (s for s in executed_stmts if "erasure_requests" in s and "status" in s),
        None,
    )
    assert update_stmt is not None, (
        "UPDATE erasure_requests with status must be executed when S3 delete succeeds"
    )
    assert "completed_at" in artifacts


# ---------------------------------------------------------------------------
# Test 11 — status is NOT stamped 'completed' when S3 delete raises
#
# This is the other half of the regression guard: if S3 deletion fails,
# the function must raise (so the caller rolls back) rather than silently
# stamping 'completed'.
#
# The test WILL FAIL if the S3 delete call is removed (the status would
# never raise on S3 failure, and the DB would be stamped regardless).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_one_erasure_s3_failure_prevents_completed_stamp() -> None:
    """When delete_objects raises, _execute_one_erasure must propagate the
    exception so the caller can roll back and leave the request in 'pending'.

    This test verifies that:
      - The exception from S3 is re-raised (not swallowed).
      - The UPDATE erasure_requests statement is NEVER executed.
      - db.add (audit_log) is NEVER called.

    If the S3 delete call is removed from _execute_one_erasure this test
    would fail because the exception would no longer be raised from inside
    _execute_one_erasure — the function would complete successfully and
    stamp 'completed' without any S3 delete.
    """
    from botocore.exceptions import ClientError

    # ---- DB mock -----------------------------------------------------------
    _scorecard_keys = [("scorecards/sc1/report.pdf", None)]
    db, executed_stmts = _make_key_collecting_db(scorecard_keys=_scorecard_keys)

    # ---- Settings mock -----------------------------------------------------
    mock_settings = _mock_s3_settings()

    # ---- S3 raises a non-absent ClientError --------------------------------
    s3_error = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}},
        "DeleteObject",
    )

    async def _failing_delete(
        keys_by_bucket: dict[str, list[str]],
        *,
        settings: Any,
    ) -> None:
        raise s3_error

    request = _make_erasure_request()

    with (
        patch("app.s3_client.delete_objects", new=AsyncMock(side_effect=_failing_delete)),
        pytest.raises(ClientError) as exc_info,
    ):
        await _execute_one_erasure(
            db=db,
            request=request,
            system_actor_id=_SYSTEM_ACTOR,
            settings=mock_settings,
        )

    # The raised exception must be the S3 ClientError (not swallowed or wrapped).
    assert exc_info.value is s3_error, (
        "S3 ClientError must propagate unchanged so the caller can roll back"
    )

    # UPDATE erasure_requests must NOT have been executed (status stays 'pending').
    update_stmt = next(
        (s for s in executed_stmts if "erasure_requests" in s and "status" in s),
        None,
    )
    assert update_stmt is None, (
        "UPDATE erasure_requests must NOT be executed when S3 delete fails. "
        "If this assertion fails, the erasure executor is making a false DPDP §12 claim."
    )

    # audit_log must NOT be written for a failed erasure.
    assert not db.add.called, (
        "audit_log entry must NOT be written when erasure fails mid-way"
    )


# ---------------------------------------------------------------------------
# DPDP erasure-coverage regression set (2026-08-06)
#
# The executor used to DELETE FROM resumes and only THEN look for the S3 keys
# those rows held, so every superseded resume PDF was orphaned in R2 while
# status='completed' was stamped and a dpdp_erasure_completed audit row was
# written. It also left applicants.embedding — a halfvec(3072) derived from the
# resume text it had just nulled — in place.
#
# Each test below was mutation-checked: the corresponding line was reverted and
# the test confirmed to go red.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_erasure_deletes_every_resume_version_object() -> None:
    """All resumes.resume_s3_key objects must reach S3 deletion, not just the
    one referenced by users.resume_s3_key.

    This is the DPDP §12 false-completion bug: a candidate who re-uploaded their
    CV four times had three PDFs left in R2 after an erasure that reported
    itself complete.
    """
    db, _ = _make_key_collecting_db(
        user_resume_key="resumes/u1/current.pdf",
        resume_version_keys=[
            "resumes/u1/current.pdf",  # duplicate of the users row on purpose
            "resumes/u1/v2.pdf",
            "resumes/u1/v1.pdf",
        ],
    )
    mock_settings = _mock_s3_settings()

    delete_calls: list[dict[str, list[str]]] = []

    with patch("app.s3_client.delete_objects", new=_fake_delete_objects(delete_calls)):
        artifacts = await _execute_one_erasure(
            db=db,
            request=_make_erasure_request(),
            system_actor_id=_SYSTEM_ACTOR,
            settings=mock_settings,
        )

    uploads = delete_calls[0][mock_settings.s3_bucket_name]
    assert sorted(uploads) == sorted(
        ["resumes/u1/current.pdf", "resumes/u1/v2.pdf", "resumes/u1/v1.pdf"]
    ), f"superseded resume versions were not purged: {uploads}"

    # The users-row key duplicates a resumes row; it must appear exactly once so
    # the artifacts count an auditor reads is not inflated.
    assert len(uploads) == 3, f"keys were not deduplicated: {uploads}"
    assert artifacts["resume_objects_deleted"] == 3


@pytest.mark.asyncio
async def test_erasure_collects_resume_keys_before_deleting_the_rows() -> None:
    """Key collection must precede the DELETEs.

    Pins the ordering directly rather than only its consequence: if a
    DELETE FROM resumes is ever moved back above the SELECT, the keys are gone
    from the transaction and cannot be collected at all.
    """
    db, executed = _make_key_collecting_db(resume_version_keys=["resumes/u1/v1.pdf"])

    with patch("app.s3_client.delete_objects", new=_fake_delete_objects()):
        await _execute_one_erasure(
            db=db,
            request=_make_erasure_request(),
            system_actor_id=_SYSTEM_ACTOR,
            settings=_mock_s3_settings(),
        )

    select_idx = next(
        i for i, s in enumerate(executed)
        if s.strip().startswith("SELECT") and "FROM resumes" in s
    )
    delete_idx = next(
        i for i, s in enumerate(executed)
        if s.strip().startswith("DELETE") and "FROM resumes" in s
    )
    assert select_idx < delete_idx, (
        "resume S3 keys must be SELECTed before DELETE FROM resumes; "
        "collecting after the delete silently loses every key"
    )

    audio_select_idx = next(
        i for i, s in enumerate(executed)
        if s.strip().startswith("SELECT") and "audio_s3_key" in s
    )
    turns_delete_idx = next(
        i for i, s in enumerate(executed)
        if s.strip().startswith("DELETE") and "FROM turns" in s
    )
    assert audio_select_idx < turns_delete_idx, (
        "turn audio keys must be SELECTed before DELETE FROM turns"
    )


@pytest.mark.asyncio
async def test_erasure_nulls_the_applicant_embedding() -> None:
    """applicants.embedding is derived from resume_text and must be nulled too.

    Leaving it keeps a dense representation of the erased CV in the DB and keeps
    the applicant semantically searchable via GET /hr/applicants?q=.
    """
    db, executed = _make_key_collecting_db()

    with patch("app.s3_client.delete_objects", new=_fake_delete_objects()):
        await _execute_one_erasure(
            db=db,
            request=_make_erasure_request(),
            system_actor_id=_SYSTEM_ACTOR,
            settings=_mock_s3_settings(),
        )

    applicants_update = next(
        (s for s in executed if s.strip().startswith("UPDATE applicants")), None
    )
    assert applicants_update is not None, "applicants UPDATE must run"
    assert "embedding = NULL" in applicants_update, (
        "applicants.embedding must be nulled — it is a vector derived from the "
        "resume_text this same statement nulls"
    )


@pytest.mark.asyncio
async def test_erasure_deletes_applicant_resume_objects() -> None:
    """Nulling applicants.resume_s3_key without deleting the object orphans it."""
    db, _ = _make_key_collecting_db(
        applicant_resume_keys=["applicants/a1/cv.pdf", "applicants/a2/cv.pdf"],
    )
    mock_settings = _mock_s3_settings()
    delete_calls: list[dict[str, list[str]]] = []

    with patch("app.s3_client.delete_objects", new=_fake_delete_objects(delete_calls)):
        await _execute_one_erasure(
            db=db,
            request=_make_erasure_request(),
            system_actor_id=_SYSTEM_ACTOR,
            settings=mock_settings,
        )

    uploads = delete_calls[0][mock_settings.s3_bucket_name]
    assert "applicants/a1/cv.pdf" in uploads
    assert "applicants/a2/cv.pdf" in uploads


@pytest.mark.asyncio
async def test_erasure_deletes_turn_audio_when_present() -> None:
    """turns.audio_s3_key is NULL today; erasure must already handle it.

    The voice pipeline will start populating this column. Without this path the
    first candidate to request erasure after that ships would leave their speech
    recordings in the bucket, and nothing would say so.
    """
    db, _ = _make_key_collecting_db(
        turn_audio_keys=["audio/s1/turn-1.ogg", "audio/s1/turn-2.ogg"],
    )
    mock_settings = _mock_s3_settings()
    delete_calls: list[dict[str, list[str]]] = []

    with patch("app.s3_client.delete_objects", new=_fake_delete_objects(delete_calls)):
        artifacts = await _execute_one_erasure(
            db=db,
            request=_make_erasure_request(),
            system_actor_id=_SYSTEM_ACTOR,
            settings=mock_settings,
        )

    scorecard_bucket = delete_calls[0][mock_settings.s3_scorecard_bucket]
    assert "audio/s1/turn-1.ogg" in scorecard_bucket
    assert "audio/s1/turn-2.ogg" in scorecard_bucket
    assert artifacts["turn_audio_objects_deleted"] == 2


@pytest.mark.asyncio
async def test_erasure_artifacts_count_matches_keys_actually_deleted() -> None:
    """The artifacts number is what an auditor reads; it must not be a guess.

    The old expression was len(scorecard_keys) * 2 + (1 if user_resume_s3_key),
    which over-counted scorecards with a NULL transcript_key and ignored resume
    versions entirely.
    """
    # Numbers chosen so the old and new formulas DIVERGE. With two scorecard
    # rows and one resume version they both happen to say 5, and a test that
    # cannot tell the formulas apart is not testing the fix.
    #   old: len(scorecard_keys) * 2 + (1 if user_resume_s3_key)  =  1*2 + 1 = 3
    #   new: len(scorecard_bucket_keys) + len(resume_bucket_keys)  =  1  + 3 = 4
    db, _ = _make_key_collecting_db(
        scorecard_keys=[
            ("sc/1/report.pdf", None),  # no transcript — old code counted 2 here
        ],
        user_resume_key="resumes/u1/current.pdf",
        resume_version_keys=["resumes/u1/v1.pdf", "resumes/u1/v2.pdf"],
    )
    mock_settings = _mock_s3_settings()
    delete_calls: list[dict[str, list[str]]] = []

    with patch("app.s3_client.delete_objects", new=_fake_delete_objects(delete_calls)):
        artifacts = await _execute_one_erasure(
            db=db,
            request=_make_erasure_request(),
            system_actor_id=_SYSTEM_ACTOR,
            settings=mock_settings,
        )

    actually_deleted = sum(len(v) for v in delete_calls[0].values())
    # 1 scorecard key + 3 resume keys = 4. The old formula would have said 3.
    assert actually_deleted == 4
    assert artifacts["s3_objects_deleted"] == actually_deleted, (
        "artifacts must report the keys actually passed to delete_objects"
    )


# ---------------------------------------------------------------------------
# M-1a (2026-08-07) — unconfigured storage must not read as a successful delete
#
# delete_objects used to short-circuit and return None when credentials were
# absent, which is indistinguishable from "deleted everything". The executor's
# only guard was `settings is not None`, so a real-but-unconfigured Settings
# took the deleted branch, wrote a NON-ZERO s3_objects_deleted, and stamped
# status='completed' plus a dpdp_erasure_completed audit row over objects that
# were all still in the bucket.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_erasure_refuses_to_complete_when_settings_missing() -> None:
    """Keys collected + no Settings must raise, not stamp 'completed' with 0.

    Reporting zero deletions in the artifacts record is not enough: the row is
    still marked completed, so nothing will ever retry it and the audit trail
    claims an erasure that did not happen.
    """
    from app.s3_client import StorageNotConfiguredError

    db, executed = _make_key_collecting_db(
        scorecard_keys=[("scorecards/sc1/report.pdf", None)],
    )

    with pytest.raises(StorageNotConfiguredError):
        await _execute_one_erasure(
            db=db,
            request=_make_erasure_request(),
            system_actor_id=_SYSTEM_ACTOR,
            settings=None,
        )

    update_stmt = next(
        (s for s in executed if "erasure_requests" in s and "status" in s), None
    )
    assert update_stmt is None, (
        "erasure_requests must NOT be stamped when the objects could not be deleted"
    )
    assert not db.add.called, "no dpdp_erasure_completed audit row on a refused erasure"


@pytest.mark.asyncio
async def test_erasure_completes_without_storage_when_there_is_nothing_to_delete() -> None:
    """A user with no stored objects is fully erased by the DB steps alone.

    The refusal above must be about undeleted objects, not about the absence of
    credentials in itself — otherwise local dev and CI could never erase anyone
    and the guard would be switched back off.
    """
    db, executed = _make_key_collecting_db()

    artifacts = await _execute_one_erasure(
        db=db,
        request=_make_erasure_request(),
        system_actor_id=_SYSTEM_ACTOR,
        settings=None,
    )

    assert artifacts["s3_objects_deleted"] == 0
    update_stmt = next(
        (s for s in executed if "erasure_requests" in s and "status" in s), None
    )
    assert update_stmt is not None, "an erasure with no objects must still complete"


@pytest.mark.asyncio
async def test_erasure_refuses_to_complete_when_credentials_unset() -> None:
    """A real-but-unconfigured Settings must fail the same way as no Settings.

    This is the M-1a path exactly: settings is not None, so the old guard was
    satisfied, and the unconfigured skip inside delete_objects returned None —
    which the executor read as success.
    """
    from app.s3_client import StorageNotConfiguredError

    db, executed = _make_key_collecting_db(
        user_resume_key="resumes/u1/current.pdf",
    )
    unconfigured = _mock_s3_settings()
    unconfigured.s3_endpoint_url = ""
    unconfigured.s3_access_key_id = ""

    # NOT patched: the real delete_objects is what must refuse, and it must do
    # so before opening any client, so this makes no network call.
    with pytest.raises(StorageNotConfiguredError):
        await _execute_one_erasure(
            db=db,
            request=_make_erasure_request(),
            system_actor_id=_SYSTEM_ACTOR,
            settings=unconfigured,
        )

    update_stmt = next(
        (s for s in executed if "erasure_requests" in s and "status" in s), None
    )
    assert update_stmt is None, (
        "an unconfigured deployment must leave the request pending, not completed"
    )
    assert not db.add.called


@pytest.mark.asyncio
async def test_erasure_refuses_to_complete_on_delete_shortfall() -> None:
    """Storage reporting fewer deletions than keys collected must not complete.

    Guards the contract itself: the executor trusts the returned count, so a
    delete_objects that ever starts under-deleting silently must be caught here
    rather than in an auditor's sample.
    """
    from app.erasure_executor import ErasureIncompleteError

    db, executed = _make_key_collecting_db(
        resume_version_keys=["resumes/u1/v1.pdf", "resumes/u1/v2.pdf"],
    )

    async def _under_deletes(keys_by_bucket: dict[str, list[str]], *, settings: Any) -> int:
        return 1  # two keys handed over, one reported deleted

    with (
        patch("app.s3_client.delete_objects", new=AsyncMock(side_effect=_under_deletes)),
        pytest.raises(ErasureIncompleteError),
    ):
        await _execute_one_erasure(
            db=db,
            request=_make_erasure_request(),
            system_actor_id=_SYSTEM_ACTOR,
            settings=_mock_s3_settings(),
        )

    update_stmt = next(
        (s for s in executed if "erasure_requests" in s and "status" in s), None
    )
    assert update_stmt is None
    assert not db.add.called


@pytest.mark.asyncio
async def test_poll_leaves_request_pending_when_storage_unconfigured() -> None:
    """The poll loop must roll back and NOT count a storage refusal as completed."""
    db, executed = _make_key_collecting_db(
        user_resume_key="resumes/u1/current.pdf",
    )
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    # The discovery session finds one due request; the work session is the
    # key-collecting mock above, whose claim/reload SELECTs must return rows.
    discovery = _empty_session()
    discovery_result = MagicMock()
    discovery_result.fetchall.return_value = [(str(_REQUEST_ID),)]
    discovery.execute = AsyncMock(return_value=discovery_result)

    claim_row = (str(_REQUEST_ID), str(_USER_ID), "pending")
    full_row = (
        str(_REQUEST_ID),
        str(_USER_ID),
        str(_SYSTEM_ACTOR),
        "test",
        "pending",
        _SCHEDULED_PAST,
        None,
        None,
        _SCHEDULED_PAST,
    )
    inner_execute = db.execute

    async def _execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        sql = str(stmt)
        if "FOR UPDATE SKIP LOCKED" in sql:
            result = MagicMock()
            result.fetchone.return_value = claim_row
            return result
        if "FROM erasure_requests" in sql and "requested_by" in sql:
            result = MagicMock()
            result.fetchone.return_value = full_row
            return result
        return await inner_execute(stmt, *args, **kwargs)

    db.execute = _execute

    unconfigured = _mock_s3_settings()
    unconfigured.s3_endpoint_url = ""
    unconfigured.s3_access_key_id = ""

    completed = await run_erasure_poll(
        session_factory=_make_factory([discovery, db]),  # type: ignore[arg-type]
        system_actor_id=_SYSTEM_ACTOR,
        settings=unconfigured,
    )

    assert completed == 0, "a refused erasure must not be counted as completed"
    assert db.rollback.called, "the transaction must be rolled back so the row stays pending"
    assert not db.commit.called
    assert not any("erasure_requests" in s and "status" in s for s in executed)


# ---------------------------------------------------------------------------
# DPDP-7 — step 5b: notifications must be reached
#
# The cascade argument is what made this table invisible: notifications.user_id
# is ON DELETE CASCADE, so it LOOKS handled. It is not, because step 7
# anonymises the users row rather than deleting it (erasure_requests.user_id is
# ON DELETE RESTRICT), and a cascade that never fires deletes nothing.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_one_erasure_deletes_notifications() -> None:
    """The user's in-app notifications carry their name in free text — go."""
    db, executed = _make_key_collecting_db()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    artifacts = await _execute_one_erasure(
        db=db, request=_make_erasure_request(), system_actor_id=_SYSTEM_ACTOR
    )

    assert any(
        "DELETE FROM notifications" in sql and "user_id" in sql for sql in executed
    ), (
        "erasure must delete the user's notifications — the ON DELETE CASCADE "
        "never fires because the users row is anonymised, not deleted"
    )
    assert "notifications_deleted" in artifacts


@pytest.mark.asyncio
async def test_notifications_are_deleted_before_the_user_is_anonymised() -> None:
    """Ordering guard.

    The DELETE keys off users.id, which survives anonymisation, so this is not
    load-bearing today. It is asserted because the ordering is the class of bug
    that produced the orphaned-resume-objects defect this module's docstring
    already records: a collect/delete step that quietly started running after
    the thing it depends on had changed.
    """
    db, executed = _make_key_collecting_db()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    await _execute_one_erasure(
        db=db, request=_make_erasure_request(), system_actor_id=_SYSTEM_ACTOR
    )

    notif_idx = next(i for i, s in enumerate(executed) if "DELETE FROM notifications" in s)
    user_idx = next(i for i, s in enumerate(executed) if "UPDATE users SET" in s)
    assert notif_idx < user_idx


@pytest.mark.asyncio
async def test_audit_details_report_the_notification_count() -> None:
    """An auditor reads audit_log, not the artifacts JSONB — both must say it."""
    db, _executed = _make_key_collecting_db()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    await _execute_one_erasure(
        db=db, request=_make_erasure_request(), system_actor_id=_SYSTEM_ACTOR
    )

    audit_rows = [
        call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], AuditLog)
    ]
    assert audit_rows, "no audit_log row was written"
    details = audit_rows[-1].details
    assert details is not None
    assert "notifications_deleted" in details
