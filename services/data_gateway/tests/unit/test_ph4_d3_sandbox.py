"""PH4-D3 — the analysis sandbox, driven for real.

Every test here spawns genuine processes rather than mocking the sandbox,
because the defects a security review found were all about what processes
actually do: a dead worker left a shared pool broken for every tenant, a
timed-out worker kept running, and resetting the pool cancelled other
callers' work -- which escaped ``except Exception`` and ended the whole
reminder sweep. Mocks cannot show any of that.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app import code_sandbox
from app.code_sandbox import AnalysisFailedError, AnalysisTimeoutError, run_isolated
from tests.unit import _sandbox_helpers as h


@pytest.mark.asyncio
async def test_a_normal_analysis_returns_its_result() -> None:
    assert await run_isolated(h.doubled, 21, timeout=30) == 42


@pytest.mark.asyncio
async def test_a_worker_that_dies_does_not_break_the_next_analysis() -> None:
    """HIGH-1: an abrupt exit used to leave the shared pool broken, and every
    later submission -- for every tenant -- became a permanent 'failed' until a
    restart. Each task now has its own process, so the next one is unaffected."""
    with pytest.raises(AnalysisFailedError) as exc:
        await run_isolated(h.dies_without_answering, timeout=30)
    assert exc.value.error_class.startswith("WorkerExited")

    assert await run_isolated(h.doubled, 5, timeout=30) == 10


@pytest.mark.asyncio
async def test_a_timeout_kills_the_process_it_timed_out() -> None:
    """HIGH-1: ``shutdown(wait=False)`` on a pool never stopped a running
    worker, so a timed-out analysis kept consuming CPU unbounded."""
    spawned: list[object] = []
    real_process = code_sandbox._MP_CONTEXT.Process

    def recording_process(*args: object, **kwargs: object) -> object:
        proc = real_process(*args, **kwargs)
        spawned.append(proc)
        return proc

    code_sandbox._MP_CONTEXT.Process = recording_process  # type: ignore[method-assign]
    try:
        with pytest.raises(AnalysisTimeoutError):
            await run_isolated(h.never_finishes, timeout=1.5)
    finally:
        code_sandbox._MP_CONTEXT.Process = real_process  # type: ignore[method-assign]

    assert spawned, "the sandbox never started a process"
    assert not spawned[0].is_alive(), "the timed-out analysis is still running"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_one_analysis_timing_out_does_not_cancel_another() -> None:
    """HIGH-2: resetting the shared pool cancelled every queued future, which
    surfaced in an unrelated caller as ``asyncio.CancelledError`` -- a
    BaseException that escaped ``except Exception`` and stopped the reminder
    sweep. Now a timeout ends only its own process."""
    slow = asyncio.create_task(run_isolated(h.never_finishes, timeout=1.5))
    healthy = asyncio.create_task(run_isolated(h.doubled, 8, timeout=60))

    with pytest.raises(AnalysisTimeoutError):
        await slow
    # Not CancelledError, not a failure -- just the right answer.
    assert await healthy == 16


@pytest.mark.asyncio
async def test_time_spent_queued_does_not_count_against_the_timeout() -> None:
    """HIGH-1: the clock used to start at submit, so a healthy task queued
    behind a slow one timed out and was stored as a permanent 'failed'."""
    slow = asyncio.create_task(run_isolated(h.never_finishes, timeout=2))
    # Queued behind `slow` (concurrency is 1) for ~2s, then needs well under
    # its own 2s -- it must succeed.
    queued = asyncio.create_task(run_isolated(h.doubled, 3, timeout=2))

    with pytest.raises(AnalysisTimeoutError):
        await slow
    assert await queued == 6


@pytest.mark.asyncio
async def test_a_failure_reports_the_class_and_never_the_message() -> None:
    """The message could quote candidate source; only the class name crosses
    back, and that is all that is ever stored."""
    with pytest.raises(AnalysisFailedError) as exc:
        await run_isolated(h.raises_with_a_message, timeout=30)
    assert exc.value.error_class == "ValueError"
    assert "secret_candidate_function" not in str(exc.value)


@pytest.mark.asyncio
async def test_the_child_starts_with_an_empty_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """LOW-1: the child used to inherit DATABASE_URL and every API key. It
    clears its environment before it touches the input."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://should-not-reach-the-child")
    env = await run_isolated(h.reports_its_environment, timeout=30)
    assert "DATABASE_URL" not in env
    assert os.environ.get("DATABASE_URL")  # the parent's own environment is untouched
