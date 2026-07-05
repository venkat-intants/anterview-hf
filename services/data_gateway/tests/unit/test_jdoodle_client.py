"""Unit tests for the JDoodle execution client (app.jdoodle_client).

Response parsing + the run_code happy/error paths with httpx mocked (no network).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import app.jdoodle_client as jc
from app.jdoodle_client import ExecResult, _parse, run_code


def _set_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jc.settings, "jdoodle_client_id", "id", raising=False)
    monkeypatch.setattr(jc.settings, "jdoodle_client_secret", "secret", raising=False)


# --- _parse ----------------------------------------------------------------
def test_parse_successful_run() -> None:
    r = _parse({"output": "42\n", "statusCode": 200, "memory": "1", "cpuTime": "0.1"})
    assert r.stdout == "42\n" and r.error is None and not r.timed_out


def test_parse_timeout_detected_from_output() -> None:
    r = _parse({"output": "JDoodle - Time limit exceeded", "statusCode": 200})
    assert r.timed_out is True and r.exit_code is None


def test_parse_error_status_without_output_is_runner_error() -> None:
    r = _parse({"output": "", "statusCode": 401})
    assert r.error is not None and "401" in r.error


# --- run_code guards -------------------------------------------------------
@pytest.mark.asyncio
async def test_run_code_unsupported_language_returns_error() -> None:
    r = await run_code(language="brainfuck", source="x", stdin="")
    assert r.error is not None and "unsupported" in r.error


@pytest.mark.asyncio
async def test_run_code_missing_credentials_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jc.settings, "jdoodle_client_id", "", raising=False)
    monkeypatch.setattr(jc.settings, "jdoodle_client_secret", "", raising=False)
    r = await run_code(language="python", source="print(1)", stdin="")
    assert r.error is not None and "credentials" in r.error


# --- run_code HTTP paths (httpx mocked) ------------------------------------
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
    _set_creds(monkeypatch)
    resp = _FakeResp(200, {"output": "5\n", "statusCode": 200})
    monkeypatch.setattr(jc.httpx, "AsyncClient", lambda *a, **k: _FakeClient(resp))

    r = await run_code(language="python", source="print(5)", stdin="")
    assert isinstance(r, ExecResult) and r.error is None and r.stdout == "5\n"


@pytest.mark.asyncio
async def test_run_code_daily_limit_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch)
    resp = _FakeResp(429, {"error": "Daily limit reached", "statusCode": 429})
    monkeypatch.setattr(jc.httpx, "AsyncClient", lambda *a, **k: _FakeClient(resp))

    r = await run_code(language="python", source="x", stdin="")
    assert r.error is not None and "daily limit" in r.error
