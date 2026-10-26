"""The slot ledger (app/worker_capacity.py).

Two layers:

* Wrapper tests use a stub whose ``eval`` returns what the Lua script returns, so
  they run everywhere and pin the result mapping.
* Semantics tests run the REAL Lua scripts against a throwaway Redis named by
  ``LEDGER_TEST_REDIS_URL`` (e.g. ``redis://127.0.0.1:56379/0``) and are skipped
  when it is unset. They are the only thing that proves atomicity, which is the
  whole point of the ledger: the defect they guard is N concurrent candidates all
  passing a cap that only counted jobs the worker had already accepted. NEVER
  point the variable at a shared or production Redis - the tests write
  ``interview:slots``.
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
    ],
)
async def test_reserve_maps_the_script_result(raw: list[int], expected: Any) -> None:
    redis = _StubRedis(raw)
    assert await wc.reserve_slot(redis, "s1", cap=4, ttl_seconds=60) == expected


@pytest.mark.asyncio
async def test_reserve_passes_cap_and_member_to_the_script() -> None:
    redis = _StubRedis([1, 1])
    await wc.reserve_slot(redis, "sess-9", cap=7, ttl_seconds=90)
    _script, nkeys, key, _now, ttl, cap, member = redis.calls[0]
    assert (nkeys, key, ttl, cap, member) == (1, wc.SLOTS_KEY, 90, 7, "sess-9")


@pytest.mark.asyncio
async def test_a_zero_cap_means_unlimited_not_always_full() -> None:
    """cap=0 is documented as "no cap"; passing 0 to the script would refuse
    everyone."""
    redis = _StubRedis([1, 1])
    await wc.reserve_slot(redis, "s", cap=0, ttl_seconds=60)
    assert redis.calls[0][5] >= 1_000_000


@pytest.mark.asyncio
async def test_a_redis_error_propagates_so_the_caller_chooses_the_policy() -> None:
    class _Down:
        async def eval(self, *a: Any) -> Any:
            raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        await wc.reserve_slot(_Down(), "s", cap=4, ttl_seconds=60)


# ----------------------------------------------------------- real-Redis semantics

_REDIS_URL = os.environ.get("LEDGER_TEST_REDIS_URL", "")
needs_redis = pytest.mark.skipif(
    not _REDIS_URL, reason="LEDGER_TEST_REDIS_URL not set (throwaway Redis only)"
)


@pytest_asyncio.fixture
async def redis() -> AsyncIterator[Any]:
    import redis.asyncio as aioredis

    client = aioredis.from_url(_REDIS_URL, decode_responses=True)  # type: ignore[no-untyped-call]
    await client.delete(wc.SLOTS_KEY)
    try:
        yield client
    finally:
        await client.delete(wc.SLOTS_KEY)
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
    won = [r for r, _ in results if r is SlotResult.RESERVED]
    assert len(won) == 4
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
async def test_a_full_ledger_still_confirms_a_session_that_already_holds_a_slot(
    redis: Any,
) -> None:
    """A reload of a running interview must not be told 'full' by its own slot."""
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
    """The crash backstop: nothing ever releases this slot, yet it frees."""
    await wc.reserve_slot(redis, "ghost", cap=1, ttl_seconds=-1)  # already expired
    result, occupied = await wc.reserve_slot(redis, "live", cap=1, ttl_seconds=60)
    assert result is SlotResult.RESERVED
    assert occupied == 1


@needs_redis
@pytest.mark.asyncio
async def test_extend_promotes_a_reservation_and_a_refresh_never_shortens_it(
    redis: Any,
) -> None:
    await wc.reserve_slot(redis, "s", cap=2, ttl_seconds=60)
    assert await wc.extend_slot(redis, "s", ttl_seconds=1800)
    long_score = float(await redis.zscore(wc.SLOTS_KEY, "s"))
    # A later token/redispatch refresh with the SHORT ttl must not cut the lease.
    await wc.reserve_slot(redis, "s", cap=2, ttl_seconds=60)
    assert float(await redis.zscore(wc.SLOTS_KEY, "s")) >= long_score


@needs_redis
@pytest.mark.asyncio
async def test_extend_does_not_resurrect_a_released_slot(redis: Any) -> None:
    await wc.reserve_slot(redis, "s", cap=2, ttl_seconds=60)
    await wc.release_slot(redis, "s")
    assert not await wc.extend_slot(redis, "s", ttl_seconds=1800)
    assert await wc.count_slots(redis) == 0


@needs_redis
@pytest.mark.asyncio
async def test_redispatch_counter_counts_and_expires(redis: Any) -> None:
    sid = f"rd-{uuid.uuid4()}"
    try:
        assert [await wc.note_redispatch(redis, sid) for _ in range(3)] == [1, 2, 3]
        ttl = await redis.ttl(f"{wc.REDISPATCH_COUNT_KEY_PREFIX}{sid}")
        assert 0 < ttl <= wc.REDISPATCH_COUNT_TTL_SECONDS
    finally:
        await redis.delete(f"{wc.REDISPATCH_COUNT_KEY_PREFIX}{sid}")
