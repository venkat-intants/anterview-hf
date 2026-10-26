"""ERASURE_EXECUTOR_ENABLED: the poll can be switched off; the default is unchanged.

Slowing a poller does not stop a serverless Postgres being woken, only not
polling does. No database or Redis: the lifespan's collaborators are patched.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app import main
from app.config import Settings


def test_default_is_enabled_so_behaviour_is_unchanged() -> None:
    assert Settings.model_fields["erasure_executor_enabled"].default is True


async def _run_lifespan(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> tuple[AsyncMock, object]:
    monkeypatch.setattr(main.settings, "erasure_executor_enabled", enabled)
    ticks = AsyncMock()

    async def fake_task() -> None:
        await ticks()
        await asyncio.sleep(3600)

    with (
        patch.object(main, "init_engine"),
        patch.object(main, "init_redis"),
        patch.object(main, "get_redis", return_value=object()),
        patch.object(main, "verify_redis_connection", new=AsyncMock(return_value=True)),
        patch.object(main, "dispose_engine", new=AsyncMock()),
        patch.object(main, "close_redis", new=AsyncMock()),
        patch.object(main, "_instrumented_erasure_task", fake_task),
    ):
        async with main.lifespan(main.app):
            await asyncio.sleep(0)
            task = main.app.state.erasure_task
    return ticks, task


async def test_enabled_starts_the_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks, task = await _run_lifespan(monkeypatch, True)
    assert task is not None
    ticks.assert_awaited_once()


async def test_disabled_never_starts_it_and_shuts_down_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks, task = await _run_lifespan(monkeypatch, False)
    assert task is None
    ticks.assert_not_awaited()
