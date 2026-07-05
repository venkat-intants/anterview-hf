"""Redis client singleton for interview_core.

Mirrors services/data_gateway/app/redis_client.py exactly so both services
share the same pattern. interview_core uses Redis for:
  - S3-005: jti blocklist (replay prevention)  key: jwt:jti:<jti>  TTL = token exp
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import Redis

from app.config import settings

_redis: Redis[Any] | None = None  # type: ignore[type-arg]


def init_redis() -> None:
    """Create the Redis connection pool. Call at application startup."""
    global _redis
    _redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url,
        decode_responses=True,
        max_connections=20,
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
