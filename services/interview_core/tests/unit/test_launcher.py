"""Explicit dispatch (app/agent/launcher.py).

Properties pinned here:
  * a room with no interviewer gets exactly ONE live dispatch, however often the
    browser asks;
  * it is a RECOVERY path: no candidate in the room => nothing is created (a
    dispatch would make LiveKit create the room and run a paid interviewer into
    an empty one - security review H1);
  * a job that is still starting is never duplicated (review S1 / M1).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from livekit import api

from app.agent import launcher
from app.config import settings
from app.worker_capacity import dispatch_lock_key

AGENT = "anthire-interviewer"
USER = "user-42"
CANDIDATE = f"candidate-{USER}"


def _participant(kind: Any = None, identity: str = CANDIDATE) -> Any:
    kind = api.ParticipantInfo.Kind.STANDARD if kind is None else kind
    return api.ParticipantInfo(identity=identity, kind=kind)


def _agent() -> Any:
    return _participant(api.ParticipantInfo.Kind.AGENT, identity="agent-1")


def _dispatch(
    dispatch_id: str,
    *,
    agent: str = AGENT,
    job_status: Any = None,
    deleted: int = 0,
    age_seconds: float = 1000.0,
) -> Any:
    jobs = []
    if job_status is not None:
        jobs = [api.Job(id=f"job-{dispatch_id}", state=api.JobState(status=job_status))]
    return api.AgentDispatch(
        id=dispatch_id,
        agent_name=agent,
        room="r",
        state=api.AgentDispatchState(
            jobs=jobs, deleted_at=deleted, created_at=int(time.time() - age_seconds)
        ),
    )


class _FakeRedis:
    """SET NX EX + the compare-and-delete release script."""

    def __init__(self, *, down: bool = False, release_fails: bool = False) -> None:
        self.keys: dict[str, str] = {}
        self.down = down
        self.release_fails = release_fails

    async def eval(self, script: str, nkeys: int, key: str, token: str) -> int:
        if self.release_fails:
            raise ConnectionError("redis dropped during release")
        if self.keys.get(key) == token:
            del self.keys[key]
            return 1
        return 0

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> Any:
        if self.down:
            raise ConnectionError("redis down")
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.keys.pop(key, None)


class _FakeLiveKit:
    """Stands in for ``api.LiveKitAPI``; records every mutation."""

    def __init__(
        self,
        *,
        participants: list[Any] | None = None,
        dispatches: list[Any] | None = None,
        room_missing: bool = False,
    ) -> None:
        self.participants = [_participant()] if participants is None else participants
        self.dispatches = dispatches or []
        self.room_missing = room_missing
        self.created: list[Any] = []
        self.deleted: list[str] = []
        self.client_kwargs: dict[str, Any] = {}
        self.room = SimpleNamespace(
            list_rooms=self._list_rooms, list_participants=self._list_participants
        )
        self.agent_dispatch = SimpleNamespace(
            list_dispatch=self._list_dispatch,
            create_dispatch=self._create_dispatch,
            delete_dispatch=self._delete_dispatch,
        )

    async def __aenter__(self) -> _FakeLiveKit:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def _list_rooms(self, req: Any) -> Any:
        return SimpleNamespace(rooms=[] if self.room_missing else [api.Room(name="r")])

    async def _list_participants(self, req: Any) -> Any:
        assert not self.room_missing, "must not list participants of a missing room"
        return SimpleNamespace(participants=self.participants)

    async def _list_dispatch(self, room: str) -> list[Any]:
        # Real LiveKit answers 503 for a missing room, so the code must not ask.
        assert not self.room_missing, "must not list dispatches of a missing room"
        return self.dispatches

    async def _create_dispatch(self, req: Any) -> Any:
        self.created.append(req)
        return api.AgentDispatch(id="new", agent_name=req.agent_name, room=req.room)

    async def _delete_dispatch(self, dispatch_id: str, room: str) -> Any:
        self.deleted.append(dispatch_id)
        return None


@pytest.fixture(autouse=True)
def _explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "interview_agent_name", AGENT, raising=False)
    monkeypatch.setattr(settings, "livekit_url", "wss://x.invalid", raising=False)
    monkeypatch.setattr(settings, "livekit_api_key", "k", raising=False)
    monkeypatch.setattr(settings, "livekit_api_secret", "s" * 32, raising=False)


def _use(monkeypatch: pytest.MonkeyPatch, fake: _FakeLiveKit) -> _FakeLiveKit:
    def factory(*args: Any, **kwargs: Any) -> _FakeLiveKit:
        fake.client_kwargs = kwargs
        return fake

    monkeypatch.setattr(launcher.api, "LiveKitAPI", factory)
    return fake


async def _ensure(
    redis: _FakeRedis | None = None, hook: Any = None, user: str = USER
) -> launcher.DispatchOutcome:
    redis = redis or _FakeRedis()
    return await launcher.ensure_interviewer(
        "r", user, redis_getter=lambda: redis, before_create=hook
    )


# ------------------------------------------------------------- token embedding


def test_the_token_room_config_dispatches_the_named_agent() -> None:
    cfg = launcher.room_configuration()
    assert cfg is not None
    assert [a.agent_name for a in cfg.agents] == [AGENT]


def test_an_empty_agent_name_means_no_room_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "interview_agent_name", "", raising=False)
    assert launcher.room_configuration() is None
    assert launcher.explicit_dispatch_enabled() is False


def test_no_candidate_data_rides_in_the_dispatch() -> None:
    cfg = launcher.room_configuration()
    assert cfg is not None
    assert cfg.agents[0].metadata == ""


# ------------------------------------------------- recovery only (security H1)


@pytest.mark.asyncio
async def test_a_room_that_does_not_exist_is_never_created_by_a_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LiveKit creates the room as a side effect of CreateDispatch; doing so for
    a room nobody joined starts a paid interviewer in an empty room."""
    fake = _use(monkeypatch, _FakeLiveKit(room_missing=True))
    hook_called: list[int] = []

    async def hook() -> None:
        hook_called.append(1)

    assert await _ensure(hook=hook) is launcher.DispatchOutcome.NOT_JOINED
    assert fake.created == [] and hook_called == []


@pytest.mark.asyncio
async def test_a_room_without_this_candidate_gets_no_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(monkeypatch, _FakeLiveKit(participants=[]))
    assert await _ensure() is launcher.DispatchOutcome.NOT_JOINED
    assert fake.created == []


@pytest.mark.asyncio
async def test_another_users_candidate_participant_does_not_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(monkeypatch, _FakeLiveKit(participants=[_participant(identity="candidate-other")]))
    assert await _ensure() is launcher.DispatchOutcome.NOT_JOINED
    assert fake.created == []


@pytest.mark.asyncio
async def test_an_agent_identity_cannot_pose_as_the_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(
        monkeypatch,
        _FakeLiveKit(participants=[_participant(api.ParticipantInfo.Kind.AGENT, CANDIDATE)]),
    )
    # The only participant is an agent => it is "already connected", not a join.
    assert await _ensure() is launcher.DispatchOutcome.NOT_JOINED
    assert fake.created == []


# -------------------------------------------------------------- ensure_interviewer


@pytest.mark.asyncio
async def test_an_agent_already_in_the_room_means_do_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(monkeypatch, _FakeLiveKit(participants=[_participant(), _agent()]))
    assert await _ensure() is launcher.DispatchOutcome.ALREADY_CONNECTED
    assert fake.created == [] and fake.deleted == []


@pytest.mark.asyncio
async def test_a_candidate_alone_in_the_room_gets_a_fresh_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(monkeypatch, _FakeLiveKit())
    assert await _ensure() is launcher.DispatchOutcome.DISPATCHED
    assert [(c.agent_name, c.room) for c in fake.created] == [(AGENT, "r")]


@pytest.mark.asyncio
async def test_before_create_runs_after_every_check_and_before_the_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(monkeypatch, _FakeLiveKit())
    order: list[str] = []

    async def hook() -> None:
        order.append(f"hook(created={len(fake.created)})")

    await _ensure(hook=hook)
    assert order == ["hook(created=0)"]
    assert len(fake.created) == 1


@pytest.mark.asyncio
async def test_a_refusing_before_create_hook_prevents_the_dispatch_and_frees_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(monkeypatch, _FakeLiveKit())
    redis = _FakeRedis()

    async def hook() -> None:
        raise RuntimeError("ledger full")

    with pytest.raises(RuntimeError):
        await _ensure(redis, hook)
    assert fake.created == []
    assert dispatch_lock_key("r") not in redis.keys  # a retry is not blocked


@pytest.mark.asyncio
async def test_a_running_job_is_never_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _use(
        monkeypatch,
        _FakeLiveKit(dispatches=[_dispatch("d1", job_status=api.JobStatus.JS_RUNNING)]),
    )
    assert await _ensure() is launcher.DispatchOutcome.STARTING
    assert fake.created == [] and fake.deleted == []


@pytest.mark.asyncio
@pytest.mark.parametrize("job_status", [api.JobStatus.JS_PENDING, None])
async def test_a_young_dispatch_counts_as_live_whatever_its_job_list_says(
    monkeypatch: pytest.MonkeyPatch, job_status: Any
) -> None:
    """Accepted by the worker, process still starting, no agent participant yet:
    deleting it and creating a second dispatch runs TWO interviewers (S1). Against
    a real livekit-server a running job can even list ``jobs: []`` at this point,
    so the job list alone cannot be trusted."""
    fake = _use(
        monkeypatch,
        _FakeLiveKit(dispatches=[_dispatch("d1", job_status=job_status, age_seconds=5)]),
    )
    assert await _ensure() is launcher.DispatchOutcome.STARTING
    assert fake.created == [] and fake.deleted == []


@pytest.mark.asyncio
async def test_an_old_pending_job_is_the_stranded_case_and_is_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(
        monkeypatch,
        _FakeLiveKit(
            dispatches=[_dispatch("old", job_status=api.JobStatus.JS_PENDING, age_seconds=300)]
        ),
    )
    assert await _ensure() is launcher.DispatchOutcome.DISPATCHED
    assert fake.deleted == ["old"] and len(fake.created) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("job_status", [None, api.JobStatus.JS_FAILED])
async def test_an_old_dead_dispatch_is_replaced_not_stacked(
    monkeypatch: pytest.MonkeyPatch, job_status: Any
) -> None:
    fake = _use(monkeypatch, _FakeLiveKit(dispatches=[_dispatch("old", job_status=job_status)]))
    assert await _ensure() is launcher.DispatchOutcome.DISPATCHED
    assert fake.deleted == ["old"]
    assert len(fake.created) == 1


@pytest.mark.asyncio
async def test_other_agents_dispatches_and_deleted_ones_are_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(
        monkeypatch,
        _FakeLiveKit(
            dispatches=[
                _dispatch("someone-else", agent="other-agent"),
                _dispatch("gone", deleted=123),
            ]
        ),
    )
    assert await _ensure() is launcher.DispatchOutcome.DISPATCHED
    assert fake.deleted == []


# ------------------------------------------------------------- lock (review M1)


@pytest.mark.asyncio
async def test_concurrent_callers_create_exactly_one_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three parallel /redispatch calls (the rate limit allows it) must not all
    run list -> delete -> create."""
    import asyncio

    fake = _use(monkeypatch, _FakeLiveKit())
    redis = _FakeRedis()
    outcomes = await asyncio.gather(*[_ensure(redis) for _ in range(3)])
    assert sorted(o.value for o in outcomes) == ["dispatched", "starting", "starting"]
    assert len(fake.created) == 1


@pytest.mark.asyncio
async def test_a_held_lock_means_starting_without_touching_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(monkeypatch, _FakeLiveKit(dispatches=[_dispatch("old")]))
    redis = _FakeRedis()
    redis.keys[dispatch_lock_key("r")] = "1"
    assert await _ensure(redis) is launcher.DispatchOutcome.STARTING
    assert fake.created == [] and fake.deleted == []


@pytest.mark.asyncio
async def test_the_lock_fails_closed_when_redis_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the lock two callers can create two interviewers."""
    fake = _use(monkeypatch, _FakeLiveKit())
    with pytest.raises(ConnectionError):
        await _ensure(_FakeRedis(down=True))
    assert fake.created == []


@pytest.mark.asyncio
async def test_a_slow_caller_cannot_delete_the_new_holders_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review F3: the release used to be an unconditional DEL, so a caller whose
    lock had already expired deleted the NEXT caller's lock."""
    _use(monkeypatch, _FakeLiveKit())
    redis = _FakeRedis()

    async def hook() -> None:
        # Our lock "expires" and someone else takes it while we are still working...
        redis.keys[dispatch_lock_key("r")] = "someone-elses-token"
        raise RuntimeError("then we fail and release")

    with pytest.raises(RuntimeError):
        await _ensure(redis, hook)
    assert redis.keys[dispatch_lock_key("r")] == "someone-elses-token"


@pytest.mark.asyncio
async def test_a_failing_release_never_masks_the_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 503 'slots full' (with Retry-After) must not turn into whatever the
    release raised."""
    _use(monkeypatch, _FakeLiveKit())

    async def hook() -> None:
        raise RuntimeError("ledger full")

    with pytest.raises(RuntimeError, match="ledger full"):
        await _ensure(_FakeRedis(release_fails=True), hook)


@pytest.mark.asyncio
async def test_the_critical_section_is_bounded_inside_the_lock_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degraded LiveKit (several 5 s calls) must not outlive the 15 s lock and
    let a second caller in."""
    import asyncio

    class _Slow(_FakeLiveKit):
        async def _list_dispatch(self, room: str) -> list[Any]:
            await asyncio.sleep(30)
            return []

    monkeypatch.setattr(settings, "dispatch_lock_seconds", 5, raising=False)
    monkeypatch.setattr(launcher, "_LOCK_MARGIN_SECONDS", 4.9)  # bound = 0.1 s
    assert launcher._critical_section_seconds() < settings.dispatch_lock_seconds
    redis = _FakeRedis()
    _use(monkeypatch, _Slow())
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await _ensure(redis)
    assert time.monotonic() - started < 3
    assert dispatch_lock_key("r") not in redis.keys  # released: a retry is not blocked


def test_the_default_critical_section_is_shorter_than_the_default_lock() -> None:
    assert launcher._critical_section_seconds() < settings.dispatch_lock_seconds


# ------------------------------------------------------------- timeouts / errors


@pytest.mark.asyncio
async def test_livekit_calls_have_an_explicit_short_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The library default is 60 s per call (M3)."""
    fake = _use(monkeypatch, _FakeLiveKit())
    await _ensure()
    timeout = fake.client_kwargs["timeout"]
    assert timeout.total == settings.livekit_api_timeout_seconds <= 10


@pytest.mark.asyncio
async def test_other_twirp_errors_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Broken(_FakeLiveKit):
        async def _list_participants(self, req: Any) -> Any:
            raise api.TwirpError(api.TwirpErrorCode.UNAVAILABLE, "down", status=503)

    _use(monkeypatch, _Broken())
    with pytest.raises(api.TwirpError):
        await _ensure()


@pytest.mark.asyncio
async def test_automatic_mode_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "interview_agent_name", "", raising=False)
    fake = _use(monkeypatch, _FakeLiveKit())
    assert await _ensure() is launcher.DispatchOutcome.AUTOMATIC
    assert fake.created == []


def test_dispatch_age_handles_every_timestamp_unit() -> None:
    now = time.time()
    for scale in (1, 10**3, 10**6, 10**9):
        assert launcher._age_seconds(int((now - 10) * scale)) == pytest.approx(10, abs=2)
    assert launcher._age_seconds(0) == float("inf")
