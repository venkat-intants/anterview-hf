"""SH-2: admin_ops' Redis client must carry the shared Upstash hardening.

The settings themselves are argued for and tested in shared/redis_factory.py +
shared/tests/test_redis_factory.py. What is asserted here is only that this
service's client is the hardened one — admin_ops used to build its own pool with
nothing but decode_responses/max_connections, which is how it inherited the
intermittent "connection forcibly closed" 500s that data_gateway had already
fixed. No I/O happens: redis-py pools are lazy.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from app import redis_client as redis_mod


@pytest_asyncio.fixture()
async def initialised_redis() -> AsyncGenerator[dict[str, Any], None]:
    redis_mod.init_redis()
    yield dict(redis_mod.get_redis().connection_pool.connection_kwargs)
    await redis_mod.close_redis()


async def test_client_detects_dropped_connections_at_checkout(
    initialised_redis: dict[str, Any]
) -> None:
    """The health check is what turns a server-side idle drop from a 500 into
    nothing at all."""
    assert initialised_redis["health_check_interval"] == 30
    assert initialised_redis["socket_keepalive"] is True


async def test_client_cannot_block_forever_on_a_dead_peer(
    initialised_redis: dict[str, Any]
) -> None:
    """Both timeouts default to None (wait forever), which silently exhausts the
    pool instead of raising."""
    assert initialised_redis["socket_connect_timeout"] == 5
    assert initialised_redis["socket_timeout"] == 5


async def test_client_retries_transport_failures(
    initialised_redis: dict[str, Any]
) -> None:
    """Health checks narrow the race but cannot close it — a connection can die
    between the PING and the command."""
    assert initialised_redis["retry"] is not None
    assert initialised_redis["retry_on_error"]


async def test_get_redis_raises_before_init() -> None:
    """Signature/contract of the module is unchanged by the delegation."""
    await redis_mod.close_redis()
    with pytest.raises(RuntimeError, match="Redis not initialised"):
        redis_mod.get_redis()
