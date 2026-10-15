"""/health/deep is service-gated, cached, and disposes its engine on failure.

Each run makes a paid TTS call, LLM completions and an S3 call, and used to be
open to the internet and uncached.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from shared.auth.jwt import issue_access_token

from app import health
from app.config import settings
from app.main import app


def _token(sub: str, roles: list[str]) -> str:
    return issue_access_token(
        user_id=sub,
        roles=roles,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        ttl_seconds=60,
    )


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    health._deep_cache = None
    yield
    health._deep_cache = None


@pytest.fixture
def counting_checks(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    async def fake() -> dict[str, Any]:
        calls.append(1)
        return {"status": "healthy", "checks": {}}

    monkeypatch.setattr(health, "_run_deep_checks", fake)
    return calls


def test_anonymous_is_rejected_and_runs_nothing(counting_checks: list[int]) -> None:
    client = TestClient(app)
    assert client.get("/health/deep").status_code == 401
    assert counting_checks == []


def test_non_service_token_is_forbidden(counting_checks: list[int]) -> None:
    client = TestClient(app)
    tok = _token("00000000-0000-0000-0000-000000000001", ["candidate"])
    r = client.get("/health/deep", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
    # a service role with an unlisted sub is also refused
    tok = _token("someone_else", ["service"])
    r = client.get("/health/deep", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
    assert counting_checks == []


def test_service_token_runs_once_then_serves_cache(counting_checks: list[int]) -> None:
    client = TestClient(app)
    hdr = {"Authorization": f"Bearer {_token('interview_core', ['service'])}"}
    assert client.get("/health/deep", headers=hdr).status_code == 200
    assert client.get("/health/deep", headers=hdr).status_code == 200
    assert client.get("/health/deep", headers=hdr).status_code == 200
    assert counting_checks == [1]
    assert health._DEEP_CACHE_SECONDS >= 60


def test_cache_expires(counting_checks: list[int], monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    hdr = {"Authorization": f"Bearer {_token('data_gateway', ['service'])}"}
    client.get("/health/deep", headers=hdr)
    ts, body = health._deep_cache  # type: ignore[misc]
    health._deep_cache = (ts - health._DEEP_CACHE_SECONDS - 1, body)
    client.get("/health/deep", headers=hdr)
    assert counting_checks == [1, 1]


def test_live_stays_open() -> None:
    assert TestClient(app).get("/health/live").status_code == 200


async def test_engine_disposed_when_connect_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    disposed: list[bool] = []

    class _Engine:
        def connect(self) -> Any:
            raise OSError("boom")

        async def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setattr(health, "create_async_engine", lambda *a, **k: _Engine())
    result = await health._check_postgres()
    assert result == {"ok": False, "error": "OSError"}
    assert disposed == [True]
