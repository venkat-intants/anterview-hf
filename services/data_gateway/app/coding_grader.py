"""Coding-round grading (HR workflow Phase 2) — the coding analog of exam_grading.

Runs a candidate's source against a question's test cases via the execution
provider (Piston), compares normalized stdout to the expected output, and turns
the per-test outcomes into a weighted score on the SAME score_raw/score_max/
score_percent/passed shape MCQ uses (so HR results views are unchanged).

SECURITY: the authoritative test cases + weights come from the SERVER question
(never the payload), so a candidate cannot shrink the denominator. Hidden test
cases' expected_output never leaves this module on the candidate path. The pure
scoring (``weighted_raw``) is split from execution so it is trivially testable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.execution import ExecResult, run_code


def normalize_output(text: str) -> str:
    """Judge-style normalization: strip trailing spaces per line + trailing blank
    lines, and normalize newlines. Tolerant of CRLF and a missing final newline."""
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).rstrip("\n")


@dataclass(frozen=True)
class CodingTestResult:
    index: int
    is_sample: bool
    weight: int
    passed: bool
    timed_out: bool
    error: str | None
    actual_output: str
    stderr: str
    # stdin + expected_output are populated for SAMPLE cases only (safe to show the
    # candidate); empty for hidden cases so they never leak on the take path.
    stdin: str = ""
    expected_output: str = ""


def _as_int(value: Any, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def _evaluate(exec_result: ExecResult, expected: str) -> bool:
    """A test passes iff the run completed (no runner error / timeout) and the
    normalized stdout equals the normalized expected output."""
    if exec_result.error is not None or exec_result.timed_out:
        return False
    return normalize_output(exec_result.stdout) == normalize_output(expected)


async def _run_one(
    *, index: int, tc: dict[str, Any], language: str, source: str, time_limit_ms: int
) -> CodingTestResult:
    """Execute ``source`` against a single test case and score it."""
    is_sample = bool(tc.get("is_sample"))
    weight = _as_int(tc.get("weight"), default=1)
    expected = str(tc.get("expected_output") or "")
    exec_result = await run_code(
        language=language,
        source=source,
        stdin=str(tc.get("stdin") or ""),
        time_limit_ms=time_limit_ms,
    )
    return CodingTestResult(
        index=index,
        is_sample=is_sample,
        weight=weight,
        passed=_evaluate(exec_result, expected),
        timed_out=exec_result.timed_out,
        error=exec_result.error,
        actual_output=normalize_output(exec_result.stdout)[:4000],
        stderr=exec_result.stderr[:2000],
        # Reveal stdin + expected ONLY for sample cases (never hidden).
        stdin=str(tc.get("stdin") or "")[:4000] if is_sample else "",
        expected_output=normalize_output(expected)[:4000] if is_sample else "",
    )


async def run_tests(
    *,
    language: str,
    source: str,
    test_cases: list[dict[str, Any]],
    time_limit_ms: int,
    include_hidden: bool,
) -> list[CodingTestResult]:
    """Execute ``source`` against the selected test cases with BOUNDED concurrency.

    Up to ``settings.code_grade_concurrency`` cases run in parallel (default 4, set
    to 1 for the old strictly-sequential behaviour). This cuts the wall-clock — and
    therefore the time a coding submit holds its DB row-lock + connection — without
    exceeding the shared runner's rate limit. Results are returned in test-case
    order regardless of completion order, so scoring/snapshots are deterministic.

    ``include_hidden=False`` runs only the sample cases (the candidate's "Run"
    button); True runs all (grading).
    """
    selected = [
        (i, tc)
        for i, tc in enumerate(test_cases)
        if include_hidden or bool(tc.get("is_sample"))
    ][: settings.code_max_test_cases]

    sem = asyncio.Semaphore(max(1, settings.code_grade_concurrency))

    async def _guarded(index: int, tc: dict[str, Any]) -> CodingTestResult:
        async with sem:
            return await _run_one(
                index=index, tc=tc, language=language,
                source=source, time_limit_ms=time_limit_ms,
            )

    # gather preserves input order → results stay in test-case order.
    return list(await asyncio.gather(*(_guarded(i, tc) for i, tc in selected)))


def weighted_raw(results: list[CodingTestResult], points: int) -> int:
    """Score this question: points * (passed weight / total weight), rounded.

    Pure — no I/O. total weight comes from the executed results (server test set),
    never a client payload. 0 when there are no weighted results.
    """
    total = sum(r.weight for r in results)
    if total <= 0:
        return 0
    passed = sum(r.weight for r in results if r.passed)
    return round(points * passed / total)
