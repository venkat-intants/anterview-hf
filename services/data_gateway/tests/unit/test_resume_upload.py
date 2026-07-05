"""Unit tests for the resume upload endpoint — B-031.

All external I/O (S3, DB) is mocked so these tests run without infrastructure.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException
from pypdf import PdfReader

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_pdf() -> bytes:
    """Return the bytes of the smallest valid single-page PDF recognised by pypdf."""
    # This is a hand-crafted minimal valid PDF with one blank page.
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
# 1. test_pdf_text_extraction — unit-tests the _extract_pdf_text helper
# ---------------------------------------------------------------------------


def test_pdf_text_extraction() -> None:
    """_extract_pdf_text_sync must join text from multiple pages with newlines.

    NOTE: _extract_pdf_text (no _sync suffix) is now the async wrapper that runs
    _extract_pdf_text_sync via asyncio.to_thread.  Unit tests for the pure text-
    extraction logic should call _extract_pdf_text_sync directly to avoid needing
    an event loop.  The async wrapper itself is tested in test_security_fixes.py.
    """
    from app.routers.resume import _extract_pdf_text_sync

    page1 = MagicMock()
    page1.extract_text.return_value = "Hello from page one"
    page2 = MagicMock()
    page2.extract_text.return_value = "Hello from page two"

    mock_reader = MagicMock(spec=PdfReader)
    mock_reader.pages = [page1, page2]

    with patch("app.routers.resume.PdfReader", return_value=mock_reader):
        result = _extract_pdf_text_sync(b"fake-pdf-bytes")

    assert result == "Hello from page one\nHello from page two"


# ---------------------------------------------------------------------------
# 2. test_non_pdf_rejected — wrong content-type → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_pdf_rejected() -> None:
    """upload_resume must raise HTTP 400 when the file is not a PDF."""
    from app.routers.resume import upload_resume

    mock_file = MagicMock()
    mock_file.content_type = "image/jpeg"

    mock_user = MagicMock()
    mock_user.user_id = str(uuid.uuid4())

    mock_db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await upload_resume(file=mock_file, current_user=mock_user, db=mock_db)

    assert exc_info.value.status_code == 400
    assert "PDF" in exc_info.value.detail


# ---------------------------------------------------------------------------
# 3. test_oversized_file_rejected — 6 MB file → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oversized_file_rejected() -> None:
    """upload_resume must raise HTTP 400 when the file exceeds 5 MB."""
    from app.routers.resume import upload_resume

    oversized_bytes = b"x" * (6 * 1024 * 1024)  # 6 MB

    mock_file = MagicMock()
    mock_file.content_type = "application/pdf"
    mock_file.read = AsyncMock(return_value=oversized_bytes)

    mock_user = MagicMock()
    mock_user.user_id = str(uuid.uuid4())

    mock_db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await upload_resume(file=mock_file, current_user=mock_user, db=mock_db)

    assert exc_info.value.status_code == 400
    assert "5 MB" in exc_info.value.detail


# ---------------------------------------------------------------------------
# 4. test_resume_upload_happy_path — mock S3 + DB, assert 200 + key format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_upload_happy_path() -> None:
    """upload_resume happy path: correct key format and text_length in response.

    Updated for the versioned router (Area 3 — UI redesign v2):
    - upload_file is a local import inside _upload_to_s3; we patch _upload_to_s3
      at the module level instead.
    - Key format is now resumes/{user_id}/{resume_id}.pdf (versioned per upload).
    - db.add() is called for the new Resume ORM row.
    """
    from app.routers.resume import upload_resume

    user_id = uuid.uuid4()
    pdf_bytes = _make_minimal_pdf()

    mock_file = MagicMock()
    mock_file.content_type = "application/pdf"
    mock_file.read = AsyncMock(return_value=pdf_bytes)
    mock_file.filename = "my_cv.pdf"

    mock_user = MagicMock()
    mock_user.user_id = str(user_id)

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    extracted_text = "Extracted resume text"

    with (
        patch("app.routers.resume._upload_to_s3", new=AsyncMock()),
        # _extract_pdf_text is now async (asyncio.to_thread wrapper) — use AsyncMock.
        patch("app.routers.resume._extract_pdf_text", new=AsyncMock(return_value=extracted_text)),
    ):
        response = await upload_resume(
            file=mock_file, current_user=mock_user, db=mock_db
        )

    assert response.message == "Resume uploaded"
    assert response.text_length == len(extracted_text)

    # DB must have been committed
    mock_db.commit.assert_awaited_once()

    # Key format must be resumes/{user_id}/{resume_id}.pdf (versioned)
    assert response.resume_s3_key.startswith(f"resumes/{user_id}/")
    assert response.resume_s3_key.endswith(".pdf")
    # resume_id segment must be a valid UUID
    raw_id = response.resume_s3_key.split("/")[-1].replace(".pdf", "")
    uuid.UUID(raw_id)


# ---------------------------------------------------------------------------
# 5. test_storage_failure_returns_502 — a boto ClientError must NOT escape as an
#    unhandled 500 (which would skip CORS headers and surface in the browser as
#    "Network error"); it must become a clean HTTP 502.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_failure_returns_502() -> None:
    """A storage ClientError (e.g. bad credentials) → HTTP 502, not a 500."""
    from app.routers.resume import upload_resume

    mock_file = MagicMock()
    mock_file.content_type = "application/pdf"
    mock_file.read = AsyncMock(return_value=_make_minimal_pdf())
    mock_file.filename = "cv.pdf"

    mock_user = MagicMock()
    mock_user.user_id = str(uuid.uuid4())

    mock_db = AsyncMock()

    storage_error = ClientError(
        {"Error": {"Code": "InvalidAccessKeyId", "Message": "Malformed Access Key Id"}},
        "PutObject",
    )

    with (
        # _extract_pdf_text is now async — use AsyncMock.
        patch("app.routers.resume._extract_pdf_text", new=AsyncMock(return_value="text")),
        patch("app.routers.resume._upload_to_s3", new=AsyncMock(side_effect=storage_error)),
        pytest.raises(HTTPException) as exc_info,
    ):
        await upload_resume(file=mock_file, current_user=mock_user, db=mock_db)

    assert exc_info.value.status_code == 502
    # The DB must never be committed when storage fails.
    mock_db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# 6. test_corrupt_pdf_returns_400 — pypdf raising on a corrupt/encrypted PDF
#    must become a clean HTTP 400, not an unhandled 500.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrupt_pdf_returns_400() -> None:
    """A pypdf parse error → HTTP 400 with a helpful message, not a 500."""
    from app.routers.resume import upload_resume

    mock_file = MagicMock()
    mock_file.content_type = "application/pdf"
    mock_file.read = AsyncMock(return_value=b"%PDF-1.4 not-really-a-pdf")
    mock_file.filename = "broken.pdf"

    mock_user = MagicMock()
    mock_user.user_id = str(uuid.uuid4())

    mock_db = AsyncMock()

    with (
        # _extract_pdf_text is now async — use AsyncMock with side_effect.
        patch(
            "app.routers.resume._extract_pdf_text",
            new=AsyncMock(side_effect=Exception("PdfReadError: EOF marker not found")),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await upload_resume(file=mock_file, current_user=mock_user, db=mock_db)

    assert exc_info.value.status_code == 400
    assert "PDF" in exc_info.value.detail


# ---------------------------------------------------------------------------
# 7. test_commit_failure_returns_503 — a DB commit failure must clean up the
#    orphaned S3 object AND become a clean HTTP 503 (CORS-decorated), not a
#    re-raised raw DB error (which would skip CORS and read as "Network error").
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_failure_returns_503() -> None:
    """DB commit failure → S3 cleanup + HTTP 503, not a raw 500."""
    from app.routers.resume import upload_resume

    mock_file = MagicMock()
    mock_file.content_type = "application/pdf"
    mock_file.read = AsyncMock(return_value=_make_minimal_pdf())
    mock_file.filename = "cv.pdf"

    mock_user = MagicMock()
    mock_user.user_id = str(uuid.uuid4())

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock(side_effect=RuntimeError("connection lost"))

    delete_mock = AsyncMock()

    with (
        # _extract_pdf_text is now async — use AsyncMock.
        patch("app.routers.resume._extract_pdf_text", new=AsyncMock(return_value="text")),
        patch("app.routers.resume._upload_to_s3", new=AsyncMock()),
        patch("app.routers.resume._delete_from_s3", new=delete_mock),
        pytest.raises(HTTPException) as exc_info,
    ):
        await upload_resume(file=mock_file, current_user=mock_user, db=mock_db)

    assert exc_info.value.status_code == 503
    # The orphaned S3 object must be cleaned up after the failed commit.
    delete_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# 8. test_db_demote_failure_returns_503 — a DB error on the PRE-commit demote
#    statement (outside the old commit-only guard) must also become a clean 503
#    with S3 cleanup, not an unhandled 500.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_demote_failure_returns_503() -> None:
    """A failure on _demote_current_resumes (before commit) → 503 + S3 cleanup."""
    from app.routers.resume import upload_resume

    mock_file = MagicMock()
    mock_file.content_type = "application/pdf"
    mock_file.read = AsyncMock(return_value=_make_minimal_pdf())
    mock_file.filename = "cv.pdf"

    mock_user = MagicMock()
    mock_user.user_id = str(uuid.uuid4())

    mock_db = AsyncMock()
    delete_mock = AsyncMock()

    with (
        # _extract_pdf_text is now async — use AsyncMock.
        patch("app.routers.resume._extract_pdf_text", new=AsyncMock(return_value="text")),
        patch("app.routers.resume._upload_to_s3", new=AsyncMock()),
        patch(
            "app.routers.resume._demote_current_resumes",
            new=AsyncMock(side_effect=RuntimeError("statement timeout")),
        ),
        patch("app.routers.resume._delete_from_s3", new=delete_mock),
        pytest.raises(HTTPException) as exc_info,
    ):
        await upload_resume(file=mock_file, current_user=mock_user, db=mock_db)

    assert exc_info.value.status_code == 503
    # Must never commit, and must clean up the orphaned S3 object.
    mock_db.commit.assert_not_awaited()
    delete_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# 9. test_malformed_user_id_returns_400 — a non-UUID JWT subject must become a
#    clean 400, not an unhandled 500 (ValueError from uuid.UUID()).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_user_id_returns_400() -> None:
    """A non-UUID current_user.user_id → HTTP 400, not an unhandled 500."""
    from app.routers.resume import upload_resume

    mock_file = MagicMock()
    mock_file.content_type = "application/pdf"
    mock_file.read = AsyncMock(return_value=_make_minimal_pdf())
    mock_file.filename = "cv.pdf"

    mock_user = MagicMock()
    mock_user.user_id = "not-a-uuid@example.com"  # e.g. an SSO email subject

    mock_db = AsyncMock()
    s3_mock = AsyncMock()

    with (
        # _extract_pdf_text is now async — use AsyncMock.
        patch("app.routers.resume._extract_pdf_text", new=AsyncMock(return_value="text")),
        patch("app.routers.resume._upload_to_s3", new=s3_mock),
        pytest.raises(HTTPException) as exc_info,
    ):
        await upload_resume(file=mock_file, current_user=mock_user, db=mock_db)

    assert exc_info.value.status_code == 400
    # The bad id is caught before any storage write happens.
    s3_mock.assert_not_awaited()
