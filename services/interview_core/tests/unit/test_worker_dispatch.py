"""Worker side of the stranded-candidate fixes (2026-10 load test).

* admission honours the Redis slot ledger and never rejects terminally;
* load is jobs/cap, not container CPU;
* warm-process count defaults to the job cap, bounded;
* the worker registers under the explicit agent name.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.worker.admission as adm
import app.worker.interview_worker as wk
from app.worker_capacity import SlotResult


def _settings(**overrides: Any) -> MagicMock:
    s = MagicMock()
    s.worker_max_concurrent_jobs = 4
    s.job_memory_limit_mb = 0
    s.container_memory_limit_mb = 0
    s.slot_reservation_seconds = 180
    s.slot_lease_seconds = 1800
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _request(room: str = "room-1") -> AsyncMock:
    req = AsyncMock()
    req.room = SimpleNamespace(name=room)
    return req


# ------------------------------------------------------------------ admission


@pytest.mark.asyncio
async def test_a_room_whose_slot_was_reserved_at_token_time_is_accepted() -> None:
    req = _request()
    with (
        patch.object(wk, "settings", _settings()),
        patch.object(adm, "reserve_slot", AsyncMock(return_value=(SlotResult.HELD, 4))),
        patch.object(wk, "_get_checkpoint_redis", MagicMock()),
    ):
        await wk._request_fnc(req)
    req.accept.assert_called_once_with()
    req.reject.assert_not_called()


@pytest.mark.asyncio
async def test_a_full_ledger_rejects_non_terminally_and_does_not_release_anything() -> None:
    req = _request()
    release = AsyncMock()
    with (
        patch.object(wk, "settings", _settings()),
        patch.object(adm, "reserve_slot", AsyncMock(return_value=(SlotResult.FULL, 4))),
        patch.object(adm, "release_slot", release),
        patch.object(wk, "_get_checkpoint_redis", MagicMock()),
    ):
        await wk._request_fnc(req)
    req.reject.assert_called_once_with(terminate=False)
    req.accept.assert_not_called()
    release.assert_not_called()  # we hold nothing for this room


@pytest.mark.asyncio
async def test_a_redis_outage_accepts_rather_than_stranding_the_candidate() -> None:
    req = _request()
    with (
        patch.object(wk, "settings", _settings()),
        patch.object(adm, "reserve_slot", AsyncMock(side_effect=ConnectionError("down"))),
        patch.object(wk, "_get_checkpoint_redis", MagicMock()),
    ):
        await wk._request_fnc(req)
    req.accept.assert_called_once_with()
    req.reject.assert_not_called()


@pytest.mark.asyncio
async def test_other_jobs_in_the_ledger_count_towards_the_memory_gate() -> None:
    """Child-process jobs are invisible to this process's counter; the ledger is
    the only place they show up, so the memory estimate must read it."""
    req = _request()
    release = AsyncMock()
    cfg = _settings(job_memory_limit_mb=150, container_memory_limit_mb=400)
    with (
        patch.object(wk, "settings", cfg),
        # 3 slots held in total, i.e. 2 OTHERS + this room -> (2+1)*150 = 450 > 400
        patch.object(adm, "reserve_slot", AsyncMock(return_value=(SlotResult.RESERVED, 3))),
        patch.object(adm, "release_slot", release),
        patch.object(wk, "_get_checkpoint_redis", MagicMock()),
    ):
        original = wk._active_jobs
        wk._active_jobs = 0  # the parent process sees nothing - the bug
        try:
            await wk._request_fnc(req)
        finally:
            wk._active_jobs = original
    req.reject.assert_called_once_with(terminate=False)
    release.assert_awaited_once()  # the slot we just took is handed back


@pytest.mark.asyncio
async def test_claim_slot_with_no_room_name_is_a_no_op() -> None:
    assert await adm.claim_slot(MagicMock(), "", cap=4, ttl_seconds=60) == (adm.SLOT_UNKNOWN, 0)


@pytest.mark.asyncio
async def test_admit_extends_the_lease_and_the_shutdown_callback_releases_it() -> None:
    ctx = MagicMock()
    ctx.job = SimpleNamespace(room=SimpleNamespace(name="room-7"))
    job = wk.InterviewJob(ctx)
    extend, release = AsyncMock(), AsyncMock()
    with (
        patch.object(wk, "_extend_slot_lease", extend),
        patch.object(wk, "_release_slot", release),
    ):
        before = wk._active_jobs
        try:
            await job._admit()
            extend.assert_awaited_once_with("room-7")
            await job._decrement_job_counter()
            release.assert_awaited_once_with("room-7")
        finally:
            wk._active_jobs = before


# ----------------------------------------------------------------------- load


@pytest.mark.parametrize(("running", "expected"), [(0, 0.0), (2, 0.5), (4, 1.0), (9, 1.0)])
def test_load_is_jobs_over_cap_not_cpu(running: int, expected: float) -> None:
    server = SimpleNamespace(active_jobs=[object()] * running)
    with patch.object(wk, "settings", _settings(worker_max_concurrent_jobs=4)):
        assert wk._worker_load(server) == expected


def test_load_never_reports_full_for_a_cap_of_zero_or_a_changed_api() -> None:
    with patch.object(wk, "settings", _settings(worker_max_concurrent_jobs=0)):
        assert wk._worker_load(SimpleNamespace(active_jobs=[1, 2, 3])) == 0.0
    with patch.object(wk, "settings", _settings()):
        assert wk._worker_load(object()) == 0.0


def test_the_default_threshold_makes_the_worker_full_exactly_at_the_cap() -> None:
    """With the old 0.75 default and load = jobs/cap, a cap of 4 went FULL at
    THREE jobs. 1.0 is 'available until the cap'."""
    from app.config import Settings

    assert Settings.model_fields["worker_load_threshold"].default == 1.0
    load_with_cap_minus_one = 3 / 4
    assert load_with_cap_minus_one < 1.0  # still AVAILABLE with one slot free
    assert 4 / 4 >= 1.0  # FULL at the cap


# --------------------------------------------------------------- warm processes


def test_idle_processes_default_to_the_job_cap() -> None:
    assert adm.idle_process_target(None, 4) == 4


def test_idle_processes_are_bounded_whatever_the_cap() -> None:
    assert adm.idle_process_target(None, 500) == adm.MAX_IDLE_PROCESSES


def test_idle_processes_can_be_set_explicitly_including_zero() -> None:
    assert adm.idle_process_target(0, 4) == 0
    assert adm.idle_process_target(2, 4) == 2


# --------------------------------------------------------------- registration


def test_run_registers_the_explicit_agent_name_load_fn_and_warm_pool() -> None:
    captured: dict[str, Any] = {}

    def fake_run_app(options: Any) -> None:
        captured["options"] = options

    cfg = _settings(
        interview_agent_name="anthire-interviewer",
        worker_load_threshold=1.0,
        worker_num_idle_processes=None,
        worker_initialize_timeout_seconds=30.0,
        worker_drain_timeout_seconds=120,
        livekit_url="wss://x.invalid",
        livekit_api_key="k",
        livekit_api_secret="s",
    )
    with (
        patch.object(wk, "settings", cfg),
        patch.object(wk, "_configure_worker_logging"),
        patch("threading.Thread", MagicMock()),
        patch.object(wk.cli, "run_app", fake_run_app),
    ):
        wk.run()

    opts = captured["options"]
    assert opts.agent_name == "anthire-interviewer"
    assert opts.load_fnc is wk._worker_load
    assert opts.load_threshold == 1.0
    assert opts.num_idle_processes == 4
    assert opts.initialize_process_timeout == 30.0


def test_an_empty_agent_name_restores_automatic_dispatch() -> None:
    captured: dict[str, Any] = {}
    cfg = _settings(
        interview_agent_name="",
        worker_load_threshold=1.0,
        worker_num_idle_processes=None,
        worker_initialize_timeout_seconds=30.0,
        worker_drain_timeout_seconds=120,
        livekit_url="wss://x.invalid",
        livekit_api_key="k",
        livekit_api_secret="s",
    )
    with (
        patch.object(wk, "settings", cfg),
        patch.object(wk, "_configure_worker_logging"),
        patch("threading.Thread", MagicMock()),
        patch.object(wk.cli, "run_app", lambda o: captured.setdefault("options", o)),
    ):
        wk.run()
    assert captured["options"].agent_name == ""
