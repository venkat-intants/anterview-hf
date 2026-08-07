"""Redis client singleton for data_gateway.

The pool configuration itself lives in ``shared/redis_factory.py`` — see that
module for the Upstash idle-drop failure mode (dead pooled sockets surfacing as
intermittent 500s from ``/auth/refresh``) and the argument behind every socket
and retry setting.

This service is the one the hardening was originally written for, and it was
the last one still carrying it as a hand-copied literal after the other three
were moved onto the factory. That inversion is worth naming: the copy that is
"already correct" is exactly the one nobody thinks to update, so the next tune
of ``HEALTH_CHECK_INTERVAL_SECONDS`` would have moved three services and
silently left the original behind. Every setting is byte-for-byte what this
module used to spell out; they are now read from the one place that defines them
rather than restated here.
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis
from shared.redis_factory import build_redis_client

from app.config import settings

_redis: Redis[Any] | None = None  # type: ignore[type-arg]


def init_redis() -> None:
    """Create the Redis connection pool. Call at application startup.

    No I/O happens here — redis-py pools are lazy, so the first socket is opened
    on the first command.
    """
    global _redis
    _redis = build_redis_client(settings.redis_url)


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
