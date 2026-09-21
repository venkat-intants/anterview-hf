"""PH4-D3 — process isolation for the (pure, non-executing) code analysers.

Analysis never executes candidate code: ``ast.parse`` (``app/code_quality.py``)
and Pygments tokenising (``app/code_quality.py``, ``app/code_similarity.py``)
only inspect syntax. It still runs in a SEPARATE OS PROCESS, with a timeout,
because a pathological input (a deeply nested expression, a Pygments lexer
pathology) can be slow or exhaust memory, and that must never be able to
affect the request-serving process or any other tenant's analysis.

``ProcessPoolExecutor(max_workers=1, max_tasks_per_child=50, mp_context=spawn)``:
one worker, recycled every 50 tasks so a slow leak in the analyser itself
cannot accumulate for the process's whole lifetime. ``spawn`` (not ``fork``)
so the child never inherits the parent's open DB/Redis connections — this
child has no network and no database, by construction, not by discipline.

RLIMIT_AS/RLIMIT_CPU cap the child on Linux; Windows has no ``resource``
module, so a dev machine skips the caps and logs it ONCE, not silently.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import platform
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, TypeVar

import structlog

from app.config import settings

log = structlog.get_logger(__name__)

T = TypeVar("T")

_MP_CONTEXT = multiprocessing.get_context("spawn")
_POOL: ProcessPoolExecutor | None = None
_POOL_LOCK = asyncio.Lock()
_RLIMIT_WARNED = False


def _apply_rlimits() -> None:
    """Child-process initializer. POSIX only — Windows has no ``resource``
    module, so this logs a WARNING once (not a startup refusal: the demo
    Space and every developer's Windows box must keep running) and returns.
    """
    global _RLIMIT_WARNED
    if platform.system() == "Windows":
        if not _RLIMIT_WARNED:
            log.warning(
                "code_sandbox.rlimits_unsupported",
                platform="Windows",
                detail="RLIMIT_AS/RLIMIT_CPU are POSIX-only; the analysis child runs "
                       "without a memory/CPU cap on this host.",
            )
            _RLIMIT_WARNED = True
        return
    import resource  # noqa: PLC0415 -- POSIX-only; must not be imported on Windows

    mem_bytes = settings.code_analysis_memory_mb * 1024 * 1024
    cpu_seconds = max(1, settings.code_analysis_timeout_seconds)
    try:
        # mypy runs on this codebase from a Windows dev machine, where
        # typeshed has no POSIX resource stub — the platform guard above
        # (unreachable to mypy's static analysis) is what actually keeps this
        # branch from ever running on Windows.
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))  # type: ignore[attr-defined]
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))  # type: ignore[attr-defined]
    except (ValueError, OSError) as exc:  # some containers refuse RLIMIT_AS
        log.warning("code_sandbox.rlimits_failed", error=str(exc))


def _build_pool() -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=1, mp_context=_MP_CONTEXT, initializer=_apply_rlimits, max_tasks_per_child=50,
    )


async def _get_pool() -> ProcessPoolExecutor:
    global _POOL
    async with _POOL_LOCK:
        if _POOL is None:
            _POOL = _build_pool()
        return _POOL


async def run_isolated(
    fn: Callable[..., T], *args: Any, timeout: float | None = None, **kwargs: Any
) -> T:
    """Run ``fn(*args, **kwargs)`` in the single-worker analysis pool and
    return its result. ``fn`` is always one of OUR analyser entry points
    (``app.code_quality.analyse`` / ``app.code_similarity.fingerprint_source``)
    — a plain, picklable, module-level function; candidate SOURCE is only ever
    a string argument to it, never code that runs. ``**kwargs`` exists because
    ``fingerprint_source``'s ``starter_code`` is keyword-only.

    Raises ``concurrent.futures.TimeoutError`` on a timeout, after which the
    (possibly wedged) pool is discarded and rebuilt on the next call so one
    slow analysis cannot wedge every future one. Any other exception ``fn``
    raised propagates unchanged.
    """
    timeout = settings.code_analysis_timeout_seconds if timeout is None else timeout
    pool = await _get_pool()
    loop = asyncio.get_running_loop()
    future = pool.submit(fn, *args, **kwargs)
    try:
        # future.result(timeout) blocks the calling thread, not the event
        # loop -- run it on the default executor so other requests keep
        # being served while this one waits.
        return await loop.run_in_executor(None, future.result, timeout)
    except FutureTimeoutError:
        future.cancel()
        await _reset_pool()
        raise


async def _reset_pool() -> None:
    global _POOL
    async with _POOL_LOCK:
        if _POOL is not None:
            _POOL.shutdown(wait=False, cancel_futures=True)
        _POOL = None


def shutdown_pool() -> None:
    """Best-effort cleanup for tests and app shutdown. Never raises."""
    global _POOL
    if _POOL is not None:
        _POOL.shutdown(wait=False, cancel_futures=True)
        _POOL = None
