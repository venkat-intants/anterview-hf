"""S-6 — /health/deep must not echo driver exception messages.

The endpoint is unauthenticated. Every check in app/health.py talks to a piece
of infrastructure whose client library puts the connection target into the
exception message: asyncpg names the Neon host, port, user and database;
botocore names the R2 endpoint and bucket; httpx echoes the request URL, which
for Gemini carries ``?key=<GEMINI_API_KEY>``.

These tests assert the contract from the outside — the response carries the
exception TYPE and nothing else — rather than checking that a particular
formatting call was made.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import health
from app.config import settings
from app.main import app


class DriverBoomError(RuntimeError):
    """Stand-in for a driver exception whose message carries the DSN."""


# A message shaped like the real ones: credentials, host, port, database.
_LEAKY_MESSAGE = (
    "connect failed: postgresql://neon_user:hunter2@"
    "ep-prod-9k2.ap-south-1.aws.neon.tech:5432/interview?sslmode=require"
)

# The distinctive substrings that must never reach a response body. Asserting on
# the whole message alone would pass if only part of it were echoed.
_LEAKY_FRAGMENTS = ("hunter2", "neon_user", "ep-prod-9k2", "5432")


def _raiser(*_args: Any, **_kwargs: Any) -> Any:
    raise DriverBoomError(_LEAKY_MESSAGE)


def _arm_all_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outbound client constructor blow up with a leaky message."""
    monkeypatch.setattr(health, "create_async_engine", _raiser)
    monkeypatch.setattr(health, "AsyncAnthropic", _raiser)
    monkeypatch.setattr(health.aioredis, "from_url", _raiser)
    monkeypatch.setattr(health.boto3, "client", _raiser)
    monkeypatch.setattr(health.httpx, "AsyncClient", _raiser)


# The two credential-gated checks return early ("… not set") unless a key is
# present, so they need one to reach the code path under test.
_CHECKS: dict[str, dict[str, str]] = {
    "_check_postgres": {},
    "_check_redis": {},
    "_check_s3": {},
    "_check_anthropic": {"anthropic_api_key": "ak-test"},
    "_check_gemini": {"gemini_api_key": "gk-test"},
    "_check_sarvam": {"sarvam_api_key": "sk-test"},
}


@pytest.mark.parametrize("check_name", sorted(_CHECKS))
async def test_check_reports_exception_type_without_the_message(
    check_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every handler, not just the first — they were all leaking identically."""
    for field, value in _CHECKS[check_name].items():
        monkeypatch.setattr(settings, field, value)
    _arm_all_failures(monkeypatch)

    result: dict[str, Any] = await getattr(health, check_name)()

    assert result["ok"] is False
    assert result["error"] == "DriverBoomError", (
        f"{check_name} must return the exception type alone; got {result['error']!r}"
    )
    serialised = json.dumps(result)
    for fragment in _LEAKY_FRAGMENTS:
        assert fragment not in serialised


async def test_gemini_http_error_does_not_echo_the_upstream_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The non-200 branch is the same leak by another route.

    The Gemini request URL carries the API key, and Google's error documents
    have been observed quoting the request back. Status code is enough to triage.
    """
    monkeypatch.setattr(settings, "gemini_api_key", "gk-test")

    class _FakeResponse:
        status_code = 403
        text = f'{{"error":{{"message":"API key not valid","request":"{_LEAKY_MESSAGE}"}}}}'

    class _FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr(health.httpx, "AsyncClient", _FakeClient)

    result = await health._check_gemini()

    assert result == {"ok": False, "status": 403}


def test_deep_health_response_body_carries_no_connection_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the assembled /health/deep body is what an attacker sees."""
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    for overrides in _CHECKS.values():
        for field, value in overrides.items():
            monkeypatch.setattr(settings, field, value)
    _arm_all_failures(monkeypatch)

    # No lifespan: /health/deep builds its own clients, so it needs no pool.
    response = TestClient(app).get("/health/deep")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["postgres"]["error"] == "DriverBoomError"
    for fragment in _LEAKY_FRAGMENTS:
        assert fragment not in response.text
