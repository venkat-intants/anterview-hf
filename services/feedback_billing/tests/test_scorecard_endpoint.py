"""Integration tests for GET /api/scorecards/{scorecard_id} — S5-007.

Tests:
  1. test_get_scorecard_returns_200
     — valid JWT + mocked DB row → 200 with full scorecard payload
  2. test_get_scorecard_not_found_returns_404
     — valid JWT + DB returns None → 404
  3. test_get_scorecard_requires_jwt
     — no auth header → 401
  4. test_get_scorecard_includes_pdf_url_when_key_set
     — PDF key in DB + mocked presign → report_pdf_url populated
  5. test_get_scorecard_pdf_url_null_when_no_key
     — report_pdf_key is None → report_pdf_url is null
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

from app.config import Settings, settings
from app.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jwt(sub: str | None = None, roles: list[str] | None = None) -> str:
    """Mint a JWT. Default sub is the scorecard owner so ownership check passes."""
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": sub if sub is not None else _OWNER_USER_ID,
        "roles": roles or ["candidate"],
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": uuid.uuid4().hex,
    }
    return str(jose_jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm))


_SCORECARD_ID = str(uuid.uuid4())
_SESSION_ID = str(uuid.uuid4())
_OWNER_USER_ID = str(uuid.uuid4())
_COMPANY_ID = str(uuid.uuid4())

_GOOD_ROW: dict[str, Any] = {
    "scorecard_id": _SCORECARD_ID,
    "session_id": _SESSION_ID,
    "scores": json.dumps(
        {"communication": 7, "technical": 6, "problem_solving": 8, "confidence": 7}
    ),
    "composite_score": 7.05,
    "rationale": json.dumps(
        {
            "communication": "Clear and structured (6-7 band).",
            "technical": "Solid fundamentals, light on depth.",
            "problem_solving": "Strong, used concrete examples.",
            "confidence": "Composed throughout.",
        }
    ),
    "strengths": json.dumps(["Clear communication", "Good examples", "Structured thinking"]),
    "improvements": json.dumps(
        [
            {"area": "Technical depth", "suggestion": "Practice system design"},
            {"area": "Confidence", "suggestion": "Speak more slowly"},
        ]
    ),
    "summary": "Solid candidate. Meets tier expectations.",
    "report_pdf_key": None,
    # Fields added by the IDOR-fix JOIN on sessions + users
    "session_owner_id": _OWNER_USER_ID,
    "owner_company_id": _COMPANY_ID,
}


def _mock_db_row(row_data: dict[str, Any] | None) -> AsyncMock:
    """Build a mock DB result that returns the given mapping or None."""
    mock_db = AsyncMock(spec=AsyncSession)

    mock_result = MagicMock()
    mock_mappings = MagicMock()

    if row_data is None:
        mock_mappings.first.return_value = None
    else:
        # Simulate a RowMapping (dict-like).
        mock_mappings.first.return_value = row_data

    mock_result.mappings.return_value = mock_mappings
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# test_get_scorecard_requires_jwt
# ---------------------------------------------------------------------------


def test_get_scorecard_requires_jwt(client: TestClient) -> None:
    """No Authorization header → 401."""
    resp = client.get(f"/api/scorecards/{_SCORECARD_ID}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# test_get_scorecard_returns_200
# ---------------------------------------------------------------------------


def test_get_scorecard_returns_200(client: TestClient) -> None:
    """Valid JWT + mocked DB row → 200 with full scorecard payload."""
    token = _make_jwt()
    mock_db = _mock_db_row(_GOOD_ROW)

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db

    try:
        with patch(
            "app.routers.scorecard._generate_presigned_url",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = client.get(
                f"/api/scorecards/{_SCORECARD_ID}",
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scorecard_id"] == _SCORECARD_ID
    assert body["session_id"] == _SESSION_ID
    assert body["composite_score"] == 7.05
    assert body["scores"]["communication"] == 7
    assert body["scores"]["technical"] == 6
    assert body["scores"]["problem_solving"] == 8
    assert body["scores"]["confidence"] == 7
    assert len(body["strengths"]) == 3
    assert len(body["improvements"]) == 2
    assert body["summary"] == "Solid candidate. Meets tier expectations."
    assert body["report_pdf_url"] is None
    # Per-axis rationale is surfaced for the UI's "why this score" panels.
    assert body["rationale"]["technical"] == "Solid fundamentals, light on depth."
    assert body["rationale"]["problem_solving"] == "Strong, used concrete examples."


def test_get_scorecard_null_rationale_returns_empty_strings(client: TestClient) -> None:
    """A legacy row with NULL rationale must yield empty strings, not 500."""
    token = _make_jwt()
    legacy_row = {**_GOOD_ROW, "rationale": None}
    mock_db = _mock_db_row(legacy_row)

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db
    try:
        resp = client.get(
            f"/api/scorecards/{_SCORECARD_ID}",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rationale"]["communication"] == ""
    assert body["rationale"]["confidence"] == ""


# ---------------------------------------------------------------------------
# test_get_scorecard_not_found_returns_404
# ---------------------------------------------------------------------------


def test_get_scorecard_not_found_returns_404(client: TestClient) -> None:
    """Valid JWT + DB returns None → 404."""
    token = _make_jwt()
    mock_db = _mock_db_row(None)

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db

    try:
        resp = client.get(
            f"/api/scorecards/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# test_get_scorecard_includes_pdf_url_when_key_set
# ---------------------------------------------------------------------------


def test_get_scorecard_includes_pdf_url_when_key_set(client: TestClient) -> None:
    """When report_pdf_key is set, report_pdf_url is populated from pre-sign."""
    token = _make_jwt()
    row_with_key = {
        **_GOOD_ROW,
        "report_pdf_key": f"scorecards/{_SCORECARD_ID}/report.pdf",
    }
    mock_db = _mock_db_row(row_with_key)
    presigned_url = f"https://r2.example.com/scorecards/{_SCORECARD_ID}/report.pdf?X-Amz-Signature=abc"

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db

    try:
        with patch(
            "app.routers.scorecard._generate_presigned_url",
            new_callable=AsyncMock,
            return_value=presigned_url,
        ):
            resp = client.get(
                f"/api/scorecards/{_SCORECARD_ID}",
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200
    assert resp.json()["report_pdf_url"] == presigned_url


# ---------------------------------------------------------------------------
# test_get_scorecard_pdf_url_null_when_no_key
# ---------------------------------------------------------------------------


def test_get_scorecard_pdf_url_null_when_no_key(client: TestClient) -> None:
    """When report_pdf_key is None, report_pdf_url is null in the response."""
    token = _make_jwt()
    mock_db = _mock_db_row(_GOOD_ROW)  # _GOOD_ROW has report_pdf_key=None

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db

    try:
        resp = client.get(
            f"/api/scorecards/{_SCORECARD_ID}",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200
    assert resp.json()["report_pdf_url"] is None


# ---------------------------------------------------------------------------
# test_generate_presigned_url_default_expiry
# ---------------------------------------------------------------------------


async def test_generate_presigned_url_default_expiry_is_short_lived() -> None:
    """A pre-signed URL is an unauthenticated bearer capability: it honours
    neither the 15-minute access token nor the auth_epoch kill switch, and it
    points at a PDF with the candidate's name, scores and AI narrative. The
    default was 30 DAYS, which survives logout-all, password change, HR account
    deletion and the candidate closing their account.

    Asserted as a BOUND rather than an exact value: one hour is the current
    choice (the frontend renders this into an <a href> the user may click after
    reading for a while), but anything materially longer re-opens the finding.
    """
    from app.routers.scorecard import _generate_presigned_url

    mock_s3 = AsyncMock()
    mock_s3.generate_presigned_url = AsyncMock(return_value="https://example.com/signed")
    mock_s3.__aenter__ = AsyncMock(return_value=mock_s3)
    mock_s3.__aexit__ = AsyncMock(return_value=False)

    mock_boto_session = MagicMock()
    mock_boto_session.client = MagicMock(return_value=mock_s3)

    presign_settings = Settings(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        jwt_secret="test-secret-that-is-at-least-32-chars-long!!",
        s3_access_key_id="test-key-id",
        s3_secret_access_key="test-secret",
        s3_endpoint_url="https://fake.r2.cloudflarestorage.com",
        s3_scorecard_bucket="intants-interview-scorecards",
    )

    with patch("aioboto3.Session", return_value=mock_boto_session):
        url = await _generate_presigned_url(
            "scorecards/x/report.pdf", presign_settings
        )

    assert url == "https://example.com/signed"
    call_kwargs = mock_s3.generate_presigned_url.call_args.kwargs
    assert call_kwargs["ExpiresIn"] <= 3600, (
        f"pre-signed scorecard URL lives {call_kwargs['ExpiresIn']}s — long-lived "
        "URLs bypass every revocation control the platform has"
    )


# ---------------------------------------------------------------------------
# BC-1: IDOR tests — ownership enforcement
# ---------------------------------------------------------------------------


def test_get_scorecard_different_user_returns_404(client: TestClient) -> None:
    """A different candidate (not the owner) sees 404 — existence is not leaked."""
    # JWT sub is a random user — NOT the scorecard owner.
    token = _make_jwt(sub=str(uuid.uuid4()), roles=["candidate"])
    mock_db = _mock_db_row(_GOOD_ROW)

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db

    try:
        resp = client.get(
            f"/api/scorecards/{_SCORECARD_ID}",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 404


def test_get_scorecard_hr_same_company_returns_200(client: TestClient) -> None:
    """An HR manager in the same company as the candidate may read the scorecard."""
    hr_user_id = str(uuid.uuid4())
    token = _make_jwt(sub=hr_user_id, roles=["hr_manager"])

    # DB: first execute returns the scorecard row; second scalar returns the
    # HR's company_id (matching the candidate's company_id in _GOOD_ROW).
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_mappings = MagicMock()
    mock_mappings.first.return_value = _GOOD_ROW
    mock_result.mappings.return_value = mock_mappings
    mock_db.execute = AsyncMock(return_value=mock_result)
    # scalar is called to get the HR's company_id.
    mock_db.scalar = AsyncMock(return_value=uuid.UUID(_COMPANY_ID))


    from app.database import get_db_session  # noqa: PLC0415

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    app.dependency_overrides[get_db_session] = _override_db

    try:
        with patch(
            "app.routers.scorecard._generate_presigned_url",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = client.get(
                f"/api/scorecards/{_SCORECARD_ID}",
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text


def test_get_scorecard_hr_different_company_returns_404(client: TestClient) -> None:
    """An HR from a DIFFERENT company sees 404 — cross-tenant IDOR blocked."""
    hr_user_id = str(uuid.uuid4())
    different_company_id = uuid.UUID(str(uuid.uuid4()))  # guaranteed different
    token = _make_jwt(sub=hr_user_id, roles=["hr_manager"])

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_mappings = MagicMock()
    mock_mappings.first.return_value = _GOOD_ROW
    mock_result.mappings.return_value = mock_mappings
    mock_db.execute = AsyncMock(return_value=mock_result)
    # The HR belongs to a DIFFERENT company.
    mock_db.scalar = AsyncMock(return_value=different_company_id)


    from app.database import get_db_session  # noqa: PLC0415

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    app.dependency_overrides[get_db_session] = _override_db

    try:
        resp = client.get(
            f"/api/scorecards/{_SCORECARD_ID}",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 404


def test_get_scorecard_platform_owner_can_read_any(client: TestClient) -> None:
    """A platform_owner may read any scorecard regardless of company."""
    token = _make_jwt(sub=str(uuid.uuid4()), roles=["platform_owner"])
    # Row where owner_company_id is set (platform owner has no company_id — NULL).
    row_with_company = {**_GOOD_ROW, "owner_company_id": uuid.UUID(_COMPANY_ID)}
    mock_db = _mock_db_row(row_with_company)

    from app.database import get_db_session  # noqa: PLC0415

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    app.dependency_overrides[get_db_session] = _override_db

    try:
        with patch(
            "app.routers.scorecard._generate_presigned_url",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = client.get(
                f"/api/scorecards/{_SCORECARD_ID}",
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# axis_feedback — per-axis went_wrong / how_to_improve read-back
# ---------------------------------------------------------------------------


def test_get_scorecard_surfaces_axis_feedback(client: TestClient) -> None:
    """axis_feedback nested in the rationale JSONB is surfaced as its own field."""
    token = _make_jwt()
    row = {
        **_GOOD_ROW,
        "rationale": json.dumps(
            {
                "communication": "Clear and structured (6-7 band).",
                "technical": "Solid fundamentals, light on depth.",
                "problem_solving": "Strong, used concrete examples.",
                "confidence": "Composed throughout.",
                "axis_feedback": {
                    "communication": {
                        "went_wrong": ["Fragmented sentences", "Long pauses mid-answer"],
                        "how_to_improve": ["Practise STAR-structured answers aloud"],
                    },
                    "technical": {
                        "went_wrong": [],
                        "how_to_improve": ["Review MLOps: serving, monitoring, versioning"],
                    },
                    # problem_solving intentionally absent, confidence malformed —
                    # both must degrade to empty lists.
                    "confidence": ["not", "a", "dict"],
                },
            }
        ),
    }
    mock_db = _mock_db_row(row)

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db
    try:
        with patch(
            "app.routers.scorecard._generate_presigned_url",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = client.get(
                f"/api/scorecards/{_SCORECARD_ID}",
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    fb = resp.json()["axis_feedback"]
    assert fb["communication"]["went_wrong"] == [
        "Fragmented sentences",
        "Long pauses mid-answer",
    ]
    assert fb["communication"]["how_to_improve"] == [
        "Practise STAR-structured answers aloud"
    ]
    assert fb["technical"]["went_wrong"] == []
    assert fb["problem_solving"] == {"went_wrong": [], "how_to_improve": []}
    assert fb["confidence"] == {"went_wrong": [], "how_to_improve": []}
    # The plain rationale strings must be untouched by the nested key.
    assert resp.json()["rationale"]["technical"] == "Solid fundamentals, light on depth."


def test_get_scorecard_legacy_row_returns_empty_axis_feedback(client: TestClient) -> None:
    """Rows scored before the feature (no axis_feedback key) → empty lists, not 500."""
    token = _make_jwt()
    mock_db = _mock_db_row(_GOOD_ROW)  # rationale has only the four axis strings

    async def _override_db() -> AsyncSession:  # type: ignore[misc]
        yield mock_db  # type: ignore[misc]

    from app.database import get_db_session  # noqa: PLC0415

    app.dependency_overrides[get_db_session] = _override_db
    try:
        with patch(
            "app.routers.scorecard._generate_presigned_url",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = client.get(
                f"/api/scorecards/{_SCORECARD_ID}",
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200, resp.text
    fb = resp.json()["axis_feedback"]
    for axis in ("communication", "technical", "problem_solving", "confidence"):
        assert fb[axis] == {"went_wrong": [], "how_to_improve": []}
