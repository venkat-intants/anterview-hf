"""Explicit dispatch (app/agent/launcher.py).

Every case is about one property: a room with no interviewer gets exactly ONE
live dispatch, however many times the candidate's browser asks.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from livekit import api

from app.agent import launcher
from app.config import settings

AGENT = "anthire-interviewer"


def _participant(kind: Any) -> Any:
    return api.ParticipantInfo(identity="p", kind=kind)


def _dispatch(
    dispatch_id: str, *, agent: str = AGENT, job_status: Any = None, deleted: int = 0
) -> Any:
    jobs = []
    if job_status is not None:
        jobs = [api.Job(id=f"job-{dispatch_id}", state=api.JobState(status=job_status))]
    return api.AgentDispatch(
        id=dispatch_id,
        agent_name=agent,
        room="r",
        state=api.AgentDispatchState(jobs=jobs, deleted_at=deleted),
    )


class _FakeLiveKit:
    """Stands in for ``api.LiveKitAPI``; records every mutation."""

    def __init__(
        self,
        *,
        participants: list[Any] | None = None,
        dispatches: list[Any] | None = None,
        room_missing: bool = False,
    ) -> None:
        self.participants = participants or []
        self.dispatches = dispatches or []
        self.room_missing = room_missing
        self.created: list[Any] = []
        self.deleted: list[str] = []
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
    monkeypatch.setattr(launcher.api, "LiveKitAPI", lambda *a, **k: fake)
    return fake


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


# -------------------------------------------------------------- ensure_interviewer


@pytest.mark.asyncio
async def test_an_agent_already_in_the_room_means_do_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(
        monkeypatch,
        _FakeLiveKit(participants=[_participant(api.ParticipantInfo.Kind.AGENT)]),
    )
    assert await launcher.ensure_interviewer("r") is launcher.DispatchOutcome.ALREADY_CONNECTED
    assert fake.created == [] and fake.deleted == []


@pytest.mark.asyncio
async def test_a_candidate_alone_in_the_room_gets_a_fresh_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(
        monkeypatch,
        _FakeLiveKit(participants=[_participant(api.ParticipantInfo.Kind.STANDARD)]),
    )
    assert await launcher.ensure_interviewer("r") is launcher.DispatchOutcome.DISPATCHED
    assert [(c.agent_name, c.room) for c in fake.created] == [(AGENT, "r")]


@pytest.mark.asyncio
async def test_a_room_that_does_not_exist_yet_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(monkeypatch, _FakeLiveKit(room_missing=True))
    assert await launcher.ensure_interviewer("r") is launcher.DispatchOutcome.DISPATCHED
    assert len(fake.created) == 1


@pytest.mark.asyncio
async def test_a_running_job_is_never_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent process is starting but has not joined: a second dispatch would
    run two interviews in one room."""
    fake = _use(
        monkeypatch,
        _FakeLiveKit(dispatches=[_dispatch("d1", job_status=api.JobStatus.JS_RUNNING)]),
    )
    assert await launcher.ensure_interviewer("r") is launcher.DispatchOutcome.STARTING
    assert fake.created == [] and fake.deleted == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "job_status", [None, api.JobStatus.JS_PENDING, api.JobStatus.JS_FAILED]
)
async def test_a_dead_dispatch_is_replaced_not_stacked(
    monkeypatch: pytest.MonkeyPatch, job_status: Any
) -> None:
    fake = _use(monkeypatch, _FakeLiveKit(dispatches=[_dispatch("old", job_status=job_status)]))
    assert await launcher.ensure_interviewer("r") is launcher.DispatchOutcome.DISPATCHED
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
    assert await launcher.ensure_interviewer("r") is launcher.DispatchOutcome.DISPATCHED
    assert fake.deleted == []


@pytest.mark.asyncio
async def test_calling_twice_is_idempotent_once_the_job_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use(monkeypatch, _FakeLiveKit())
    assert await launcher.ensure_interviewer("r") is launcher.DispatchOutcome.DISPATCHED
    # LiveKit now reports the dispatch we created, with a running job.
    fake.dispatches = [_dispatch("new", job_status=api.JobStatus.JS_RUNNING)]
    assert await launcher.ensure_interviewer("r") is launcher.DispatchOutcome.STARTING
    assert len(fake.created) == 1


@pytest.mark.asyncio
async def test_other_twirp_errors_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Broken(_FakeLiveKit):
        async def _list_participants(self, req: Any) -> Any:
            raise api.TwirpError(api.TwirpErrorCode.UNAVAILABLE, "down", status=503)

    _use(monkeypatch, _Broken())
    with pytest.raises(api.TwirpError):
        await launcher.ensure_interviewer("r")


@pytest.mark.asyncio
async def test_automatic_mode_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "interview_agent_name", "", raising=False)
    fake = _use(monkeypatch, _FakeLiveKit())
    assert await launcher.ensure_interviewer("r") is launcher.DispatchOutcome.AUTOMATIC
    assert fake.created == []
