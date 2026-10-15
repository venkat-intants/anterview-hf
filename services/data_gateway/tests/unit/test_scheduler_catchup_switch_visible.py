"""``scheduler_catchup_enabled`` must be VISIBLE to the platform owner.

Off, a scheduler window missed while the service slept (retention purge, nightly
watchers) is never replayed. The platform owner's authenticated job-status
output (``GET /admin/scheduled-jobs``) reports the switch so one left off is
seen. No unauthenticated endpoint carries it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


async def _call(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> dict:
    from app.routers import admin_hr as admin

    async def _status(_f: object) -> list[dict]:
        return []

    monkeypatch.setattr(admin, "job_status", _status)
    monkeypatch.setattr(admin, "get_session_factory", lambda: None)
    monkeypatch.setattr(admin.settings, "scheduler_catchup_enabled", enabled, raising=False)
    return await admin.scheduled_jobs(current_user=MagicMock())


@pytest.mark.asyncio
async def test_switch_on_is_reported_as_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    body = await _call(monkeypatch, True)
    (sw,) = body["switches"]
    assert sw["name"] == "scheduler_catchup_enabled"
    assert sw["enabled"] is True and sw["needs_attention"] is False


@pytest.mark.asyncio
async def test_switch_left_off_is_reported_and_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    body = await _call(monkeypatch, False)
    (sw,) = body["switches"]
    assert sw["enabled"] is False and sw["needs_attention"] is True
    assert "not replayed" in sw["effect"]


def test_the_output_is_platform_owner_only() -> None:
    from app.main import app

    (route,) = (r for r in app.routes if getattr(r, "path", "") == "/admin/scheduled-jobs")
    from shared.testing.route_authz import route_guards

    assert route_guards(route).require == (frozenset({"platform_owner"}),)
