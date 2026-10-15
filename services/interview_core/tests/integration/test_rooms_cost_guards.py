"""M3: per-user rate limit + one running interview per user on the costly routes.

``POST /api/rooms/{id}/token`` dispatches a worker (LLM + speech + avatar) and
``POST /api/sessions`` feeds it; neither had any limit. Redis is replaced by an
in-memory fake patched into ``app.rate_limit`` (no live Redis); the DB is the
same substring-mocked session the rooms suite uses.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.config import settings
from app.routers.rooms import _has_other_active_session
from tests.integration.test_rooms_router import (  # noqa: F401 - fixtures + helpers
    _livekit_configured,
    _patch_db,
    _post_token,
    _token,
    client,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expiries: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expiries[key] = seconds


# ----------------------------------------------------------------- the limiter itself
@pytest.mark.asyncio
async def test_limiter_allows_up_to_the_cap_then_429(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.rate_limit as rl

    fake = _FakeRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: fake)
    for _ in range(3):
        await rl.enforce_user_rate_limit("b", "u1", 3)
    with pytest.raises(HTTPException) as exc:
        await rl.enforce_user_rate_limit("b", "u1", 3)
    assert exc.value.status_code == 429
    assert fake.expiries["rl:b:u1"] == 60
    # Another user is unaffected.
    await rl.enforce_user_rate_limit("b", "u2", 3)


@pytest.mark.asyncio
async def test_limiter_fails_open_when_redis_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.rate_limit as rl

    def _boom() -> Any:
        raise ConnectionError("redis down")

    monkeypatch.setattr(rl, "get_redis", _boom)
    await rl.enforce_user_rate_limit("b", "u1", 1)  # no exception: fail OPEN


@pytest.mark.asyncio
async def test_limiter_is_disabled_by_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.rate_limit as rl

    fake = _FakeRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: fake)
    for _ in range(50):
        await rl.enforce_user_rate_limit("b", "u1", 0)
    assert fake.counts == {}


# --------------------------------------------------------------- wired into the routes
@pytest.mark.asyncio
async def test_room_token_route_is_429_after_the_per_user_cap(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.rate_limit as rl

    shared_fake = _FakeRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: shared_fake)
    monkeypatch.setattr(settings, "room_token_per_minute", 2, raising=False)
    uid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    token = _token(uid)
    codes = []
    for _ in range(3):
        override = _patch_db(session_user_id=uid, session_status="created", has_consent=True)
        codes.append((await _post_token(client, sid, token, override=override)).status_code)
    assert codes == [200, 200, 429]


def test_defaults_are_generous_and_env_configurable() -> None:
    fields = type(settings).model_fields
    assert fields["room_token_per_minute"].default >= 10
    assert fields["session_create_per_minute"].default >= 5
    assert fields["max_active_sessions_per_user"].default == 1
    # a reload / dropped-connection retry is ONE call; the defaults sit well above that


# ------------------------------------------------- one running interview per user
@pytest.mark.asyncio
async def test_second_session_is_refused_while_another_is_running(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    dispatch = AsyncMock(return_value=True)
    monkeypatch.setattr(rooms, "dispatch_interview_agent", dispatch)
    uid = str(uuid.uuid4())
    override = _patch_db(
        session_user_id=uid, session_status="created", has_consent=True, other_active_sessions=1
    )
    resp = await _post_token(client, str(uuid.uuid4()), _token(uid), override=override)
    assert resp.status_code == 409, resp.text
    assert "in progress" in resp.json()["detail"].lower()
    dispatch.assert_not_awaited()  # no worker was started


@pytest.mark.asyncio
async def test_rejoining_the_same_session_is_not_blocked(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """The probe excludes THIS session, so a reload of the running one finds none."""
    import app.routers.rooms as rooms

    monkeypatch.setattr(rooms, "dispatch_interview_agent", AsyncMock(return_value=True))
    uid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    override = _patch_db(
        session_user_id=uid, session_status="in_progress", has_consent=True,
        other_active_sessions=0,
    )
    resp = await _post_token(client, sid, _token(uid), override=override)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_probe_excludes_the_same_session_and_is_bounded_by_a_window() -> None:
    uid, sid = uuid.uuid4(), uuid.uuid4()
    db = MagicMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = 0
    db.execute = AsyncMock(return_value=res)
    assert await _has_other_active_session(db, str(uid), sid) is False
    sql = str(db.execute.call_args.args[0])
    params = db.execute.call_args.args[1]
    assert "id <> :sid" in sql and "status = 'in_progress'" in sql
    assert "updated_at >" in sql, "a row stranded by a crashed worker must age out"
    assert params["sid"] == sid and params["uid"] == uid


@pytest.mark.asyncio
async def test_probe_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_active_sessions_per_user", 0, raising=False)
    db = MagicMock()
    db.execute = AsyncMock()
    assert await _has_other_active_session(db, str(uuid.uuid4()), uuid.uuid4()) is False
    db.execute.assert_not_awaited()


# ------------------------------------------------------------- POST /api/sessions wiring
@pytest.mark.asyncio
async def test_session_create_route_is_429_after_the_per_user_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from httpx import ASGITransport
    from httpx import AsyncClient as _Client

    import app.rate_limit as rl
    from app.database import get_db_session
    from app.main import app
    from tests.integration.test_sessions_router import (
        _build_fake_job,
        _patch_db_session,
        _valid_token,
    )

    shared_fake = _FakeRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: shared_fake)
    monkeypatch.setattr(settings, "session_create_per_minute", 2, raising=False)
    token, _uid = _valid_token()
    job = _build_fake_job(is_active=True)
    codes = []
    async with _Client(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for _ in range(3):
            app.dependency_overrides[get_db_session] = _patch_db_session(job=job)
            try:
                resp = await ac.post(
                    "/api/sessions",
                    json={"job_id": str(job.id), "language": "en"},
                    headers={"Authorization": f"Bearer {token}"},
                )
            finally:
                app.dependency_overrides.pop(get_db_session, None)
            codes.append(resp.status_code)
    assert codes == [201, 201, 429]
