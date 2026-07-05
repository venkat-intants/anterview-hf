"""Unit tests for the JD document upload endpoint — B-032.

All external I/O (S3, DB) is mocked so these tests run without infrastructure.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_pdf() -> bytes:
    """Return the bytes of the smallest valid single-page PDF recognised by pypdf."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 3 3]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\n"
        b"startxref\n190\n%%EOF"
    )


# ---------------------------------------------------------------------------
# 1. test_jd_upload_happy_path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jd_upload_happy_path() -> None:
    """upload_jd_document happy path: key format is jd-documents/{id}.pdf."""
    from app.routers.jd import upload_jd_document

    job_id = uuid.uuid4()
    pdf_bytes = _make_minimal_pdf()
    extracted_text = "Senior Python Engineer responsibilities..."

    mock_file = MagicMock()
    mock_file.content_type = "application/pdf"
    mock_file.read = AsyncMock(return_value=pdf_bytes)

    mock_user = MagicMock()
    mock_user.user_id = str(uuid.uuid4())

    # DB: scalar_one_or_none returns a fake job so the 404 branch is skipped.
    fake_job = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = fake_job

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    expected_key = f"jd-documents/{job_id}.pdf"

    with (
        patch("app.routers.jd.upload_file", new=AsyncMock(return_value=expected_key)),
        # _extract_pdf_text is now async (asyncio.to_thread wrapper) — use AsyncMock.
        patch("app.routers.jd._extract_pdf_text", new=AsyncMock(return_value=extracted_text)),
    ):
        response = await upload_jd_document(
            job_id=job_id, file=mock_file, current_user=mock_user, db=mock_db
        )

    assert response.message == "JD uploaded"
    assert response.jd_s3_key == expected_key
    assert response.text_length == len(extracted_text)

    # Key must match jd-documents/{uuid}.pdf
    assert response.jd_s3_key == f"jd-documents/{job_id}.pdf"

    # DB must have been committed
    mock_db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# 2. test_jd_job_not_found — unknown job_id → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jd_job_not_found() -> None:
    """upload_jd_document must raise HTTP 404 when the job does not exist."""
    from app.routers.jd import upload_jd_document

    job_id = uuid.uuid4()
    pdf_bytes = _make_minimal_pdf()

    mock_file = MagicMock()
    mock_file.content_type = "application/pdf"
    mock_file.read = AsyncMock(return_value=pdf_bytes)

    mock_user = MagicMock()
    mock_user.user_id = str(uuid.uuid4())

    # DB: scalar_one_or_none returns None → job not found
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(HTTPException) as exc_info:
        await upload_jd_document(
            job_id=job_id, file=mock_file, current_user=mock_user, db=mock_db
        )

    assert exc_info.value.status_code == 404
    assert str(job_id) in exc_info.value.detail
