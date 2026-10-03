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


def get_redis() -> Redis:
    """Return the Redis client singleton. Raises if not initialised."""
    if _redis is None:
        raise RuntimeError("Redis not initialised. Call init_redis() first.")
    return _redis
