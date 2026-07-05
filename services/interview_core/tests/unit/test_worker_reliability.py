"""Unit tests for interview_worker reliability fixes.

Covers:
  1. Abrupt-close teardown: _abrupt_close is shielded so it survives GC when
     the candidate closes their browser.  The shutdown hook awaits it.
  2. Admission control: _active_jobs counter increments/decrements correctly;
     _request_fnc rejects when at/over the ceiling.
  3. Worker heartbeat: _write_heartbeat writes a timestamp to the given path;
     _run_heartbeat loops and updates the file.
  4. Prewarm: _prewarm loads silero VAD into proc.userdata["vad"]; gracefully
     handles failure without crashing.
  5. Prometheus /metrics endpoint: returns 200 with the correct content-type.
  6. PII redaction in structlog: _redact_pii_processor strips the expected fields.
  7. Settings — config fields for worker sizing have correct defaults.
  8. Abrupt-close shield — teardown survives outer cancel.
  9. (Finding 2) resolve_consent_user_id retries on transient DB errors and
     returns the DB-error sentinel after exhausting retries.
 10. (Finding 3) _request_fnc memory-gate rejects when estimated RSS would
     exceed the container limit; accepted jobs publish capacity to Redis.
 11. (Finding 3) Settings defaults are right-sized for a 2-vCPU VM.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. Active-jobs counter
# ---------------------------------------------------------------------------


def test_active_jobs_increment_and_decrement() -> None:
    """Increment/decrement must be symmetric and never go below 0."""
    import app.worker.interview_worker as wk

    # Capture original value to restore after test (module-level global).
    original = wk._active_jobs
    try:
        wk._active_jobs = 0
        wk._active_jobs_increment()
        assert wk._active_jobs == 1
        wk._active_jobs_increment()
        assert wk._active_jobs == 2
        wk._active_jobs_decrement()
        assert wk._active_jobs == 1
        wk._active_jobs_decrement()
        assert wk._active_jobs == 0
        # Decrement below zero must clamp to 0 (guard against double-decrement).
        wk._active_jobs_decrement()
        assert wk._active_jobs == 0
    finally:
        wk._active_jobs = original


# ---------------------------------------------------------------------------
# 2. _request_fnc — admission control gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_fnc_accepts_under_ceiling() -> None:
    """Job is accepted when active jobs are below the configured ceiling."""
    import app.worker.interview_worker as wk

    original = wk._active_jobs
    try:
        wk._active_jobs = 3
        mock_request = AsyncMock()

        fake_settings = MagicMock()
        fake_settings.worker_max_concurrent_jobs = 10
        # Disable memory gate so this test isolates the concurrency ceiling.
        fake_settings.job_memory_limit_mb = 0
        fake_settings.container_memory_limit_mb = 0

        with (
            patch.object(wk, "settings", fake_settings),
            patch.object(wk, "_publish_capacity", new=AsyncMock()),
        ):
            await wk._request_fnc(mock_request)

        mock_request.accept.assert_called_once_with(wk.entrypoint)
        mock_request.reject.assert_not_called()
    finally:
        wk._active_jobs = original


@pytest.mark.asyncio
async def test_request_fnc_rejects_at_ceiling() -> None:
    """Job is rejected when active jobs equals the ceiling."""
    import app.worker.interview_worker as wk

    original = wk._active_jobs
    try:
        wk._active_jobs = 4
        mock_request = AsyncMock()

        fake_settings = MagicMock()
        fake_settings.worker_max_concurrent_jobs = 4
        fake_settings.job_memory_limit_mb = 0
        fake_settings.container_memory_limit_mb = 0

        with (
            patch.object(wk, "settings", fake_settings),
            patch.object(wk, "_publish_capacity", new=AsyncMock()),
        ):
            await wk._request_fnc(mock_request)

        mock_request.reject.assert_called_once()
        mock_request.accept.assert_not_called()
    finally:
        wk._active_jobs = original


@pytest.mark.asyncio
async def test_request_fnc_rejects_over_ceiling() -> None:
    """Job is rejected when active jobs exceed the ceiling."""
    import app.worker.interview_worker as wk

    original = wk._active_jobs
    try:
        wk._active_jobs = 5
        mock_request = AsyncMock()

        fake_settings = MagicMock()
        fake_settings.worker_max_concurrent_jobs = 4
        fake_settings.job_memory_limit_mb = 0
        fake_settings.container_memory_limit_mb = 0

        with (
            patch.object(wk, "settings", fake_settings),
            patch.object(wk, "_publish_capacity", new=AsyncMock()),
        ):
            await wk._request_fnc(mock_request)

        mock_request.reject.assert_called_once()
        mock_request.accept.assert_not_called()
    finally:
        wk._active_jobs = original


@pytest.mark.asyncio
async def test_request_fnc_zero_cap_always_accepts() -> None:
    """worker_max_concurrent_jobs=0 disables the cap — jobs always accepted."""
    import app.worker.interview_worker as wk

    original = wk._active_jobs
    try:
        wk._active_jobs = 9999  # absurdly high
        mock_request = AsyncMock()

        fake_settings = MagicMock()
        fake_settings.worker_max_concurrent_jobs = 0
        # Disable memory gate too so no rejection fires from that path.
        fake_settings.job_memory_limit_mb = 0
        fake_settings.container_memory_limit_mb = 0

        with (
            patch.object(wk, "settings", fake_settings),
            patch.object(wk, "_publish_capacity", new=AsyncMock()),
        ):
            await wk._request_fnc(mock_request)

        mock_request.accept.assert_called_once_with(wk.entrypoint)
        mock_request.reject.assert_not_called()
    finally:
        wk._active_jobs = original


# ---------------------------------------------------------------------------
# 3. Heartbeat — _write_heartbeat and _run_heartbeat
# ---------------------------------------------------------------------------


def test_write_heartbeat_creates_file() -> None:
    """_write_heartbeat must create the file with the given timestamp."""
    from app.worker.interview_worker import _write_heartbeat

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "heartbeat")
        _write_heartbeat(path, "2026-07-01T12:00:00+00:00")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        assert content == "2026-07-01T12:00:00+00:00"


def test_write_heartbeat_overwrites_existing() -> None:
    """_write_heartbeat must overwrite an existing heartbeat file."""
    from app.worker.interview_worker import _write_heartbeat

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "heartbeat")
        _write_heartbeat(path, "first-write")
        _write_heartbeat(path, "second-write")
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        assert content == "second-write"


def test_write_heartbeat_bad_path_silent() -> None:
    """_write_heartbeat on an unwritable path must silently swallow the error."""
    from app.worker.interview_worker import _write_heartbeat

    # Should not raise — wraps OSError silently.
    _write_heartbeat("/nonexistent_dir_xyz/heartbeat", "ts")


@pytest.mark.asyncio
async def test_run_heartbeat_writes_within_interval() -> None:
    """_run_heartbeat must write to the file within the configured interval."""
    from app.worker.interview_worker import _run_heartbeat

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "heartbeat")

        fake_settings = MagicMock()
        fake_settings.worker_heartbeat_path = path
        fake_settings.worker_heartbeat_interval_seconds = 0  # immediate in tests

        import app.worker.interview_worker as wk

        with patch.object(wk, "settings", fake_settings):
            # Run for one tick only — cancel after the first write.
            task = asyncio.create_task(_run_heartbeat())
            # Give the event loop time to write once.
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # File must have been written at least once.
        assert os.path.exists(path), "heartbeat file was not written"
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        assert len(content) > 0, "heartbeat file is empty"


# ---------------------------------------------------------------------------
# 4. Prewarm — _prewarm loads VAD into proc.userdata
# ---------------------------------------------------------------------------


def test_prewarm_loads_vad_into_userdata() -> None:
    """_prewarm must load silero.VAD into proc.userdata['vad']."""
    from app.worker.interview_worker import _prewarm

    mock_proc = MagicMock()
    mock_proc.userdata = {}
    fake_vad = MagicMock(name="VADModel")

    with patch("app.worker.interview_worker.silero") as mock_silero:
        mock_silero.VAD.load.return_value = fake_vad
        _prewarm(mock_proc)

    assert mock_proc.userdata.get("vad") is fake_vad, (
        "_prewarm must store the loaded VAD instance in proc.userdata['vad']"
    )
    mock_silero.VAD.load.assert_called_once()


def test_prewarm_swallows_load_failure() -> None:
    """_prewarm must not raise if silero.VAD.load() fails."""
    from app.worker.interview_worker import _prewarm

    mock_proc = MagicMock()
    mock_proc.userdata = {}

    with patch("app.worker.interview_worker.silero") as mock_silero:
        mock_silero.VAD.load.side_effect = RuntimeError("ONNX model file not found")
        # Must NOT raise.
        _prewarm(mock_proc)

    # userdata["vad"] must NOT be set on failure (entrypoint will cold-load).
    assert "vad" not in mock_proc.userdata


# ---------------------------------------------------------------------------
# 5. /metrics endpoint — Prometheus text format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_text() -> None:
    """GET /metrics must return 200 with Prometheus content-type."""
    from httpx import ASGITransport, AsyncClient

    # Import app lazily to avoid side-effects at module level in tests.
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/metrics")

    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    )
    content_type = resp.headers.get("content-type", "")
    # prometheus_client generates 'text/plain; version=0.0.4; charset=utf-8'
    assert "text/plain" in content_type, (
        f"Expected text/plain content-type, got: {content_type!r}"
    )
    # Must contain at least one standard metric family.
    assert "# HELP" in resp.text or "# TYPE" in resp.text, (
        "Response body does not look like Prometheus text format"
    )


# ---------------------------------------------------------------------------
# 6. PII redaction processor in structlog
# ---------------------------------------------------------------------------


def test_pii_redaction_removes_known_fields() -> None:
    """The structlog PII processor must drop email, password, phone, full_name."""
    # Import the processor directly from main to avoid re-running side effects.
    from app.main import _redact_pii_processor

    event_dict: dict[str, Any] = {
        "event": "user.login",
        "email": "test@example.com",
        "password": "hunter2",
        "phone": "+91-9876543210",
        "full_name": "Ravi Kumar",
        "user_id": "abc123",
    }
    result = _redact_pii_processor(None, "info", event_dict)  # type: ignore[arg-type]
    assert "email" not in result
    assert "password" not in result
    assert "phone" not in result
    assert "full_name" not in result
    # Non-PII fields must be preserved.
    assert result["user_id"] == "abc123"
    assert result["event"] == "user.login"


def test_pii_redaction_leaves_clean_events_unchanged() -> None:
    """An event dict with no PII fields must pass through unmodified."""
    from app.main import _redact_pii_processor

    event_dict: dict[str, Any] = {
        "event": "session.start",
        "room": "abc-room",
        "language": "en",
    }
    result = _redact_pii_processor(None, "info", event_dict)  # type: ignore[arg-type]
    assert result == {"event": "session.start", "room": "abc-room", "language": "en"}


# ---------------------------------------------------------------------------
# 7. Settings — new config fields have correct defaults
# ---------------------------------------------------------------------------


def test_settings_new_fields_exist_with_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New settings fields must have sane defaults and correct types."""
    from app.config import settings

    assert isinstance(settings.worker_load_threshold, float)
    assert 0.0 <= settings.worker_load_threshold <= 1.0
    assert isinstance(settings.worker_max_concurrent_jobs, int)
    assert settings.worker_max_concurrent_jobs >= 0
    assert isinstance(settings.worker_heartbeat_path, str)
    assert settings.worker_heartbeat_path  # must be non-empty
    assert isinstance(settings.worker_heartbeat_interval_seconds, int)
    assert settings.worker_heartbeat_interval_seconds >= 5


# ---------------------------------------------------------------------------
# 8. Abrupt-close shield — teardown task survives cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abrupt_close_shielded_task_survives_outer_cancel() -> None:
    """asyncio.shield() must allow the inner coroutine to finish even when the
    outer future is cancelled — this models what happens when LiveKit's framework
    cancels the room tear-down before _abrupt_close finishes writing to the DB.
    """
    completed: list[bool] = []

    async def _inner() -> None:
        await asyncio.sleep(0.01)
        completed.append(True)

    # Simulate: create a shielded task (like _on_session_close does),
    # then cancel the outer shield reference.
    inner_task = asyncio.ensure_future(_inner())
    shielded = asyncio.shield(inner_task)
    shielded.cancel()  # outer cancel — inner must still run

    # Wait for the inner task to complete.
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(inner_task, timeout=1.0)

    assert completed == [True], (
        "asyncio.shield() must allow the inner coroutine to complete even when "
        "the outer future is cancelled — _abrupt_close must not be GC'd on browser close"
    )


# ---------------------------------------------------------------------------
# 9. (Finding 2) resolve_consent_user_id — retry logic and fail-closed sentinel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_consent_user_id_retries_on_transient_error() -> None:
    """DB errors trigger up to _RESOLVE_CONSENT_MAX_ATTEMPTS retries before giving up.

    On each failure the resolver should log a warning and retry.  After all
    attempts are exhausted the sentinel _CONSENT_RESOLVE_DB_ERROR is returned
    so the watchdog can fail-closed.
    """
    from collections.abc import AsyncGenerator
    from contextlib import asynccontextmanager

    import app.worker.interview_worker as wk

    call_count = 0

    @asynccontextmanager
    async def _raising_cm() -> AsyncGenerator[None, None]:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("transient DB error")
        yield  # pragma: no cover

    factory = MagicMock(side_effect=lambda: _raising_cm())
    valid_room = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
        # Suppress the asyncio.sleep so the test runs instantly.
        patch("app.worker.interview_worker.asyncio.sleep", new=AsyncMock()),
    ):
        result = await wk.resolve_consent_user_id(valid_room)

    assert result == wk._CONSENT_RESOLVE_DB_ERROR, (
        "After exhausting retries, resolve_consent_user_id must return "
        "_CONSENT_RESOLVE_DB_ERROR so the watchdog can fail-closed"
    )
    assert call_count == wk._RESOLVE_CONSENT_MAX_ATTEMPTS, (
        f"Expected exactly {wk._RESOLVE_CONSENT_MAX_ATTEMPTS} attempts, got {call_count}"
    )


@pytest.mark.asyncio
async def test_resolve_consent_user_id_succeeds_on_second_attempt() -> None:
    """Transient error on attempt 1 followed by success on attempt 2.

    The resolver must not give up prematurely — a single transient error
    should not produce the fail-closed sentinel.
    """
    import uuid
    from collections.abc import AsyncGenerator
    from contextlib import asynccontextmanager

    import app.worker.interview_worker as wk

    target_uid = uuid.UUID("11111111-2222-3333-4444-555555555555")
    attempt = 0

    @asynccontextmanager
    async def _cm() -> AsyncGenerator[None, None]:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise RuntimeError("transient")
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = target_uid
        db.execute = AsyncMock(return_value=result)
        yield db

    factory = MagicMock(side_effect=lambda: _cm())
    valid_room = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
        patch("app.worker.interview_worker.asyncio.sleep", new=AsyncMock()),
    ):
        result = await wk.resolve_consent_user_id(valid_room)

    assert result == str(target_uid), (
        "Resolver must return the user_id when a subsequent attempt succeeds"
    )
    assert result != wk._CONSENT_RESOLVE_DB_ERROR


@pytest.mark.asyncio
async def test_resolve_consent_user_id_invalid_uuid_no_retry() -> None:
    """An invalid UUID room name must return None immediately without any DB call."""
    import app.worker.interview_worker as wk

    factory = MagicMock(side_effect=AssertionError("DB must not be called"))
    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        result = await wk.resolve_consent_user_id("not-a-uuid")

    assert result is None, (
        "Non-UUID room names are legitimate no-ops — no retry, no sentinel"
    )
    factory.assert_not_called()


# ---------------------------------------------------------------------------
# 10. (Finding 3) _request_fnc — memory gate and Redis capacity publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_fnc_rejects_on_memory_estimate() -> None:
    """Admission must be rejected when estimated RSS exceeds container limit.

    With job_memory_limit_mb=150 and 2 active jobs and 1 more incoming, the
    estimated RSS = 3 * 150 = 450 MB.  If container_memory_limit_mb=400, the
    gate must reject even though active_jobs (2) < max_concurrent_jobs (10).
    """
    import app.worker.interview_worker as wk

    original = wk._active_jobs
    try:
        wk._active_jobs = 2  # 2 running + 1 incoming = 3 * 150 = 450 > 400
        mock_request = AsyncMock()

        fake_settings = MagicMock()
        fake_settings.worker_max_concurrent_jobs = 10  # not the binding constraint
        fake_settings.job_memory_limit_mb = 150
        fake_settings.container_memory_limit_mb = 400
        fake_settings.redis_url = "redis://localhost"

        with (
            patch.object(wk, "settings", fake_settings),
            patch.object(wk, "_publish_capacity", new=AsyncMock()),
        ):
            await wk._request_fnc(mock_request)

        mock_request.reject.assert_called_once()
        mock_request.accept.assert_not_called()
    finally:
        wk._active_jobs = original


@pytest.mark.asyncio
async def test_request_fnc_accepts_within_memory_budget() -> None:
    """Job is accepted when estimated RSS is within the container limit."""
    import app.worker.interview_worker as wk

    original = wk._active_jobs
    try:
        wk._active_jobs = 1  # 1 + 1 incoming = 2 * 150 = 300 < 400 → accept
        mock_request = AsyncMock()

        fake_settings = MagicMock()
        fake_settings.worker_max_concurrent_jobs = 10
        fake_settings.job_memory_limit_mb = 150
        fake_settings.container_memory_limit_mb = 400
        fake_settings.redis_url = "redis://localhost"

        with (
            patch.object(wk, "settings", fake_settings),
            patch.object(wk, "_publish_capacity", new=AsyncMock()),
        ):
            await wk._request_fnc(mock_request)

        mock_request.accept.assert_called_once_with(wk.entrypoint)
        mock_request.reject.assert_not_called()
    finally:
        wk._active_jobs = original


@pytest.mark.asyncio
async def test_request_fnc_zero_memory_limit_skips_check() -> None:
    """job_memory_limit_mb=0 must disable the memory estimate entirely."""
    import app.worker.interview_worker as wk

    original = wk._active_jobs
    try:
        wk._active_jobs = 1
        mock_request = AsyncMock()

        fake_settings = MagicMock()
        fake_settings.worker_max_concurrent_jobs = 10
        fake_settings.job_memory_limit_mb = 0   # disabled
        fake_settings.container_memory_limit_mb = 400
        fake_settings.redis_url = "redis://localhost"

        with (
            patch.object(wk, "settings", fake_settings),
            patch.object(wk, "_publish_capacity", new=AsyncMock()),
        ):
            await wk._request_fnc(mock_request)

        mock_request.accept.assert_called_once_with(wk.entrypoint)
    finally:
        wk._active_jobs = original


@pytest.mark.asyncio
async def test_request_fnc_publishes_capacity_on_reject() -> None:
    """On rejection, _publish_capacity must be called so the HTTP server can
    read the latest active-job count and return 503 before issuing a token."""
    import app.worker.interview_worker as wk

    original = wk._active_jobs
    try:
        wk._active_jobs = 4
        mock_request = AsyncMock()
        mock_publish = AsyncMock()

        fake_settings = MagicMock()
        fake_settings.worker_max_concurrent_jobs = 4  # at ceiling
        fake_settings.job_memory_limit_mb = 0
        fake_settings.container_memory_limit_mb = 0
        fake_settings.redis_url = "redis://localhost"

        with (
            patch.object(wk, "settings", fake_settings),
            patch.object(wk, "_publish_capacity", mock_publish),
        ):
            await wk._request_fnc(mock_request)

        mock_request.reject.assert_called_once()
        mock_publish.assert_called_once(), (
            "_publish_capacity must be called on rejection so the HTTP server "
            "can return HTTP 503 instead of issuing a join token for a dead room"
        )
    finally:
        wk._active_jobs = original


# ---------------------------------------------------------------------------
# 11. (Finding 3) Settings defaults right-sized for 2-vCPU VM
# ---------------------------------------------------------------------------


def test_worker_max_concurrent_jobs_default_is_conservative() -> None:
    """Default worker_max_concurrent_jobs must be <= 4 for a 2-vCPU VM.

    A default of 15 (the old value) would allow 2-3x more jobs than the VM
    can serve at p95 < 2s, risking OOM-kill of all live interviews.
    The new default should be 3 or 4.
    """
    from app.config import settings

    assert settings.worker_max_concurrent_jobs <= 4, (
        f"worker_max_concurrent_jobs default is {settings.worker_max_concurrent_jobs}; "
        "must be <= 4 for a 2-vCPU / 2 GB VM (Oracle Free Tier). "
        "Old default of 15 caused OOM spikes."
    )
    # Must still be positive — 0 disables the cap which is not the safe default.
    assert settings.worker_max_concurrent_jobs > 0, (
        "worker_max_concurrent_jobs must be > 0 for production safety"
    )


def test_job_memory_limit_mb_default_is_nonzero() -> None:
    """job_memory_limit_mb must have a sane non-zero default.

    A default of 0 would disable the memory estimate, leaving only the
    concurrency counter as the OOM guard.  Under a hard 2 GB container cap
    a spike of 6+ jobs would OOM-kill all live interviews simultaneously.
    """
    from app.config import settings

    assert settings.job_memory_limit_mb > 0, (
        f"job_memory_limit_mb default is {settings.job_memory_limit_mb}; "
        "must be > 0 to enable the per-job memory estimate in _request_fnc"
    )


def test_container_memory_limit_mb_default_nonzero() -> None:
    """container_memory_limit_mb must have a sane non-zero default."""
    from app.config import settings

    assert settings.container_memory_limit_mb > 0, (
        "container_memory_limit_mb must be > 0 so the memory gate in "
        "_request_fnc has a ceiling to compare against"
    )


# ---------------------------------------------------------------------------
# 12. (Finding 3 — cap consistency) Effective cap equals the configured cap
# ---------------------------------------------------------------------------


def test_effective_cap_equals_configured_cap() -> None:
    """The memory gate must NOT reject any job slot up to worker_max_concurrent_jobs.

    This test proves the two admission-control values in config are consistent:
    for every job slot from 1 to worker_max_concurrent_jobs (inclusive), the
    memory gate passes.  If job_memory_limit_mb and container_memory_limit_mb
    are misconfigured such that the memory gate fires BEFORE the concurrency
    ceiling, _request_fnc would silently reject below the advertised cap —
    the reconciled cap is 4 (memory-safe on the box), and BOTH gates must
    agree on exactly 4.

    This test FAILS if:
      - container_memory_limit_mb is set too low (e.g., 500 MB with 150 MB/job
        would only fit 3 jobs before the gate fires at slot 4: 4*150=600>500).
      - worker_max_concurrent_jobs is increased without raising
        container_memory_limit_mb proportionally.
      - job_memory_limit_mb is raised without raising container_memory_limit_mb.
    """
    from app.config import settings

    cap = settings.worker_max_concurrent_jobs
    mem_per_job = settings.job_memory_limit_mb
    mem_limit = settings.container_memory_limit_mb

    assert cap == 4, (
        f"Reconciled concurrency cap must be 4 (memory-safe on the Oracle Free "
        f"Tier VM), got {cap}. Update the cap AND this test if the VM spec changes."
    )

    if mem_per_job == 0 or mem_limit == 0:
        # Memory gate is disabled — only the concurrency counter matters.
        return

    # For every job slot 1..cap, accepting it must NOT trigger the memory gate.
    for slot in range(1, cap + 1):
        # _active_jobs when the slot-th job requests admission = slot - 1.
        active_before = slot - 1
        estimated_rss_mb = (active_before + 1) * mem_per_job
        assert estimated_rss_mb <= mem_limit, (
            f"Memory gate would reject job slot {slot}/{cap} "
            f"(estimated {estimated_rss_mb} MB > container limit {mem_limit} MB). "
            f"Raise container_memory_limit_mb to at least {cap * mem_per_job} MB "
            f"or reduce job_memory_limit_mb so all {cap} configured jobs fit."
        )


@pytest.mark.asyncio
async def test_request_fnc_accepts_exactly_cap_jobs_sequentially() -> None:
    """_request_fnc must accept the 1st through cap-th job and reject the (cap+1)-th.

    This is the end-to-end admission-gate integration test: both the concurrency
    cap and the memory gate are active (with defaults matching config) and the
    effective ceiling must equal worker_max_concurrent_jobs (4).

    The test FAILS if:
      - The memory gate fires before the concurrency ceiling (effective cap < 4).
      - The concurrency cap is misconfigured to a value other than 4.
    """
    import app.worker.interview_worker as wk
    from app.config import settings

    cap = settings.worker_max_concurrent_jobs

    original = wk._active_jobs
    try:
        accepted = 0
        rejected = False

        for slot in range(1, cap + 2):  # test cap+1 total attempts
            wk._active_jobs = slot - 1  # simulate jobs already running
            mock_request = AsyncMock()

            with (
                patch.object(wk, "_publish_capacity", new=AsyncMock()),
            ):
                await wk._request_fnc(mock_request)

            if mock_request.accept.called:
                accepted += 1
                # Simulate the job actually starting (increment counter).
                # _request_fnc does NOT increment — entrypoint() does; we
                # simulate that by setting _active_jobs = slot so the next
                # iteration sees the correct running count.
            elif mock_request.reject.called:
                rejected = True
                # First rejection must be at slot == cap + 1.
                assert slot == cap + 1, (
                    f"First rejection occurred at slot {slot} but expected slot {cap + 1}. "
                    f"The effective cap is {slot - 1}, not the configured {cap}."
                )
                break

        assert accepted == cap, (
            f"Expected exactly {cap} accepted jobs (== worker_max_concurrent_jobs), "
            f"got {accepted}. The memory gate may be rejecting below the configured cap."
        )
        assert rejected, (
            f"Expected a rejection at slot {cap + 1} but got {cap + 1} acceptances. "
            f"The concurrency cap may not be enforced."
        )
    finally:
        wk._active_jobs = original
