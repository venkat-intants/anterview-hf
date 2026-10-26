"""Stranded-candidate fixes at the HTTP layer (2026-10 load test).

* POST /token embeds an explicit dispatch for the interview worker in the join
  token, and reserves a capacity slot atomically BEFORE issuing it;
* POST /redispatch is the idempotent "no interviewer joined" recovery path.

Redis and LiveKit are faked (no network); the DB is the same substring-mocked
session the rest of the rooms suite uses. The ATOMICITY of the ledger itself is
proved against a real Redis in tests/unit/test_worker_capacity.py - what is
asserted here is that the endpoints are wired to it correctly.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
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
    """Just enough for app.rate_limit (INCR + EXPIRE)."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        return None


class _FakeLedger:
    """Python stand-in with the ledger's observable semantics (atomic by virtue
    of running on one event loop with no await between check and set)."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.held: set[str] = set()

    async def reserve(
        self, redis: Any, session_id: str, *, cap: int, ttl_seconds: int
    ) -> tuple[SlotResult, int]:
        await asyncio.sleep(0)  # a scheduling point BEFORE the atomic section
        if session_id in self.held:
            return SlotResult.HELD, len(self.held)
        if len(self.held) >= self.cap:
            return SlotResult.FULL, len(self.held)
        self.held.add(session_id)
        return SlotResult.RESERVED, len(self.held)


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
    monkeypatch.setattr(
        rooms, "reserve_slot", AsyncMock(return_value=(SlotResult.RESERVED, 1))
    )
    monkeypatch.setattr(rooms, "note_redispatch", AsyncMock(return_value=1))


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
    agents = claims["roomConfig"]["agents"]
    assert [a["agentName"] for a in agents] == [AGENT]
    # The candidate may still only join THIS room.
    assert claims["video"]["room"] == resp.json()["room_name"]


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


# ----------------------------------------------------------------- /token: capacity


@pytest.mark.asyncio
async def test_a_full_ledger_is_a_clear_503_and_no_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    monkeypatch.setattr(
        rooms, "reserve_slot", AsyncMock(return_value=(SlotResult.FULL, 3))
    )
    uid = str(uuid.uuid4())
    resp = await _post_token(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 503
    assert "slots" in resp.json()["detail"].lower()
    assert resp.headers["retry-after"] == "30"
    assert "token" not in resp.json()


@pytest.mark.asyncio
async def test_the_reservation_uses_the_configured_cap_and_lease(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    reserve = AsyncMock(return_value=(SlotResult.RESERVED, 1))
    monkeypatch.setattr(rooms, "reserve_slot", reserve)
    sid = str(uuid.uuid4())
    uid = str(uuid.uuid4())
    resp = await _post_token(client, sid, _token(uid), override=_patch_db(session_user_id=uid))
    assert resp.status_code == 200
    reserve.assert_awaited_once()
    assert reserve.await_args.args[1] == sid
    assert reserve.await_args.kwargs == {
        "cap": 3,
        "ttl_seconds": settings.slot_reservation_seconds,
    }


@pytest.mark.asyncio
async def test_a_ledger_outage_fails_open(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Blocking every candidate because Redis is unreachable is worse than
    over-admitting briefly - /redispatch recovers anyone over-admitted."""
    import app.routers.rooms as rooms

    monkeypatch.setattr(
        rooms, "reserve_slot", AsyncMock(side_effect=ConnectionError("redis down"))
    )
    uid = str(uuid.uuid4())
    resp = await _post_token(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_no_slot_is_taken_when_an_earlier_gate_refuses(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """A refusal (consent, other session, finished) must not leave a reservation
    behind: the reservation comes AFTER every other gate."""
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


@pytest.mark.asyncio
async def test_concurrent_candidates_never_exceed_the_cap(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """10 candidates ask in the same instant for 3 slots: exactly 3 get a token,
    7 get the clear 503 - not 10 tokens and 7 empty rooms."""
    import app.routers.rooms as rooms
    from app.database import get_db_session

    ledger = _FakeLedger(cap=3)
    monkeypatch.setattr(rooms, "reserve_slot", ledger.reserve)

    callers = [str(uuid.uuid4()) for _ in range(10)]
    try:
        # dependency_overrides is global, so every request shares one owner uid;
        # the 10 distinct session ids are what compete for the 3 slots.
        uid = str(uuid.uuid4())
        app.dependency_overrides[get_db_session] = _patch_db(session_user_id=uid)
        token = _token(uid)
        responses = await asyncio.gather(
            *[
                client.post(
                    f"/api/rooms/{sid}/token", headers={"Authorization": f"Bearer {token}"}
                )
                for sid in callers
            ]
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    codes = sorted(r.status_code for r in responses)
    assert codes == [200] * 3 + [503] * 7, codes
    assert len(ledger.held) == 3


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


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", list(DispatchOutcome))
async def test_redispatch_reports_what_happened(
    client: AsyncClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    outcome: DispatchOutcome,
) -> None:
    import app.routers.rooms as rooms

    ensure = AsyncMock(return_value=outcome)
    monkeypatch.setattr(rooms, "ensure_interviewer", ensure)
    uid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    resp = await _post_redispatch(
        client, sid, _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": outcome.value, "attempt": 1, "max_attempts": 3}
    ensure.assert_awaited_once_with(sid)


@pytest.mark.asyncio
async def test_redispatch_takes_the_same_gates_as_the_token_endpoint(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    ensure = AsyncMock(return_value=DispatchOutcome.DISPATCHED)
    monkeypatch.setattr(rooms, "ensure_interviewer", ensure)
    uid, other = str(uuid.uuid4()), str(uuid.uuid4())
    sid = str(uuid.uuid4())

    wrong_owner = await _post_redispatch(
        client, sid, _token(uid), override=_patch_db(session_user_id=other)
    )
    finished = await _post_redispatch(
        client, sid, _token(uid), override=_patch_db(session_user_id=uid, session_status="completed")
    )
    no_consent = await _post_redispatch(
        client, sid, _token(uid), override=_patch_db(session_user_id=uid, has_consent=False)
    )
    missing = await _post_redispatch(
        client, sid, _token(uid), override=_patch_db(session_user_id=None)
    )
    assert [r.status_code for r in (wrong_owner, finished, no_consent, missing)] == [
        403, 409, 403, 404
    ]
    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_redispatch_cannot_run_interviews_past_the_cap(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    ensure = AsyncMock(return_value=DispatchOutcome.DISPATCHED)
    monkeypatch.setattr(rooms, "ensure_interviewer", ensure)
    monkeypatch.setattr(
        rooms, "reserve_slot", AsyncMock(return_value=(SlotResult.FULL, 3))
    )
    uid = str(uuid.uuid4())
    resp = await _post_redispatch(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 503
    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_redispatch_has_a_hard_per_session_ceiling(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import app.routers.rooms as rooms

    ensure = AsyncMock(return_value=DispatchOutcome.DISPATCHED)
    monkeypatch.setattr(rooms, "ensure_interviewer", ensure)
    monkeypatch.setattr(rooms, "note_redispatch", AsyncMock(return_value=4))  # max is 3
    uid = str(uuid.uuid4())
    resp = await _post_redispatch(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 429
    ensure.assert_not_awaited()


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
async def test_redispatch_fails_open_on_a_ledger_outage(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """The candidate is already waiting in a room: Redis being down must not be
    the reason their interviewer is never re-requested."""
    import app.routers.rooms as rooms

    monkeypatch.setattr(
        rooms, "ensure_interviewer", AsyncMock(return_value=DispatchOutcome.DISPATCHED)
    )
    monkeypatch.setattr(rooms, "note_redispatch", AsyncMock(side_effect=ConnectionError("x")))
    uid = str(uuid.uuid4())
    resp = await _post_redispatch(
        client, str(uuid.uuid4()), _token(uid), override=_patch_db(session_user_id=uid)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "dispatched"


def test_the_endpoints_are_in_the_openapi_schema() -> None:
    paths = app.openapi()["paths"]
    assert "/api/rooms/{session_id}/redispatch" in paths
    assert "post" in paths["/api/rooms/{session_id}/redispatch"]
