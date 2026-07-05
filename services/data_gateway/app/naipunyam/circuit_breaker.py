"""Simple in-process circuit breaker for external API calls.

State machine: CLOSED → OPEN (on failure_threshold failures)
                      → HALF_OPEN (after recovery_timeout seconds)
                      → CLOSED (on half_open_max_calls successful calls)
                      → OPEN (if any call fails in HALF_OPEN state)

Usage::

    cb = CircuitBreaker("naipunyam", failure_threshold=5, recovery_timeout=30.0)
    result = await cb.call(some_async_fn, arg1, kwarg=val)

Raises ``CircuitOpenError`` if the breaker is OPEN and the recovery window has
not yet elapsed.  Callers should catch this and return a 503 to the client.
"""

from __future__ import annotations

import asyncio
import enum
import time
from collections.abc import Callable
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class CircuitOpenError(Exception):
    """Raised when a call is attempted while the circuit breaker is OPEN."""


class _State(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe (asyncio) in-process circuit breaker.

    Parameters
    ----------
    name:
        Human-readable label used in structured log events.
    failure_threshold:
        Number of consecutive failures before the breaker opens.  Default: 5.
    recovery_timeout:
        Seconds to wait in OPEN state before transitioning to HALF_OPEN.
        Default: 30.0.
    half_open_max_calls:
        Number of probe calls allowed in HALF_OPEN state.  If all succeed the
        breaker closes.  A single failure re-opens immediately.  Default: 3.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
    ) -> None:
        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls

        self._state: _State = _State.CLOSED
        self._failure_count: int = 0
        self._opened_at: float = 0.0
        self._half_open_successes: int = 0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        """Return the current state label (closed / open / half_open)."""
        return self._state.value

    async def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute *fn* through the breaker.

        Parameters
        ----------
        fn:
            An async callable to invoke.
        *args, **kwargs:
            Forwarded to *fn*.

        Raises
        ------
        CircuitOpenError
            If the breaker is OPEN and the recovery timeout has not elapsed.
        Exception
            Any exception raised by *fn* is re-raised after updating internal
            state (failure count / re-open logic).
        """
        async with self._lock:
            await self._maybe_transition()

            if self._state is _State.OPEN:
                log.warning(
                    "circuit_breaker.rejected",
                    name=self._name,
                    state=self._state.value,
                )
                raise CircuitOpenError(
                    f"Circuit breaker '{self._name}' is OPEN. "
                    "Service unavailable — try again later."
                )

        # Execute outside the lock so other coroutines are not blocked
        # while the I/O call is in flight.
        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:
            async with self._lock:
                await self._on_failure()
            raise exc

        async with self._lock:
            await self._on_success()

        return result

    # ------------------------------------------------------------------
    # Internal state-machine helpers  (called while _lock is held)
    # ------------------------------------------------------------------

    async def _maybe_transition(self) -> None:
        """Check whether the OPEN → HALF_OPEN transition is due."""
        if self._state is _State.OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._recovery_timeout:
                self._state = _State.HALF_OPEN
                self._half_open_successes = 0
                log.info(
                    "circuit_breaker.half_open",
                    name=self._name,
                    elapsed_s=round(elapsed, 2),
                )

    async def _on_success(self) -> None:
        """Handle a successful call."""
        if self._state is _State.HALF_OPEN:
            self._half_open_successes += 1
            if self._half_open_successes >= self._half_open_max_calls:
                self._state = _State.CLOSED
                self._failure_count = 0
                self._half_open_successes = 0
                log.info("circuit_breaker.closed", name=self._name)
        elif self._state is _State.CLOSED:
            # Reset consecutive failure count on any success.
            self._failure_count = 0

    async def _on_failure(self) -> None:
        """Handle a failed call."""
        if self._state is _State.HALF_OPEN:
            # Any failure in HALF_OPEN immediately re-opens the breaker.
            self._state = _State.OPEN
            self._opened_at = time.monotonic()
            self._half_open_successes = 0
            log.warning(
                "circuit_breaker.reopened",
                name=self._name,
                reason="failure_during_half_open",
            )
        else:
            # CLOSED state: increment and check threshold.
            self._failure_count += 1
            if self._failure_count >= self._failure_threshold:
                self._state = _State.OPEN
                self._opened_at = time.monotonic()
                log.warning(
                    "circuit_breaker.opened",
                    name=self._name,
                    failure_count=self._failure_count,
                )
