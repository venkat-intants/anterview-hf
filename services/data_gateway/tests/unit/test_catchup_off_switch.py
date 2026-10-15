"""The catch-up loop must be switchable off, and must not write when nothing ran.

This project already lost a month of serverless-database compute to pollers: the
unit that is billed is a wake-up, not a query. The catch-up loop ticks every 15
minutes and used to DELETE-prune on every tick whether or not any job ran.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

import app.scheduling as sch
from app import main
from app.config import Settings


def test_catchup_flag_defaults_on_so_behaviour_is_unchanged() -> None:
    assert Settings.model_fields["scheduler_catchup_enabled"].default is True


def test_lifespan_only_starts_the_loop_when_enabled() -> None:
    src = inspect.getsource(main.lifespan)
    assert "if settings.scheduler_catchup_enabled:" in src
    assert "start_catchup(_factory, _catchup_jobs)" in src  # asserted elsewhere too
    guard = src.index("if settings.scheduler_catchup_enabled:")
    assert guard < src.index("start_catchup(_factory, _catchup_jobs)")


async def _one_tick(monkeypatch: pytest.MonkeyPatch, *, ran: int) -> AsyncMock:
    prune = AsyncMock()
    monkeypatch.setattr(sch, "run_overdue_jobs_on_startup", AsyncMock(return_value=ran))
    monkeypatch.setattr(sch, "_prune_history", prune)

    sleeps = 0

    async def fake_sleep(_s: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 2:  # first = boot delay, second = after the first tick
            raise asyncio.CancelledError

    monkeypatch.setattr(sch.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await sch._catchup_loop(object(), [], timedelta(minutes=15))  # type: ignore[arg-type]
    return prune


async def test_a_quiet_tick_does_not_prune(monkeypatch: pytest.MonkeyPatch) -> None:
    prune = await _one_tick(monkeypatch, ran=0)
    prune.assert_not_awaited()


async def test_a_tick_that_ran_a_job_prunes(monkeypatch: pytest.MonkeyPatch) -> None:
    prune = await _one_tick(monkeypatch, ran=1)
    prune.assert_awaited_once()


async def test_run_overdue_jobs_reports_how_many_ran() -> None:
    from tests.unit.test_group_a_reliability import _db, _FakeFactory

    seen: list[str] = []

    async def _good() -> None:
        seen.append("x")

    ran = await sch.run_overdue_jobs_on_startup(
        _FakeFactory(_db(scalar=1)),  # type: ignore[arg-type]
        [("a", timedelta(days=1), _good), ("b", timedelta(days=1), _good)],
    )
    assert ran == 2
    ran = await sch.run_overdue_jobs_on_startup(
        _FakeFactory(_db(scalar=None)),  # type: ignore[arg-type]
        [("a", timedelta(days=1), _good)],
    )
    assert ran == 0
