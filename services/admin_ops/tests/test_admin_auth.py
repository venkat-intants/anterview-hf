"""S5-002: admin_ops — JWT admin-role guard tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from shared.auth.jwt import issue_access_token

from app.config import settings
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _make_token(role: str) -> str:
    return issue_access_token(
        user_id="test-user-id",
        roles=[role],
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )


def test_admin_route_rejects_non_admin_jwt(client: TestClient) -> None:
    token = _make_token(role="user")
    resp = client.get("/admin/status", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin role required"


def test_admin_route_rejects_missing_token(client: TestClient) -> None:
    resp = client.get("/admin/status")
    assert resp.status_code == 401


def test_admin_route_accepts_admin_jwt(client: TestClient) -> None:
    token = _make_token(role="admin")
    resp = client.get("/admin/status", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
