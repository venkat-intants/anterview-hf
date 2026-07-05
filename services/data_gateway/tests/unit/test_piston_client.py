"""Unit tests for the Piston execution client (app.piston_client).

Response parsing + the run_code happy/error paths with httpx + runtime
resolution mocked (no network).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import app.piston_client as pc
from app.piston_client import ExecResult, _parse, run_code


# --- _parse ----------------------------------------------------------------
def test_parse_successful_run() -> None:
    r = _parse({"run": {"stdout": "hi\n", "stderr": "", "code": 0, "signal": None}})
    assert r.ok is True and r.stdout == "hi\n" and r.exit_code == 0 and not r.timed_out


def test_parse_compile_error() -> None:
    r = _parse({"compile": {"code": 1, "stderr": "boom"}, "run": {}})
    assert r.error == "compile error" and "boom" in r.stderr and r.ok is False


def test_parse_timeout_sigkill() -> None:
    r = _parse({"run": {"stdout": "", "code": None, "signal": "SIGKILL"}})
    assert r.timed_out is True and r.ok is False


def test_parse_runtime_nonzero_exit_is_not_ok() -> None:
    r = _parse({"run": {"stdout": "partial", "stderr": "trace", "code": 1, "signal": None}})
    assert r.ok is False and r.exit_code == 1 and r.stdout == "partial"


# --- run_code --------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_code_unsupported_language_returns_error() -> None:
    r = await run_code(language="brainfuck", source="x", stdin="")
    assert r.error is not None and "unsupported" in r.error


class _FakeResp:
    def __init__(self, status_code: int, data: dict[str, Any]) -> None:
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data)

    def json(self) -> dict[str, Any]:
        return self._data


class _FakeClient:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def post(self, *_: Any, **__: Any) -> _FakeResp:
        return self._resp


@pytest.mark.asyncio
async def test_run_code_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_runtimes() -> dict[str, dict[str, str]]:
        return {"python": {"language": "python", "version": "3.10.0", "file": "main.py"}}

    monkeypatch.setattr(pc, "_load_runtimes", fake_runtimes)
    resp = _FakeResp(200, {"run": {"stdout": "5\n", "stderr": "", "code": 0, "signal": None}})
    monkeypatch.setattr(pc.httpx, "AsyncClient", lambda *a, **k: _FakeClient(resp))

    r = await run_code(language="python", source="print(5)", stdin="")
    assert isinstance(r, ExecResult) and r.ok and r.stdout == "5\n"


@pytest.mark.asyncio
async def test_run_code_runtime_unavailable_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty_runtimes() -> dict[str, dict[str, str]]:
        return {}

    monkeypatch.setattr(pc, "_load_runtimes", empty_runtimes)
    r = await run_code(language="python", source="x", stdin="")
    assert r.error is not None and "runtime unavailable" in r.error
