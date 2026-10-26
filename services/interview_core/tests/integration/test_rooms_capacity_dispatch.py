"""Stranded-candidate fixes at the HTTP layer (2026-10 load test + review round).

* POST /token embeds an explicit dispatch for the interview worker in the join
  token, and reserves a capacity slot atomically BEFORE issuing it (one per user,
  never extended by the API);
* POST /redispatch is the idempotent "no interviewer joined" recovery path: same
  database gates as /token (including one-running-interview), refused unless the
  candidate is actually in the room, ceiling fails closed, slot reserved last.

Redis and LiveKit are faked except where a test says otherwise (the concurrency
test uses a real throwaway Redis when LEDGER_TEST_REDIS_URL is set, which ci.yml
does). The DB is the substring-mocked session the rest of the rooms suite uses.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import Request
from httpx import AsyncClient

from app.agent.launcher import DispatchOutcome
from app.config import settings
from app.main import app
from app.worker_capacity import SlotResult
from tests.integration.test_rooms_router import (  # noqa: F401 - fixtures + helpers
    _livekit_configured,
    _patch_db,
    _post_token,
    _token,
    client,
)

AGENT = "anthire-interviewer"


class _MemoryRedis:
    """Just enough for app.rate_limit (INCR + EXPIRE) and best-effort resets."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def delete(self, key: str) -> None:
        self.counts.pop(key, None)


@pytest.fixture(autouse=True)
def _wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.rate_limit as rl
    import app.routers.rooms as rooms

    mem = _MemoryRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: mem)
    monkeypatch.setattr(rooms, "get_redis", lambda: mem)
    monkeypatch.setattr(settings, "interview_agent_name", AGENT, raising=False)
    monkeypatch.setattr(settings, "worker_max_concurrent_jobs", 3, raising=False)
    monkeypatch.setattr(settings, "room_token_per_minute", 0, raising=False)
    monkeypatch.setattr(settings, "room_redispatch_per_minute", 0, raising=False)
    monkeypatch.setattr(settings, "room_redispatch_max_per_session", 3, raising=False)
    monkeypatch.setattr(rooms, "reserve_slot", AsyncMock(return_value=(SlotResult.RESERVED, 1)))
    monkeypatch.setattr(rooms, "note_redispatch", AsyncMock(return_value=1))
    monkeypatch.setattr(rooms, "reset_redispatch", AsyncMock())


def _claims(token: str) -> dict[str, Any]:
    return dict(jwt.decode(token, options={"verify_signature": False}))


# ----------------------------------------------------------------- /token: dispatch


@pytest.mark.asyncio
async def test_the_join_token_carries_the_explicit_dispatch(client: AsyncClient) -> None:  # noqa: F811
    uid = str(uuid.uuid4())
    resp = await _post_token(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 200, resp.text
    claims = _claims(resp.json()["token"])
    assert [a["agentName"] for a in claims["roomConfig"]["agents"]] == [AGENT]
    assert claims["video"]["room"] == resp.json()["room_name"]
    # The identity the redispatch participant check looks for.
    assert claims["sub"] == f"candidate-{uid}"


@pytest.mark.asyncio
async def test_automatic_mode_issues_a_token_without_a_dispatch(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setattr(settings, "interview_agent_name", "", raising=False)
    uid = str(uuid.uuid4())
    resp = await _post_token(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 200, resp.text
    assert "roomConfig" not in _claims(resp.json()["token"])


@pytest.mark.asyncio
async def test_a_fresh_token_resets_the_redispatch_budget(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Otherwise two 'Try again' cycles exhaust the per-session ceiling and every
    later recovery 429s for an hour (review S4)."""
    import app.routers.rooms as rooms

    reset = AsyncMock()
    monkeypatch.setattr(rooms, "reset_redispatch", reset)
    uid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    resp = await _post_token(client, sid, _token(uid), override=_patch_db(session_user_id=uid))
    assert resp.status_code == 200
    reset.assert_awaited_once()
    assert reset.await_args.args[1] == sid


# ----------------------------------------------------------------- /token: capacity


@pytest.mark.asyncio
async def test_a_full_ledger_is_a_clear_503_and_no_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    monkeypatch.setattr(rooms, "reserve_slot", AsyncMock(return_value=(SlotResult.FULL, 3)))
    uid = str(uuid.uuid4())
    resp = await _post_token(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 503
    assert "slots" in resp.json()["detail"].lower()
    assert resp.headers["retry-after"] == "30"
    assert "token" not in resp.json()


@pytest.mark.asyncio
async def test_a_user_who_already_holds_a_slot_for_another_session_is_refused(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """One account must not hold every slot without ever joining (review H2)."""
    import app.routers.rooms as rooms

    monkeypatch.setattr(rooms, "reserve_slot", AsyncMock(return_value=(SlotResult.USER_BUSY, 1)))
    uid = str(uuid.uuid4())
    resp = await _post_token(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 409
    assert "token" not in resp.json()


@pytest.mark.asyncio
async def test_the_reservation_is_per_user_with_the_configured_cap_and_lease(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    reserve = AsyncMock(return_value=(SlotResult.RESERVED, 1))
    monkeypatch.setattr(rooms, "reserve_slot", reserve)
    sid, uid = str(uuid.uuid4()), str(uuid.uuid4())
    resp = await _post_token(client, sid, _token(uid), override=_patch_db(session_user_id=uid))
    assert resp.status_code == 200
    reserve.assert_awaited_once()
    assert reserve.await_args.args[1] == sid
    assert reserve.await_args.kwargs == {
        "cap": 3,
        "ttl_seconds": settings.slot_reservation_seconds,
        "user_id": uid,
    }


@pytest.mark.asyncio
async def test_a_ledger_outage_fails_open(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    monkeypatch.setattr(rooms, "reserve_slot", AsyncMock(side_effect=ConnectionError("down")))
    uid = str(uuid.uuid4())
    resp = await _post_token(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_no_slot_is_taken_when_an_earlier_gate_refuses(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    reserve = AsyncMock(return_value=(SlotResult.RESERVED, 1))
    monkeypatch.setattr(rooms, "reserve_slot", reserve)
    uid = str(uuid.uuid4())
    resp = await _post_token(
        client,
        str(uuid.uuid4()),
        _token(uid),
        override=_patch_db(session_user_id=uid, has_consent=False),
    )
    assert resp.status_code == 403
    reserve.assert_not_awaited()


def _per_caller_db() -> Any:
    """DB override that gives every request a session owned by ITS OWN caller
    (dependency_overrides is global, so the owner is read from the bearer token)."""

    async def override(request: Request) -> AsyncGenerator[Any, None]:
        token = request.headers["authorization"].split()[1]
        sub = jwt.decode(token, options={"verify_signature": False})["sub"]
        async for db in _patch_db(session_user_id=sub)():
            yield db

    return override


_REAL_REDIS = os.environ.get("LEDGER_TEST_REDIS_URL", "")


@pytest.mark.skipif(not _REAL_REDIS, reason="LEDGER_TEST_REDIS_URL not set (throwaway Redis only)")
@pytest.mark.asyncio
async def test_concurrent_candidates_never_exceed_the_cap_against_a_real_redis(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """10 different candidates ask in the same instant for 3 slots, through the
    real endpoint AND the real Lua script: exactly 3 get a token, 7 get the clear
    503. (An in-process fake ledger is atomic by construction and can never fail
    for the real reason, so this one is not faked.)"""
    import redis.asyncio as aioredis

    import app.routers.rooms as rooms
    from app import worker_capacity
    from app.database import get_db_session
    from app.worker_capacity import slots_key

    # The autouse fixture mocks reserve_slot; this test wants the REAL script.
    monkeypatch.setattr(rooms, "reserve_slot", worker_capacity.reserve_slot)
    monkeypatch.setattr(settings, "redis_key_namespace", f"t-{uuid.uuid4().hex[:8]}", raising=False)
    real = aioredis.from_url(_REAL_REDIS, decode_responses=True)  # type: ignore[no-untyped-call]
    monkeypatch.setattr(rooms, "get_redis", lambda: real)

    callers = [(str(uuid.uuid4()), str(uuid.uuid4())) for _ in range(10)]
    app.dependency_overrides[get_db_session] = _per_caller_db()
    try:
        responses = await asyncio.gather(
            *[
                client.post(
                    f"/api/rooms/{sid}/token", headers={"Authorization": f"Bearer {_token(uid)}"}
                )
                for uid, sid in callers
            ]
        )
        assert sorted(r.status_code for r in responses) == [200] * 3 + [503] * 7
        assert await real.zcard(slots_key()) == 3
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        for key in await real.keys(f"{settings.redis_key_namespace}:*"):
            await real.delete(key)
        await real.aclose()


# ----------------------------------------------------------------- /redispatch


async def _post_redispatch(
    client: AsyncClient,  # noqa: F811
    sid: str,
    token: str,
    *,
    override: Any,
) -> Any:
    from app.database import get_db_session

    app.dependency_overrides[get_db_session] = override
    try:
        return await client.post(
            f"/api/rooms/{sid}/redispatch", headers={"Authorization": f"Bearer {token}"}
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def _ensure(outcome: DispatchOutcome, *, call_hook: bool = True) -> AsyncMock:
    """Fake launcher.ensure_interviewer that runs the before_create hook the way
    the real one does when it is about to create a dispatch."""

    async def fake(room: str, user: str, *, redis_getter: Any, before_create: Any = None) -> Any:
        if call_hook and outcome is DispatchOutcome.DISPATCHED and before_create is not None:
            await before_create()
        return outcome

    return AsyncMock(side_effect=fake)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        DispatchOutcome.ALREADY_CONNECTED,
        DispatchOutcome.STARTING,
        DispatchOutcome.DISPATCHED,
        DispatchOutcome.AUTOMATIC,
    ],
)
async def test_redispatch_reports_what_happened(
    client: AsyncClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    outcome: DispatchOutcome,
) -> None:
    import app.routers.rooms as rooms

    ensure = _ensure(outcome)
    monkeypatch.setattr(rooms, "ensure_interviewer", ensure)
    uid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    resp = await _post_redispatch(
        client, sid, _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": outcome.value, "attempt": 1, "max_attempts": 3}
    assert ensure.await_args.args == (sid, uid)


@pytest.mark.asyncio
async def test_redispatch_refuses_a_candidate_who_is_not_in_the_room(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Security review H1: LiveKit creates the room as a side effect of creating
    a dispatch, so this must be a clear 409 and never reach the ledger."""
    import app.routers.rooms as rooms

    reserve = AsyncMock(return_value=(SlotResult.RESERVED, 1))
    monkeypatch.setattr(rooms, "reserve_slot", reserve)
    monkeypatch.setattr(rooms, "ensure_interviewer", _ensure(DispatchOutcome.NOT_JOINED))
    uid = str(uuid.uuid4())
    resp = await _post_redispatch(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 409
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_redispatch_takes_the_same_database_gates_as_the_token_endpoint(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    ensure = _ensure(DispatchOutcome.DISPATCHED)
    monkeypatch.setattr(rooms, "ensure_interviewer", ensure)
    uid, other, sid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    token = _token(uid)

    responses = [
        await _post_redispatch(client, sid, token, override=_patch_db(session_user_id=other)),
        await _post_redispatch(
            client, sid, token, override=_patch_db(session_user_id=uid, session_status="completed")
        ),
        await _post_redispatch(
            client, sid, token, override=_patch_db(session_user_id=uid, has_consent=False)
        ),
        await _post_redispatch(client, sid, token, override=_patch_db(session_user_id=None)),
        # Security review H1: the one-running-interview rule used to be /token only.
        await _post_redispatch(
            client, sid, token, override=_patch_db(session_user_id=uid, other_active_sessions=1)
        ),
    ]
    assert [r.status_code for r in responses] == [403, 409, 403, 404, 409]
    assert "in progress" in responses[4].json()["detail"].lower()
    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_redispatch_cannot_run_interviews_past_the_cap(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    monkeypatch.setattr(rooms, "ensure_interviewer", _ensure(DispatchOutcome.DISPATCHED))
    monkeypatch.setattr(rooms, "reserve_slot", AsyncMock(return_value=(SlotResult.FULL, 3)))
    uid = str(uuid.uuid4())
    resp = await _post_redispatch(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_redispatch_reservation_is_per_user_and_never_extends(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    reserve = AsyncMock(return_value=(SlotResult.RESERVED, 1))
    monkeypatch.setattr(rooms, "reserve_slot", reserve)
    monkeypatch.setattr(rooms, "ensure_interviewer", _ensure(DispatchOutcome.DISPATCHED))
    uid = str(uuid.uuid4())
    await _post_redispatch(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert reserve.await_args.kwargs["user_id"] == uid


@pytest.mark.asyncio
async def test_redispatch_has_a_hard_per_session_ceiling_checked_before_anything_else(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    ensure = _ensure(DispatchOutcome.DISPATCHED)
    reserve = AsyncMock(return_value=(SlotResult.RESERVED, 1))
    monkeypatch.setattr(rooms, "ensure_interviewer", ensure)
    monkeypatch.setattr(rooms, "reserve_slot", reserve)
    monkeypatch.setattr(rooms, "note_redispatch", AsyncMock(return_value=4))  # max is 3
    uid = str(uuid.uuid4())
    resp = await _post_redispatch(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 429
    ensure.assert_not_awaited()
    reserve.assert_not_awaited()  # a refused call never touches (or extends) the ledger


@pytest.mark.asyncio
async def test_redispatch_ceiling_fails_closed_on_a_redis_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Security review M2: Redis errors (including pool exhaustion) used to turn
    the per-session ceiling off. It is a recovery path - fail closed."""
    import app.routers.rooms as rooms

    ensure = _ensure(DispatchOutcome.DISPATCHED)
    monkeypatch.setattr(rooms, "ensure_interviewer", ensure)
    monkeypatch.setattr(rooms, "note_redispatch", AsyncMock(side_effect=ConnectionError("x")))
    uid = str(uuid.uuid4())
    resp = await _post_redispatch(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 503
    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_redispatch_dispatch_lock_failure_is_a_503_not_an_unprotected_dispatch(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    monkeypatch.setattr(
        rooms, "ensure_interviewer", AsyncMock(side_effect=ConnectionError("lock redis down"))
    )
    uid = str(uuid.uuid4())
    resp = await _post_redispatch(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_redispatch_fails_open_only_on_the_slot_ledger(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """The candidate is already in the room: a ledger outage must not be the
    reason their interviewer is never re-requested."""
    import app.routers.rooms as rooms

    monkeypatch.setattr(rooms, "ensure_interviewer", _ensure(DispatchOutcome.DISPATCHED))
    monkeypatch.setattr(rooms, "reserve_slot", AsyncMock(side_effect=ConnectionError("x")))
    uid = str(uuid.uuid4())
    resp = await _post_redispatch(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "dispatched"


@pytest.mark.asyncio
async def test_redispatch_maps_a_livekit_outage_to_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from livekit import api

    import app.routers.rooms as rooms

    monkeypatch.setattr(
        rooms,
        "ensure_interviewer",
        AsyncMock(side_effect=api.TwirpError("unavailable", "down", status=503)),
    )
    uid = str(uuid.uuid4())
    resp = await _post_redispatch(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 503
    assert "try again" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_the_db_connection_is_released_before_any_livekit_call(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Security review M3: holding the request's pooled connection across up to
    five LiveKit calls starved the (now bounded) pool during a LiveKit incident."""
    import app.routers.rooms as rooms

    events: list[str] = []

    async def fake(room: str, user: str, *, redis_getter: Any, before_create: Any = None) -> Any:
        events.append("livekit")
        return DispatchOutcome.STARTING

    monkeypatch.setattr(rooms, "ensure_interviewer", AsyncMock(side_effect=fake))
    uid = str(uuid.uuid4())
    inner = _patch_db(session_user_id=uid)

    async def override() -> AsyncGenerator[Any, None]:
        async for db in inner():
            real_close = db.close

            async def close(_real: Any = real_close) -> None:
                events.append("db.close")
                await _real()

            db.close = close
            yield db

    resp = await _post_redispatch(client, str(uuid.uuid4()), _token(uid), override=override)
    assert resp.status_code == 200
    assert events[:2] == ["db.close", "livekit"]


@pytest.mark.asyncio
async def test_the_token_endpoint_also_releases_the_db_connection_before_redis(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    events: list[str] = []

    async def reserve(*a: Any, **k: Any) -> Any:
        events.append("reserve")
        return SlotResult.RESERVED, 1

    monkeypatch.setattr(rooms, "reserve_slot", reserve)
    uid = str(uuid.uuid4())
    inner = _patch_db(session_user_id=uid)

    async def override() -> AsyncGenerator[Any, None]:
        async for db in inner():
            real_close = db.close

            async def close(_real: Any = real_close) -> None:
                events.append("db.close")
                await _real()

            db.close = close
            yield db

    resp = await _post_token(client, str(uuid.uuid4()), _token(uid), override=override)
    assert resp.status_code == 200
    assert events[:2] == ["db.close", "reserve"]


def test_the_endpoints_are_in_the_openapi_schema() -> None:
    paths = app.openapi()["paths"]
    assert "/api/rooms/{session_id}/redispatch" in paths
    assert "post" in paths["/api/rooms/{session_id}/redispatch"]
