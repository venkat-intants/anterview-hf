"""PH4-D3 — process isolation for the (pure, non-executing) code analysers.

Analysis never executes candidate code: ``ast.parse`` (``app/code_quality.py``)
and Pygments tokenising (``app/code_quality.py``, ``app/code_similarity.py``)
only inspect syntax. It still runs in a SEPARATE OS PROCESS, with a timeout,
because a pathological input (a deeply nested expression, a Pygments lexer
pathology) can be slow or exhaust memory, and that must never affect the
request-serving process or any other analysis.

ONE PROCESS PER TASK, NOT A SHARED POOL
A security review found that the first version -- a single-worker
``ProcessPoolExecutor`` -- broke that promise in two ways, both because the
pool was shared:

- **One dead worker disabled analysis for every tenant, permanently.** An
  abrupt worker exit (SIGXCPU, OOM, a C-level crash) leaves a
  ``ProcessPoolExecutor`` broken; only a timeout rebuilt it, so every later
  submission was stored as a permanent ``failed`` until the service
  restarted. Worse, ``RLIMIT_CPU`` is a budget for a process's whole life,
  and a pooled worker served 50 tasks, so an ordinary pair of large answers
  was enough to exhaust it.
- **Resetting the pool cancelled other callers' work.** ``cancel_futures``
  turned a queued caller's future into ``asyncio.CancelledError`` -- a
  BaseException, which walked straight past every ``except Exception`` and
  ended the whole reminder sweep: reminders, no-shows, SLAs, offer expiry.

A process per task has no shared state to break and nothing of anyone
else's to cancel. Each one gets its own CPU and memory limit (so the CPU
limit really is per task), is killed outright on timeout -- ``shutdown`` on a
pool never stopped a running worker -- and is reaped every time. Concurrency
is capped by a semaphore, and the timeout clock starts only once a task is
actually running, so time spent queued behind a slow analysis never fails a
healthy one.

The wait is a poll on the pipe, not ``future.result`` on the default
executor: that held a thread per call, and enough concurrent on-demand
requests would have filled the executor that asyncpg also resolves hosts
through.

THE CHILD
``spawn`` (not ``fork``), so it never inherits open DB or Redis connections.
It clears its environment before touching the input -- so the inherited
DATABASE_URL and API keys are gone -- and it never imports ``app.config``
through this module: the limits arrive as arguments. It has network access;
the claim is not that it cannot reach a network but that nothing in it ever
executes the code it is given.

RLIMIT_AS / RLIMIT_CPU are applied separately, so a container that refuses
one still gets the other. Windows has no ``resource`` module; a developer
machine runs uncapped and says so once.
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
import os
import platform
from collections.abc import Callable
from typing import Any, TypeVar

import structlog

log = structlog.get_logger(__name__)

T = TypeVar("T")

_MP_CONTEXT = multiprocessing.get_context("spawn")
_POLL_SECONDS = 0.02
_RLIMIT_WARNED = False

# Created lazily and per event loop: an asyncio.Semaphore is bound to the loop
# it was first used on, and tests run several loops in one process.
_SEM: asyncio.Semaphore | None = None
_SEM_LOOP: asyncio.AbstractEventLoop | None = None


class SandboxUnavailableError(RuntimeError):
    """The sandbox could not run at all (a process would not start).

    Infrastructure, not the input: callers must NOT record this as a
    permanent ``failed`` report, or the submission is never analysed again.
    """


class AnalysisTimeoutError(TimeoutError):
    """The input took longer than the timeout and its process was killed.

    A property of the input, so recording it as ``failed`` is correct.
    Subclasses the builtin ``TimeoutError`` -- which is what
    ``concurrent.futures.TimeoutError`` is on Python 3.11+ -- so existing
    ``except FutureTimeoutError`` handlers keep working."""


class AnalysisFailedError(RuntimeError):
    """The analyser raised on this input, or its process died without
    answering (the CPU or memory limit, a crash in a C extension).

    ``error_class`` is a class name only -- never source, never a message
    that might quote it."""

    def __init__(self, error_class: str) -> None:
        super().__init__(error_class)
        self.error_class = error_class


# ---------------------------------------------------------------------------
# The child
# ---------------------------------------------------------------------------
def _apply_rlimits(mem_mb: int, cpu_seconds: int) -> None:
    """POSIX only. Each limit in its own ``try``: a container that refuses
    RLIMIT_AS must not silently lose RLIMIT_CPU too."""
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

    mem_bytes = mem_mb * 1024 * 1024
    # mypy runs on this codebase from a Windows dev machine, where typeshed
    # has no POSIX resource stub -- the platform guard above (unreachable to
    # mypy's static analysis) is what keeps this branch off Windows.
    try:
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))  # type: ignore[attr-defined]
    except (ValueError, OSError) as exc:
        log.warning("code_sandbox.rlimit_as_failed", error_class=type(exc).__name__)
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))  # type: ignore[attr-defined]
    except (ValueError, OSError) as exc:
        log.warning("code_sandbox.rlimit_cpu_failed", error_class=type(exc).__name__)


def _child_main(
    conn: Any, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any],
    mem_mb: int, cpu_seconds: int,
) -> None:
    """Entry point of the analysis process. Module-level so ``spawn`` can
    pickle it. Sends exactly one message: ``("ok", result)`` or
    ``("err", class_name)``."""
    os.environ.clear()  # inherited secrets are nobody's business in here
    _apply_rlimits(mem_mb, cpu_seconds)
    try:
        result = fn(*args, **kwargs)
        conn.send(("ok", result))
    except BaseException as exc:  # noqa: BLE001 -- report every failure, as a class name only
        # If even that fails, the parent treats the silence as a failure.
        with contextlib.suppress(BaseException):
            conn.send(("err", type(exc).__name__))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The parent
# ---------------------------------------------------------------------------
def _semaphore(limit: int) -> asyncio.Semaphore:
    global _SEM, _SEM_LOOP
    loop = asyncio.get_running_loop()
    if _SEM is None or _SEM_LOOP is not loop:
        _SEM = asyncio.Semaphore(max(1, limit))
        _SEM_LOOP = loop
    return _SEM


def _has_message(parent: Any) -> bool:
    """True if the child has written something. A pipe whose writer is gone
    with nothing in it is not a message: POSIX reports that as readable-EOF,
    but Windows raises ``BrokenPipeError`` from ``poll()`` itself -- a real
    difference the first version missed, caught by driving a real dying
    process rather than a mock."""
    try:
        return bool(parent.poll())
    except (EOFError, OSError):
        return False


async def _await_result(parent: Any, proc: Any) -> Any:
    """Wait for the child's one message without holding a thread, so this is
    cancellable and never starves the default executor."""
    while True:
        if _has_message(parent):
            try:
                kind, payload = parent.recv()
            except (EOFError, OSError) as exc:
                raise AnalysisFailedError("WorkerExited") from exc
            if kind == "ok":
                return payload
            raise AnalysisFailedError(str(payload))
        if not proc.is_alive():
            # It may have written its answer and exited between the two checks.
            if _has_message(parent):
                continue
            # Died without answering: a resource limit or a crash on THIS input.
            raise AnalysisFailedError(f"WorkerExited({proc.exitcode})")
        await asyncio.sleep(_POLL_SECONDS)


def _reap(proc: Any) -> None:
    """Never leave a process behind: kill it if it is still running, then
    collect it. ``kill`` is SIGKILL on POSIX and TerminateProcess on Windows,
    so a wedged analysis cannot outlive its timeout."""
    try:
        if proc.is_alive():
            proc.kill()
        proc.join(timeout=2)
    except (OSError, ValueError, AssertionError):
        pass


async def run_isolated(
    fn: Callable[..., T], *args: Any, timeout: float | None = None, **kwargs: Any
) -> T:
    """Run ``fn(*args, **kwargs)`` in a fresh analysis process and return its
    result. ``fn`` is always one of OUR analyser entry points
    (``app.code_quality.analyse`` / ``app.code_similarity.fingerprint_source``)
    -- a module-level function; candidate SOURCE is only ever a string argument
    to it, never code that runs.

    Raises ``AnalysisTimeoutError`` (the input was too slow; its process is dead),
    ``AnalysisFailedError`` (the analyser raised, or its process died on the input)
    or ``SandboxUnavailableError`` (no process could be started -- retry later).
    """
    from app.config import settings  # noqa: PLC0415 -- parent only; keeps config out of the child

    timeout = settings.code_analysis_timeout_seconds if timeout is None else timeout
    # The CPU limit is a backstop behind the wall-clock kill, and it is now
    # per TASK: the process lives for exactly one analysis.
    cpu_seconds = max(1, int(timeout)) + 5
    mem_mb = settings.code_analysis_memory_mb
    limit = getattr(settings, "code_analysis_max_concurrency", 1)

    async with _semaphore(limit):
        # The timeout starts HERE, once the task is really running -- queueing
        # behind someone else's slow analysis is not this input's fault.
        # Untyped on purpose: Pipe() returns Connection on POSIX but
        # PipeConnection on Windows, and only one of those exists per host.
        parent, child = _MP_CONTEXT.Pipe(duplex=False)
        proc = _MP_CONTEXT.Process(
            target=_child_main, args=(child, fn, args, kwargs, mem_mb, cpu_seconds), daemon=True,
        )
        try:
            proc.start()
        except OSError as exc:
            parent.close()
            child.close()
            log.error("code_sandbox.spawn_failed", error_class=type(exc).__name__)
            raise SandboxUnavailableError(type(exc).__name__) from exc
        child.close()  # the parent keeps only its own end
        try:
            result: T = await asyncio.wait_for(_await_result(parent, proc), timeout)
            return result
        except TimeoutError as exc:  # asyncio.TimeoutError is TimeoutError on 3.11+
            raise AnalysisTimeoutError(f"analysis exceeded {timeout}s") from exc
        finally:
            _reap(proc)
            parent.close()


def shutdown_pool() -> None:
    """Kept for callers of the old pool API. There is no pool: every task's
    process is reaped when it finishes, so there is nothing left to stop."""
