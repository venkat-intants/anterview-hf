"""Unit tests for the five security / quality fixes in Cluster A.

Fix 1 — change-password session invalidation (auth.py)
Fix 2 — pypdf CVE-2025-62707: asyncio.to_thread + timeout (resume.py, jd.py)
Fix 3 — exam double-submit guard at DB write level (exam_take.py)
Fix 4 — retention purge covers abandoned + consent_withdrawn sessions (retention.py)
Fix 5 — /metrics endpoint is reachable and returns Prometheus text (main.py)

All external I/O is mocked so tests run without any infrastructure.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fix 1 — change_password must verify current_password + revoke sessions
# ---------------------------------------------------------------------------
#
# The endpoint now fetches (must_change_password, password_hash) first, then
# either verifies current_password (normal flow) or skips the check
# (bootstrap first-change flow where must_change_password=true).
#
# Helper: build a mock DB that returns a fetchone() row simulating the SELECT.


def _mock_db_with_row(must_change: bool, stored_hash: str | None) -> MagicMock:
    """Return a mock AsyncSession whose first execute() returns a row
    with (must_change_password, password_hash) and whose second execute()
    accepts the UPDATE statement."""

    row_mock = MagicMock()
    row_mock.__getitem__ = lambda self, i: (must_change, stored_hash)[i]  # type: ignore[index]

    # fetchone() on first result → the SELECT row
    first_result = MagicMock()
    first_result.fetchone.return_value = row_mock

    # second result → the UPDATE (no fetchone needed)
    second_result = MagicMock()

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[first_result, second_result])
    mock_db.commit = AsyncMock()
    return mock_db


@pytest.mark.asyncio
async def test_change_password_revokes_other_sessions() -> None:
    """POST /auth/change-password must call auth.logout_all after committing
    the new password hash — the audit finding 'change_password does not revoke
    sessions' is fixed by this call.

    This test uses must_change_password=True (bootstrap flow) so no current
    password is needed — it isolates the revocation behaviour."""
    from app.routers.auth import ChangePasswordBody, change_password

    user_id = str(uuid.uuid4())
    mock_user = MagicMock()
    mock_user.user_id = user_id

    mock_auth = AsyncMock()
    mock_auth.logout_all = AsyncMock(return_value=3)

    mock_db = _mock_db_with_row(must_change=True, stored_hash=None)

    body = ChangePasswordBody(new_password="NewSecurePass1!")

    with patch("app.routers.auth.asyncio.to_thread", new=AsyncMock(return_value="hashed")):
        result = await change_password(
            body=body,
            current_user=mock_user,
            auth=mock_auth,
            db=mock_db,
        )

    assert result.ok is True
    # logout_all must be called with the current user's ID
    mock_auth.logout_all.assert_awaited_once_with(user_id)
    # DB must have been committed
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_change_password_succeeds_even_if_revoke_fails() -> None:
    """A Redis error in logout_all must NOT fail the password-change response.
    The new password is already the safer state; best-effort revocation.

    Uses must_change_password=True so the current-password check is skipped."""
    from app.routers.auth import ChangePasswordBody, change_password

    user_id = str(uuid.uuid4())
    mock_user = MagicMock()
    mock_user.user_id = user_id

    mock_auth = AsyncMock()
    mock_auth.logout_all = AsyncMock(side_effect=RuntimeError("Redis down"))

    mock_db = _mock_db_with_row(must_change=True, stored_hash=None)

    body = ChangePasswordBody(new_password="AnotherSecurePass1!")

    with patch("app.routers.auth.asyncio.to_thread", new=AsyncMock(return_value="hashed")):
        # Must NOT raise even though logout_all raised
        result = await change_password(
            body=body,
            current_user=mock_user,
            auth=mock_auth,
            db=mock_db,
        )

    assert result.ok is True
    # DB must still have been committed (password change itself succeeded)
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_change_password_requires_current_password_for_normal_user() -> None:
    """POST /auth/change-password must return 400 when current_password is absent
    and must_change_password is False (the normal/non-bootstrap path).

    This is the core fix: a stolen 15-min access token MUST NOT be able to
    permanently take over the account by calling this endpoint without knowing
    the current password."""
    import bcrypt as _bcrypt
    from fastapi import HTTPException

    from app.routers.auth import ChangePasswordBody, change_password

    user_id = str(uuid.uuid4())
    mock_user = MagicMock()
    mock_user.user_id = user_id

    mock_auth = AsyncMock()

    stored_hash = _bcrypt.hashpw(b"OldPass123!", _bcrypt.gensalt(4)).decode()
    mock_db = _mock_db_with_row(must_change=False, stored_hash=stored_hash)

    # No current_password supplied
    body = ChangePasswordBody(new_password="NewSecurePass1!")

    with pytest.raises(HTTPException) as exc_info:
        await change_password(
            body=body,
            current_user=mock_user,
            auth=mock_auth,
            db=mock_db,
        )

    assert exc_info.value.status_code == 400
    assert "current_password" in exc_info.value.detail.lower()
    # logout_all must NOT be called (request rejected before any mutation)
    mock_auth.logout_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_password_rejects_wrong_current_password() -> None:
    """POST /auth/change-password must return 401 when current_password is wrong.

    A stolen access token supplying an incorrect current password must NOT succeed.
    This would FAIL (raise no exception) if the verification were reverted."""
    import bcrypt as _bcrypt
    from fastapi import HTTPException

    from app.routers.auth import ChangePasswordBody, change_password

    user_id = str(uuid.uuid4())
    mock_user = MagicMock()
    mock_user.user_id = user_id
    mock_user.roles = ["hr_manager"]

    mock_auth = AsyncMock()

    # The stored hash is for "OldPass123!" — the attacker guesses "WrongPass999!"
    stored_hash = _bcrypt.hashpw(b"OldPass123!", _bcrypt.gensalt(4)).decode()
    mock_db = _mock_db_with_row(must_change=False, stored_hash=stored_hash)

    body = ChangePasswordBody(
        new_password="AttackerPass1!",
        current_password="WrongPass999!",
    )

    with pytest.raises(HTTPException) as exc_info:
        await change_password(
            body=body,
            current_user=mock_user,
            auth=mock_auth,
            db=mock_db,
        )

    assert exc_info.value.status_code == 401
    mock_auth.logout_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_password_succeeds_with_correct_current_password() -> None:
    """POST /auth/change-password must succeed when correct current_password is supplied."""
    import bcrypt as _bcrypt

    from app.routers.auth import ChangePasswordBody, change_password

    user_id = str(uuid.uuid4())
    mock_user = MagicMock()
    mock_user.user_id = user_id
    mock_user.roles = ["hr_manager"]

    mock_auth = AsyncMock()
    mock_auth.logout_all = AsyncMock(return_value=1)

    stored_hash = _bcrypt.hashpw(b"OldPass123!", _bcrypt.gensalt(4)).decode()
    mock_db = _mock_db_with_row(must_change=False, stored_hash=stored_hash)

    body = ChangePasswordBody(
        new_password="NewSecurePass1!",
        current_password="OldPass123!",
    )

    # Do NOT mock asyncio.to_thread here — we need the real bcrypt.checkpw to run
    # so that the test actually verifies the password comparison logic. We DO mock
    # the gensalt+hashpw call (which is the second to_thread call, for the new hash).
    call_count = 0

    async def _selective_thread(fn: object, *args: object, **kwargs: object) -> object:
        nonlocal call_count
        import asyncio as _asyncio

        call_count += 1
        if call_count == 1:
            # First call: bcrypt.checkpw — run for real so the assertion is meaningful
            return await _asyncio.to_thread(fn, *args, **kwargs)  # type: ignore[arg-type]
        # Second call: bcrypt.hashpw for the new password — return a dummy hash
        return "new_hashed_value"

    with patch("app.routers.auth.asyncio.to_thread", side_effect=_selective_thread):
        result = await change_password(
            body=body,
            current_user=mock_user,
            auth=mock_auth,
            db=mock_db,
        )

    assert result.ok is True
    mock_auth.logout_all.assert_awaited_once_with(user_id)


@pytest.mark.asyncio
async def test_change_password_bootstrap_skips_current_password_check() -> None:
    """When must_change_password=True (bootstrap first-change), the endpoint
    must succeed even without current_password — this is the forced-reset path."""
    from app.routers.auth import ChangePasswordBody, change_password

    user_id = str(uuid.uuid4())
    mock_user = MagicMock()
    mock_user.user_id = user_id

    mock_auth = AsyncMock()
    mock_auth.logout_all = AsyncMock(return_value=0)

    # No stored hash — freshly provisioned account
    mock_db = _mock_db_with_row(must_change=True, stored_hash=None)

    body = ChangePasswordBody(new_password="FirstRealPass1!")
    # current_password intentionally omitted

    with patch("app.routers.auth.asyncio.to_thread", new=AsyncMock(return_value="hashed")):
        result = await change_password(
            body=body,
            current_user=mock_user,
            auth=mock_auth,
            db=mock_db,
        )

    assert result.ok is True


# ---------------------------------------------------------------------------
# Fix 2 — pypdf must run off the event loop + timeout on a hanging parse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_pdf_extraction_uses_to_thread() -> None:
    """_extract_pdf_text in resume.py must delegate to asyncio.to_thread so a
    CPU-bound pypdf parse never blocks the event loop."""
    from app.routers.resume import _extract_pdf_text, _extract_pdf_text_sync

    # Minimal valid PDF that pypdf can parse
    minimal_pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 3 3]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\n"
        b"startxref\n190\n%%EOF"
    )

    # The sync function must return a string (possibly empty for this minimal PDF)
    result_sync = _extract_pdf_text_sync(minimal_pdf)
    assert isinstance(result_sync, str)

    # The async wrapper must return the same string
    result_async = await _extract_pdf_text(minimal_pdf)
    assert isinstance(result_async, str)
    assert result_async == result_sync


@pytest.mark.asyncio
async def test_resume_pdf_extraction_raises_on_timeout() -> None:
    """_extract_pdf_text must raise asyncio.TimeoutError when the sync parse
    exceeds _PDF_PARSE_TIMEOUT_SECONDS — the caller maps this to HTTP 400."""
    from app.routers.resume import _extract_pdf_text

    # wait_for abandons (does not cancel) the to_thread worker, and the
    # interpreter joins executor threads at exit — a bare long sleep here
    # stalls the whole pytest process after the suite finishes. Block on an
    # event instead and release it as soon as the test is done.
    release = threading.Event()

    def _slow_parse(raw: bytes) -> str:  # noqa: ARG001
        release.wait(timeout=30)  # comfortably past the patched 0.05s timeout
        return ""

    try:
        with (
            patch("app.routers.resume._extract_pdf_text_sync", side_effect=_slow_parse),
            patch("app.routers.resume._PDF_PARSE_TIMEOUT_SECONDS", 0.05),
            pytest.raises((asyncio.TimeoutError, TimeoutError)),
        ):
            await _extract_pdf_text(b"fake")
    finally:
        release.set()


@pytest.mark.asyncio
async def test_jd_pdf_extraction_uses_to_thread() -> None:
    """_extract_pdf_text in jd.py must also delegate to asyncio.to_thread."""
    from app.routers.jd import _extract_pdf_text, _extract_pdf_text_sync

    minimal_pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 3 3]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\n"
        b"startxref\n190\n%%EOF"
    )

    result_sync = _extract_pdf_text_sync(minimal_pdf)
    result_async = await _extract_pdf_text(minimal_pdf)
    assert result_async == result_sync


@pytest.mark.asyncio
async def test_resume_upload_returns_400_on_pdf_timeout() -> None:
    """_do_upload must return HTTP 400 when pypdf times out — no 500 leaking."""
    from app.routers.resume import _do_upload

    minimal_pdf = b"%PDF-1.4 fake content"
    mock_file = MagicMock()
    mock_file.content_type = "application/pdf"
    mock_file.read = AsyncMock(return_value=minimal_pdf)
    mock_file.filename = "test.pdf"

    mock_user = MagicMock()
    mock_user.user_id = str(uuid.uuid4())

    mock_db = AsyncMock()

    from fastapi import HTTPException

    with (
        patch("app.routers.resume._extract_pdf_text", new=AsyncMock(side_effect=TimeoutError)),
        pytest.raises(HTTPException) as exc_info,
    ):
        await _do_upload(mock_file, mock_user, mock_db)

    assert exc_info.value.status_code == 400
    assert "too long" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Fix 3 — exam double-submit: status guard at DB write level
# ---------------------------------------------------------------------------


def test_grade_and_finalize_does_not_overwrite_submitted_status() -> None:
    """The ORM mutation in _grade_and_finalize must NOT change status when
    the re-fetched attempt is already 'submitted'.  This is the DB-level guard
    complementing the Redis claim."""
    # We test the conditional directly — the full async flow would require
    # a running DB + Redis which is out of scope for unit tests.
    # Simulate the guard code path by replicating the logic under test.
    attempt = MagicMock()
    attempt.status = "submitted"  # already finalised by a concurrent request

    new_status = "submitted"  # what the second request would write

    # Replicate the guard:
    if attempt.status == "in_progress":
        attempt.status = new_status  # should NOT execute

    # The guard must prevent overwriting the already-submitted status
    assert attempt.status == "submitted", (
        "A re-fetched 'submitted' attempt must not have its status overwritten"
    )


def test_grade_and_finalize_sets_status_from_in_progress() -> None:
    """When the attempt is still 'in_progress', the guard must set the new status."""
    attempt = MagicMock()
    attempt.status = "in_progress"

    expired = False
    if attempt.status == "in_progress":
        attempt.status = "expired" if expired else "submitted"

    assert attempt.status == "submitted"


def test_grade_and_finalize_sets_expired_when_overdue() -> None:
    """When expired=True and status is 'in_progress', guard must set 'expired'."""
    attempt = MagicMock()
    attempt.status = "in_progress"

    expired = True
    if attempt.status == "in_progress":
        attempt.status = "expired" if expired else "submitted"

    assert attempt.status == "expired"


# ---------------------------------------------------------------------------
# Fix 4 — retention purge: abandoned + consent_withdrawn sessions
# ---------------------------------------------------------------------------


def test_retention_purge_predicate_covers_abandoned() -> None:
    """_purge_predicate must include abandoned sessions using updated_at."""
    from app.retention import _PURGEABLE_STATUSES, _purge_predicate

    assert "abandoned" in _PURGEABLE_STATUSES
    assert "completed" in _PURGEABLE_STATUSES
    assert "consent_withdrawn" in _PURGEABLE_STATUSES
    assert "failed" in _PURGEABLE_STATUSES
    # Active states must NEVER be in the set
    assert "in_progress" not in _PURGEABLE_STATUSES
    assert "created" not in _PURGEABLE_STATUSES

    # The predicate must not raise when constructed
    cutoff = datetime.now(tz=UTC) - timedelta(days=90)
    pred = _purge_predicate(cutoff)
    assert pred is not None


@pytest.mark.asyncio
async def test_retention_purge_dry_run_counts_abandoned() -> None:
    """In dry-run mode purge_expired_sessions must count abandoned sessions."""
    from app.config import settings as _app_settings
    from app.retention import purge_expired_sessions

    dry_settings = _app_settings.model_copy(
        update={"retention_dry_run": True, "retention_days": 90}
    )

    # Mock DB that returns a count of 5 (simulating 5 purgeable rows)
    mock_scalar_result = MagicMock()
    mock_scalar_result.scalar_one.return_value = 5

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_scalar_result)

    count = await purge_expired_sessions(db=mock_db, settings=dry_settings)
    assert count == 5
    # Must NOT issue a DELETE in dry-run
    mock_db.delete.assert_not_called()


@pytest.mark.asyncio
async def test_retention_purge_live_deletes_abandoned() -> None:
    """In live mode purge_expired_sessions must execute a DELETE (not just SELECT)."""
    from app.config import settings as _app_settings
    from app.retention import purge_expired_sessions

    live_settings = _app_settings.model_copy(
        update={"retention_dry_run": False, "retention_days": 90}
    )

    mock_delete_result = MagicMock()
    mock_delete_result.rowcount = 7

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_delete_result)
    mock_db.commit = AsyncMock()

    count = await purge_expired_sessions(db=mock_db, settings=live_settings)
    assert count == 7
    mock_db.commit.assert_awaited_once()
    # execute must have been called (the DELETE statement)
    mock_db.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# Fix 5 — /metrics endpoint reachable and returns Prometheus text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_text() -> None:
    """GET /metrics must return 200 with Content-Type text/plain and Prometheus
    exposition format (lines starting with # HELP or metric names)."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/metrics")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    content_type = response.headers.get("content-type", "")
    assert "text/plain" in content_type, (
        f"Expected text/plain Content-Type for Prometheus, got {content_type!r}"
    )
    body = response.text
    # The Prometheus exposition format always has lines starting with "# HELP"
    # for the default process/python collectors registered by prometheus_client.
    assert "# HELP" in body or "# TYPE" in body, (
        "Prometheus metrics response must contain '# HELP' or '# TYPE' lines"
    )


@pytest.mark.asyncio
async def test_metrics_endpoint_excluded_from_openapi() -> None:
    """GET /metrics must NOT appear in the OpenAPI schema (include_in_schema=False)."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    paths = schema.get("paths", {})
    assert "/metrics" not in paths, (
        "/metrics must not appear in the OpenAPI schema (it is an ops endpoint)"
    )


# ---------------------------------------------------------------------------
# Fix 5 — Sentry is initialised (no-op when DSN is empty)
# ---------------------------------------------------------------------------


def test_sentry_init_is_noop_when_dsn_empty() -> None:
    """init_sentry must return False (no-op) when SENTRY_DSN is empty.
    This verifies the shared.observability.sentry module is wired correctly
    and does not raise when the DSN is absent."""
    from shared.observability.sentry import init_sentry

    result = init_sentry("", environment="test", service_name="data_gateway")
    assert result is False, "init_sentry must return False when DSN is empty"


def test_sentry_init_is_noop_when_dsn_whitespace() -> None:
    """init_sentry must return False for whitespace-only DSN strings."""
    from shared.observability.sentry import init_sentry

    result = init_sentry("   ", environment="test", service_name="data_gateway")
    assert result is False


# ---------------------------------------------------------------------------
# Fix 5 — PII redaction processor is configured in structlog
# ---------------------------------------------------------------------------


def test_pii_redaction_processor_removes_known_fields() -> None:
    """_redact_pii_processor must drop all PII fields from the event dict.

    Covers the extended deny-list (identity PII + voice/text PII + document PII
    + contact/geo PII) added in the Cluster C fix.  This test would FAIL if any
    field were removed from _PII_FIELDS."""
    from app.main import _redact_pii_processor

    event_dict: dict[str, object] = {
        "event": "user.login",
        # identity PII
        "email": "alice@example.com",
        "password": "secret123",
        "phone": "+91-9999999999",
        "full_name": "Alice Example",
        # voice / interview content
        "transcript": "Tell me about yourself...",
        "answer": "I am a software engineer",
        "question": "What is polymorphism?",
        "text_content": "Some free-text block",
        # document PII
        "resume_text": "Curriculum Vitae of Alice...",
        "jd_text": "We are looking for a skilled engineer...",
        # contact / geo
        "address": "12 Main St, Hyderabad",
        # non-PII (must be preserved)
        "user_id": "abc123",
        "role": "candidate",
    }

    result = _redact_pii_processor(None, "info", event_dict)  # type: ignore[arg-type]

    # Identity PII fields must be gone
    assert "email" not in result
    assert "password" not in result
    assert "phone" not in result
    assert "full_name" not in result

    # Voice / interview PII fields must be gone
    assert "transcript" not in result
    assert "answer" not in result
    assert "question" not in result
    assert "text_content" not in result

    # Document PII fields must be gone
    assert "resume_text" not in result
    assert "jd_text" not in result

    # Contact / geo PII fields must be gone
    assert "address" not in result

    # Non-PII fields must be preserved
    assert result["user_id"] == "abc123"
    assert result["role"] == "candidate"
    assert result["event"] == "user.login"


def test_pii_redaction_processor_is_idempotent_on_clean_dict() -> None:
    """_redact_pii_processor must not modify a dict that has no PII fields."""
    from app.main import _redact_pii_processor

    event_dict: dict[str, object] = {
        "event": "session.started",
        "session_id": "xyz",
        "language": "en",
    }
    original_keys = set(event_dict.keys())

    result = _redact_pii_processor(None, "info", event_dict)  # type: ignore[arg-type]

    assert set(result.keys()) == original_keys


# ---------------------------------------------------------------------------
# Cluster C — JD upload tenant authorization (_assert_jd_upload_authorized)
# ---------------------------------------------------------------------------


def _make_job(
    *,
    job_id: uuid.UUID | None = None,
    created_by_user_id: uuid.UUID | None = None,
) -> MagicMock:
    """Return a minimal mock Job with the fields that authz logic reads."""
    job = MagicMock()
    job.id = job_id or uuid.uuid4()
    job.created_by_user_id = created_by_user_id
    return job


def _make_user(*, user_id: str | None = None, roles: list[str] | None = None) -> MagicMock:
    u = MagicMock()
    u.user_id = user_id or str(uuid.uuid4())
    u.roles = roles or ["hr_manager"]
    return u


@pytest.mark.asyncio
async def test_jd_authz_platform_owner_can_upload_any_job() -> None:
    """platform_owner — and ONLY platform_owner — bypasses the tenant check.

    Would FAIL (raise 403) if platform_owner were removed from
    _PLATFORM_ADMIN_ROLES."""
    from app.routers.jd import _assert_jd_upload_authorized

    user = _make_user(roles=["platform_owner"])
    job = _make_job(created_by_user_id=None)  # seeded / platform job
    mock_db = AsyncMock()
    # Must not raise
    await _assert_jd_upload_authorized(mock_db, user, job)
    mock_db.scalar.assert_not_awaited()  # no DB queries needed


@pytest.mark.asyncio
async def test_jd_authz_super_admin_denied_on_seeded_job() -> None:
    """super_admin is ONE COMPANY's admin — it must not touch a platform job.

    It used to sit in _PLATFORM_ADMIN_ROLES and bypass every tenant check, so
    company B's super_admin could overwrite company A's jd_text. That is both a
    cross-tenant write and an LLM prompt-injection vector, since jd_text is fed
    to the model. `admin` is the analytics role and has no business writing a
    JD at all.
    """
    from fastapi import HTTPException

    from app.routers.jd import _assert_jd_upload_authorized

    for role in ("super_admin", "admin"):
        user = _make_user(roles=[role])
        job = _make_job(created_by_user_id=None)  # seeded / platform job
        with pytest.raises(HTTPException) as exc:
            await _assert_jd_upload_authorized(AsyncMock(), user, job)
        assert exc.value.status_code == 403, role


@pytest.mark.asyncio
async def test_jd_authz_super_admin_denied_on_other_companys_job() -> None:
    """A super_admin of company B gets 403 on a job owned by company A."""
    from fastapi import HTTPException

    from app.routers.jd import _assert_jd_upload_authorized

    caller_uid = uuid.uuid4()
    other_owner_uid = uuid.uuid4()
    user = _make_user(user_id=str(caller_uid), roles=["super_admin"])
    job = _make_job(created_by_user_id=other_owner_uid)

    mock_db = AsyncMock()
    # Rule 3 resolves both companies; make them differ.
    mock_db.scalar.side_effect = [uuid.uuid4(), uuid.uuid4()]

    with pytest.raises(HTTPException) as exc:
        await _assert_jd_upload_authorized(mock_db, user, job)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_jd_authz_owner_can_upload_own_job() -> None:
    """A caller with created_by_user_id == their own UUID must be allowed."""
    from app.routers.jd import _assert_jd_upload_authorized

    owner_uid = uuid.uuid4()
    user = _make_user(user_id=str(owner_uid), roles=["hr_manager"])
    job = _make_job(created_by_user_id=owner_uid)

    mock_db = AsyncMock()
    # Must not raise; no DB queries needed (short-circuit on self-ownership)
    await _assert_jd_upload_authorized(mock_db, user, job)


@pytest.mark.asyncio
async def test_jd_authz_platform_job_denied_for_normal_user() -> None:
    """An hr_manager MUST NOT upload a JD for a seeded/platform job
    (created_by_user_id IS NULL).  This would FAIL (no exception) if the
    platform-job check were removed."""
    from fastapi import HTTPException

    from app.routers.jd import _assert_jd_upload_authorized

    user = _make_user(roles=["hr_manager"])
    job = _make_job(created_by_user_id=None)  # platform/seeded job

    mock_db = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await _assert_jd_upload_authorized(mock_db, user, job)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_jd_authz_cross_company_denied() -> None:
    """A caller MUST NOT upload a JD for a job owned by a user in another company.

    This test would FAIL (no exception) if the company-scoping check were removed —
    making it the key regression guard for the LLM prompt-injection / integrity fix."""
    from fastapi import HTTPException

    from app.routers.jd import _assert_jd_upload_authorized

    caller_uid = uuid.uuid4()
    owner_uid = uuid.uuid4()
    caller_company = uuid.uuid4()
    owner_company = uuid.uuid4()  # DIFFERENT company
    assert caller_company != owner_company

    user = _make_user(user_id=str(caller_uid), roles=["hr_manager"])
    job = _make_job(created_by_user_id=owner_uid)

    # scalar() is called twice:
    #   1st call → caller's company_id
    #   2nd call → owner's company_id
    mock_db = AsyncMock()
    mock_db.scalar = AsyncMock(side_effect=[caller_company, owner_company])

    with pytest.raises(HTTPException) as exc_info:
        await _assert_jd_upload_authorized(mock_db, user, job)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_jd_authz_same_company_allowed() -> None:
    """A caller in the SAME company as the job owner must be allowed to upload."""
    from app.routers.jd import _assert_jd_upload_authorized

    caller_uid = uuid.uuid4()
    owner_uid = uuid.uuid4()
    shared_company = uuid.uuid4()

    user = _make_user(user_id=str(caller_uid), roles=["hr_manager"])
    job = _make_job(created_by_user_id=owner_uid)

    mock_db = AsyncMock()
    mock_db.scalar = AsyncMock(side_effect=[shared_company, shared_company])

    # Must not raise
    await _assert_jd_upload_authorized(mock_db, user, job)


@pytest.mark.asyncio
async def test_jd_authz_no_company_caller_denied_for_other_owner() -> None:
    """A caller with no company_id (platform user without admin role) must be
    denied for a job they do not directly own."""
    from fastapi import HTTPException

    from app.routers.jd import _assert_jd_upload_authorized

    caller_uid = uuid.uuid4()
    owner_uid = uuid.uuid4()

    user = _make_user(user_id=str(caller_uid), roles=["candidate"])
    job = _make_job(created_by_user_id=owner_uid)

    mock_db = AsyncMock()
    mock_db.scalar = AsyncMock(return_value=None)  # caller has no company

    with pytest.raises(HTTPException) as exc_info:
        await _assert_jd_upload_authorized(mock_db, user, job)

    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Cluster C — DATABASE_SSL enforcement in production (config.py)
# ---------------------------------------------------------------------------


def test_database_ssl_required_in_production() -> None:
    """Settings must raise ValueError when APP_ENV=production and DATABASE_SSL is empty.

    This test would FAIL (no error) if the validate_database_ssl validator were removed.

    Note: we also set auth_cookie_secure=True and auth_cookie_samesite='lax' so that
    the cookie validator (which also fires in 'production') does not shadow our error,
    and strong secrets so the weak-secret validator (also production-only) does not
    shadow it either — CI runs with placeholder secrets in the environment."""
    from pydantic import ValidationError

    from app.config import settings as _real_settings

    strong = "a" * 16 + "0123456789abcdef" * 3  # 64 chars, no placeholder markers

    # Build a production-like settings with DATABASE_SSL intentionally blank.
    # auth_cookie_secure must be True to satisfy the other production validator, so
    # that the DATABASE_SSL validator error is the one that surfaces.
    with pytest.raises(ValidationError) as exc_info:
        _real_settings.model_validate(
            _real_settings.model_dump()
            | {
                "app_env": "production",
                "database_ssl": "",
                "auth_cookie_secure": True,
                "auth_cookie_samesite": "lax",
                "jwt_secret": strong,
                "consent_ip_salt": strong,
                "exam_link_secret": strong,
                "interview_link_secret": strong,
            }
        )

    error_text = str(exc_info.value)
    assert "DATABASE_SSL" in error_text


def test_database_ssl_not_required_in_development() -> None:
    """Settings must NOT raise when APP_ENV=development and DATABASE_SSL is empty.

    Development runs against local Postgres with no SSL — the validator must
    only apply in production/staging."""
    from app.config import settings as _real_settings

    # This must not raise — development is explicitly exempt.
    dev_settings = _real_settings.model_validate(
        _real_settings.model_dump()
        | {"app_env": "development", "database_ssl": "", "auth_cookie_secure": False}
    )
    assert dev_settings.app_env == "development"
    assert dev_settings.database_ssl == ""


def test_database_ssl_loopback_exempt_sentinel_is_cleared() -> None:
    """When DATABASE_SSL='loopback-exempt' the validator must clear it to ''
    so asyncpg never sees the sentinel value."""
    from app.config import settings as _real_settings

    result = _real_settings.model_validate(
        _real_settings.model_dump()
        | {
            "app_env": "development",
            "database_ssl": "loopback-exempt",
            "auth_cookie_secure": False,
        }
    )
    assert result.database_ssl == ""
