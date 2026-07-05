"""Sandboxed code execution via Piston (coding round — HR workflow Phase 2).

Runs untrusted candidate code OUTSIDE our infra so our services stay light and
never execute attacker-controlled code. ``EXECUTION_PROVIDER=piston`` uses the
free public Piston API now; point ``PISTON_API_URL`` at a self-hosted Piston
in-region later for India residency + the L1 cost cap (same code).

DESIGN: never raise into a 500 that loses an attempt — an unreachable runner or a
timeout returns an ExecResult with ``error``/``timed_out`` set so the grader can
score that test case as a fail. Language→version is resolved once from /runtimes
and cached (the public API requires an explicit version).

Approved provider: the user selected Piston for v1 (free, no card). Self-hosting
in Mumbai is the Tier-2 path; revisit per cfo-cost-watcher before bid.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)

# Our language slug -> Piston identifiers (name/aliases as they appear in
# /runtimes) + the source filename to send (matters for compiled langs, e.g.
# Java expects a public class `Main` in Main.java).
LANGUAGES: dict[str, dict[str, Any]] = {
    "python": {"aliases": {"python", "python3", "py"}, "file": "main.py"},
    "javascript": {"aliases": {"javascript", "node", "js"}, "file": "main.js"},
    "typescript": {"aliases": {"typescript", "ts"}, "file": "main.ts"},
    "java": {"aliases": {"java"}, "file": "Main.java"},
    "cpp": {"aliases": {"c++", "cpp", "g++"}, "file": "main.cpp"},
    "c": {"aliases": {"c", "gcc"}, "file": "main.c"},
    "go": {"aliases": {"go", "golang"}, "file": "main.go"},
    "csharp": {"aliases": {"csharp", "cs", "c#", "mono", "csharp.net"}, "file": "main.cs"},
    "ruby": {"aliases": {"ruby", "rb"}, "file": "main.rb"},
    "rust": {"aliases": {"rust", "rs"}, "file": "main.rs"},
}

SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(LANGUAGES.keys())

_RETRY_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})
_MAX_ATTEMPTS: int = 3
_BACKOFF_BASE_SECONDS: float = 0.5

# Resolved {slug: {"language": <piston name>, "version": <ver>, "file": <name>}}.
# NOTE: process-local — NOT shared across Uvicorn workers. Benign: each worker
# resolves /runtimes independently once on first use (Tier-2 could move this to Redis).
_runtime_cache: dict[str, dict[str, str]] | None = None
_runtime_lock = asyncio.Lock()


@dataclass(frozen=True)
class ExecResult:
    """Outcome of one execution. ``error`` set => runner failure (treat as fail)."""

    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out and self.exit_code == 0


async def _load_runtimes() -> dict[str, dict[str, str]]:
    """Fetch + cache language→version from Piston /runtimes (once)."""
    global _runtime_cache
    if _runtime_cache is not None:
        return _runtime_cache
    async with _runtime_lock:
        if _runtime_cache is not None:  # double-checked after acquiring the lock
            return _runtime_cache
        url = f"{settings.piston_api_url.rstrip('/')}/runtimes"
        resolved: dict[str, dict[str, str]] = {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
            resp.raise_for_status()
            runtimes: list[dict[str, Any]] = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("piston.runtimes_unavailable", error=str(exc))
            return {}  # not cached — retried on the next call
        for slug, meta in LANGUAGES.items():
            aliases = meta["aliases"]
            for rt in runtimes:
                names = {str(rt.get("language", "")).lower(), *(
                    str(a).lower() for a in rt.get("aliases", [])
                )}
                if names & aliases and rt.get("version"):
                    resolved[slug] = {
                        "language": str(rt["language"]),
                        "version": str(rt["version"]),
                        "file": str(meta["file"]),
                    }
                    break
        _runtime_cache = resolved
        log.info("piston.runtimes_loaded", count=len(resolved))
        return resolved


async def run_code(
    *, language: str, source: str, stdin: str = "", time_limit_ms: int | None = None
) -> ExecResult:
    """Execute ``source`` in ``language`` against ``stdin``. Never raises."""
    slug = (language or "").lower().strip()
    if slug not in LANGUAGES:
        return ExecResult("", "", None, False, error=f"unsupported language '{language}'")
    runtimes = await _load_runtimes()
    rt = runtimes.get(slug)
    if rt is None:
        return ExecResult("", "", None, False, error="execution runtime unavailable")

    run_ms = int(time_limit_ms or settings.code_run_timeout_ms)
    body = {
        "language": rt["language"],
        "version": rt["version"],
        "files": [{"name": rt["file"], "content": source}],
        "stdin": stdin,
        "run_timeout": run_ms,
        "compile_timeout": 10_000,
    }
    url = f"{settings.piston_api_url.rstrip('/')}/execute"
    # Our HTTP timeout sits ABOVE the runner's own budget (run_ms + the 10s
    # compile_timeout, in seconds) plus a 10s network margin — so Piston returns a
    # timed-out/SIGKILL verdict we can grade, rather than us aborting first.
    client_timeout = (run_ms + 10_000) / 1000 + 10

    resp: httpx.Response | None = None
    last_error = "no attempt made"
    for attempt in range(_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=client_timeout) as client:
                resp = await client.post(url, json=body)
        except httpx.RequestError as exc:
            resp = None
            last_error = f"runner unreachable: {exc}"
        else:
            if resp.status_code == 200:
                return _parse(resp.json())
            last_error = f"runner HTTP {resp.status_code}: {resp.text[:160]}"
            if resp.status_code not in _RETRY_STATUSES:
                break
        if attempt < _MAX_ATTEMPTS - 1:
            await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2**attempt))
    return ExecResult("", "", None, False, error=last_error)


def _parse(payload: dict[str, Any]) -> ExecResult:
    """Map a Piston /execute response to an ExecResult.

    A non-zero compile step is surfaced as a failed run (stderr from compile).
    """
    compile_step = payload.get("compile") or {}
    if compile_step and compile_step.get("code") not in (0, None):
        return ExecResult(
            stdout="",
            stderr=str(compile_step.get("stderr") or compile_step.get("output") or "")[:8000],
            exit_code=compile_step.get("code"),
            timed_out=str(compile_step.get("signal") or "") == "SIGKILL",
            error="compile error",
        )
    run = payload.get("run") or {}
    signal = str(run.get("signal") or "")
    return ExecResult(
        stdout=str(run.get("stdout") or "")[:64_000],
        stderr=str(run.get("stderr") or "")[:8000],
        exit_code=run.get("code"),
        # Piston SIGKILLs a run that exceeds the wall-clock/memory budget.
        timed_out=signal in {"SIGKILL", "SIGXCPU"},
    )
