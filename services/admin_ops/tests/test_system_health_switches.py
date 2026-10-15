"""The erasure executor's off-switch must be VISIBLE to the operator.

``erasure_executor_enabled=false`` stops every physical DPDP purge while requests
keep being accepted. A switch that is easy to turn off and invisible once off is
how it gets left off, so the authenticated system-health output reports its
current value. (Admin-only: the route sits behind ``verify_admin_role``, which
tests/test_role_rule_matrix.py pins.)
"""

from __future__ import annotations

import pytest


async def _health(monkeypatch: pytest.MonkeyPatch, *, enabled: bool):  # type: ignore[no-untyped-def]
    from app.routers import system

    async def _ok(*_a: object, **_k: object) -> dict[str, object]:
        return {"ok": True}

    async def _ping(_c: object, name: str, _b: str) -> system.ServiceHealth:
        return system.ServiceHealth(name=name, kind="service", status="operational")

    monkeypatch.setattr(system, "_check_postgres", _ok)
    monkeypatch.setattr(system, "_check_redis", _ok)
    monkeypatch.setattr(system, "_ping", _ping)
    monkeypatch.setattr(system.settings, "erasure_executor_enabled", enabled, raising=False)
    return await system.system_health()


@pytest.mark.asyncio
async def test_switch_on_is_reported_as_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    out = await _health(monkeypatch, enabled=True)
    sw = {s.name: s for s in out.switches}
    assert sw["erasure_executor_enabled"].enabled is True
    assert sw["erasure_executor_enabled"].needs_attention is False


@pytest.mark.asyncio
async def test_switch_left_off_is_reported_and_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    out = await _health(monkeypatch, enabled=False)
    sw = {s.name: s for s in out.switches}
    assert sw["erasure_executor_enabled"].enabled is False
    assert sw["erasure_executor_enabled"].needs_attention is True
    assert "retention window" in sw["erasure_executor_enabled"].effect
    # reachability is a separate statement and is not silently rewritten
    assert out.overall == "operational"


def test_the_route_stays_admin_only_and_the_output_is_not_a_new_public_endpoint() -> None:
    from app.main import app

    paths = [r.path for r in app.routes if "erasure_executor" in getattr(r, "path", "")]
    assert paths == []
    health = [r for r in app.routes if getattr(r, "path", "") == "/admin/system/health"]
    assert len(health) == 1
    deps = {getattr(d.call, "__name__", "") for d in health[0].dependant.dependencies}  # type: ignore[attr-defined]
    assert "verify_admin_role" in deps
