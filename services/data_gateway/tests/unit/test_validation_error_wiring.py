"""The readable 422 (audit X1) is wired into this service's real app.

``shared/tests/test_validation_errors.py`` owns the handler's semantics. This
file owns the one thing that file cannot see: that ``app/main.py`` actually
installs it, so a future edit that drops the call fails here, not as an
"HTTP 422" toast in production.

The route is attached for one test and removed again, because no real endpoint
here can be reached with a bad body without first passing authentication, and
what is under test is the app-level handler rather than any one endpoint.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.main import app

_PATH = "/__validation_probe__"


class _Probe(BaseModel):
    max_candidates: int = Field(ge=1)
    password: str = Field(min_length=12)


@pytest.fixture()
def probe_route() -> Iterator[str]:
    async def probe(body: _Probe) -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route(_PATH, probe, methods=["POST"], include_in_schema=False)
    try:
        yield _PATH
    finally:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", "") != _PATH]


def test_a_validation_failure_carries_a_sentence_and_no_echo(probe_route: str) -> None:
    resp = TestClient(app).post(probe_route, json={"max_candidates": 0, "password": "weak-pass"})

    assert resp.status_code == 422
    body = resp.json()
    assert body["message"] == "Max candidates must be 1 or more."
    assert [e["loc"] for e in body["detail"]] == [
        ["body", "max_candidates"],
        ["body", "password"],
    ]
    assert all("input" not in e for e in body["detail"])
    assert "weak-pass" not in resp.text
