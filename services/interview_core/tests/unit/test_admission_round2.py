"""Round-2 review/security fixes for worker admission.

F1  a starting job's marker is short and the running job stretches it
F2  the cap holds with Redis DOWN (parent-visible launched jobs)
F5  a HELD reservation is lengthened at accept, not 30 s later
F8  warm-pool sizing uses the CONTAINER's CPUs
N1  a CLAIMed slot (no live reservation) re-checks the one-interview-per-user rule
N4  the default agent name is environment-aware
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.worker.admission as adm
import app.worker.interview_worker as wk
from app.config import DEV_AGENT_NAME, PRODUCTION_AGENT_NAME, Settings
from app.worker_capacity import SlotResult
from tests.unit.test_worker_dispatch import _Ledger, _request, _settings


def _server(*rooms: str) -> Any:
    return SimpleNamespace(
        active_jobs=[
            SimpleNamespace(job=SimpleNamespace(room=SimpleNamespace(name=r))) for r in rooms
        ]
    )


@pytest.fixture(autouse=True)
def _no_server() -> Any:
    saved = wk._server
    wk._server = None
    yield
    wk._server = saved


# ------------------------------------------------------------------------ F1


def test_the_starting_marker_ttl_is_just_over_the_process_init_timeout() -> None:
    """120 s let a never-started job block the watchdog's whole 60 s budget."""
    cfg = Settings.model_fields
    marker = cfg["job_marker_seconds"].default
    init = cfg["worker_initialize_timeout_seconds"].default
    assert init < marker <= init + 20


@pytest.mark.asyncio
async def test_a_running_jobs_lease_stretches_the_marker_with_the_slot() -> None:
    ledger = _Ledger()
    with ledger.patches():
        assert await adm.extend_lease(MagicMock(), "room-1", 1800) is True
    ledger.extend.assert_awaited_once()
    ledger.refresh_marker.assert_awaited_once()
    assert ledger.refresh_marker.await_args.args[1:] == ("room-1", 1800)


@pytest.mark.asyncio
async def test_admit_passes_through_to_the_marker_refresh() -> None:
    """_admit -> _extend_slot_lease -> extend_lease(refresh_marker default True)."""
    ledger = _Ledger()
    with ledger.patches():
        await wk._extend_slot_lease("room-1")
    ledger.refresh_marker.assert_awaited_once()


# ------------------------------------------------------------------------ F2


@pytest.mark.asyncio
async def test_the_cap_holds_while_redis_is_down() -> None:
    """The parent's `_active_jobs` is always 0 (jobs count in children), so with
    Redis down nothing bounded the worker except LiveKit's cap+2 load gate."""
    req, ledger = _request("room-9"), _Ledger()
    ledger.reserve.side_effect = ConnectionError("down")
    ledger.claim_marker.side_effect = ConnectionError("down")
    wk._server = _server("a", "b")  # 2 launched jobs
    with ledger.patches(_settings(worker_max_concurrent_jobs=2)):
        await wk._request_fnc(req)
    req.reject.assert_called_once_with(terminate=False)
    req.accept.assert_not_called()


@pytest.mark.asyncio
async def test_with_redis_down_a_job_under_the_cap_is_still_accepted() -> None:
    req, ledger = _request("room-9"), _Ledger()
    ledger.reserve.side_effect = ConnectionError("down")
    ledger.claim_marker.side_effect = ConnectionError("down")
    wk._server = _server("a")
    with ledger.patches(_settings(worker_max_concurrent_jobs=2)):
        await wk._request_fnc(req)
    req.accept.assert_called_once_with()


def test_at_capacity_is_safe_against_an_unknown_server_shape() -> None:
    assert adm.at_capacity(None, 2) is False
    assert adm.at_capacity(object(), 2) is False
    assert adm.at_capacity(_server("a", "b"), 0) is False  # cap 0 = no cap
    assert adm.at_capacity(_server("a", "b"), 2) is True


# ------------------------------------------------------------------------ F5


@pytest.mark.asyncio
async def test_a_held_reservation_is_lengthened_at_accept() -> None:
    """Joining late in the 180 s window used to let the slot lapse before the job
    process reached _admit, leaving the interview with no ledger entry."""
    req, ledger = _request(), _Ledger(slot=(SlotResult.HELD, 1))
    with ledger.patches():
        await wk._request_fnc(req)
    req.accept.assert_called_once_with()
    ledger.extend.assert_awaited_once()
    assert ledger.extend.await_args.kwargs == {"ttl_seconds": 1800}
    # ... but NOT the marker: that stays short until the job is really running.
    ledger.refresh_marker.assert_not_called()


@pytest.mark.asyncio
async def test_a_fresh_claim_is_not_extended_twice() -> None:
    req, ledger = _request(), _Ledger(slot=(SlotResult.RESERVED, 1))
    with ledger.patches():
        await wk._request_fnc(req)
    ledger.extend.assert_not_called()


# ------------------------------------------------------------------------ F8


def test_container_cpus_prefers_the_cgroup_aware_monitor_over_os_cpu_count() -> None:
    fake = SimpleNamespace(cpu_count=lambda: 2.0)
    with (
        patch("livekit.agents.utils.hw.get_cpu_monitor", return_value=fake),
        patch("os.cpu_count", return_value=16),
    ):
        assert adm.container_cpus() == 2
        # 2-vCPU container on a 16-core host, 12 slots: 4 warm, not 8.
        assert adm.idle_process_target(None, 12) == 4


def test_a_fractional_cpu_quota_rounds_up_and_a_broken_monitor_falls_back() -> None:
    with patch("livekit.agents.utils.hw.get_cpu_monitor", return_value=SimpleNamespace(cpu_count=lambda: 1.5)):
        assert adm.container_cpus() == 2
    with (
        patch("livekit.agents.utils.hw.get_cpu_monitor", side_effect=RuntimeError("no cgroup")),
        patch("os.cpu_count", return_value=6),
    ):
        assert adm.container_cpus() == 6


# ------------------------------------------------------------------------ N1


@pytest.mark.asyncio
async def test_a_claimed_slot_is_refused_when_the_user_may_not_have_another() -> None:
    """The join token can no longer outlive its reservation, but a dispatch that
    bypasses the token endpoint, or a lapse before accept, ends here: the worker
    CLAIMs a slot and must not skip the one-interview-per-user rule."""
    req, ledger = _request(), _Ledger(slot=(SlotResult.RESERVED, 1))
    ledger.permitted.return_value = False
    with ledger.patches():
        await wk._request_fnc(req)
    req.reject.assert_called_once_with(terminate=False)
    req.accept.assert_not_called()
    ledger.clear_marker.assert_awaited()  # not left blocking a retry
    ledger.release_slot.assert_awaited_once()  # THIS call claimed it: handed back


@pytest.mark.asyncio
async def test_a_held_slot_skips_the_claim_check() -> None:
    req, ledger = _request(), _Ledger(slot=(SlotResult.HELD, 1))
    ledger.permitted.return_value = False
    with ledger.patches():
        await wk._request_fnc(req)
    ledger.permitted.assert_not_awaited()
    req.accept.assert_called_once_with()


class _FakeDb:
    def __init__(self, user_id: str | None, others: int) -> None:
        self.user_id, self.others, self.n = user_id, others, 0

    async def __aenter__(self) -> _FakeDb:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def execute(self, *_a: Any, **_k: Any) -> Any:
        self.n += 1
        value = self.user_id if self.n == 1 else self.others
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        result.scalar_one.return_value = value
        return result


def _patch_claim(db: _FakeDb, pointer: str | bytes | None, pointer_held: bool) -> Any:
    from contextlib import ExitStack

    redis = MagicMock()
    redis.get = AsyncMock(return_value=pointer)
    stack = ExitStack()
    stack.enter_context(patch("app.database.init_engine", lambda: None))
    stack.enter_context(patch("app.database.get_session_factory", lambda: (lambda: db)))
    stack.enter_context(patch.object(adm, "slot_held", AsyncMock(return_value=pointer_held)))
    return stack, redis


ROOM = "5d8c7f06-1d0f-4f3b-9d64-1c3f5c1d0a11"


@pytest.mark.asyncio
async def test_claim_permitted_refuses_a_user_with_another_interview_in_progress() -> None:
    stack, redis = _patch_claim(_FakeDb("u1", others=1), None, False)
    with stack:
        assert await adm.claim_permitted(lambda: redis, ROOM) is False


@pytest.mark.asyncio
async def test_claim_permitted_refuses_a_user_whose_pointer_names_another_held_slot() -> None:
    stack, redis = _patch_claim(_FakeDb("u1", others=0), "other-session", True)
    with stack:
        assert await adm.claim_permitted(lambda: redis, ROOM) is False


@pytest.mark.asyncio
async def test_claim_permitted_allows_a_clean_user_and_a_lapsed_pointer() -> None:
    stack, redis = _patch_claim(_FakeDb("u1", others=0), None, False)
    with stack:
        assert await adm.claim_permitted(lambda: redis, ROOM) is True
    stack, redis = _patch_claim(_FakeDb("u1", others=0), "stale", False)
    with stack:
        assert await adm.claim_permitted(lambda: redis, ROOM) is True
    stack, redis = _patch_claim(_FakeDb("u1", others=0), ROOM, True)  # our own slot
    with stack:
        assert await adm.claim_permitted(lambda: redis, ROOM) is True


@pytest.mark.asyncio
async def test_claim_permitted_treats_a_bytes_pointer_to_our_own_room_as_ours() -> None:
    """The worker's Redis client returns bytes; our own slot must not count as another."""
    stack, redis = _patch_claim(_FakeDb("u1", others=0), ROOM.encode(), True)
    with stack:
        assert await adm.claim_permitted(lambda: redis, ROOM) is True


@pytest.mark.asyncio
async def test_claim_permitted_refuses_a_bytes_pointer_to_another_held_slot() -> None:
    stack, redis = _patch_claim(_FakeDb("u1", others=0), b"other-session", True)
    with stack:
        assert await adm.claim_permitted(lambda: redis, ROOM) is False


@pytest.mark.asyncio
async def test_claim_permitted_fails_open_when_the_database_is_slow() -> None:
    """A cold database must not hold the job offer past the bound."""
    import asyncio

    class _SlowDb(_FakeDb):
        async def execute(self, *_a: Any, **_k: Any) -> Any:
            await asyncio.sleep(5)
            return await super().execute()

    stack, redis = _patch_claim(_SlowDb("u1", others=1), None, False)
    with stack, patch.object(adm, "CLAIM_CHECK_TIMEOUT_SECONDS", 0.05):
        assert await adm.claim_permitted(lambda: redis, ROOM) is True


@pytest.mark.asyncio
async def test_claim_permitted_fails_open_on_any_lookup_error() -> None:
    """Refusing because a lookup failed would strand a candidate."""
    with patch("app.database.get_session_factory", side_effect=RuntimeError("no db")):
        assert await adm.claim_permitted(lambda: MagicMock(), ROOM) is True
    assert await adm.claim_permitted(lambda: MagicMock(), "not-a-uuid") is True


# ------------------------------------------------------------------------ N4


@pytest.mark.parametrize(
    ("app_env", "configured", "expected"),
    [
        ("production", "auto", PRODUCTION_AGENT_NAME),
        ("development", "auto", DEV_AGENT_NAME),
        ("staging", "auto", DEV_AGENT_NAME),
        ("test", "auto", DEV_AGENT_NAME),
        ("development", "my-own-name", "my-own-name"),  # explicit override wins
        ("production", "", ""),  # the kill switch to automatic dispatch survives
    ],
)
def test_the_default_agent_name_is_environment_aware(
    app_env: str, configured: str, expected: str
) -> None:
    """A local worker on the shared LiveKit project must not default to the
    production name and take real candidates' dispatches (security N4)."""
    # production/staging fail fast on weak/plain-text settings; satisfy them.
    s = Settings(
        app_env=app_env,
        interview_agent_name=configured,
        database_ssl="require",
        redis_url="rediss://default:pw@example.upstash.io:6379",
        jwt_secret="x" * 48,
    )
    assert s.interview_agent_name == expected


def test_the_unset_default_is_auto_not_the_production_name() -> None:
    assert Settings.model_fields["interview_agent_name"].default == "auto"
    assert DEV_AGENT_NAME != PRODUCTION_AGENT_NAME
