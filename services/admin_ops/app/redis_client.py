"""Redis client singleton for admin_ops.

The pool configuration itself lives in ``shared/redis_factory.py`` — see that
module for the Upstash idle-drop failure mode and why each socket/retry setting
is set. It is delegated rather than copied because this file previously built
its own pool with nothing but ``decode_responses`` and ``max_connections``,
which is how three of the four services silently missed the hardening that
data_gateway had.
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis
from shared.redis_factory import build_redis_client

from app.config import settings

_redis: Redis[Any] | None = None  # type: ignore[type-arg]


def init_redis() -> None:
    global _redis
    _redis = build_redis_client(settings.redis_url)


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def get_redis() -> Redis[Any]:  # type: ignore[type-arg]
    if _redis is None:
        raise RuntimeError("Redis not initialised. Call init_redis() first.")
    return _redis
