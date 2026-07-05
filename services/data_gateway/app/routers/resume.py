"""Resume upload and history endpoints — B-031 + UI redesign v2 Area 3.

Endpoints:
  POST   /users/me/resume              — upload a new resume version (B-031 compat)
  POST   /users/me/resumes             — alias for upload (versioned path)
  GET    /users/me/resumes             — list all resume versions (history)
  GET    /users/me/resume              — get current resume metadata
  POST   /users/me/resumes/{id}/set-current — promote a version to current
  DELETE /users/me/resumes/{id}        — delete a version

Design:
  Each upload inserts a new ``resumes`` row (is_current=true), demotes all
  previous rows to is_current=false, AND keeps ``users.resume_text`` /
  ``users.resume_s3_key`` updated so the B-033 interview enrichment path
  that reads those columns continues to work without changes.

  Each version is stored at a unique S3 key:
    resumes/{user_id}/{resume_id}.pdf
  instead of overwriting a single key per user.

PII note: resume_text is NEVER logged.  Only user_id, s3 key, and character
count (a non-PII metric) appear in log lines.

Authz: all endpoints enforce that the resume.user_id equals the JWT sub.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from datetime import UTC, datetime
from typing import Annotated

import aioboto3
import structlog
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from pypdf import PdfReader
from shared.auth.base import User
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.dependencies import get_current_user
from app.models import Resume

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/users/me", tags=["resume"])

# ---------------------------------------------------------------------------
# Dependency shortcuts
# ---------------------------------------------------------------------------
CurrentUserDep = Annotated[User, Depends(get_current_user)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MAX_RESUME_BYTES = 5 * 1024 * 1024  # 5 MB
_PRESIGN_EXPIRY_SECONDS = 86400 * 7  # 7-day presigned URL


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ResumeUploadResponse(BaseModel):
    """Response body for a successful resume upload."""

    message: str
    resume_id: str = Field(..., description="UUID of the new resume version.")
    resume_s3_key: str
    text_length: int


class ResumeVersionItem(BaseModel):
    """A single resume version in the history list."""

    resume_id: str
    filename: str
    resume_s3_key: str
    text_length: int
    is_current: bool
    uploaded_at: datetime
    created_at: datetime
    download_url: str | None = Field(
        default=None,
        description="Pre-signed download URL (7 days). Null if S3 not configured.",
    )


class ResumeCurrentResponse(BaseModel):
    """Response for GET /users/me/resume (current resume metadata)."""

    resume_id: str
    filename: str
    resume_s3_key: str
    text_length: int
    uploaded_at: datetime
    created_at: datetime
    download_url: str | None = None


class SetCurrentResponse(BaseModel):
    """Response for POST /users/me/resumes/{id}/set-current."""

    message: str
    resume_id: str


class DeleteResumeResponse(BaseModel):
    """Response for DELETE /users/me/resumes/{id}."""

    message: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_PDF_PARSE_TIMEOUT_SECONDS: float = 30.0  # crafted PDFs must not hang the event loop


def _extract_pdf_text_sync(raw: bytes) -> str:
    """Extract plain text from a PDF byte payload using pypdf (synchronous).

    This function is CPU-bound and must be called via asyncio.to_thread to avoid
    blocking the event loop.  CVE-2025-62707 fixed by pypdf>=6.1.1 — still kept
    off the event loop via to_thread + a hard wall-clock timeout so a pathologically
    large or malformed PDF cannot DoS the service.

    Returns an empty string for scanned PDFs with no text layer — never raises.
    """
    reader = PdfReader(io.BytesIO(raw))
    pages: list[str] = []
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted:
            pages.append(extracted)
    return "\n".join(pages)


async def _extract_pdf_text(raw: bytes) -> str:
    """Async wrapper: runs pypdf parsing in a thread with a hard timeout.

    Raises asyncio.TimeoutError when the PDF takes longer than
    _PDF_PARSE_TIMEOUT_SECONDS — the caller should surface a 400.
    """
    return await asyncio.wait_for(
        asyncio.to_thread(_extract_pdf_text_sync, raw),
        timeout=_PDF_PARSE_TIMEOUT_SECONDS,
    )


async def _presign_url(s3_key: str) -> str | None:
    """Generate a 7-day pre-signed GET URL for *s3_key*.

    Returns None when S3 credentials are not configured (dev without MinIO).
    """
    if not settings.s3_access_key_id:
        return None

    endpoint_url: str | None = settings.s3_endpoint if settings.s3_endpoint else None
    boto_config: BotoConfig | None = (
        BotoConfig(s3={"addressing_style": "path"}) if endpoint_url else None
    )

    try:
        session = aioboto3.Session(
            aws_access_key_id=settings.s3_access_key_id or None,
            aws_secret_access_key=settings.s3_secret_access_key or None,
            region_name=settings.s3_region,
        )
        async with session.client(
            "s3",
            endpoint_url=endpoint_url,
            use_ssl=settings.s3_use_ssl,
            config=boto_config,
        ) as s3:
            url: str = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.s3_bucket_name, "Key": s3_key},
                ExpiresIn=_PRESIGN_EXPIRY_SECONDS,
            )
        return url
    except Exception as exc:
        log.error(
            "resume.presign_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None


async def _upload_to_s3(raw: bytes, s3_key: str) -> None:
    """Upload resume bytes to S3/R2."""
    from app.s3_upload import upload_file  # local import — avoids circular if any

    await upload_file(
        bucket=settings.s3_bucket_name,
        key=s3_key,
        data=raw,
        content_type="application/pdf",
        settings=settings,
    )


async def _delete_from_s3(s3_key: str) -> None:
    """Best-effort delete of an S3 object.

    Called to clean up an orphaned upload when the subsequent DB commit fails.
    Logs and swallows all exceptions — the caller must re-raise the original
    error regardless of whether the S3 cleanup succeeded.
    """
    if not settings.s3_access_key_id:
        return  # No S3 configured (dev without MinIO) — nothing to clean up.

    import aioboto3
    from botocore.config import Config as BotoConfig

    endpoint_url: str | None = settings.s3_endpoint if settings.s3_endpoint else None
    boto_config: BotoConfig | None = (
        BotoConfig(s3={"addressing_style": "path"}) if endpoint_url else None
    )
    try:
        session = aioboto3.Session(
            aws_access_key_id=settings.s3_access_key_id or None,
            aws_secret_access_key=settings.s3_secret_access_key or None,
            region_name=settings.s3_region,
        )
        async with session.client(
            "s3",
            endpoint_url=endpoint_url,
            use_ssl=settings.s3_use_ssl,
            config=boto_config,
        ) as s3:
            await s3.delete_object(Bucket=settings.s3_bucket_name, Key=s3_key)
        log.info("resume.s3_cleanup.ok", s3_key=s3_key)
    except Exception as exc:
        # Best-effort only — log the failure and let the caller re-raise the
        # original DB error.  An orphaned S3 object is far better than a
        # swallowed DB error.
        log.warning(
            "resume.s3_cleanup.failed",
            s3_key=s3_key,
            error_type=type(exc).__name__,
            error=str(exc),
        )


async def _sync_users_table(
    db: AsyncSession,
    user_uuid: uuid.UUID,
    resume_text: str,
    resume_s3_key: str,
) -> None:
    """Keep users.resume_text + users.resume_s3_key in sync with the current resume.

    Called whenever is_current changes.  The B-033 enrichment path reads
    users.resume_text directly — this keeps it pointing at the active version.
    """
    await db.execute(
        text(
            "UPDATE users "
            "SET resume_text = :resume_text, resume_s3_key = :resume_s3_key, "
            "updated_at = now() "
            "WHERE id = :uid"
        ),
        {
            "resume_text": resume_text,
            "resume_s3_key": resume_s3_key,
            "uid": user_uuid,
        },
    )


async def _demote_current_resumes(db: AsyncSession, user_uuid: uuid.UUID) -> None:
    """Set is_current=false for all existing current resumes of the user."""
    await db.execute(
        text(
            "UPDATE resumes SET is_current = false "
            "WHERE user_id = :uid AND is_current = true"
        ),
        {"uid": user_uuid},
    )


# ---------------------------------------------------------------------------
# Shared upload logic (used by both POST /resume and POST /resumes)
# ---------------------------------------------------------------------------


async def _do_upload(
    file: UploadFile,
    user: User,
    db: AsyncSession,
) -> ResumeUploadResponse:
    """Core upload logic shared by the B-031 compat route and the new versioned route."""

    # --- 1. content-type check ---
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted for resume upload.",
        )

    # --- 2. read + size check ---
    # A mid-upload client disconnect (starlette ClientDisconnect) or a spooled
    # temp-file OSError would otherwise escape as an unhandled, CORS-less 500.
    try:
        raw: bytes = await file.read()
    except Exception as exc:
        log.warning("resume.upload.read_failed", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read the uploaded file. Please try again.",
        ) from exc
    if len(raw) > _MAX_RESUME_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resume must be under 5 MB.",
        )

    # --- 3. text extraction (off the event loop — CVE-2025-62707 + DoS guard) ---
    # pypdf is synchronous and CPU-bound. Running it via asyncio.to_thread with a
    # hard timeout prevents a crafted PDF from blocking the event loop and DoS-ing
    # the service.  CVE-2025-62707 is fixed in pypdf>=6.1.1 (pinned in
    # requirements.txt); the to_thread wrapper is defence-in-depth.
    try:
        text_content = await _extract_pdf_text(raw)
    except TimeoutError as exc:
        log.warning(
            "resume.upload.pdf_parse_timeout",
            timeout_seconds=_PDF_PARSE_TIMEOUT_SECONDS,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The PDF took too long to process. Please try a simpler file.",
        ) from exc
    except Exception as exc:
        log.warning(
            "resume.upload.pdf_parse_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read the PDF. Please upload a valid, unencrypted PDF file.",
        ) from exc

    # --- 4. Build versioned S3 key ---
    # The JWT subject is not guaranteed to be a canonical UUID (the google/
    # keycloak/naipunyam auth providers may carry an email or opaque id), so
    # uuid.UUID() can raise — guard it into a clean 400 rather than an
    # unhandled, CORS-less 500.
    try:
        user_uuid = uuid.UUID(user.user_id)
    except (ValueError, AttributeError, TypeError) as exc:
        log.warning("resume.upload.bad_user_id", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not resolve your user account. Please sign in again.",
        ) from exc
    resume_id = uuid.uuid4()
    filename = file.filename or f"resume_{resume_id}.pdf"
    versioned_key = f"resumes/{user_uuid}/{resume_id}.pdf"

    # --- 4b. upload to object storage ---
    # A storage failure (bad/malformed credentials, unreachable endpoint, etc.)
    # must NOT escape as an unhandled 500: that response bypasses CORSMiddleware
    # and the browser surfaces it as "Network error — could not reach server"
    # with no actionable detail. Convert it to a 502 so the client receives a
    # CORS-decorated, meaningful error, and log the underlying cause for ops.
    try:
        await _upload_to_s3(raw, versioned_key)
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "resume.upload.storage_failed",
            user_id=user.user_id,
            s3_key=versioned_key,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Resume storage is currently unavailable. Please try again later.",
        ) from exc

    # --- 5. DB: demote previous current → insert new row → sync users table ---
    # IMPORTANT: S3 upload succeeded above. The ENTIRE write sequence is guarded
    # as one unit — a transient DB error or an autoflush IntegrityError can fire
    # on the demote/insert/sync statements, not only on commit(). On ANY failure
    # we best-effort delete the just-uploaded S3 object (to avoid an orphan) and
    # re-raise as an HTTPException so the response is produced INSIDE the CORS
    # middleware and carries the CORS headers the browser requires — an unhandled
    # DB error here would otherwise surface as a misleading "Network error".
    now = datetime.now(tz=UTC)
    try:
        await _demote_current_resumes(db, user_uuid)

        new_resume = Resume(
            id=resume_id,
            user_id=user_uuid,
            filename=filename,
            resume_text=text_content,
            resume_s3_key=versioned_key,
            is_current=True,
            uploaded_at=now,
            created_at=now,
        )
        db.add(new_resume)

        await _sync_users_table(db, user_uuid, text_content, versioned_key)
        await db.commit()
    except Exception as db_exc:
        # Rollback is handled by SQLAlchemy on the next operation, but the S3
        # object has already been written.  Best-effort delete it now so we
        # don't leave an unreferenced file in storage.
        log.error(
            "resume.upload.db_write_failed",
            user_id=str(user_uuid),
            s3_key=versioned_key,
            error_type=type(db_exc).__name__,
            error=str(db_exc),
        )
        await _delete_from_s3(versioned_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not save the resume. Please try again.",
        ) from db_exc

    log.info(
        "resume.upload.ok",
        user_id=user.user_id,
        resume_id=str(resume_id),
        s3_key=versioned_key,
        text_length=len(text_content),
    )

    return ResumeUploadResponse(
        message="Resume uploaded",
        resume_id=str(resume_id),
        resume_s3_key=versioned_key,
        text_length=len(text_content),
    )


# ---------------------------------------------------------------------------
# POST /users/me/resume  (B-031 backward-compat endpoint)
# ---------------------------------------------------------------------------


@router.post(
    "/resume",
    status_code=status.HTTP_200_OK,
    response_model=ResumeUploadResponse,
    summary="Upload a PDF resume for the current user (B-031)",
    description=(
        "Accepts a PDF file (multipart/form-data field name: ``file``). "
        "Extracts plain text, stores the file in R2/S3 at a versioned key, "
        "inserts a new ``resumes`` row (is_current=true), demotes previous versions, "
        "and updates ``users.resume_text`` for B-033 enrichment compatibility. "
        "Maximum file size: 5 MB."
    ),
)
async def upload_resume(
    file: UploadFile,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> ResumeUploadResponse:
    """Upload a resume PDF — backward-compatible B-031 endpoint."""
    return await _do_upload(file, current_user, db)


# ---------------------------------------------------------------------------
# POST /users/me/resumes  (versioned alias)
# ---------------------------------------------------------------------------


@router.post(
    "/resumes",
    status_code=status.HTTP_200_OK,
    response_model=ResumeUploadResponse,
    summary="Upload a new resume version",
    description=(
        "Alias for POST /users/me/resume with versioned semantics. "
        "Each call creates a new ``resumes`` row; previous versions are retained "
        "in history with is_current=false."
    ),
)
async def upload_resume_versioned(
    file: UploadFile,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> ResumeUploadResponse:
    """Upload a new resume version."""
    return await _do_upload(file, current_user, db)


# ---------------------------------------------------------------------------
# GET /users/me/resumes  (history list)
# ---------------------------------------------------------------------------


@router.get(
    "/resumes",
    status_code=status.HTTP_200_OK,
    response_model=list[ResumeVersionItem],
    summary="List all resume versions for the current user",
    description=(
        "Returns all uploaded resume versions for the authenticated user, "
        "newest-first.  Each item includes a 7-day pre-signed download URL "
        "(null when S3 credentials are not configured). "
        "AUTHZ: only the caller's own resumes."
    ),
)
async def list_resumes(
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> list[ResumeVersionItem]:
    """List resume version history for the authenticated user."""
    user_uuid = uuid.UUID(current_user.user_id)

    result = await db.execute(
        select(Resume)
        .where(Resume.user_id == user_uuid)
        .order_by(Resume.uploaded_at.desc())
    )
    resumes = result.scalars().all()

    items: list[ResumeVersionItem] = []
    for r in resumes:
        download_url = await _presign_url(r.resume_s3_key)
        items.append(
            ResumeVersionItem(
                resume_id=str(r.id),
                filename=r.filename,
                resume_s3_key=r.resume_s3_key,
                text_length=len(r.resume_text),
                is_current=r.is_current,
                uploaded_at=r.uploaded_at,
                created_at=r.created_at,
                download_url=download_url,
            )
        )

    log.info("resume.list", user_id=current_user.user_id, count=len(items))
    return items


# ---------------------------------------------------------------------------
# GET /users/me/resume  (current resume metadata)
# ---------------------------------------------------------------------------


@router.get(
    "/resume",
    status_code=status.HTTP_200_OK,
    response_model=ResumeCurrentResponse | None,
    summary="Get the current resume metadata",
    description=(
        "Returns metadata for the currently active resume version, or null when "
        "no resume has been uploaded yet. 'No resume on file' is a normal empty "
        "state — not an error — so this returns 200/null rather than 404 (which "
        "would surface as a console/network error in the browser)."
    ),
)
async def get_current_resume(
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> ResumeCurrentResponse | None:
    """Return the current resume version for the authenticated user, or None."""
    user_uuid = uuid.UUID(current_user.user_id)

    result = await db.execute(
        select(Resume)
        .where(Resume.user_id == user_uuid, Resume.is_current.is_(True))
        .limit(1)
    )
    resume: Resume | None = result.scalar_one_or_none()

    if resume is None:
        return None

    download_url = await _presign_url(resume.resume_s3_key)

    return ResumeCurrentResponse(
        resume_id=str(resume.id),
        filename=resume.filename,
        resume_s3_key=resume.resume_s3_key,
        text_length=len(resume.resume_text),
        uploaded_at=resume.uploaded_at,
        created_at=resume.created_at,
        download_url=download_url,
    )


# ---------------------------------------------------------------------------
# POST /users/me/resumes/{resume_id}/set-current
# ---------------------------------------------------------------------------


@router.post(
    "/resumes/{resume_id}/set-current",
    status_code=status.HTTP_200_OK,
    response_model=SetCurrentResponse,
    summary="Set a resume version as current",
    description=(
        "Promotes the specified resume version to is_current=true, "
        "demotes all other versions, and syncs users.resume_text / "
        "users.resume_s3_key so the B-033 enrichment path stays correct. "
        "AUTHZ: only own resumes; 404 on other users' ids."
    ),
)
async def set_current_resume(
    resume_id: uuid.UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SetCurrentResponse:
    """Promote a resume version to current."""
    user_uuid = uuid.UUID(current_user.user_id)

    result = await db.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user_uuid)
    )
    resume: Resume | None = result.scalar_one_or_none()

    if resume is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resume {resume_id} not found.",
        )

    # Demote all, then promote this one.
    await _demote_current_resumes(db, user_uuid)
    await db.execute(
        text("UPDATE resumes SET is_current = true WHERE id = :rid"),
        {"rid": resume_id},
    )
    await _sync_users_table(db, user_uuid, resume.resume_text, resume.resume_s3_key)
    await db.commit()

    log.info(
        "resume.set_current",
        user_id=current_user.user_id,
        resume_id=str(resume_id),
    )

    return SetCurrentResponse(message="Resume set as current.", resume_id=str(resume_id))


# ---------------------------------------------------------------------------
# DELETE /users/me/resumes/{resume_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/resumes/{resume_id}",
    status_code=status.HTTP_200_OK,
    response_model=DeleteResumeResponse,
    summary="Delete a resume version",
    description=(
        "Deletes the specified resume version from the database AND performs a "
        "best-effort deletion of the corresponding S3 object (DPDP Act 2023 "
        "§17 right-to-erasure requirement — personal data must be removed from "
        "all storage tiers). "
        "If the S3 deletion fails the DB deletion still completes and the error "
        "is logged; a future cleanup job can sweep unreferenced objects. "
        "If the deleted version was is_current=true, the next-newest version "
        "is automatically promoted; if none remains, users.resume_text / "
        "users.resume_s3_key are cleared. "
        "AUTHZ: only own resumes; 404 on other users' ids."
    ),
)
async def delete_resume(
    resume_id: uuid.UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> DeleteResumeResponse:
    """Delete a resume version; promote next-newest if it was current.

    DPDP Act 2023 §17 right-to-erasure: the S3 object is deleted best-effort
    after the DB row is removed.  S3 errors are logged but do not fail the
    request — the row is already gone and the file is now unreferenced.
    """
    user_uuid = uuid.UUID(current_user.user_id)

    result = await db.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user_uuid)
    )
    resume: Resume | None = result.scalar_one_or_none()

    if resume is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resume {resume_id} not found.",
        )

    was_current = resume.is_current
    # Capture the S3 key before deleting the ORM object (the attribute
    # becomes unavailable after session.delete() + flush).
    s3_key_to_delete: str = resume.resume_s3_key

    # Delete the row.
    await db.delete(resume)
    await db.flush()

    if was_current:
        # Promote the next-newest version (if any).
        next_result = await db.execute(
            select(Resume)
            .where(Resume.user_id == user_uuid)
            .order_by(Resume.uploaded_at.desc())
            .limit(1)
        )
        next_resume: Resume | None = next_result.scalar_one_or_none()

        if next_resume is not None:
            await db.execute(
                text("UPDATE resumes SET is_current = true WHERE id = :rid"),
                {"rid": next_resume.id},
            )
            await _sync_users_table(
                db, user_uuid, next_resume.resume_text, next_resume.resume_s3_key
            )
        else:
            # No remaining resumes — clear the users columns.
            await db.execute(
                text(
                    "UPDATE users "
                    "SET resume_text = NULL, resume_s3_key = NULL, updated_at = now() "
                    "WHERE id = :uid"
                ),
                {"uid": user_uuid},
            )

    await db.commit()

    # DPDP Act 2023 §17 — best-effort S3 erasure after successful DB commit.
    # We delete after commit so a S3 failure does not roll back the DB deletion.
    # _delete_from_s3 logs any error internally and never raises.
    await _delete_from_s3(s3_key_to_delete)

    log.info(
        "resume.delete",
        user_id=current_user.user_id,
        resume_id=str(resume_id),
        was_current=was_current,
        s3_key=s3_key_to_delete,
    )

    return DeleteResumeResponse(message=f"Resume {resume_id} deleted.")
