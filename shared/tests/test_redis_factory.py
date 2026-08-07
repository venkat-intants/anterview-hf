"""Regression suite for ``shared.redis_factory``.

The bug this guards: three of the four services built their Redis pool with
only ``decode_responses`` + ``max_connections``, so Upstash's silent
idle-connection close handed them dead pooled sockets and they emitted
intermittent 500s (WinError 64 / 10054 on Windows, ECONNRESET on Linux). The
factory exists so that hardening cannot be missing from one service and present
in another.

None of these tests open a socket. redis-py pools are lazy, so a client can be
fully constructed and inspected against a URL that resolves to nothing — which
is also the property that makes the factory safe to call at import time, and is
asserted below rather than assumed.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest
from redis.asyncio.connection import SSLConnection
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from shared.redis_factory import build_redis_client

# Neither host is ever contacted; they only exercise the two URL schemes.
_PLAIN_URL = "redis://localhost:6379/0"
_TLS_URL = "rediss://default:not-a-real-token@fake.upstash.io:6379"


def _connection_kwargs(url: str = _PLAIN_URL, **kwargs: Any) -> dict[str, Any]:
    client = build_redis_client(url, **kwargs)
    return dict(client.connection_pool.connection_kwargs)


# --------------------------------------------------------------------------
# The hardening is present at all
# --------------------------------------------------------------------------


def test_idle_connections_are_health_checked_before_reuse() -> None:
    """The core defence: a connection idle >30s is PINGed before it is handed
    back, so an Upstash-closed socket is replaced at checkout instead of
    failing mid-command."""
    assert _connection_kwargs()["health_check_interval"] == 30


def test_tcp_keepalive_is_enabled() -> None:
    """Keepalives stop NAT/idle timers from dropping the connection at all —
    prevention, where the health check is detection."""
    assert _connection_kwargs()["socket_keepalive"] is True


def test_socket_operations_cannot_hang_forever() -> None:
    """Both timeouts default to None in redis-py. Left unset, a connect to a
    black-holed peer pins a pool slot for the life of the process and the
    service dies of pool exhaustion without logging an error."""
    kwargs = _connection_kwargs()
    assert kwargs["socket_connect_timeout"] == 5
    assert kwargs["socket_timeout"] == 5


def test_transport_errors_are_listed_for_client_level_retry() -> None:
    """``retry_on_error`` is separate from Retry's own supported-error tuple:
    it is the list consulted in execute_command. Dropping it would leave the
    Retry object looking correct while dropped connections still surfaced."""
    retry_on_error = _connection_kwargs()["retry_on_error"]
    assert RedisConnectionError in retry_on_error
    assert RedisTimeoutError in retry_on_error


def test_retry_policy_is_bounded_exponential_backoff() -> None:
    retry = _connection_kwargs()["retry"]
    assert isinstance(retry, Retry)
    assert isinstance(retry._backoff, ExponentialBackoff)
    assert RedisConnectionError in retry._supported_errors
    assert RedisTimeoutError in retry._supported_errors


# --------------------------------------------------------------------------
# The hardening actually does something
# --------------------------------------------------------------------------


async def test_a_dropped_connection_is_retried_rather_than_surfaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: the first two attempts die the way an Upstash-closed
    socket dies, and the caller still gets its value instead of a 500."""
    monkeypatch.setattr("redis.asyncio.retry.sleep", _no_sleep)
    retry: Retry = _connection_kwargs()["retry"]
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RedisConnectionError("Connection closed by server")
        return "pong"

    assert await retry.call_with_retry(flaky, _ignore_failure) == "pong"
    assert attempts == 3


async def test_a_timeout_is_retried_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """socket_timeout=5 converts a lost response into a TimeoutError, so that
    error must be retryable or the timeout would trade a hang for a 500."""
    monkeypatch.setattr("redis.asyncio.retry.sleep", _no_sleep)
    retry: Retry = _connection_kwargs()["retry"]
    attempts = 0

    async def slow_then_fine() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RedisTimeoutError("Timeout reading from socket")
        return "pong"

    assert await retry.call_with_retry(slow_then_fine, _ignore_failure) == "pong"
    assert attempts == 2


async def test_a_permanently_dead_endpoint_still_gives_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying must not become hanging: if Redis is genuinely down the error
    reaches the caller after a bounded number of attempts (1 + 3 retries)."""
    monkeypatch.setattr("redis.asyncio.retry.sleep", _no_sleep)
    retry: Retry = _connection_kwargs()["retry"]
    attempts = 0

    async def always_dead() -> str:
        nonlocal attempts
        attempts += 1
        raise RedisConnectionError("Connection refused")

    with pytest.raises(RedisConnectionError):
        await retry.call_with_retry(always_dead, _ignore_failure)
    assert attempts == 4


async def test_application_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WRONGTYPE or bad command is a bug, not a flaky socket. Retrying it
    would quadruple the latency of every genuine failure and hide the cause."""
    monkeypatch.setattr("redis.asyncio.retry.sleep", _no_sleep)
    retry: Retry = _connection_kwargs()["retry"]
    attempts = 0

    async def wrong_type() -> str:
        nonlocal attempts
        attempts += 1
        raise RedisError("WRONGTYPE Operation against a key holding the wrong kind of value")

    with pytest.raises(RedisError):
        await retry.call_with_retry(wrong_type, _ignore_failure)
    assert attempts == 1


async def test_retry_backoff_stays_inside_the_turn_latency_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """p95 turn latency must stay under 2s, so an unbounded retry storm is not
    an acceptable trade for the 500 it replaces. base=0.1/cap=2.0 with 3
    retries sleeps 0.2 + 0.4 + 0.8s at worst."""
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("redis.asyncio.retry.sleep", record)
    retry: Retry = _connection_kwargs()["retry"]

    async def always_dead() -> str:
        raise RedisConnectionError("Connection refused")

    with pytest.raises(RedisConnectionError):
        await retry.call_with_retry(always_dead, _ignore_failure)

    assert slept == [0.2, 0.4, 0.8]
    assert max(slept) <= 2.0
    assert sum(slept) < 2.0


# --------------------------------------------------------------------------
# Contract with callers
# --------------------------------------------------------------------------


def test_building_a_client_opens_no_connection() -> None:
    """The factory must be callable at import/startup time and in tests with
    no Redis running — which only holds while the pool stays lazy."""
    client = build_redis_client("redis://this-host-does-not-exist.invalid:6379/0")
    pool = client.connection_pool
    assert pool._available_connections == []
    assert pool._in_use_connections == set()


def test_defaults_match_what_the_services_already_relied_on() -> None:
    """Callers are being migrated onto this factory, so its defaults must be
    the values they passed by hand — otherwise the migration is a behaviour
    change smuggled in as a refactor."""
    client = build_redis_client(_PLAIN_URL)
    assert client.connection_pool.connection_kwargs["decode_responses"] is True
    assert client.connection_pool.max_connections == 20


def test_callers_can_override_the_defaults() -> None:
    client = build_redis_client(_PLAIN_URL, decode_responses=False, max_connections=50)
    assert client.connection_pool.connection_kwargs["decode_responses"] is False
    assert client.connection_pool.max_connections == 50


def test_tls_urls_get_the_same_hardening() -> None:
    """Upstash is rediss://, so the TLS path is the one that actually runs in
    production. A scheme-dependent gap here would mean the hardening was only
    ever exercised locally."""
    client = build_redis_client(_TLS_URL)
    kwargs = client.connection_pool.connection_kwargs
    assert client.connection_pool.connection_class is SSLConnection
    assert kwargs["health_check_interval"] == 30
    assert kwargs["socket_keepalive"] is True
    assert kwargs["socket_timeout"] == 5
    assert isinstance(kwargs["retry"], Retry)


def test_each_client_gets_its_own_pool() -> None:
    """The factory is a builder, not a singleton. Services keep their own
    init/close lifecycle, so two calls must not share a pool — closing one
    would otherwise kill the other's connections."""
    first = build_redis_client(_PLAIN_URL)
    second = build_redis_client(_PLAIN_URL)
    assert first.connection_pool is not second.connection_pool


# --------------------------------------------------------------------------
# shared/ stays importable from all four service images
# --------------------------------------------------------------------------


def test_factory_stays_dependency_light() -> None:
    """shared/ is COPY'd into every service image, so an import of ``app.*`` or
    ``services.*`` here would break all four at container start — and reaching
    for a heavier third-party dep would put it in four requirements files. The
    allowlist is deliberately narrow: widening it should be a decision someone
    makes on purpose, not a diff nobody notices."""
    allowed = {"__future__", "typing", "redis", "pydantic", "structlog"}
    source = pathlib.Path(__file__).parent.parent / "redis_factory.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])

    assert roots <= allowed, f"disallowed imports in redis_factory: {sorted(roots - allowed)}"


async def _ignore_failure(error: RedisError) -> None:
    """Retry.call_with_retry awaits its failure handler; these tests assert on
    attempt counts rather than on the handler, so it is a no-op."""


async def _no_sleep(seconds: float) -> None:
    """Skip the real backoff so the retry tests stay fast; the schedule itself
    is asserted in test_retry_backoff_stays_inside_the_turn_latency_budget."""
