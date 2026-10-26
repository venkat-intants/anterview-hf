"""Interview slot ledger - the one atomic source of truth for worker capacity.

WHY this module exists
----------------------
The LiveKit interview worker (``app/worker/interview_worker.py``) is a SEPARATE
process from the FastAPI server, and livekit-agents runs every interview in its
OWN child process. The old design had the job process publish its module-level
``_active_jobs`` counter to a single Redis key (``worker:active_jobs``) and the
token endpoint read that key. That could not work:

* each job process only ever sees its own counter (0 or 1), so the published
  value was whichever process wrote last;
* the counter only rose AFTER the worker accepted a job, so N concurrent token
  requests all read "0 active" and all passed the cap together (load test,
  2026-10: candidates admitted past the cap, then stranded in rooms with no
  interviewer).

The ledger
----------
A Redis sorted set, ``interview:slots``: member = session_id (== LiveKit room
name), score = the unix time the slot EXPIRES. One member per interview that is
either waiting for its interviewer or running one.

* ``reserve_slot`` (token endpoint) - one Lua script that drops expired members,
  then either refreshes an already-held slot (a reload / re-dispatch of the same
  session must never consume a second slot), or admits a new one if
  ``ZCARD < cap``, or refuses. Atomic, so concurrent requests cannot oversubscribe.
* ``extend_slot`` (job process, on accept) - the short reservation lease becomes
  a long interview lease.
* ``release_slot`` (job process, shutdown callback) - frees the slot the moment
  the interview ends, on every exit path.
* The lease is the crash backstop: a worker that dies without releasing leaves a
  member that simply expires. Capacity is therefore self-healing, with no
  "counter stuck too high forever" mode.

FAIL-OPEN vs FAIL-CLOSED (deliberate, unchanged in spirit)
-----------------------------------------------------------
Every helper here RAISES on a Redis error and leaves the policy to the caller:

* token endpoint: fails OPEN. A Redis outage must not block every candidate from
  starting; the worker's own gates (``load_fnc`` and the memory estimate) still
  bound the real load, and an over-admitted candidate is recovered by the
  re-dispatch path rather than being stranded.
* worker ``request_fnc``: fails OPEN too (accept) - rejecting a job for a ledger
  we cannot read is the one choice that strands a candidate for certain.
* release: best-effort. A failed release is repaired by lease expiry.

Redis keys are namespaced with the ``interview:`` prefix; nothing else in the
codebase writes them.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

import structlog

log = structlog.get_logger(__name__)

SLOTS_KEY: str = "interview:slots"
# Redis key counting how many times a session has been re-dispatched, so a
# misbehaving client cannot hammer the LiveKit API for one room.
REDISPATCH_COUNT_KEY_PREFIX: str = "interview:redispatch:"
REDISPATCH_COUNT_TTL_SECONDS: int = 3600


class SlotResult(str, Enum):
    """Outcome of ``reserve_slot``."""

    RESERVED = "reserved"  # a new slot was taken
    HELD = "held"  # this session already held a slot; lease refreshed
    FULL = "full"  # at the cap, nothing taken


# KEYS[1] = ledger; ARGV = now, ttl_seconds, cap, member.
# Returns {code, count}: code 1 = reserved, 2 = already held (refreshed), 0 = full;
# count = occupied slots after the call (excluding expired).
# An already-held slot keeps the LATER of its current expiry and now+ttl, so a
# re-dispatch can never SHORTEN a running interview's long lease.
_RESERVE_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
local held = redis.call('ZSCORE', KEYS[1], ARGV[4])
local expiry = tonumber(ARGV[1]) + tonumber(ARGV[2])
if held then
  if tonumber(held) < expiry then
    redis.call('ZADD', KEYS[1], expiry, ARGV[4])
  end
  return {2, redis.call('ZCARD', KEYS[1])}
end
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[3]) then
  return {0, count}
end
redis.call('ZADD', KEYS[1], expiry, ARGV[4])
return {1, count + 1}
"""

# KEYS[1] = ledger; ARGV = now, ttl_seconds, member. Extends only a slot that is
# still held; returns 1 if extended, 0 if the slot was gone.
_EXTEND_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZSCORE', KEYS[1], ARGV[3]) then
  redis.call('ZADD', KEYS[1], tonumber(ARGV[1]) + tonumber(ARGV[2]), ARGV[3])
  return 1
end
return 0
"""


async def reserve_slot(
    redis: Any, session_id: str, *, cap: int, ttl_seconds: int
) -> tuple[SlotResult, int]:
    """Atomically take (or confirm) this session's slot.

    Returns ``(result, occupied)``. ``cap <= 0`` means "no cap": the slot is
    still recorded so the ledger stays accurate, but it is never refused.
    Raises on a Redis error - see the module docstring for who fails open.
    """
    now = int(time.time())
    effective_cap = cap if cap > 0 else 1_000_000
    raw = await redis.eval(
        _RESERVE_LUA, 1, SLOTS_KEY, now, ttl_seconds, effective_cap, session_id
    )
    code, occupied = int(raw[0]), int(raw[1])
    if code == 1:
        return SlotResult.RESERVED, occupied
    if code == 2:
        return SlotResult.HELD, occupied
    return SlotResult.FULL, occupied


async def extend_slot(redis: Any, session_id: str, *, ttl_seconds: int) -> bool:
    """Turn a short reservation into the long interview lease. False if gone."""
    now = int(time.time())
    raw = await redis.eval(_EXTEND_LUA, 1, SLOTS_KEY, now, ttl_seconds, session_id)
    return int(raw) == 1


async def release_slot(redis: Any, session_id: str) -> None:
    """Free this session's slot. Idempotent; raises on a Redis error."""
    await redis.zrem(SLOTS_KEY, session_id)


async def count_slots(redis: Any) -> int:
    """Number of unexpired slots (expired members are trimmed first)."""
    await redis.zremrangebyscore(SLOTS_KEY, "-inf", int(time.time()))
    return int(await redis.zcard(SLOTS_KEY))


async def slot_held(redis: Any, session_id: str) -> bool:
    """True when this session holds an unexpired slot."""
    score = await redis.zscore(SLOTS_KEY, session_id)
    return score is not None and float(score) > time.time()


async def note_redispatch(redis: Any, session_id: str) -> int:
    """Count one re-dispatch for this session and return the new total."""
    key = f"{REDISPATCH_COUNT_KEY_PREFIX}{session_id}"
    total = int(await redis.incr(key))
    if total == 1:
        await redis.expire(key, REDISPATCH_COUNT_TTL_SECONDS)
    return total
