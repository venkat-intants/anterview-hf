"""Hosted code execution via JDoodle (coding round — HR workflow Phase 2).

An alternative to ``piston_client`` selected with ``EXECUTION_PROVIDER=jdoodle``.
JDoodle is a hosted HTTP API, so — unlike Piston — it needs **no VM and no
privileged container**: the backend just makes an outbound call. Trade-offs vs
self-hosted Piston: a free-tier daily cap (~200 runs/day) and candidate code
leaving our infra to a third party.

Same contract as ``piston_client``: exposes ``run_code(...) -> ExecResult`` and
``SUPPORTED_LANGUAGES``, reusing the canonical ``ExecResult`` so the grader is
provider-agnostic. Never raises — an auth/limit/network failure returns an
ExecResult with ``error`` set so the grader scores that test case as a fail.

NOTE: JDoodle merges stdout+stderr into one ``output`` field and gives no exit
code or separate stderr, so ``stderr`` is always "" here and grading compares
``stdout`` (the merged output) to the expected output — which is what candidate
solutions print anyway.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from app.config import settings
from app.piston_client import ExecResult  # reuse the canonical result type

log = structlog.get_logger(__name__)

# Our language slug -> (JDoodle language id, versionIndex).
# Version indices drift over time — see https://www.jdoodle.com/compiler-api .
# A wrong index degrades to a failed test (logged), never a crash; bump here if a
# language starts erroring with a version message.
LANGUAGES: dict[str, tuple[str, str]] = {
    "python": ("python3", "4"),
    "javascript": ("nodejs", "4"),
    "typescript": ("typescript", "0"),
    "java": ("java", "4"),
    "cpp": ("cpp17", "0"),
    "c": ("c", "5"),
    "go": ("go", "4"),
    "csharp": ("csharp", "4"),
    "ruby": ("ruby", "4"),
    "rust": ("rust", "4"),
}

SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(LANGUAGES.keys())

# Retry transient server/network failures only — NOT 429 (that's the daily cap,
# retrying won't help) and NOT 401 (bad credentials).
_RETRY_STATUSES: frozenset[int] = frozenset({500, 502, 503, 504})
_MAX_ATTEMPTS: int = 3
_BACKOFF_BASE_SECONDS: float = 0.5


async def run_code(
    *, language: str, source: str, stdin: str = "", time_limit_ms: int | None = None
) -> ExecResult:
    """Execute ``source`` in ``language`` against ``stdin`` via JDoodle. Never raises."""
    slug = (language or "").lower().strip()
    if slug not in LANGUAGES:
        return ExecResult("", "", None, False, error=f"unsupported language '{language}'")
    if not settings.jdoodle_client_id or not settings.jdoodle_client_secret:
        return ExecResult("", "", None, False, error="jdoodle credentials not configured")

    jd_language, version_index = LANGUAGES[slug]
    body = {
        "clientId": settings.jdoodle_client_id,
        "clientSecret": settings.jdoodle_client_secret,
        "script": source,
        "stdin": stdin,
        "language": jd_language,
        "versionIndex": version_index,
    }
    url = f"{settings.jdoodle_api_url.rstrip('/')}/execute"
    run_ms = int(time_limit_ms or settings.code_run_timeout_ms)
    # JDoodle enforces its own (~10-20s) execution cap; our HTTP timeout sits above it.
    client_timeout = run_ms / 1000 + 20

    last_error = "no attempt made"
    for attempt in range(_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=client_timeout) as client:
                resp = await client.post(url, json=body)
        except httpx.RequestError as exc:
            last_error = f"runner unreachable: {exc}"
        else:
            if resp.status_code == 200:
                return _parse(resp.json())
            if resp.status_code == 401:
                log.error("jdoodle.auth_failed")
                return ExecResult("", "", None, False, error="jdoodle auth failed (check credentials)")
            if resp.status_code == 429:
                log.warning("jdoodle.daily_limit_reached")
                return ExecResult("", "", None, False, error="jdoodle daily limit reached")
            last_error = f"runner HTTP {resp.status_code}: {resp.text[:160]}"
            if resp.status_code not in _RETRY_STATUSES:
                break
        if attempt < _MAX_ATTEMPTS - 1:
            await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2**attempt))
    return ExecResult("", "", None, False, error=last_error)


def _parse(payload: dict[str, Any]) -> ExecResult:
    """Map a JDoodle /execute response to an ExecResult.

    JDoodle returns ``{output, statusCode, memory, cpuTime}``. ``output`` is the
    merged stdout+stderr; there is no exit code or stream split. A non-OK body
    ``statusCode`` with no output is surfaced as a runner error.
    """
    output = str(payload.get("output") or "")
    status = payload.get("statusCode")
    if status not in (200, None) and not output:
        return ExecResult("", "", None, False, error=f"jdoodle status {status}")
    # JDoodle signals its own wall-clock kill inside the output text.
    lowered = output.lower()
    timed_out = "time limit exceeded" in lowered
    return ExecResult(
        stdout=output[:64_000],
        stderr="",
        exit_code=None if timed_out else 0,
        timed_out=timed_out,
    )
