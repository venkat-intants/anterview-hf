"""POST /internal/scorecards/{scorecard_id}/pdf — the missing-PDF retry (A1).

What matters here is the decision table, not ReportLab: when to render, when to
leave a scorecard alone, when to refuse for good, and what to do when the row
disappears mid-render. The render and upload themselves are patched out.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.main import app
from app.routers import scorecard_pdf

_SCID = str(uuid.uuid4())
_URL = f"/internal/scorecards/{_SCID}/pdf"


def _token(sub: str = "data_gateway", roles: list[str] | None = None) -> str:
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": sub, "roles": roles or ["service"], "iat": now,
        "exp": now + timedelta(minutes=1), "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience, "jti": uuid.uuid4().hex,
    }
    return str(jose_jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm))


_ROW: dict[str, Any] = {
    "scorecard_id": _SCID,
    "session_id": str(uuid.uuid4()),
    "scores": json.dumps({"communication": 7, "technical": 6,
                          "problem_solving": 8, "confidence": 7}),
    "composite_score": 7.05,
    "strengths": json.dumps(["Clear"]),
    "improvements": json.dumps([{"area": "Depth", "suggestion": "Practise"}]),
    "summary": "Solid.",
    "lang": "te",
    "report_pdf_key": None,
    "session_deleted_at": None,
    "full_name": "Farah Khan",
    "user_deleted_at": None,
    "job_title": "Staff Nurse",
}


def _db(row: dict[str, Any] | None, *, update_hit: bool = True,
        still_there: bool = True) -> AsyncMock:
    db = AsyncMock(spec=AsyncSession)
    select = MagicMock()
    select.mappings.return_value.first.return_value = row
    update = MagicMock()
    update.first.return_value = (_SCID,) if update_hit else None
    db.execute = AsyncMock(side_effect=[select, update])
    db.scalar = AsyncMock(return_value=1 if still_there else None)
    return db


@pytest.fixture()
def call():  # noqa: ANN201
    """POST as data_gateway with a mocked session and storage configured."""
    client = TestClient(app, raise_server_exceptions=False)
    configured = settings.model_copy(update={"s3_access_key_id": "test-key"})

    def _call(db: AsyncMock, *, s3: bool = True, token: str | None = None):  # noqa: ANN202
        async def _override():  # noqa: ANN202
            yield db

        app.dependency_overrides[get_db_session] = _override
        app.dependency_overrides[scorecard_pdf._get_settings] = (
            lambda: configured if s3 else settings.model_copy(update={"s3_access_key_id": ""})
        )
        try:
            headers = {"Authorization": f"Bearer {token or _token()}"}
            return client.post(_URL, headers=headers)
        finally:
            app.dependency_overrides.pop(get_db_session, None)
            app.dependency_overrides.pop(scorecard_pdf._get_settings, None)

    return _call


def _render(key: str | None = f"scorecards/{_SCID}/report.pdf") -> Any:
    return patch.object(scorecard_pdf, "render_scorecard_pdf", AsyncMock(return_value=key))


def test_renders_and_records_the_key(call) -> None:  # noqa: ANN001
    db = _db(_ROW)
    with _render() as render:
        resp = call(db)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rendered"
    args, kwargs = render.await_args
    assert args[2] == "Farah Khan" and args[4] == "te"
    assert args[5] == {"communication": 7, "technical": 6, "problem_solving": 8, "confidence": 7}
    # The key is written here, where a failure is visible — not by the
    # renderer's own swallow-everything write-back.
    assert kwargs["db_session_factory"] is None
    update_sql = str(db.execute.await_args_list[1].args[0])
    assert "report_pdf_key IS NULL" in update_sql
    db.commit.assert_awaited()


def test_a_scorecard_that_has_its_pdf_is_left_alone(call) -> None:  # noqa: ANN001
    with _render() as render:
        resp = call(_db({**_ROW, "report_pdf_key": "scorecards/x/report.pdf"}))

    assert resp.json()["status"] == "already_present"
    render.assert_not_awaited()


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"full_name": ""}, "no_candidate_name"),
        ({"session_deleted_at": datetime.now(tz=UTC)}, "pending_erasure"),
        ({"user_deleted_at": datetime.now(tz=UTC)}, "pending_erasure"),
    ],
)
def test_scorecards_that_can_never_get_a_pdf_say_why(call, over, reason) -> None:  # noqa: ANN001
    with _render() as render:
        resp = call(_db({**_ROW, **over}))

    assert resp.status_code == 200
    assert resp.json() == {"scorecard_id": _SCID, "status": "not_applicable", "reason": reason}
    render.assert_not_awaited()


def test_unknown_scorecard_is_404(call) -> None:  # noqa: ANN001
    assert call(_db(None)).status_code == 404


def test_storage_not_configured_is_a_retryable_503(call) -> None:  # noqa: ANN001
    with _render() as render:
        resp = call(_db(_ROW), s3=False)
    assert resp.status_code == 503
    render.assert_not_awaited()


def test_a_failed_render_is_a_retryable_502(call) -> None:  # noqa: ANN001
    with _render(key=None):
        assert call(_db(_ROW)).status_code == 502


def test_losing_the_race_to_the_live_task_is_not_an_error(call) -> None:  # noqa: ANN001
    """The live render recorded its key while this one rendered. Same object
    key, so nothing is orphaned and nothing needs undoing."""
    with _render(), patch.object(scorecard_pdf, "_delete_object", AsyncMock()) as delete:
        resp = call(_db(_ROW, update_hit=False, still_there=True))

    assert resp.json()["status"] == "already_present"
    delete.assert_not_awaited()


def test_a_row_erased_mid_render_takes_its_upload_with_it(call) -> None:  # noqa: ANN001
    """Erasure collected the scorecard's objects and deleted the row while this
    rendered. The PDF just uploaded carries the candidate's name and would
    otherwise sit in the bucket with nothing pointing at it."""
    with _render(), patch.object(scorecard_pdf, "_delete_object", AsyncMock()) as delete:
        resp = call(_db(_ROW, update_hit=False, still_there=False))

    assert resp.json() == {"scorecard_id": _SCID, "status": "not_applicable",
                           "reason": "scorecard_deleted"}
    delete.assert_awaited_once()
    assert delete.await_args.args[0] == f"scorecards/{_SCID}/report.pdf"


def test_candidate_tokens_cannot_reach_it(call) -> None:  # noqa: ANN001
    resp = call(_db(_ROW), token=_token(sub=str(uuid.uuid4()), roles=["candidate"]))
    assert resp.status_code == 403


def test_requires_a_token() -> None:
    assert TestClient(app).post(_URL).status_code == 401
