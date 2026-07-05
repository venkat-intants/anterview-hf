from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import Redis

from app.config import settings

_redis: Redis[Any] | None = None  # type: ignore[type-arg]


def init_redis() -> None:
    global _redis
    _redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url,
        decode_responses=True,
        max_connections=20,
    )


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def get_redis() -> Redis[Any]:  # type: ignore[type-arg]
    if _redis is None:
        raise RuntimeError("Redis not initialised. Call init_redis() first.")
    return _redis
