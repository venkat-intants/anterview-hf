"""Unit tests for coding-round grading (app.coding_grader).

Pure scoring + the test runner with execution mocked (no Piston, no network).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.coding_grader import (
    CodingTestResult,
    normalize_output,
    run_tests,
    weighted_raw,
)
from app.piston_client import ExecResult


def _result(*, weight: int, passed: bool) -> CodingTestResult:
    return CodingTestResult(
        index=0, is_sample=False, weight=weight, passed=passed,
        timed_out=False, error=None, actual_output="", stderr="",
    )


# --- normalize_output ------------------------------------------------------
def test_normalize_output_trims_trailing_ws_and_blank_lines() -> None:
    assert normalize_output("a \nb\n\n") == "a\nb"
    assert normalize_output("a\r\nb\r\n") == "a\nb"
    assert normalize_output("  ") == ""
    assert normalize_output("") == ""


# --- weighted_raw (pure) ---------------------------------------------------
def test_weighted_raw_all_pass_is_full_points() -> None:
    res = [_result(weight=1, passed=True), _result(weight=3, passed=True)]
    assert weighted_raw(res, points=100) == 100


def test_weighted_raw_none_pass_is_zero() -> None:
    res = [_result(weight=2, passed=False), _result(weight=2, passed=False)]
    assert weighted_raw(res, points=100) == 0


def test_weighted_raw_partial_is_weight_proportional() -> None:
    # 1 of 3 total weight passed -> round(100 * 1/3) == 33
    res = [_result(weight=1, passed=True), _result(weight=2, passed=False)]
    assert weighted_raw(res, points=100) == 33


def test_weighted_raw_no_results_is_zero() -> None:
    assert weighted_raw([], points=100) == 0


# --- run_tests (execution mocked) ------------------------------------------
@pytest.mark.asyncio
async def test_run_tests_grades_and_hides_hidden_expected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_code(**_: Any) -> ExecResult:
        return ExecResult(stdout="42\n", stderr="", exit_code=0, timed_out=False)

    monkeypatch.setattr("app.coding_grader.run_code", fake_run_code)
    test_cases = [
        {"stdin": "", "expected_output": "42", "is_sample": True, "weight": 1},
        {"stdin": "", "expected_output": "99", "is_sample": False, "weight": 2},
    ]
    results = await run_tests(
        language="python", source="print(42)", test_cases=test_cases,
        time_limit_ms=2000, include_hidden=True,
    )
    assert len(results) == 2
    assert results[0].passed is True   # 42 == 42
    assert results[1].passed is False  # 42 != 99
    # Hidden case never leaks its expected output / stdin.
    assert results[1].is_sample is False
    assert results[1].expected_output == ""
    assert results[1].stdin == ""
    # Sample case may show them.
    assert results[0].expected_output == "42"
    # Weighted: 1 of 3 weight passed -> 33/100.
    assert weighted_raw(results, points=100) == 33


@pytest.mark.asyncio
async def test_run_tests_samples_only_skips_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_code(**_: Any) -> ExecResult:
        return ExecResult(stdout="ok", stderr="", exit_code=0, timed_out=False)

    monkeypatch.setattr("app.coding_grader.run_code", fake_run_code)
    test_cases = [
        {"expected_output": "ok", "is_sample": True, "weight": 1},
        {"expected_output": "secret", "is_sample": False, "weight": 1},
    ]
    results = await run_tests(
        language="python", source="x", test_cases=test_cases,
        time_limit_ms=2000, include_hidden=False,
    )
    assert len(results) == 1 and results[0].is_sample is True


@pytest.mark.asyncio
async def test_run_tests_preserves_order_under_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With concurrency > 1, results must still map to the right test case even
    when cases finish out of order. The fake delays EARLIER cases longer, so
    completion order is the reverse of input order — if gather ordering were wrong
    the indices/pass-fail would scramble."""

    async def fake_run_code(*, language: str, source: str, stdin: str, time_limit_ms: int) -> ExecResult:
        await asyncio.sleep(0.01 * (5 - int(stdin)))  # case "0" finishes last
        return ExecResult(stdout=stdin, stderr="", exit_code=0, timed_out=False)

    monkeypatch.setattr("app.coding_grader.run_code", fake_run_code)
    test_cases = [
        {"stdin": str(i), "expected_output": str(i), "is_sample": False, "weight": 1}
        for i in range(5)
    ]
    results = await run_tests(
        language="python", source="x", test_cases=test_cases,
        time_limit_ms=2000, include_hidden=True,
    )
    assert [r.index for r in results] == [0, 1, 2, 3, 4]
    assert all(r.passed for r in results)  # each case's echo matched its expected


@pytest.mark.asyncio
async def test_run_tests_runner_error_is_failed_test(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_code(**_: Any) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=None, timed_out=False, error="down")

    monkeypatch.setattr("app.coding_grader.run_code", fake_run_code)
    results = await run_tests(
        language="python", source="x",
        test_cases=[{"expected_output": "", "is_sample": False, "weight": 1}],
        time_limit_ms=2000, include_hidden=True,
    )
    assert results[0].passed is False and results[0].error == "down"
