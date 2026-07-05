"""Redis client singleton for data_gateway."""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import settings

_redis: Redis[Any] | None = None  # type: ignore[type-arg]


def init_redis() -> None:
    """Create the Redis connection pool. Call at application startup.

    Hardened against serverless-Redis (Upstash) idle-connection drops: Upstash
    closes idle TCP connections, so a pooled connection can be dead on next use
    (Windows surfaces this as WinError 64 / 10054). health_check_interval pings a
    connection before reuse, and retry_on_error transparently reconnects on a
    dropped/timed-out socket — otherwise the next /auth/refresh 500s.
    """
    global _redis
    _redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url,
        decode_responses=True,
        max_connections=20,
        health_check_interval=30,
        socket_keepalive=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry=Retry(ExponentialBackoff(cap=2.0, base=0.1), retries=3),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
    )


async def close_redis() -> None:
    """Close the Redis connection pool. Call at application shutdown."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def get_redis() -> Redis[Any]:  # type: ignore[type-arg]
    """Return the Redis client singleton. Raises if not initialised."""
    if _redis is None:
        raise RuntimeError("Redis not initialised. Call init_redis() first.")
    return _redis
