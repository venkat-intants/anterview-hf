"""InterviewJob — the lifecycle object extracted from ``entrypoint()`` (IC-3, IC-4).

``entrypoint()`` was a 599-line function with nine closures over shared mutable
locals, and its correctness depended on the order those locals were bound in.
That constraint was written down only in prose comments, so nothing failed when
it was broken — and it had been broken once, on the normal close path (IC-3).

These tests hold down the two properties the extraction exists to make
enforceable:

  1. **Every task the worker creates is held by a strong reference.** asyncio
     keeps only weak references to tasks, so a discarded handle can be collected
     mid-flight. On the normal close path that costs the closing line, the status
     write, the transcript, the scorecard and the room deletion — the candidate
     sits connected to a dead room until the reaper sweeps it. The AST test
     below pins the property for the whole module, not just the call site that
     was wrong, because the same mistake is one keystroke away anywhere else.

  2. **The phase order in ``run()`` is the contract.** Three orderings are
     load-bearing (connect before context, wiring before avatar, avatar before
     session start) and each has a comment saying so. The order test is what
     makes those comments checkable.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.worker.interview_worker as wk
from app.worker.interview_worker import (
    MAX_CANDIDATE_ANSWERS,
    InterviewJob,
    InterviewState,
)

_WORKER_SRC = (
    pathlib.Path(__file__).resolve().parents[2] / "app" / "worker" / "interview_worker.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chat_message(role: str, text: str) -> Any:
    """A minimal stand-in for livekit's ChatMessage, as InterviewState sees it."""
    from livekit.agents.llm.chat_context import ChatMessage

    msg = MagicMock(spec=ChatMessage)
    msg.role = role
    msg.text_content = text
    msg.interrupted = False
    return msg


def _item_event(role: str, text: str) -> Any:
    event = MagicMock()
    event.item = _chat_message(role, text)
    return event


def _job(session_id: str = "room-1") -> InterviewJob:
    """An InterviewJob with a mock JobContext, wired far enough to drive handlers."""
    ctx = MagicMock()
    ctx.connect = AsyncMock()
    job = InterviewJob(ctx)
    job.session_id = session_id
    job.state = InterviewState()
    job.session = MagicMock()
    return job


# ---------------------------------------------------------------------------
# IC-3 — the normal close task must be held, not dropped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_final_answer_close_task_is_held_on_the_job() -> None:
    """The close scheduled after the last answer must survive as a strong ref.

    This is the NORMAL end of every interview. Before IC-3 the handle was
    discarded: ``asyncio.create_task(_on_close(timed_out=False))``.
    """
    job = _job()
    closes: list[bool] = []

    async def fake_on_close(*, timed_out: bool, consent_withdrawn: bool = False) -> None:
        closes.append(timed_out)

    with (
        patch.object(job, "_on_close", fake_on_close),
        patch.object(wk, "save_checkpoint", new=AsyncMock(return_value=True)),
    ):
        # Alternate candidate/interviewer turns: consecutive user fragments
        # collapse into one answer, so the interviewer must speak between them.
        for i in range(MAX_CANDIDATE_ANSWERS):
            job._on_conversation_item_added(_item_event("user", f"answer {i}"))
            job._on_conversation_item_added(_item_event("assistant", f"question {i}"))

        assert job.close_task is not None, (
            "the close task after the final answer was not retained on the job — "
            "asyncio references tasks only weakly, so this is the IC-3 regression"
        )
        await job.close_task

    assert closes == [False], "the held task must actually run the warm close"


@pytest.mark.asyncio
async def test_close_is_not_scheduled_before_the_final_answer() -> None:
    """No close task exists while the interview is still running."""
    job = _job()
    with patch.object(wk, "save_checkpoint", new=AsyncMock(return_value=True)):
        for i in range(MAX_CANDIDATE_ANSWERS - 1):
            job._on_conversation_item_added(_item_event("user", f"answer {i}"))
            job._on_conversation_item_added(_item_event("assistant", f"question {i}"))
    assert job.close_task is None


def test_every_task_created_in_the_worker_has_its_handle_bound() -> None:
    """No ``create_task``/``ensure_future`` result may be thrown away. IC-3, module-wide.

    Asserted against the source because the hazard is invisible at runtime:
    dropping the handle usually works, and fails only under memory pressure at
    exactly the wrong moment. A bound handle can be an attribute, a local, a
    dict slot, an ``await``, or an argument to something that keeps it — what is
    forbidden is a bare expression statement, which is precisely the shape the
    IC-3 defect had.
    """
    tree = ast.parse(_WORKER_SRC.read_text(encoding="utf-8"))

    def _is_task_factory(call: ast.Call) -> bool:
        func = call.func
        return (
            isinstance(func, ast.Attribute)
            and func.attr in ("create_task", "ensure_future")
            and isinstance(func.value, ast.Name)
            and func.value.id == "asyncio"
        )

    discarded = [
        node.value.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and _is_task_factory(node.value)
    ]
    assert not discarded, (
        f"asyncio task handle discarded at line(s) {discarded} of "
        f"{_WORKER_SRC.name}. asyncio holds only WEAK references to tasks: an "
        "unheld task can be garbage-collected mid-flight. Bind it to an "
        "InterviewJob attribute (which is what the class is for) or await it."
    )


# ---------------------------------------------------------------------------
# IC-4 — the phase order in run() is the contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_executes_phases_in_the_documented_order() -> None:
    """Pin the ordering that used to live only in comments inside entrypoint().

    Three of these are load-bearing rather than tidy:
      • ctx.connect() before _resolve_context — rtc.Room.name is empty until the
        room info arrives, and it IS the session_id;
      • _wire_lifecycle before _start_avatar — a "close" fired during avatar
        startup must find its handler and its cap task;
      • _start_avatar before _start_interview — avatar.start() before
        session.start(), or the avatar never publishes video.
    """
    job = _job()
    calls: list[str] = []

    def _sync(name: str) -> Any:
        return lambda *a, **k: calls.append(name)

    def _async(name: str) -> Any:
        async def _run(*a: Any, **k: Any) -> None:
            calls.append(name)

        return _run

    job.ctx.connect = _async("connect")
    for name in (
        "_admit",
        "_resolve_context",
        "_start_avatar",
        "_start_interview",
        "_start_consent_watchdog",
    ):
        setattr(job, name, _async(name))
    for name in ("_build_agent_session", "_wire_lifecycle"):
        setattr(job, name, _sync(name))

    await job.run()

    assert calls == [
        "_admit",
        "connect",
        "_resolve_context",
        "_build_agent_session",
        "_wire_lifecycle",
        "_start_avatar",
        "_start_interview",
        "_start_consent_watchdog",
    ], f"run() phase order changed: {calls}"


@pytest.mark.asyncio
async def test_entrypoint_delegates_to_interview_job() -> None:
    """entrypoint() stays the exported name the deployment binds to."""
    ctx = MagicMock()
    with patch.object(wk.InterviewJob, "run", new=AsyncMock()) as run_mock:
        await wk.entrypoint(ctx)
    run_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# IC-4 — attributes dissolve the binding-order hazard
# ---------------------------------------------------------------------------


def test_session_close_before_wiring_does_not_raise() -> None:
    """A "close" arriving before _wire_lifecycle must be a no-op, not a crash.

    The old body carried a comment about a fixed UnboundLocalError here: the
    "close" handler read ``cap_task``, a local that might not be bound yet. The
    attribute defaults to None, so the hazard is now structural rather than
    ordering-dependent — and the ordering in _wire_lifecycle is kept anyway.
    """
    job = _job()
    assert job.cap_task is None
    assert job.consent_task is None

    job.state.mark_close_triggered()  # already closing: no teardown to schedule
    job._on_session_close(MagicMock())  # must not raise

    assert job.teardown_task is None


@pytest.mark.asyncio
async def test_session_close_cancels_background_tasks_and_holds_teardown() -> None:
    """The abrupt path cancels the cap + watchdog and retains the real teardown task."""
    job = _job()

    async def _forever() -> None:
        await asyncio.sleep(3600)

    job.cap_task = asyncio.create_task(_forever())
    job.consent_task = asyncio.create_task(_forever())

    ran = asyncio.Event()

    async def fake_abrupt_close() -> None:
        ran.set()

    with patch.object(job, "_abrupt_close", fake_abrupt_close):
        job._on_session_close(MagicMock())
        assert job.teardown_task is not None, (
            "the abrupt-close task must be held on the job, not dropped"
        )
        await job.teardown_task

    assert ran.is_set()
    assert job.cap_task.cancelled() or job.cap_task.cancelling()
    assert job.consent_task.cancelled() or job.consent_task.cancelling()


@pytest.mark.asyncio
async def test_checkpoint_soon_tracks_then_releases_the_write() -> None:
    """In-flight checkpoint writes are held while running and released after."""
    job = _job()
    with patch.object(wk, "save_checkpoint", new=AsyncMock(return_value=True)) as saver:
        job._checkpoint_soon()
        assert len(job.checkpoint_tasks) == 1, (
            "the checkpoint write must be held while in flight"
        )
        await asyncio.gather(*job.checkpoint_tasks)
        # done_callback runs on the next loop pass.
        await asyncio.sleep(0)
    assert job.checkpoint_tasks == set(), "completed writes must not accumulate"
    saver.assert_awaited_once()


@pytest.mark.asyncio
async def test_wall_clock_cap_closes_with_timed_out_true() -> None:
    """The safety cap fires the close path flagged as a timeout."""
    job = _job()
    seen: list[bool] = []

    async def fake_on_close(*, timed_out: bool, consent_withdrawn: bool = False) -> None:
        seen.append(timed_out)

    with (
        patch.object(job, "_on_close", fake_on_close),
        patch.object(wk.asyncio, "sleep", new=AsyncMock()),
    ):
        await job._wall_clock_cap()

    assert seen == [True]


@pytest.mark.asyncio
async def test_wall_clock_cap_is_silent_when_close_already_ran() -> None:
    """No double close: the cap defers to whichever path closed first."""
    job = _job()
    job.state.mark_close_triggered()
    on_close = AsyncMock()

    with (
        patch.object(job, "_on_close", on_close),
        patch.object(wk.asyncio, "sleep", new=AsyncMock()),
    ):
        await job._wall_clock_cap()

    on_close.assert_not_awaited()


@pytest.mark.asyncio
async def test_admit_registers_the_decrement_before_anything_can_fail() -> None:
    """The counter release is registered on entry so every exit path frees the slot."""
    job = _job()
    with patch.object(wk, "_publish_capacity", new=AsyncMock()):
        before = wk._active_jobs
        try:
            await job._admit()
            assert wk._active_jobs == before + 1
            job.ctx.add_shutdown_callback.assert_called_once_with(
                job._decrement_job_counter
            )
            await job._decrement_job_counter()
            assert wk._active_jobs == before
        finally:
            wk._active_jobs = before
