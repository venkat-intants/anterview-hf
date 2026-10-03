"""Redis client singleton for interview_core.

The pool itself is built by ``shared.redis_factory.build_redis_client``, which
is the single place the serverless-Redis hardening lives (idle health checks,
TCP keepalive, connect/read timeouts, transport-error retries). This module
keeps only the singleton lifecycle the FastAPI app drives.

This docstring used to say the module "Mirrors
services/data_gateway/app/redis_client.py exactly". It did not — data_gateway
had been hardened against Upstash dropping idle connections and this file had
not, building a bare pool with nothing but ``decode_responses`` and
``max_connections``. A claim of parity is not parity, and the claim is exactly
what let the gap sit unnoticed; delegating to one factory makes the parity real
instead of asserted. See ``shared/redis_factory.py`` for the failure mode and
the reasoning behind each setting.

interview_core uses Redis for:
  - S3-005: jti blocklist (replay prevention)  key: jwt:jti:<jti>  TTL = token exp
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
    """Create the Redis connection pool. Call at application startup."""
    global _redis
    _redis = build_redis_client(settings.redis_url, decode_responses=True, max_connections=20)


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
