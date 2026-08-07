"""data_gateway's Redis client must carry the shared Upstash hardening.

Why this file exists at all: data_gateway is where the hardening was ORIGINALLY
written, after Upstash's silent idle-connection close handed the pool dead
sockets and ``/auth/refresh`` started emitting intermittent 500s (WinError 64 /
10054 on Windows, ECONNRESET on Linux). The settings were then copied into
``shared/redis_factory.py`` and the other three services were moved onto it —
leaving this one, the service that actually had the bug, as the only remaining
hand-written copy and the only one with no test holding the settings in place.
Nothing here would have failed if someone had deleted ``health_check_interval``
from this module; that is the gap being closed.

The argument for each individual setting lives in ``shared/redis_factory.py``
and is tested in ``shared/tests/test_redis_factory.py``. What is pinned here is
that *this service's* client is the hardened one.

No I/O happens in any of these tests — redis-py connection pools are lazy, so a
client is fully constructible and inspectable against a URL that resolves to
nothing. That is also what makes ``init_redis()`` safe to call at startup before
Redis is reachable, so it is asserted below rather than assumed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from app import redis_client as redis_mod


@pytest_asyncio.fixture
async def pool_kwargs() -> AsyncGenerator[dict[str, Any], None]:
    """Initialise the singleton and hand back the pool's connection settings."""
    redis_mod.init_redis()
    try:
        yield dict(redis_mod.get_redis().connection_pool.connection_kwargs)
    finally:
        # Restore the module global; later test files patch get_redis rather than
        # using the singleton, but leaving a live client behind is still litter.
        await redis_mod.close_redis()


@pytest.mark.asyncio
async def test_dropped_connections_are_detected_at_checkout(
    pool_kwargs: dict[str, Any],
) -> None:
    """The health check is what turns a server-side idle drop into a no-op.

    Without it the failure lands on the next command written to an
    already-dead socket, which is why the original bug looked like a random
    500 from an endpoint that did nothing but a Redis GET.
    """
    assert pool_kwargs["health_check_interval"] == 30
    assert pool_kwargs["socket_keepalive"] is True


@pytest.mark.asyncio
async def test_a_dead_peer_cannot_block_the_pool_forever(
    pool_kwargs: dict[str, Any],
) -> None:
    """Both timeouts default to ``None`` — i.e. wait forever.

    A connect to a black-holed peer then pins an event-loop task and a pool slot
    for the life of the process, which exhausts the pool without logging a
    single error.
    """
    assert pool_kwargs["socket_connect_timeout"] == 5
    assert pool_kwargs["socket_timeout"] == 5


@pytest.mark.asyncio
async def test_transport_failures_are_retried(pool_kwargs: dict[str, Any]) -> None:
    """Health checks narrow the race but cannot close it — a connection can die
    between the PING and the command."""
    assert pool_kwargs["retry"] is not None
    assert pool_kwargs["retry_on_error"]


@pytest.mark.asyncio
async def test_responses_are_decoded_to_str(pool_kwargs: dict[str, Any]) -> None:
    """Callers store text (JTIs, epochs, JSON) and compare against ``str``.

    Flipping this would not raise anywhere — every comparison would just start
    returning False, silently breaking session revocation.
    """
    assert pool_kwargs["decode_responses"] is True


@pytest.mark.asyncio
async def test_init_does_not_connect_to_an_unreachable_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup must not depend on Redis already being reachable.

    ``init_redis()`` runs in the FastAPI lifespan, so an eager connect would turn
    a momentarily-unavailable Upstash into a crash loop rather than a few failed
    requests. Asserted against a port with nothing listening: if the pool were
    eager, ``socket_connect_timeout=5`` would make this raise rather than return.
    """
    monkeypatch.setattr(redis_mod.settings, "redis_url", "redis://127.0.0.1:1/0")

    redis_mod.init_redis()
    try:
        assert redis_mod.get_redis() is not None
    finally:
        await redis_mod.close_redis()


@pytest.mark.asyncio
async def test_get_redis_raises_before_init() -> None:
    """The module contract is unchanged by delegating the pool build."""
    await redis_mod.close_redis()
    with pytest.raises(RuntimeError, match="Redis not initialised"):
        redis_mod.get_redis()
