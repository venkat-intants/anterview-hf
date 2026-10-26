"""The slot ledger (app/worker_capacity.py).

Two layers:

* Wrapper tests use a stub whose ``eval`` returns what the Lua script returns, so
  they run everywhere and pin the result mapping and the key namespace.
* Semantics tests run the REAL Lua scripts against a throwaway Redis named by
  ``LEDGER_TEST_REDIS_URL`` and are skipped when it is unset. They are the only
  thing that proves atomicity, which is the whole point of the ledger: the defect
  they guard is N concurrent candidates all passing a cap that only counted jobs
  the worker had already accepted. CI sets the variable (ci.yml starts a
  throwaway redis:7 service for the interview_core job). NEVER point it at a
  shared or production Redis - the tests write ``*:interview:*`` keys.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app import worker_capacity as wc
from app.config import settings
from app.worker_capacity import SlotResult

# --------------------------------------------------------------------- wrappers


class _StubRedis:
    def __init__(self, eval_result: Any) -> None:
        self.eval_result = eval_result
        self.calls: list[tuple[Any, ...]] = []

    async def eval(self, *args: Any) -> Any:
        self.calls.append(args)
        return self.eval_result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([1, 3], (SlotResult.RESERVED, 3)),
        ([2, 3], (SlotResult.HELD, 3)),
        ([0, 4], (SlotResult.FULL, 4)),
        ([3, 2], (SlotResult.USER_BUSY, 2)),
    ],
)
async def test_reserve_maps_the_script_result(raw: list[int], expected: Any) -> None:
    redis = _StubRedis(raw)
    assert await wc.reserve_slot(redis, "s1", cap=4, ttl_seconds=60) == expected


@pytest.mark.asyncio
async def test_reserve_passes_cap_member_and_user_to_the_script() -> None:
    redis = _StubRedis([1, 1])
    await wc.reserve_slot(redis, "sess-9", cap=7, ttl_seconds=90, user_id="u1")
    _script, nkeys, ledger, pointer, _now, ttl, cap, member, user, _ptl = redis.calls[0]
    assert (nkeys, ledger, ttl, cap, member, user) == (2, wc.slots_key(), 90, 7, "sess-9", "u1")
    assert pointer == wc.user_pointer_key("u1")


@pytest.mark.asyncio
async def test_a_zero_cap_means_unlimited_not_always_full() -> None:
    redis = _StubRedis([1, 1])
    await wc.reserve_slot(redis, "s", cap=0, ttl_seconds=60)
    assert redis.calls[0][6] >= 1_000_000


@pytest.mark.asyncio
async def test_a_redis_error_propagates_so_the_caller_chooses_the_policy() -> None:
    class _Down:
        async def eval(self, *a: Any) -> Any:
            raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        await wc.reserve_slot(_Down(), "s", cap=4, ttl_seconds=60)


# ------------------------------------------------------------------ namespacing


def test_every_key_is_namespaced_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local dev shares the production Upstash; an un-namespaced ledger let a
    developer's API or worker reserve - and RELEASE - production slots (L3)."""
    monkeypatch.setattr(settings, "redis_key_namespace", "", raising=False)
    monkeypatch.setattr(settings, "app_env", "production", raising=False)
    prod = {
        wc.slots_key(),
        wc.user_pointer_key("u"),
        wc.redispatch_count_key("s"),
        wc.dispatch_lock_key("r"),
        wc.job_marker_key("r"),
    }
    monkeypatch.setattr(settings, "app_env", "development", raising=False)
    dev = {
        wc.slots_key(),
        wc.user_pointer_key("u"),
        wc.redispatch_count_key("s"),
        wc.dispatch_lock_key("r"),
        wc.job_marker_key("r"),
    }
    assert prod.isdisjoint(dev)
    assert all(k.startswith("production:interview:") for k in prod)
    assert all(k.startswith("development:interview:") for k in dev)


def test_an_explicit_namespace_wins_over_app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "redis_key_namespace", "staging-b", raising=False)
    assert wc.slots_key() == "staging-b:interview:slots"


# ----------------------------------------------------------- real-Redis semantics

_REDIS_URL = os.environ.get("LEDGER_TEST_REDIS_URL", "")
needs_redis = pytest.mark.skipif(
    not _REDIS_URL, reason="LEDGER_TEST_REDIS_URL not set (throwaway Redis only)"
)


@pytest_asyncio.fixture
async def redis(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    import redis.asyncio as aioredis

    # A private namespace per test so a shared CI Redis is never disturbed.
    monkeypatch.setattr(settings, "redis_key_namespace", f"t-{uuid.uuid4().hex[:8]}", raising=False)
    client = aioredis.from_url(_REDIS_URL, decode_responses=True)  # type: ignore[no-untyped-call]
    try:
        yield client
    finally:
        for key in await client.keys(f"{settings.redis_key_namespace}:*"):
            await client.delete(key)
        await client.aclose()


@needs_redis
@pytest.mark.asyncio
async def test_cap_is_enforced_and_a_full_ledger_refuses(redis: Any) -> None:
    for i in range(3):
        result, _ = await wc.reserve_slot(redis, f"s{i}", cap=3, ttl_seconds=60)
        assert result is SlotResult.RESERVED
    result, occupied = await wc.reserve_slot(redis, "s-extra", cap=3, ttl_seconds=60)
    assert (result, occupied) == (SlotResult.FULL, 3)
    assert await wc.count_slots(redis) == 3
    assert not await wc.slot_held(redis, "s-extra")


@needs_redis
@pytest.mark.asyncio
async def test_concurrent_requests_cannot_oversubscribe(redis: Any) -> None:
    """THE regression. 40 candidates ask at once for a cap of 4: exactly 4 win.
    The old counter-read design admitted all 40."""
    results = await asyncio.gather(
        *[wc.reserve_slot(redis, str(uuid.uuid4()), cap=4, ttl_seconds=60) for _ in range(40)]
    )
    assert [r for r, _ in results].count(SlotResult.RESERVED) == 4
    assert [r for r, _ in results].count(SlotResult.FULL) == 36
    assert await wc.count_slots(redis) == 4


@needs_redis
@pytest.mark.asyncio
async def test_re_requesting_the_same_session_never_takes_a_second_slot(redis: Any) -> None:
    first, _ = await wc.reserve_slot(redis, "same", cap=2, ttl_seconds=60)
    again = [await wc.reserve_slot(redis, "same", cap=2, ttl_seconds=60) for _ in range(5)]
    assert first is SlotResult.RESERVED
    assert {r for r, _ in again} == {SlotResult.HELD}
    assert await wc.count_slots(redis) == 1


@needs_redis
@pytest.mark.asyncio
async def test_the_api_can_never_extend_a_hold(redis: Any) -> None:
    """Security review H2: refreshing a reservation every <180 s let one account
    hold a slot forever without joining. A re-request must leave the expiry
    exactly where it was."""
    await wc.reserve_slot(redis, "s", cap=2, ttl_seconds=60)
    before = float(await redis.zscore(wc.slots_key(), "s"))
    await asyncio.sleep(1.1)
    result, _ = await wc.reserve_slot(redis, "s", cap=2, ttl_seconds=3600)
    assert result is SlotResult.HELD
    assert float(await redis.zscore(wc.slots_key(), "s")) == before


@needs_redis
@pytest.mark.asyncio
async def test_a_lapsed_reservation_can_be_taken_again(redis: Any) -> None:
    await wc.reserve_slot(redis, "s", cap=1, ttl_seconds=-1)  # already expired
    result, _ = await wc.reserve_slot(redis, "s", cap=1, ttl_seconds=60)
    assert result is SlotResult.RESERVED


@needs_redis
@pytest.mark.asyncio
async def test_one_user_holds_at_most_one_slot(redis: Any) -> None:
    """Security review H2: four 'created' sessions + a reservation each = four
    slots held by an account that never joins."""
    first, _ = await wc.reserve_slot(redis, "a", cap=4, ttl_seconds=60, user_id="u1")
    second, occupied = await wc.reserve_slot(redis, "b", cap=4, ttl_seconds=60, user_id="u1")
    other_user, _ = await wc.reserve_slot(redis, "c", cap=4, ttl_seconds=60, user_id="u2")
    assert (first, second, other_user) == (
        SlotResult.RESERVED,
        SlotResult.USER_BUSY,
        SlotResult.RESERVED,
    )
    assert occupied == 1
    assert not await wc.slot_held(redis, "b")  # nothing was taken for the refused one


@needs_redis
@pytest.mark.asyncio
async def test_a_user_can_take_another_slot_once_the_first_is_released_or_lapsed(
    redis: Any,
) -> None:
    await wc.reserve_slot(redis, "a", cap=4, ttl_seconds=60, user_id="u1")
    await wc.release_slot(redis, "a")
    result, _ = await wc.reserve_slot(redis, "b", cap=4, ttl_seconds=60, user_id="u1")
    assert result is SlotResult.RESERVED
    await wc.reserve_slot(redis, "x", cap=4, ttl_seconds=-1, user_id="u3")  # lapses at once
    result, _ = await wc.reserve_slot(redis, "y", cap=4, ttl_seconds=60, user_id="u3")
    assert result is SlotResult.RESERVED


@needs_redis
@pytest.mark.asyncio
async def test_a_full_ledger_still_confirms_a_session_that_already_holds_a_slot(
    redis: Any,
) -> None:
    await wc.reserve_slot(redis, "running", cap=1, ttl_seconds=60)
    result, _ = await wc.reserve_slot(redis, "running", cap=1, ttl_seconds=60)
    assert result is SlotResult.HELD


@needs_redis
@pytest.mark.asyncio
async def test_release_frees_the_slot_for_the_next_candidate(redis: Any) -> None:
    await wc.reserve_slot(redis, "a", cap=1, ttl_seconds=60)
    assert (await wc.reserve_slot(redis, "b", cap=1, ttl_seconds=60))[0] is SlotResult.FULL
    await wc.release_slot(redis, "a")
    assert (await wc.reserve_slot(redis, "b", cap=1, ttl_seconds=60))[0] is SlotResult.RESERVED
    await wc.release_slot(redis, "a")  # idempotent


@needs_redis
@pytest.mark.asyncio
async def test_an_abandoned_reservation_expires_on_its_own(redis: Any) -> None:
    await wc.reserve_slot(redis, "ghost", cap=1, ttl_seconds=-1)
    result, occupied = await wc.reserve_slot(redis, "live", cap=1, ttl_seconds=60)
    assert (result, occupied) == (SlotResult.RESERVED, 1)


@needs_redis
@pytest.mark.asyncio
async def test_only_the_worker_extend_lengthens_a_lease(redis: Any) -> None:
    await wc.reserve_slot(redis, "s", cap=2, ttl_seconds=60)
    assert await wc.extend_slot(redis, "s", ttl_seconds=1800)
    long_score = float(await redis.zscore(wc.slots_key(), "s"))
    await wc.reserve_slot(redis, "s", cap=2, ttl_seconds=60)
    assert float(await redis.zscore(wc.slots_key(), "s")) == long_score


@needs_redis
@pytest.mark.asyncio
async def test_extend_does_not_resurrect_a_released_slot(redis: Any) -> None:
    await wc.reserve_slot(redis, "s", cap=2, ttl_seconds=60)
    await wc.release_slot(redis, "s")
    assert not await wc.extend_slot(redis, "s", ttl_seconds=1800)
    assert await wc.count_slots(redis) == 0


@needs_redis
@pytest.mark.asyncio
async def test_redispatch_counter_counts_expires_and_resets(redis: Any) -> None:
    sid = f"rd-{uuid.uuid4()}"
    assert [await wc.note_redispatch(redis, sid) for _ in range(3)] == [1, 2, 3]
    ttl = await redis.ttl(wc.redispatch_count_key(sid))
    assert 0 < ttl <= wc.REDISPATCH_COUNT_TTL_SECONDS
    await wc.reset_redispatch(redis, sid)
    assert await wc.note_redispatch(redis, sid) == 1


@needs_redis
@pytest.mark.asyncio
async def test_the_counter_always_has_a_ttl(redis: Any) -> None:
    """INCR and EXPIRE are one script: there is no instant at which the counter
    exists without a TTL (L2)."""
    sid = f"rd-{uuid.uuid4()}"
    await wc.note_redispatch(redis, sid)
    assert await redis.ttl(wc.redispatch_count_key(sid)) > 0


@needs_redis
@pytest.mark.asyncio
async def test_dispatch_lock_and_job_marker_are_exclusive_and_expire(redis: Any) -> None:
    token = await wc.acquire_dispatch_lock(redis, "r", 15)
    assert token
    assert await wc.acquire_dispatch_lock(redis, "r", 15) is None
    assert not await wc.release_dispatch_lock(redis, "r", "not-the-owner")  # CAS
    assert await wc.acquire_dispatch_lock(redis, "r", 15) is None  # still held
    assert await wc.release_dispatch_lock(redis, "r", token)
    assert await wc.acquire_dispatch_lock(redis, "r", 15)

    assert await wc.claim_job_marker(redis, "r", 120)
    assert not await wc.claim_job_marker(redis, "r", 120)
    assert 0 < await redis.ttl(wc.job_marker_key("r")) <= 120
    await wc.clear_job_marker(redis, "r")
    assert await wc.claim_job_marker(redis, "r", 120)


@needs_redis
@pytest.mark.asyncio
async def test_a_starting_jobs_marker_expires_so_it_cannot_block_a_redispatch(
    redis: Any,
) -> None:
    """Review F1: a job accepted but never reaching the job process leaves its
    marker behind. With a 120 s TTL the watchdog's whole 60 s budget was spent
    inside it. The marker is short while STARTING..."""
    assert await wc.claim_job_marker(redis, "r", 1)
    assert not await wc.claim_job_marker(redis, "r", 1)
    await asyncio.sleep(1.2)
    assert await wc.claim_job_marker(redis, "r", 1)  # expired on its own


@needs_redis
@pytest.mark.asyncio
async def test_a_running_job_stretches_its_marker_to_the_lease(redis: Any) -> None:
    """... and the running job re-sets it to the interview lease, which also keeps
    a second replica from accepting a duplicate mid-interview (security N2)."""
    await wc.claim_job_marker(redis, "r", 45)
    await wc.refresh_job_marker(redis, "r", 1800)
    assert 1700 < await redis.ttl(wc.job_marker_key("r")) <= 1800
