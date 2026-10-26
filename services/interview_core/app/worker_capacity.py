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
A Redis sorted set, ``<ns>:interview:slots``: member = session_id (== LiveKit
room name), score = the unix time the slot EXPIRES. One member per interview
that is either waiting for its interviewer or running one.

* ``reserve_slot`` (token endpoint) - one Lua script that drops expired members,
  then either confirms an already-held slot, or admits a new one if
  ``ZCARD < cap``, or refuses. Atomic, so concurrent requests cannot oversubscribe.
* **The API can never lengthen a hold.** Confirming an existing slot returns
  HELD and leaves its expiry alone. Only ``extend_slot`` (job process, on accept)
  lengthens a lease. A re-request can take a slot again once the reservation has
  lapsed, but cannot refresh it indefinitely (security review H2: refreshing
  every <180 s let one account hold slots forever without ever joining).
* **One reservation per user.** ``reserve_slot(user_id=...)`` records
  ``user -> session`` in the same script and refuses a second, different session
  while the first still holds its slot (``USER_BUSY``). One account can therefore
  hold at most one slot.
* ``release_slot`` (job process, shutdown callback) - frees the slot the moment
  the interview ends, on every exit path.
* The lease is the crash backstop: a worker that dies without releasing leaves a
  member that simply expires. Capacity is self-healing.

KEY NAMESPACE
-------------
Every key is prefixed with ``settings.redis_key_namespace`` (default: ``APP_ENV``).
Local development shares the production Upstash instance (services/*/.env), so an
un-namespaced ledger let a developer's API or worker reserve, and RELEASE,
production slots. ``development:`` and ``production:`` ledgers cannot touch each
other. (The LiveKit project is shared too - see ``INTERVIEW_AGENT_NAME`` in
``.env.example``.)

FAIL-OPEN vs FAIL-CLOSED
------------------------
Every helper RAISES on a Redis error and leaves the policy to the caller:

* token endpoint: the slot reservation fails OPEN. A Redis outage must not block
  every candidate from starting; the worker's own gates (load function, memory
  estimate) still bound the real load.
* worker ``request_fnc``: fails OPEN too (accept) - rejecting a job for a ledger
  we cannot read is the one choice that strands a candidate for certain.
* ``/redispatch``: the per-session ceiling and the dispatch lock fail CLOSED (it
  is a recovery path, not the entry path; the candidate can still use "Try
  again", which is a fresh ``/token``). Only the slot reservation fails open.
* release: best-effort. A failed release is repaired by lease expiry.

NOTE on recovery: ``/redispatch`` is gated by the same ledger, so it recovers a
candidate whose JOB was lost (refused, timed out, crashed) - it does not rescue
an over-admitted candidate; when the ledger is full it answers 503.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

import structlog

from app.config import settings

log = structlog.get_logger(__name__)

REDISPATCH_COUNT_TTL_SECONDS: int = 3600
# How long the per-user "this is your session" pointer outlives its slot; the
# pointer is only trusted while its session still holds an unexpired slot.
USER_POINTER_TTL_SECONDS: int = 7200


def _ns() -> str:
    """Key namespace: explicit setting, else APP_ENV (see module docstring)."""
    return (settings.redis_key_namespace or settings.app_env or "development").strip()


def slots_key() -> str:
    return f"{_ns()}:interview:slots"


def user_pointer_key(user_id: str) -> str:
    return f"{_ns()}:interview:user_slot:{user_id}"


def redispatch_count_key(session_id: str) -> str:
    return f"{_ns()}:interview:redispatch:{session_id}"


def dispatch_lock_key(room: str) -> str:
    return f"{_ns()}:interview:dispatch_lock:{room}"


def job_marker_key(room: str) -> str:
    return f"{_ns()}:interview:job:{room}"


class SlotResult(str, Enum):
    """Outcome of ``reserve_slot``."""

    RESERVED = "reserved"  # a new slot was taken
    HELD = "held"  # this session already held a slot (expiry NOT extended)
    FULL = "full"  # at the cap, nothing taken
    USER_BUSY = "user_busy"  # this user already holds a slot for another session


# KEYS[1] = ledger, KEYS[2] = per-user pointer
# ARGV = now, ttl_seconds, cap, member, user_id ("" = no per-user rule), pointer_ttl
# Returns {code, count}: 1 reserved, 2 held (NOT extended), 0 full, 3 user busy.
_RESERVE_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZSCORE', KEYS[1], ARGV[4]) then
  return {2, redis.call('ZCARD', KEYS[1])}
end
if ARGV[5] ~= '' then
  local other = redis.call('GET', KEYS[2])
  if other and other ~= ARGV[4] and redis.call('ZSCORE', KEYS[1], other) then
    return {3, redis.call('ZCARD', KEYS[1])}
  end
end
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[3]) then
  return {0, count}
end
redis.call('ZADD', KEYS[1], tonumber(ARGV[1]) + tonumber(ARGV[2]), ARGV[4])
if ARGV[5] ~= '' then
  redis.call('SET', KEYS[2], ARGV[4], 'EX', tonumber(ARGV[6]))
end
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

# KEYS[1] = counter; ARGV = ttl. INCR and EXPIRE in one step so a failure between
# them can never leave a counter with no TTL (which would block a session for good).
_INCR_EXPIRE_LUA = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1])) end
return n
"""


async def reserve_slot(
    redis: Any,
    session_id: str,
    *,
    cap: int,
    ttl_seconds: int,
    user_id: str = "",
) -> tuple[SlotResult, int]:
    """Atomically take (or confirm) this session's slot.

    Returns ``(result, occupied)``. ``cap <= 0`` means "no cap". ``user_id`` ""
    skips the one-reservation-per-user rule (the worker's own claim has no user).
    Raises on a Redis error - see the module docstring for who fails open.
    """
    now = int(time.time())
    effective_cap = cap if cap > 0 else 1_000_000
    raw = await redis.eval(
        _RESERVE_LUA,
        2,
        slots_key(),
        user_pointer_key(user_id) if user_id else user_pointer_key("-"),
        now,
        ttl_seconds,
        effective_cap,
        session_id,
        user_id,
        USER_POINTER_TTL_SECONDS,
    )
    code, occupied = int(raw[0]), int(raw[1])
    if code == 1:
        return SlotResult.RESERVED, occupied
    if code == 2:
        return SlotResult.HELD, occupied
    if code == 3:
        return SlotResult.USER_BUSY, occupied
    return SlotResult.FULL, occupied


async def extend_slot(redis: Any, session_id: str, *, ttl_seconds: int) -> bool:
    """Turn a short reservation into the long interview lease. False if gone."""
    now = int(time.time())
    raw = await redis.eval(_EXTEND_LUA, 1, slots_key(), now, ttl_seconds, session_id)
    return int(raw) == 1


async def release_slot(redis: Any, session_id: str) -> None:
    """Free this session's slot. Idempotent; raises on a Redis error."""
    await redis.zrem(slots_key(), session_id)


async def count_slots(redis: Any) -> int:
    """Number of unexpired slots (expired members are trimmed first)."""
    await redis.zremrangebyscore(slots_key(), "-inf", int(time.time()))
    return int(await redis.zcard(slots_key()))


async def slot_held(redis: Any, session_id: str) -> bool:
    """True when this session holds an unexpired slot."""
    score = await redis.zscore(slots_key(), session_id)
    return score is not None and float(score) > time.time()


async def note_redispatch(redis: Any, session_id: str) -> int:
    """Count one re-dispatch for this session (atomic INCR+EXPIRE)."""
    return int(
        await redis.eval(
            _INCR_EXPIRE_LUA, 1, redispatch_count_key(session_id), REDISPATCH_COUNT_TTL_SECONDS
        )
    )


async def reset_redispatch(redis: Any, session_id: str) -> None:
    """A fresh join token starts a fresh re-dispatch budget (best-effort)."""
    await redis.delete(redispatch_count_key(session_id))


async def acquire_dispatch_lock(redis: Any, room: str, ttl_seconds: int) -> bool:
    """Per-room debounce/lock around list -> delete -> create. False = held."""
    return bool(await redis.set(dispatch_lock_key(room), "1", nx=True, ex=ttl_seconds))


async def release_dispatch_lock(redis: Any, room: str) -> None:
    await redis.delete(dispatch_lock_key(room))


async def claim_job_marker(redis: Any, room: str, ttl_seconds: int) -> bool:
    """One interviewer JOB per room: True if this caller is the first."""
    return bool(await redis.set(job_marker_key(room), "1", nx=True, ex=ttl_seconds))


async def clear_job_marker(redis: Any, room: str) -> None:
    await redis.delete(job_marker_key(room))
