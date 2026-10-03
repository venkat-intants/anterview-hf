"""Redis client singleton for feedback_billing.

The pool configuration itself lives in ``shared/redis_factory.py`` — see that
module for the Upstash idle-drop failure mode and why each socket/retry setting
is set. It is delegated rather than copied because this file previously built
its own pool with nothing but ``decode_responses`` and ``max_connections``,
which is how three of the four services silently missed the hardening that
data_gateway had.
"""

from __future__ import annotations

from redis.asyncio import Redis
from shared.redis_factory import build_redis_client

from app.config import settings

# ``Redis`` is NOT subscripted here, deliberately. The annotation used to carry
# a type argument plus a ``# type: ignore[type-arg]`` — the subscript for a
# reader, the ignore because redis-py's class is not generic to mypy either.
# That worked only while nothing evaluated it: ``from __future__ import
# annotations`` keeps annotations as strings, and fastapi <=0.115 never resolved
# this one. fastapi 0.133 resolves a dependency's annotations with
# ``get_type_hints()``, which evaluates the string for real and raised "Redis is
# not a generic class" at import time on all four services. Bare ``Redis``
# satisfies mypy (which expects no type arguments) and the runtime (which must
# not subscript it).
_redis: Redis | None = None


def init_redis() -> None:
    global _redis
    _redis = build_redis_client(settings.redis_url)


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def get_redis() -> Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialised. Call init_redis() first.")
    return _redis
