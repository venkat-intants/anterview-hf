"""The one hardened Redis client factory, shared by all four services.

Why this module exists
----------------------
``services/data_gateway/app/redis_client.py`` was hardened against serverless
Redis idle-connection drops. The other three were not: interview_core,
admin_ops and feedback_billing each built their pool with nothing but
``decode_responses`` and ``max_connections``. interview_core's docstring even
claimed it "Mirrors services/data_gateway/app/redis_client.py exactly" — it did
not, and that false claim is what let the gap sit unnoticed. Four hand-copied
pool configurations drift; one factory cannot.

The failure mode being defended against
---------------------------------------
Upstash — like any serverless Redis — closes idle TCP connections server-side
without telling the client. A pooled connection therefore still looks alive to
redis-py long after the peer has gone, and the failure lands not at checkout but
on the *next* command written to that dead socket. On Windows this surfaces as
WinError 64 ("the specified network name is no longer available") or WinError
10054 ("an existing connection was forcibly closed by the remote host"); on
Linux as ECONNRESET. Either way the caller sees an intermittent 500 from an
endpoint that did nothing but a Redis GET — ``/auth/refresh`` was the one that
kept doing it — and it is unreproducible on demand, because it only happens to
whichever connection happened to go idle long enough.

Why each setting is here
------------------------
``health_check_interval=30``
    redis-py PINGs any connection idle longer than this before handing it back,
    so a dead socket is detected and replaced *at checkout* rather than failing
    mid-command. This is the setting that turns the bug from "500" into
    "nothing happened".
``socket_keepalive=True``
    Asks the OS to send TCP keepalives, which stops NAT tables and idle timers
    along the path from quietly dropping the connection in the first place.
    Prevention, where health checks are detection.
``socket_connect_timeout=5`` / ``socket_timeout=5``
    Both default to ``None``, i.e. *wait forever*. A connect to a black-holed
    peer, or a response that never arrives, then pins an event-loop task and a
    pool slot for the life of the process; enough of those and the pool is
    exhausted and the service is down without a single error logged. Five
    seconds is far above Upstash's normal round trip and far below any sane
    HTTP timeout, so a stuck connection fails fast enough to be retried.
``retry`` + ``retry_on_error``
    Health checks narrow the race but cannot close it: a connection can die in
    the window between the PING and the command. Retrying
    ``ConnectionError``/``TimeoutError`` reconnects transparently, which is what
    turns "intermittent 500" into "a slightly slower request". With
    ``retries=3`` that is one attempt plus three retries; the
    ``ExponentialBackoff(base=0.1, cap=2.0)`` schedule sleeps 0.2s, 0.4s, 0.8s
    between them, so the worst case adds ~1.4s rather than an unbounded stall,
    and ``cap`` keeps any single sleep from exceeding 2s. Note this deliberately
    retries only transport failures — a genuine application error (a bad
    command, a WRONGTYPE) is raised on the first attempt, not four times.

Dependency rule
---------------
``shared/`` is COPY'd into all four service images, so it may import only
stdlib + pydantic + structlog + redis, and nothing from ``services/`` or
``app.*``. This module needs only redis. ``shared/tests/test_redis_factory.py``
asserts that mechanically.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

# Named so callers and tests refer to the contract rather than to magic numbers,
# and so a future change lands in exactly one place.
HEALTH_CHECK_INTERVAL_SECONDS = 30
SOCKET_CONNECT_TIMEOUT_SECONDS = 5
SOCKET_TIMEOUT_SECONDS = 5
RETRY_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.1
BACKOFF_CAP_SECONDS = 2.0


def build_redis_client(
    url: str,
    *,
    decode_responses: bool = True,
    max_connections: int = 20,
) -> Redis[Any]:  # type: ignore[type-arg]
    """Return a Redis client hardened for serverless (Upstash) Redis.

    See the module docstring for the failure mode and the reason behind every
    setting applied here.

    No I/O happens in this call. redis-py connection pools are lazy — the first
    socket is opened on the first command — so this is safe to call at import or
    startup time, and safe to call in a test without a Redis running.

    Args:
        url: A ``redis://`` or ``rediss://`` URL. Upstash is ``rediss://``
            (TLS); ``from_url`` picks the TLS connection class from the scheme,
            which is why the URL is passed through rather than decomposed.
        decode_responses: Return ``str`` rather than ``bytes``. Every caller in
            this repo stores text (JTIs, epochs, JSON), so this defaults on; the
            flag exists for a future byte-oriented value.
        max_connections: Per-process ceiling on the pool.
    """
    # Both ignore codes are needed the day mypy is tightened per CLAUDE.md:
    # from_url is untyped in redis-py, so it is a no-untyped-call whose result
    # is Any. Neither fires under the current non-strict root config.
    return aioredis.from_url(  # type: ignore[no-any-return,no-untyped-call]
        url,
        decode_responses=decode_responses,
        max_connections=max_connections,
        health_check_interval=HEALTH_CHECK_INTERVAL_SECONDS,
        socket_keepalive=True,
        socket_connect_timeout=SOCKET_CONNECT_TIMEOUT_SECONDS,
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
        retry=Retry(
            ExponentialBackoff(cap=BACKOFF_CAP_SECONDS, base=BACKOFF_BASE_SECONDS),
            retries=RETRY_ATTEMPTS,
        ),
        # Distinct from Retry's own supported-error tuple: this is the
        # client-level list consulted in execute_command. Both must name the
        # transport errors or a dropped connection is re-raised, not retried.
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
    )
