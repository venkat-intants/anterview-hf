"""Worker side of the stranded-candidate fixes (2026-10 load test).

* admission honours the Redis slot ledger and never rejects terminally;
* a room never gets two interviewer jobs;
* load is jobs/(cap+headroom), not container CPU;
* warm-process count defaults to min(cap, 2 x CPUs, 8);
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
    s.job_marker_seconds = 120
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _request(room: str = "room-1") -> AsyncMock:
    req = AsyncMock()
    req.room = SimpleNamespace(name=room)
    return req


class _Ledger:
    """Patch targets for one admission test, with call recording."""

    def __init__(
        self,
        slot: tuple[SlotResult, int] = (SlotResult.RESERVED, 1),
        marker_free: bool = True,
    ) -> None:
        self.reserve = AsyncMock(return_value=slot)
        self.release_slot = AsyncMock()
        self.claim_marker = AsyncMock(return_value=marker_free)
        self.clear_marker = AsyncMock()
        self.extend = AsyncMock(return_value=True)
        self.refresh_marker = AsyncMock()
        self.permitted = AsyncMock(return_value=True)

    def patches(self, cfg: MagicMock | None = None) -> Any:
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(patch.object(wk, "settings", cfg or _settings()))
        stack.enter_context(patch.object(adm, "reserve_slot", self.reserve))
        stack.enter_context(patch.object(adm, "release_slot", self.release_slot))
        stack.enter_context(patch.object(adm, "claim_job_marker", self.claim_marker))
        stack.enter_context(patch.object(adm, "clear_job_marker", self.clear_marker))
        stack.enter_context(patch.object(adm, "extend_slot", self.extend))
        stack.enter_context(patch.object(adm, "refresh_job_marker", self.refresh_marker))
        stack.enter_context(patch.object(adm, "claim_permitted", self.permitted))
        stack.enter_context(patch.object(wk, "_get_checkpoint_redis", MagicMock()))
        return stack


@pytest.fixture(autouse=True)
def _no_server() -> Any:
    saved = wk._server
    wk._server = None
    yield
    wk._server = saved


# ------------------------------------------------------------------ admission


@pytest.mark.asyncio
async def test_a_room_whose_slot_was_reserved_at_token_time_is_accepted() -> None:
    req, ledger = _request(), _Ledger(slot=(SlotResult.HELD, 4))
    with ledger.patches():
        await wk._request_fnc(req)
    req.accept.assert_called_once_with()
    req.reject.assert_not_called()


@pytest.mark.asyncio
async def test_a_full_ledger_rejects_non_terminally_and_releases_nothing() -> None:
    req, ledger = _request(), _Ledger(slot=(SlotResult.FULL, 4))
    with ledger.patches():
        await wk._request_fnc(req)
    req.reject.assert_called_once_with(terminate=False)
    req.accept.assert_not_called()
    ledger.release_slot.assert_not_called()  # we hold nothing for this room
    ledger.clear_marker.assert_awaited_once()  # but the marker WE set is cleared


@pytest.mark.asyncio
async def test_a_redis_outage_accepts_rather_than_stranding_the_candidate() -> None:
    req, ledger = _request(), _Ledger()
    ledger.reserve.side_effect = ConnectionError("down")
    ledger.claim_marker.side_effect = ConnectionError("down")
    with ledger.patches():
        await wk._request_fnc(req)
    req.accept.assert_called_once_with()
    req.reject.assert_not_called()


@pytest.mark.asyncio
async def test_other_jobs_in_the_ledger_count_towards_the_memory_gate() -> None:
    """Child-process jobs are invisible to this process's counter; the ledger is
    the only place they show up, so the memory estimate must read it."""
    req, ledger = _request(), _Ledger(slot=(SlotResult.RESERVED, 3))
    cfg = _settings(job_memory_limit_mb=150, container_memory_limit_mb=400)
    original = wk._active_jobs
    wk._active_jobs = 0  # the parent process sees nothing - the bug
    try:
        with ledger.patches(cfg):
            await wk._request_fnc(req)
    finally:
        wk._active_jobs = original
    req.reject.assert_called_once_with(terminate=False)
    ledger.release_slot.assert_awaited_once()  # THIS call claimed it: handed back


@pytest.mark.asyncio
async def test_a_held_slot_is_not_released_when_the_job_is_refused() -> None:
    """The HELD slot belongs to the token endpoint's reservation or to a job that
    is already running; freeing it undercounts the ledger (review S1)."""
    req, ledger = _request(), _Ledger(slot=(SlotResult.HELD, 4))
    cfg = _settings(job_memory_limit_mb=150, container_memory_limit_mb=400)
    original = wk._active_jobs
    wk._active_jobs = 3
    try:
        with ledger.patches(cfg):
            await wk._request_fnc(req)
    finally:
        wk._active_jobs = original
    req.reject.assert_called_once_with(terminate=False)
    ledger.release_slot.assert_not_called()


@pytest.mark.asyncio
async def test_claim_slot_with_no_room_name_is_a_no_op() -> None:
    assert await adm.claim_slot(MagicMock(), "", cap=4, ttl_seconds=60) == (adm.SLOT_UNKNOWN, 0)


# ------------------------------------------------------- duplicate interviewers


def _server_running(*rooms: str) -> Any:
    return SimpleNamespace(
        active_jobs=[SimpleNamespace(job=SimpleNamespace(room=SimpleNamespace(name=r))) for r in rooms]
    )


@pytest.mark.asyncio
async def test_a_second_job_for_a_room_this_worker_already_runs_is_refused() -> None:
    """Two interviewers in one room: double billing, two voices, two writers on
    one transcript (review S1 / security M1)."""
    req, ledger = _request("room-1"), _Ledger(slot=(SlotResult.HELD, 1))
    wk._server = _server_running("room-1")
    with ledger.patches():
        await wk._request_fnc(req)
    req.reject.assert_called_once_with(terminate=False)
    req.accept.assert_not_called()
    # We own nothing: touching the ledger would free the running interview's slot.
    ledger.reserve.assert_not_called()
    ledger.release_slot.assert_not_called()


@pytest.mark.asyncio
async def test_a_job_for_a_different_room_is_unaffected() -> None:
    req, ledger = _request("room-2"), _Ledger()
    wk._server = _server_running("room-1")
    with ledger.patches():
        await wk._request_fnc(req)
    req.accept.assert_called_once_with()


@pytest.mark.asyncio
async def test_a_room_marker_held_by_another_worker_refuses_the_job() -> None:
    """Cross-worker version of the same rule: one job marker per room."""
    req, ledger = _request(), _Ledger(marker_free=False)
    with ledger.patches():
        await wk._request_fnc(req)
    req.reject.assert_called_once_with(terminate=False)
    ledger.reserve.assert_not_called()  # the duplicate never touched the ledger
    ledger.release_slot.assert_not_called()
    ledger.clear_marker.assert_not_called()  # not ours to clear


@pytest.mark.asyncio
async def test_an_accepted_job_keeps_its_marker_until_the_job_ends() -> None:
    req, ledger = _request(), _Ledger()
    with ledger.patches():
        await wk._request_fnc(req)
    req.accept.assert_called_once_with()
    ledger.claim_marker.assert_awaited_once()
    ledger.clear_marker.assert_not_called()


@pytest.mark.asyncio
async def test_releasing_a_room_frees_its_slot_and_its_marker() -> None:
    ledger = _Ledger()
    with ledger.patches():
        await adm.release(MagicMock(), "room-1")
    ledger.release_slot.assert_awaited_once()
    ledger.clear_marker.assert_awaited_once()


def test_room_has_job_is_safe_against_an_unknown_server_shape() -> None:
    assert adm.room_has_job(None, "r") is False
    assert adm.room_has_job(object(), "r") is False
    assert adm.room_has_job(_server_running("r"), "") is False


# -------------------------------------------------------------- lifecycle


@pytest.mark.asyncio
async def test_admit_registers_the_release_before_it_awaits_redis() -> None:
    """A job cancelled during the awaited extend would otherwise leak the counter
    and the lease (review N3)."""
    ctx = MagicMock()
    ctx.job = SimpleNamespace(room=SimpleNamespace(name="room-7"))
    order: list[str] = []
    ctx.add_shutdown_callback.side_effect = lambda cb: order.append("callback")

    async def extend(room: str) -> bool:
        order.append("extend")
        return True

    job = wk.InterviewJob(ctx)
    release = AsyncMock()
    before = wk._active_jobs
    try:
        with (
            patch.object(wk, "_extend_slot_lease", extend),
            patch.object(wk, "_release_slot", release),
        ):
            await job._admit()
            assert order == ["callback", "extend"]
            await job._decrement_job_counter()
        release.assert_awaited_once_with("room-7")
    finally:
        wk._active_jobs = before


@pytest.mark.asyncio
async def test_a_lapsed_reservation_at_accept_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The interview would otherwise run with no ledger entry, silently
    undercounting capacity (review N4)."""
    import logging

    with (
        patch.object(adm, "extend_slot", AsyncMock(return_value=False)),
        patch.object(adm, "refresh_job_marker", AsyncMock()),
        caplog.at_level(logging.WARNING, logger="interview-worker"),
    ):
        assert await adm.extend_lease(MagicMock(), "room-9", 1800) is False
    assert any("slot_missing_on_accept" in r.getMessage() for r in caplog.records)


# ----------------------------------------------------------------------- load


@pytest.mark.parametrize("running", [0, 1, 5, 11, 12])
def test_load_is_jobs_over_cap_plus_headroom_not_cpu(running: int) -> None:
    server = SimpleNamespace(active_jobs=[object()] * running)
    with patch.object(wk, "settings", _settings(worker_max_concurrent_jobs=12)):
        assert wk._worker_load(server) == pytest.approx(running / (12 + adm.LOAD_HEADROOM_SLOTS))


def _livekit_effective_load(load: float, running: int, reserved_offers: int) -> float:
    """livekit-agents 1.5.15 AgentServer._get_effective_load, restated."""
    estimate = load / running if running else 0.0
    return load + reserved_offers * estimate


@pytest.mark.parametrize("cap", [2, 4, 12])
def test_one_slot_short_of_the_cap_is_still_available_to_livekit(cap: int) -> None:
    """Replica test, 2026-10: 11 of 12 jobs running, a 12th offer in flight, and
    LiveKit logged 'no worker is available' because an in-flight offer is counted
    in BOTH terms of its effective-load formula. With the headroom the 12th (and
    even a second in-flight offer) is still offered; the exact cap is the
    ledger's job."""
    running = cap - 1
    load = adm.worker_load(SimpleNamespace(active_jobs=[object()] * running), cap)
    for in_flight in (1, 2):
        assert _livekit_effective_load(load, running, in_flight) < 1.0, in_flight
    # The pre-fix definition, for contrast, WAS full at exactly this point.
    assert _livekit_effective_load(running / cap, running, 1) >= 1.0 - 1e-9


def test_load_still_reaches_full_so_livekit_stops_offering_beyond_the_cap() -> None:
    full = adm.worker_load(SimpleNamespace(active_jobs=[object()] * 40), 4)
    assert full == 1.0


def test_load_never_reports_full_for_a_cap_of_zero_or_a_changed_api() -> None:
    with patch.object(wk, "settings", _settings(worker_max_concurrent_jobs=0)):
        assert wk._worker_load(SimpleNamespace(active_jobs=[1, 2, 3])) == 0.0
    with patch.object(wk, "settings", _settings()):
        assert wk._worker_load(object()) == 0.0


def test_the_load_function_remembers_the_server_for_request_fnc() -> None:
    server = SimpleNamespace(active_jobs=[])
    with patch.object(wk, "settings", _settings()):
        wk._worker_load(server)
    assert wk._server is server


def test_the_default_threshold_is_one() -> None:
    from app.config import Settings

    assert Settings.model_fields["worker_load_threshold"].default == 1.0


# --------------------------------------------------------------- warm processes


@pytest.mark.parametrize(
    ("cap", "cpus", "expected"),
    [
        (2, 2, 2),  # the HF Space: its cap
        (4, 4, 4),
        (12, 2, 4),  # 2-vCPU replica with 12 slots: 2 per CPU, not 12 warm VADs
        (12, 16, 8),  # never beyond MAX_IDLE_PROCESSES
        (500, 64, 8),
        (1, 8, 1),
    ],
)
def test_idle_processes_default_to_min_of_cap_two_per_cpu_and_the_bound(
    cap: int, cpus: int, expected: int
) -> None:
    assert adm.idle_process_target(None, cap, cpus) == expected


def test_idle_processes_can_be_set_explicitly_including_zero() -> None:
    assert adm.idle_process_target(0, 4, 2) == 0
    assert adm.idle_process_target(6, 4, 2) == 6


# --------------------------------------------------------------- registration


def _run_settings(**kw: Any) -> MagicMock:
    return _settings(
        interview_agent_name="anthire-interviewer",
        worker_load_threshold=1.0,
        worker_num_idle_processes=None,
        worker_initialize_timeout_seconds=30.0,
        worker_drain_timeout_seconds=120,
        livekit_url="wss://x.invalid",
        livekit_api_key="k",
        livekit_api_secret="s",
        **kw,
    )


def test_run_registers_the_explicit_agent_name_load_fn_and_warm_pool() -> None:
    captured: dict[str, Any] = {}
    with (
        patch.object(wk, "settings", _run_settings()),
        patch.object(wk, "_configure_worker_logging"),
        patch("threading.Thread", MagicMock()),
        patch.object(adm, "container_cpus", return_value=4),
        patch.object(wk.cli, "run_app", lambda o: captured.setdefault("options", o)),
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
    cfg = _run_settings()
    cfg.interview_agent_name = ""
    with (
        patch.object(wk, "settings", cfg),
        patch.object(wk, "_configure_worker_logging"),
        patch("threading.Thread", MagicMock()),
        patch.object(wk.cli, "run_app", lambda o: captured.setdefault("options", o)),
    ):
        wk.run()
    assert captured["options"].agent_name == ""
