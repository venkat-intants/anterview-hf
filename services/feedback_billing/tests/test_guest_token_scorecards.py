"""A guest (interview magic-link) token must not act as the real account.

``data_gateway/routers/interview_take._issue_guest_token`` mints a token whose
``sub`` is the applicant's REAL user id, role ``guest_candidate``, bound to one
``session_id``. ``require_jwt`` verifies it like any user token, so before this
fix both scorecard routes treated a forwarded interview link as the account:

* ``GET /api/scorecards`` listed every scorecard on the account;
* ``GET /api/scorecards/{id}`` passed the owner check for ANY of the account's
  scorecards, not only the one for the session the link was issued for.

No DB or Redis is touched: the DB dependency is overridden and the revocation
check fails open on an unreachable Redis (which the env pins to a dead address).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.main import app

_OWNER = str(uuid.uuid4())
_SESSION_THIS = str(uuid.uuid4())
_SESSION_OTHER = str(uuid.uuid4())
_SC_THIS = str(uuid.uuid4())
_SC_OTHER = str(uuid.uuid4())


def _token(roles: list[str], session_id: str | None = None) -> str:
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": _OWNER,
        "roles": roles,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": uuid.uuid4().hex,
    }
    if session_id is not None:
        claims["session_id"] = session_id
    return str(pyjwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm))


def _row(scorecard_id: str, session_id: str) -> dict[str, Any]:
    return {
        "scorecard_id": scorecard_id,
        "session_id": session_id,
        "scores": json.dumps(
            {"communication": 7, "technical": 6, "problem_solving": 8, "confidence": 7}
        ),
        "composite_score": 7.0,
        "rationale": None,
        "strengths": json.dumps([]),
        "improvements": json.dumps([]),
        "summary": "ok",
        "report_pdf_key": None,
        "session_owner_id": _OWNER,
        "owner_company_id": uuid.uuid4(),
    }


def _db_returning(row: dict[str, Any] | None) -> AsyncMock:
    db = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    result.mappings.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _with_db(db: AsyncMock) -> None:
    async def _override() -> AsyncIterator[AsyncMock]:
        yield db

    app.dependency_overrides[get_db_session] = _override


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.pop(get_db_session, None)


def test_list_refuses_guest_role_token(client: TestClient) -> None:
    db = _db_returning(None)
    _with_db(db)
    resp = client.get(
        "/api/scorecards",
        headers={"Authorization": f"Bearer {_token(['guest_candidate'], _SESSION_THIS)}"},
    )
    assert resp.status_code == 403
    db.execute.assert_not_called()


def test_list_refuses_session_bound_token_without_role(client: TestClient) -> None:
    db = _db_returning(None)
    _with_db(db)
    resp = client.get(
        "/api/scorecards",
        headers={"Authorization": f"Bearer {_token(['candidate'], _SESSION_THIS)}"},
    )
    assert resp.status_code == 403
    db.execute.assert_not_called()


def test_list_still_serves_a_normal_candidate(client: TestClient) -> None:
    _with_db(_db_returning(None))
    # count query returns a mapping without "cnt" -> total 0; only the status matters
    resp = client.get(
        "/api/scorecards",
        headers={"Authorization": f"Bearer {_token(['candidate'])}"},
    )
    assert resp.status_code != 403


def test_single_scorecard_guest_denied_for_other_session(client: TestClient) -> None:
    _with_db(_db_returning(_row(_SC_OTHER, _SESSION_OTHER)))
    resp = client.get(
        f"/api/scorecards/{_SC_OTHER}",
        headers={"Authorization": f"Bearer {_token(['guest_candidate'], _SESSION_THIS)}"},
    )
    assert resp.status_code == 404


def test_single_scorecard_guest_allowed_for_its_own_session(client: TestClient) -> None:
    _with_db(_db_returning(_row(_SC_THIS, _SESSION_THIS)))
    with patch(
        "app.routers.scorecard._generate_presigned_url",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = client.get(
            f"/api/scorecards/{_SC_THIS}",
            headers={"Authorization": f"Bearer {_token(['guest_candidate'], _SESSION_THIS)}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["session_id"] == _SESSION_THIS


def test_single_scorecard_ordinary_owner_unaffected(client: TestClient) -> None:
    _with_db(_db_returning(_row(_SC_OTHER, _SESSION_OTHER)))
    with patch(
        "app.routers.scorecard._generate_presigned_url",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = client.get(
            f"/api/scorecards/{_SC_OTHER}",
            headers={"Authorization": f"Bearer {_token(['candidate'])}"},
        )
    assert resp.status_code == 200


def test_non_uuid_scorecard_id_is_422_not_500(client: TestClient) -> None:
    _with_db(_db_returning(None))
    resp = client.get(
        "/api/scorecards/not-a-uuid",
        headers={"Authorization": f"Bearer {_token(['candidate'])}"},
    )
    assert resp.status_code == 422
