"""Execution-provider factory for the coding round.

All callers import ``run_code`` / ``ExecResult`` / ``SUPPORTED_LANGUAGES`` from
HERE (not from a concrete client), so swapping the code runner is one env var:

    EXECUTION_PROVIDER=jdoodle   # hosted API — no VM (default)
    EXECUTION_PROVIDER=piston    # self-hosted sandbox — needs a VM

Both providers return the SAME ``ExecResult`` type and accept the same call
signature, so grading (``coding_grader``) is provider-agnostic. The provider is
read per call so it can be changed/tested without re-import.
"""

from __future__ import annotations

from app.config import settings
from app.jdoodle_client import run_code as _jdoodle_run_code

# ExecResult + the supported-language set are identical across providers
# (same slug set); re-export the canonical ones from piston_client.
from app.piston_client import SUPPORTED_LANGUAGES, ExecResult
from app.piston_client import run_code as _piston_run_code

__all__ = ["ExecResult", "SUPPORTED_LANGUAGES", "run_code"]


async def run_code(
    *, language: str, source: str, stdin: str = "", time_limit_ms: int | None = None
) -> ExecResult:
    """Dispatch a code run to the configured execution provider. Never raises."""
    if (settings.execution_provider or "").lower() == "jdoodle":
        return await _jdoodle_run_code(
            language=language, source=source, stdin=stdin, time_limit_ms=time_limit_ms
        )
    return await _piston_run_code(
        language=language, source=source, stdin=stdin, time_limit_ms=time_limit_ms
    )
