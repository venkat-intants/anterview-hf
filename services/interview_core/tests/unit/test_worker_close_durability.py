"""The close path must not discard the checkpoint unless the record was written.

The checkpoint is the only durable copy of the transcript. ``_update_session_status``
and ``_persist_turns`` swallowed every exception and the checkpoint was cleared
unconditionally, so one DB blip at close lost the transcript for good. Also covered
here: the scorer call's timeout/retry policy, the bounded closing ``say()``, and the
close task's done-callback.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

import app.worker.interview_worker as wk
from app.worker import close_path as cp
from app.worker import session_store as store
from app.worker.interview_worker import (
    CHECKPOINT_STALE_AFTER_SECONDS,
    InterviewJob,
    InterviewState,
    claim_close,
    load_checkpoint,
    reap_stale_sessions,
    save_checkpoint,
)
from tests.unit.test_worker_crash_recovery import (
    _SESSION_ID,
    _checkpoint_payload,
    _FakeRedis,
    _reaper_env,
    fake_redis,  # noqa: F401 — fixture
)

_TURNS = [
    {"role": "ai", "text": "Tell me about yourself."},
    {"role": "user", "text": "I am a welder."},
]


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "_DB_WRITE_BACKOFF_SECONDS", (0.0, 0.0))


def _factory(commit_effects: list[Any], calls: list[int] | None = None) -> Any:
    """Session factory whose Nth commit raises/returns per *commit_effects*."""
    seq = iter(commit_effects)

    @asynccontextmanager
    async def _cm() -> Any:
        db = AsyncMock()
        db.add_all = MagicMock()

        async def _commit() -> None:
            if calls is not None:
                calls.append(1)
            effect = next(seq)
            if isinstance(effect, BaseException):
                raise effect

        db.commit = _commit
        yield db

    return MagicMock(side_effect=lambda: _cm())


# ---------------------------------------------------------------------------
# session_store: report success, retry with backoff
# ---------------------------------------------------------------------------
async def test_persist_turns_retries_then_reports_success() -> None:
    calls: list[int] = []
    factory = _factory([RuntimeError("blip"), RuntimeError("blip"), None], calls)
    with patch("app.database.init_engine"), patch(
        "app.database.get_session_factory", return_value=factory
    ):
        ok = await store._persist_turns(_SESSION_ID, _TURNS)
    assert ok is True
    assert len(calls) == 3


async def test_persist_turns_reports_failure_after_exhausting_attempts() -> None:
    calls: list[int] = []
    factory = _factory([RuntimeError("down")] * 3, calls)
    with patch("app.database.init_engine"), patch(
        "app.database.get_session_factory", return_value=factory
    ):
        ok = await store._persist_turns(_SESSION_ID, _TURNS)
    assert ok is False
    assert len(calls) == 3


async def test_persist_turns_unique_violation_means_already_stored() -> None:
    boom = IntegrityError("INSERT", {}, Exception("uq_turns_session_turn_number"))
    factory = _factory([boom])
    with patch("app.database.init_engine"), patch(
        "app.database.get_session_factory", return_value=factory
    ):
        assert await store._persist_turns(_SESSION_ID, _TURNS) is True


async def test_persist_turns_empty_is_vacuously_true_and_bad_uuid_is_false() -> None:
    assert await store._persist_turns(_SESSION_ID, []) is True
    with patch("app.database.init_engine"):
        assert await store._persist_turns("not-a-uuid", _TURNS) is False


async def test_update_session_status_retries_and_reports() -> None:
    calls: list[int] = []
    ok_factory = _factory([RuntimeError("blip"), None], calls)
    with patch("app.database.init_engine"), patch(
        "app.database.get_session_factory", return_value=ok_factory
    ):
        assert await store._update_session_status(_SESSION_ID, "completed") is True
    assert len(calls) == 2

    bad_factory = _factory([RuntimeError("down")] * 3)
    with patch("app.database.init_engine"), patch(
        "app.database.get_session_factory", return_value=bad_factory
    ):
        assert await store._update_session_status(_SESSION_ID, "completed") is False


# ---------------------------------------------------------------------------
# _write_final_record: turns first, status withheld when they fail
# ---------------------------------------------------------------------------
async def test_status_is_not_written_when_the_transcript_is_not() -> None:
    persist = AsyncMock(return_value=False)
    update = AsyncMock(return_value=True)
    with patch.object(cp, "_persist_turns", persist), patch.object(
        cp, "_update_session_status", update
    ):
        ok = await cp.write_final_record(
            _SESSION_ID, _TURNS, "completed", completed_at=datetime.now(tz=UTC),
            duration_seconds=5,
        )
    assert ok is False
    update.assert_not_awaited()


async def test_final_record_ok_only_when_both_land() -> None:
    with patch.object(cp, "_persist_turns", AsyncMock(return_value=True)), patch.object(
        cp, "_update_session_status", AsyncMock(return_value=False)
    ):
        assert not await cp.write_final_record(
            _SESSION_ID, _TURNS, "completed", completed_at=datetime.now(tz=UTC),
            duration_seconds=5,
        )
    with patch.object(cp, "_persist_turns", AsyncMock(return_value=True)), patch.object(
        cp, "_update_session_status", AsyncMock(return_value=True)
    ):
        assert await cp.write_final_record(
            _SESSION_ID, _TURNS, "completed", completed_at=datetime.now(tz=UTC),
            duration_seconds=5,
        )


# ---------------------------------------------------------------------------
# _on_close / _abrupt_close: checkpoint cleared only on success
# ---------------------------------------------------------------------------
def _fake_job(handle: Any = None) -> Any:
    state = InterviewState()
    state.transcript = list(_TURNS)
    state.candidate_answer_count = 1
    session = SimpleNamespace(
        say=MagicMock(return_value=handle if handle is not None else _done()),
        shutdown=MagicMock(),
    )
    return SimpleNamespace(
        state=state, session_id=_SESSION_ID, session=session, language="en",
        session_started_at=datetime.now(tz=UTC), job_title="Welder",
        experience_level="entry", jd_text="", role_profile=None,
    )


async def _done() -> None:
    return None


@pytest.fixture
def close_env(fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:  # noqa: F811
    monkeypatch.setattr(wk, "should_score_stub", None, raising=False)
    delete_room = AsyncMock()
    lkapi = MagicMock()
    lkapi.room.delete_room = delete_room
    lkapi.aclose = AsyncMock()
    monkeypatch.setattr(wk.lk_api, "LiveKitAPI", lambda **kw: lkapi)
    post = AsyncMock()
    monkeypatch.setattr(wk, "_post_score", post)
    return SimpleNamespace(redis=fake_redis, post=post)


async def _run_close(job: Any, *, abrupt: bool = False) -> None:
    await save_checkpoint(job.session_id, job.state, started_at=job.session_started_at)
    assert await load_checkpoint(job.session_id) is not None
    if abrupt:
        await InterviewJob._abrupt_close(job)
    else:
        await InterviewJob._on_close(job, timed_out=False)


@pytest.mark.parametrize("abrupt", [False, True])
async def test_checkpoint_cleared_when_the_record_is_written(
    close_env: SimpleNamespace, abrupt: bool
) -> None:
    job = _fake_job()
    with patch.object(cp, "_persist_turns", AsyncMock(return_value=True)), patch.object(
        cp, "_update_session_status", AsyncMock(return_value=True)
    ):
        await _run_close(job, abrupt=abrupt)
    assert await load_checkpoint(_SESSION_ID) is None
    # the guard stays held: that is what keeps a close single-fire
    assert await claim_close(_SESSION_ID) is False


@pytest.mark.parametrize("abrupt", [False, True])
async def test_checkpoint_kept_and_guard_released_when_the_record_fails(
    close_env: SimpleNamespace, abrupt: bool
) -> None:
    job = _fake_job()
    with patch.object(cp, "_persist_turns", AsyncMock(return_value=False)), patch.object(
        cp, "_update_session_status", AsyncMock(return_value=True)
    ) as update:
        await _run_close(job, abrupt=abrupt)
    assert await load_checkpoint(_SESSION_ID) is not None  # transcript still recoverable
    update.assert_not_awaited()
    # guard handed back, so the reaper does not read it as "already finalised"
    assert await claim_close(_SESSION_ID) is True


async def test_status_failure_also_keeps_the_checkpoint(close_env: SimpleNamespace) -> None:
    job = _fake_job()
    with patch.object(cp, "_persist_turns", AsyncMock(return_value=True)), patch.object(
        cp, "_update_session_status", AsyncMock(return_value=False)
    ):
        await _run_close(job)
    assert await load_checkpoint(_SESSION_ID) is not None


# ---------------------------------------------------------------------------
# reaper: a failed write keeps the checkpoint and gives the claim back
# ---------------------------------------------------------------------------
async def test_reaper_keeps_checkpoint_when_turns_cannot_be_written() -> None:
    client = _FakeRedis()
    key = wk._checkpoint_key(_SESSION_ID)
    client.store[key] = _checkpoint_payload(
        answers=1, turns=_TURNS, age_seconds=CHECKPOINT_STALE_AFTER_SECONDS + 60
    )
    with _reaper_env(client, "in_progress") as (update, persist):
        persist.return_value = False
        reaped = await reap_stale_sessions()
    assert reaped == []
    assert key in client.store
    update.assert_not_awaited()
    assert wk._close_guard_key(_SESSION_ID) not in client.store  # next sweep can claim


async def test_reaper_keeps_checkpoint_when_status_cannot_be_written() -> None:
    client = _FakeRedis()
    key = wk._checkpoint_key(_SESSION_ID)
    client.store[key] = _checkpoint_payload(
        answers=1, turns=_TURNS, age_seconds=CHECKPOINT_STALE_AFTER_SECONDS + 60
    )
    with _reaper_env(client, "in_progress") as (update, persist):
        persist.return_value = True
        update.return_value = False
        reaped = await reap_stale_sessions()
    assert reaped == []
    assert key in client.store
    assert wk._close_guard_key(_SESSION_ID) not in client.store


# ---------------------------------------------------------------------------
# (a) scorer call: timeout above the scorer's worst case; never retry a timeout
# ---------------------------------------------------------------------------
def test_score_timeout_exceeds_the_scorers_worst_case() -> None:
    # 4 attempts x 60 s + 1+2+4 s backoff = 247 s
    assert wk._SCORE_TIMEOUT_SECONDS >= 250


async def _score(monkeypatch: pytest.MonkeyPatch, post: AsyncMock) -> None:
    monkeypatch.setattr(wk.settings, "feedback_billing_url", "http://localhost:9999")
    monkeypatch.setattr(wk, "_mint_service_jwt", lambda: "tok")
    monkeypatch.setattr(wk.asyncio, "sleep", AsyncMock())
    with patch("httpx.AsyncClient.post", post):
        await wk._post_score(
            session_id="s", job_title="t", experience_level="entry", language="en",
            jd_text="", transcript=_TURNS,
        )


async def test_a_timeout_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    post = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
    await _score(monkeypatch, post)
    assert post.await_count == 1


async def test_a_fast_failure_is_still_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    await _score(monkeypatch, post)
    assert post.await_count == 2


# ---------------------------------------------------------------------------
# (c) bounded closing say(); close task failures are logged
# ---------------------------------------------------------------------------
async def test_a_stalled_closing_say_does_not_hang_the_close(
    close_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cp, "CLOSING_SAY_TIMEOUT_SECONDS", 0.05)

    async def never() -> None:
        await asyncio.Event().wait()

    job = _fake_job(handle=never())
    with patch.object(cp, "_persist_turns", AsyncMock(return_value=True)), patch.object(
        cp, "_update_session_status", AsyncMock(return_value=True)
    ):
        await asyncio.wait_for(_run_close(job), timeout=5)
    job.session.shutdown.assert_called_once()  # got past the say() to the rest of the close
    assert await load_checkpoint(_SESSION_ID) is None


async def test_close_task_exception_is_logged_not_dropped() -> None:
    async def boom() -> None:
        raise RuntimeError("close blew up")

    task = asyncio.ensure_future(boom())
    task.add_done_callback(cp.log_task_failure)
    with patch.object(cp, "logger") as lg:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert any("background_task_failed" in str(c.args[0]) for c in lg.error.call_args_list)


def test_normal_close_task_gets_the_done_callback() -> None:
    import inspect

    src = inspect.getsource(InterviewJob)
    assert "self.close_task.add_done_callback(close_path.log_task_failure)" in src
