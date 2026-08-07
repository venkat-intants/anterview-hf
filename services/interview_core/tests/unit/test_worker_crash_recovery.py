"""Crash-recovery tests for the interview worker (finding RT-4).

Graceful shutdown was already covered by ``test_worker_reliability.py`` — the
drain hook handles tab close, abrupt disconnect and SIGTERM. What was NOT
covered, and what these tests are about, is a HARD kill: OOM, SIGKILL, node
loss. Before the checkpoint machinery the answer count, the transcript and the
close flag lived in process memory only, so such a kill left the session row
'in_progress' forever, the transcript unrecoverable, and no scoring call ever
made — while analytics went on counting the row as a live interview.

Covered here:
  1. Every recorded turn schedules a checkpoint; items that record nothing do not.
  2. A session's answers and transcript survive the process dying (save → new
     InterviewState → restore).
  3. The close/scoring path is single-fire ACROSS processes, not just within one.
  4. The startup reaper marks a stale 'in_progress' session 'abandoned' and
     flushes the transcript the dead worker never wrote — and leaves live,
     already-finalised and unreadable sessions alone.
  5. Redis being down degrades every one of these to the pre-existing behaviour
     and never propagates an error into the interview.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.worker.interview_worker as wk
from app.worker.interview_worker import (
    CHECKPOINT_STALE_AFTER_SECONDS,
    MIN_ANSWERS_TO_SCORE,
    InterviewState,
    claim_close,
    clear_checkpoint,
    load_checkpoint,
    reap_stale_sessions,
    record_conversation_item,
    restore_state_from_checkpoint,
    save_checkpoint,
)

_SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _RedisDownError(Exception):
    """Stand-in for redis.exceptions.ConnectionError — the code catches Exception."""


class _FakeRedis:
    """In-memory stand-in for the redis-py subset the checkpoint code uses.

    ``down=True`` makes every command raise, which is how the "Redis outage must
    not touch the interview" tests are driven.
    """

    def __init__(self, *, down: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.down = down
        self.closed = False

    async def set(
        self, key: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if self.down:
            raise _RedisDownError("connection refused")
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        if self.down:
            raise _RedisDownError("connection refused")
        return self.store.get(key)

    async def delete(self, *keys: str) -> int:
        if self.down:
            raise _RedisDownError("connection refused")
        removed = 0
        for key in keys:
            removed += 1 if self.store.pop(key, None) is not None else 0
        return removed

    def scan_iter(
        self, *, match: str | None = None, count: int | None = None
    ) -> AsyncIterator[str]:
        prefix = (match or "*").rstrip("*")
        keys = [k for k in list(self.store) if k.startswith(prefix)]

        async def _gen() -> AsyncIterator[str]:
            for key in keys:
                yield key

        return _gen()

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_redis() -> Iterator[_FakeRedis]:
    """Install a working in-memory Redis as this process's checkpoint client."""
    original = wk._checkpoint_redis
    client = _FakeRedis()
    wk._checkpoint_redis = client
    try:
        yield client
    finally:
        wk._checkpoint_redis = original


@pytest.fixture()
def dead_redis() -> Iterator[_FakeRedis]:
    """Install a checkpoint client whose every command raises."""
    original = wk._checkpoint_redis
    client = _FakeRedis(down=True)
    wk._checkpoint_redis = client
    try:
        yield client
    finally:
        wk._checkpoint_redis = original


def _chat_message(role: str, text: str | None) -> object:
    from livekit.agents.llm.chat_context import ChatMessage

    msg = MagicMock(spec=ChatMessage)
    msg.role = role
    msg.text_content = text
    return msg


def _answered(state: InterviewState, n: int) -> None:
    """Drive *n* complete question→answer exchanges through the real state."""
    for i in range(1, n + 1):
        state.handle_conversation_item(_chat_message("assistant", f"Question {i}"))
        state.handle_conversation_item(_chat_message("user", f"Answer {i}"))


def _checkpoint_payload(
    *,
    answers: int,
    turns: list[dict[str, str]],
    age_seconds: float,
    ran_for: float = 300.0,
    close_triggered: bool = False,
    session_id: str = _SESSION_ID,
) -> str:
    """A checkpoint last refreshed *age_seconds* ago, *ran_for* seconds into the session."""
    now = datetime.now(tz=UTC)
    return json.dumps(
        {
            "session_id": session_id,
            "updated_at": (now - timedelta(seconds=age_seconds)).isoformat(),
            "started_at": (now - timedelta(seconds=age_seconds + ran_for)).isoformat(),
            "candidate_answer_count": answers,
            "transcript": turns,
            "close_triggered": close_triggered,
        }
    )


# ---------------------------------------------------------------------------
# 1. Recording a turn makes it durable
# ---------------------------------------------------------------------------


def test_every_recorded_turn_schedules_a_checkpoint() -> None:
    """Each item that lands in the transcript must trigger a checkpoint write.

    This is the whole of "the transcript is no longer only in process memory":
    if a turn is credited to the candidate but never checkpointed, a hard kill
    loses it and the reaper has nothing to flush.
    """
    state = InterviewState()
    scheduled: list[int] = []

    for role, text in (("assistant", "Question 1"), ("user", "Answer 1")):
        record_conversation_item(
            _chat_message(role, text),
            state=state,
            schedule_checkpoint=lambda: scheduled.append(1),
        )

    assert len(scheduled) == 2, (
        "both the interviewer question and the candidate answer entered the "
        "transcript, so both must have been checkpointed"
    )


@pytest.mark.parametrize(
    ("role", "text"),
    [("user", ""), ("user", "   "), ("user", None), ("system", "ignored")],
)
def test_items_that_record_nothing_do_not_checkpoint(role: str, text: str | None) -> None:
    """Dropped items must not cost a Redis round-trip — nothing changed."""
    state = InterviewState()
    scheduled: list[int] = []

    record_conversation_item(
        _chat_message(role, text),
        state=state,
        schedule_checkpoint=lambda: scheduled.append(1),
    )

    assert state.transcript == []
    assert scheduled == []


def test_record_conversation_item_still_returns_the_close_signal() -> None:
    """Adding the checkpoint hop must not swallow the 10th-answer close signal."""
    state = InterviewState()
    _answered(state, wk.MAX_CANDIDATE_ANSWERS - 1)

    state.handle_conversation_item(_chat_message("assistant", "Question 10"))
    should_close = record_conversation_item(
        _chat_message("user", "Answer 10"),
        state=state,
        schedule_checkpoint=lambda: None,
    )

    assert should_close is True


# ---------------------------------------------------------------------------
# 2. State survives the process dying
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_answers_and_transcript_survive_a_hard_kill(fake_redis: _FakeRedis) -> None:
    """A restarted worker resumes the interview instead of orphaning it.

    Models the actual RT-4 failure: the worker is SIGKILLed after five answers,
    so nothing on the close path ever ran. The replacement worker must pick the
    session up at answer five with the earlier transcript intact — before this,
    it started the candidate at question one and the first five answers were
    gone for good.
    """
    live = InterviewState()
    _answered(live, 5)
    assert await save_checkpoint(_SESSION_ID, live, started_at=datetime.now(tz=UTC))

    # --- process dies here; a brand-new worker takes the same room ---
    resumed = await restore_state_from_checkpoint(_SESSION_ID)

    assert resumed is not live
    assert resumed.candidate_answer_count == 5
    assert resumed.transcript == live.transcript
    assert resumed.should_score() is True


@pytest.mark.asyncio
async def test_no_checkpoint_starts_a_fresh_interview(fake_redis: _FakeRedis) -> None:
    """The normal case — no prior checkpoint — must behave exactly as before."""
    state = await restore_state_from_checkpoint(_SESSION_ID)

    assert state.candidate_answer_count == 0
    assert state.transcript == []
    assert state.close_triggered is False


@pytest.mark.asyncio
async def test_close_flag_survives_a_kill_during_the_close_path(
    fake_redis: _FakeRedis,
) -> None:
    """A crash mid-close must not look like an interview still waiting for answers."""
    closing = InterviewState()
    _answered(closing, MIN_ANSWERS_TO_SCORE)
    closing.mark_close_triggered()
    await save_checkpoint(_SESSION_ID, closing)

    resumed = await restore_state_from_checkpoint(_SESSION_ID)

    assert resumed.close_triggered is True


@pytest.mark.asyncio
async def test_corrupt_checkpoint_degrades_to_a_fresh_start(
    fake_redis: _FakeRedis,
) -> None:
    """Junk in Redis must never cost a candidate their session.

    A checkpoint can be written by an older build, truncated, or hand-edited
    during an incident. Restoring runs on the path that starts the interview, so
    the only acceptable failure is "resume nothing", never an exception.
    """
    fake_redis.store[wk._checkpoint_key(_SESSION_ID)] = "{not json at all"

    state = await restore_state_from_checkpoint(_SESSION_ID)
    assert state.candidate_answer_count == 0

    fake_redis.store[wk._checkpoint_key(_SESSION_ID)] = json.dumps(
        {"candidate_answer_count": "seven", "transcript": ["not-a-turn", {"role": "user"}]}
    )
    state = await restore_state_from_checkpoint(_SESSION_ID)
    assert state.candidate_answer_count == 0
    assert state.transcript == [], "malformed turns must be dropped, not partially kept"


@pytest.mark.asyncio
async def test_partially_malformed_transcript_keeps_the_good_turns(
    fake_redis: _FakeRedis,
) -> None:
    """Recovery is best-effort: usable turns are kept, unusable ones dropped."""
    fake_redis.store[wk._checkpoint_key(_SESSION_ID)] = json.dumps(
        {
            "candidate_answer_count": 2,
            "transcript": [
                {"role": "ai", "text": "Tell me about yourself."},
                {"missing": "role and text"},
                {"role": "user", "text": "I am a backend engineer."},
            ],
        }
    )

    state = await restore_state_from_checkpoint(_SESSION_ID)

    assert state.candidate_answer_count == 2
    assert state.transcript == [
        {"role": "ai", "text": "Tell me about yourself."},
        {"role": "user", "text": "I am a backend engineer."},
    ]


# ---------------------------------------------------------------------------
# 3. Redis failures never reach the interview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_write_failure_does_not_propagate(
    dead_redis: _FakeRedis,
) -> None:
    """A Redis outage during a checkpoint must be logged and swallowed.

    Same contract as the best-effort transcript flush: an interview must be able
    to run start-to-finish with Redis down. It just does not get crash recovery.
    """
    state = InterviewState()
    _answered(state, 3)

    assert await save_checkpoint(_SESSION_ID, state) is False
    # The interview state itself is untouched — nothing was rolled back.
    assert state.candidate_answer_count == 3


@pytest.mark.asyncio
async def test_checkpoint_read_failure_starts_a_fresh_interview(
    dead_redis: _FakeRedis,
) -> None:
    """With Redis unreachable at startup, resume degrades to the old behaviour."""
    assert await load_checkpoint(_SESSION_ID) is None

    state = await restore_state_from_checkpoint(_SESSION_ID)
    assert state.candidate_answer_count == 0


@pytest.mark.asyncio
async def test_clear_checkpoint_failure_does_not_propagate(
    dead_redis: _FakeRedis,
) -> None:
    """Clearing runs at the very end of the close path — it must never raise."""
    await clear_checkpoint(_SESSION_ID)


@pytest.mark.asyncio
async def test_checkpoint_write_cannot_stall_the_turn_loop(
    fake_redis: _FakeRedis,
) -> None:
    """A hung Redis must time out fast, not hold the interview open.

    p95 turn latency < 2 s is an RFP requirement; the shared client's own 5 s
    socket timeout plus its retry schedule is far above that budget, so the
    checkpoint applies its own tighter bound.
    """

    async def _never_returns(*args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(3600)

    fake_redis.set = _never_returns  # type: ignore[method-assign]

    with patch.object(wk, "_CHECKPOINT_TIMEOUT_SECONDS", 0.01):
        wrote = await save_checkpoint(_SESSION_ID, InterviewState())

    assert wrote is False


# ---------------------------------------------------------------------------
# 4. The close/scoring path is single-fire ACROSS processes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_can_be_claimed_only_once(fake_redis: _FakeRedis) -> None:
    """The durable guard admits exactly one finaliser per session."""
    assert await claim_close(_SESSION_ID) is True
    assert await claim_close(_SESSION_ID) is False


@pytest.mark.asyncio
async def test_a_restarted_worker_does_not_score_the_session_twice(
    fake_redis: _FakeRedis,
) -> None:
    """The in-memory close flag cannot stop a SECOND process — the claim can.

    Simulates the double-fire the finding describes: one worker finalises the
    session and is then replaced (crash, redeploy, reaper). The replacement has
    a fresh InterviewState whose ``close_triggered`` is False, so every
    in-memory guard says "go ahead". Only the durable claim stops the second
    scorecard being generated for one interview.
    """
    scored: list[str] = []

    async def _finalise(state: InterviewState) -> None:
        """The finalise decision as both close paths make it."""
        if state.close_triggered:
            return
        state.mark_close_triggered()
        if not await claim_close(_SESSION_ID):
            return
        scored.append(_SESSION_ID)

    first = InterviewState()
    _answered(first, MIN_ANSWERS_TO_SCORE)
    await _finalise(first)

    second = InterviewState()  # brand-new process, flag back to False
    _answered(second, MIN_ANSWERS_TO_SCORE)
    assert second.close_triggered is False
    await _finalise(second)

    assert scored == [_SESSION_ID], f"scored {len(scored)} times, must be exactly 1"


@pytest.mark.asyncio
async def test_concurrent_close_paths_claim_once(fake_redis: _FakeRedis) -> None:
    """The wall-clock cap and the 10th-answer handler can race — one must lose."""
    results = await asyncio.gather(*(claim_close(_SESSION_ID) for _ in range(5)))

    assert results.count(True) == 1
    assert results.count(False) == 4


@pytest.mark.asyncio
async def test_close_claim_fails_open_when_redis_is_down(
    dead_redis: _FakeRedis,
) -> None:
    """Redis being unavailable must not cost the candidate their scorecard.

    The two failure modes are not symmetric: a skipped close means no scorecard
    and a row wedged 'in_progress' forever, while a duplicated close costs at
    most a repeat scoring request that feedback_billing rejects with 409.
    """
    assert await claim_close(_SESSION_ID) is True


@pytest.mark.asyncio
async def test_clearing_the_checkpoint_keeps_the_close_guard(
    fake_redis: _FakeRedis,
) -> None:
    """Finalising drops the checkpoint but must NOT re-open the scoring door."""
    assert await claim_close(_SESSION_ID) is True
    await clear_checkpoint(_SESSION_ID)

    assert await load_checkpoint(_SESSION_ID) is None
    assert await claim_close(_SESSION_ID) is False


# ---------------------------------------------------------------------------
# 5. Startup reaper
# ---------------------------------------------------------------------------


@contextmanager
def _reaper_env(
    client: _FakeRedis, status: str | None
) -> Iterator[tuple[AsyncMock, AsyncMock]]:
    """Run the reaper against *client* with the DB reporting *status*.

    Yields the (status_update, persist_turns) doubles so each test can assert
    what the sweep actually wrote.
    """
    update = AsyncMock()
    persist = AsyncMock()
    with (
        patch.object(wk, "build_redis_client", lambda url, **kw: client),
        patch.object(wk, "_read_session_status", new=AsyncMock(return_value=status)),
        patch.object(wk, "_update_session_status", new=update),
        patch.object(wk, "_persist_turns", new=persist),
    ):
        yield update, persist


@pytest.mark.asyncio
async def test_reaper_marks_a_stale_in_progress_session_abandoned() -> None:
    """A session whose worker was killed must stop counting as a live interview.

    'abandoned' is the existing status vocabulary; the point of using it is that
    every analytics query already excludes it, whereas a permanently
    'in_progress' row is indistinguishable from an interview happening right now.
    """
    client = _FakeRedis()
    turns = [
        {"role": "ai", "text": "Tell me about yourself."},
        {"role": "user", "text": "I am a backend engineer."},
    ]
    client.store[wk._checkpoint_key(_SESSION_ID)] = _checkpoint_payload(
        answers=1, turns=turns,
        age_seconds=CHECKPOINT_STALE_AFTER_SECONDS + 60, ran_for=240,
    )

    with _reaper_env(client, "in_progress") as (update, persist):
        reaped = await reap_stale_sessions()

    assert reaped == [_SESSION_ID]
    assert update.await_args.args == (_SESSION_ID, "abandoned")
    # The transcript the dead worker never flushed is the only copy there is.
    assert persist.await_args.args[0] == _SESSION_ID
    assert persist.await_args.args[1] == turns
    # duration_seconds is how long the interview actually ran before the worker
    # died — NOT the time until the reaper noticed, which would inflate every
    # crashed session's duration by the whole staleness window and skew the
    # duration analytics the dashboard reports.
    assert update.await_args.kwargs["duration_seconds"] == pytest.approx(240, abs=2)
    # completed_at must agree with that: the session ended when its worker
    # stopped checkpointing, not when this sweep noticed.
    completed_at = update.await_args.kwargs["completed_at"]
    assert (datetime.now(tz=UTC) - completed_at).total_seconds() == pytest.approx(
        CHECKPOINT_STALE_AFTER_SECONDS + 60, abs=5
    )
    # Checkpoint consumed; the client it opened was closed again.
    assert wk._checkpoint_key(_SESSION_ID) not in client.store
    assert client.closed is True


@pytest.mark.asyncio
async def test_reaper_never_touches_a_live_session() -> None:
    """Reaping an interview in progress would cut a candidate off mid-answer."""
    client = _FakeRedis()
    client.store[wk._checkpoint_key(_SESSION_ID)] = _checkpoint_payload(
        answers=3, turns=[{"role": "user", "text": "still talking"}], age_seconds=5
    )

    with _reaper_env(client, "in_progress") as (update, persist):
        reaped = await reap_stale_sessions()

    assert reaped == []
    update.assert_not_awaited()
    persist.assert_not_awaited()
    assert wk._checkpoint_key(_SESSION_ID) in client.store


@pytest.mark.asyncio
async def test_reaper_leaves_an_already_finalised_session_alone() -> None:
    """A session the close path completed must not be rewritten to 'abandoned'."""
    client = _FakeRedis()
    client.store[wk._checkpoint_key(_SESSION_ID)] = _checkpoint_payload(
        answers=10, turns=[{"role": "user", "text": "done"}],
        age_seconds=CHECKPOINT_STALE_AFTER_SECONDS + 60,
    )

    with _reaper_env(client, "completed") as (update, _persist):
        reaped = await reap_stale_sessions()

    assert reaped == []
    update.assert_not_awaited()
    # The stale checkpoint is litter at this point and is cleaned up.
    assert wk._checkpoint_key(_SESSION_ID) not in client.store


@pytest.mark.asyncio
async def test_reaper_keeps_the_checkpoint_when_the_db_will_not_answer() -> None:
    """A DB blip must not destroy the only copy of a crashed session's transcript."""
    client = _FakeRedis()
    client.store[wk._checkpoint_key(_SESSION_ID)] = _checkpoint_payload(
        answers=4, turns=[{"role": "user", "text": "unflushed"}],
        age_seconds=CHECKPOINT_STALE_AFTER_SECONDS + 60,
    )

    with _reaper_env(client, None) as (update, _persist):
        reaped = await reap_stale_sessions()

    assert reaped == []
    update.assert_not_awaited()
    assert wk._checkpoint_key(_SESSION_ID) in client.store, (
        "the checkpoint must survive for the next sweep — deleting it here would "
        "lose the transcript to a transient DB error"
    )


@pytest.mark.asyncio
async def test_reaper_will_not_finalise_a_session_someone_else_claimed() -> None:
    """The reaper is bound by the same single-fire guard as the close paths."""
    client = _FakeRedis()
    client.store[wk._close_guard_key(_SESSION_ID)] = "already-closed"
    client.store[wk._checkpoint_key(_SESSION_ID)] = _checkpoint_payload(
        answers=6, turns=[{"role": "user", "text": "scored already"}],
        age_seconds=CHECKPOINT_STALE_AFTER_SECONDS + 60,
    )

    with _reaper_env(client, "in_progress") as (update, persist):
        reaped = await reap_stale_sessions()

    assert reaped == []
    update.assert_not_awaited()
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaper_discards_an_unparseable_checkpoint() -> None:
    """Junk keys must be cleaned up, not re-examined on every worker start."""
    client = _FakeRedis()
    client.store[wk._checkpoint_key(_SESSION_ID)] = "<<corrupt>>"

    with _reaper_env(client, "in_progress") as (update, _persist):
        reaped = await reap_stale_sessions()

    assert reaped == []
    update.assert_not_awaited()
    assert client.store == {}


@pytest.mark.asyncio
async def test_reaper_survives_a_redis_outage_at_startup() -> None:
    """A worker must still boot and take interviews when the sweep cannot run."""
    def _boom(url: str, **kwargs: Any) -> Any:
        raise _RedisDownError("no route to host")

    with patch.object(wk, "build_redis_client", _boom):
        assert await reap_stale_sessions() == []


@pytest.mark.asyncio
async def test_reaper_continues_after_one_bad_session() -> None:
    """One session that cannot be finalised must not abort the whole sweep."""
    other = "11111111-2222-3333-4444-555555555555"
    client = _FakeRedis()
    for sid in (other, _SESSION_ID):
        client.store[wk._checkpoint_key(sid)] = _checkpoint_payload(
            answers=2, turns=[{"role": "user", "text": "hi"}],
            age_seconds=CHECKPOINT_STALE_AFTER_SECONDS + 60, session_id=sid,
        )

    update = AsyncMock()

    async def _status(session_id: str) -> str:
        if session_id == other:
            raise _RedisDownError("boom on the first session")
        return "in_progress"

    with (
        patch.object(wk, "build_redis_client", lambda url, **kw: client),
        patch.object(wk, "_read_session_status", new=_status),
        patch.object(wk, "_update_session_status", new=update),
        patch.object(wk, "_persist_turns", new=AsyncMock()),
    ):
        reaped = await reap_stale_sessions()

    assert reaped == [_SESSION_ID]
    assert update.await_args.args == (_SESSION_ID, "abandoned")


def test_stale_threshold_is_longer_than_any_legal_session() -> None:
    """The reaper must be incapable of firing on an interview that is still running.

    Every session ends at SESSION_WALL_CLOCK_CAP_SECONDS at the latest, so the
    staleness window has to sit above that with room for the close path (final
    scoring call plus room delete) to finish.
    """
    assert CHECKPOINT_STALE_AFTER_SECONDS > wk.SESSION_WALL_CLOCK_CAP_SECONDS
    assert wk.SESSION_CHECKPOINT_TTL_SECONDS > CHECKPOINT_STALE_AFTER_SECONDS, (
        "a checkpoint must outlive its own staleness window, or it would expire "
        "before the reaper could ever see it as stale"
    )
