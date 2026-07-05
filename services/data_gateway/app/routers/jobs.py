"""Jobs REST endpoints — S2-002 / self-serve custom jobs.

Contract:
  GET  /jobs           → 200 JobsListResponse | 401
         Only returns public jobs (created_by_user_id IS NULL).
  GET  /jobs/{job_id}  → 200 JobDetail        | 401 | 404
         Returns any active, non-deleted job regardless of owner.
  POST /jobs           → 201 JobCreateResponse | 401 | 422
         Creates a user-owned "practice" job; excludes it from the browse list.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared.auth.base import User
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models import Job
from app.schemas.jobs import (
    JobCreateCustom,
    JobCreateResponse,
    JobDetail,
    JobListItem,
    JobsListResponse,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

# ---------------------------------------------------------------------------
# Dependency shortcuts
# ---------------------------------------------------------------------------
CurrentUserDep = Annotated[User, Depends(get_current_user)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    response_model=JobsListResponse,
    summary="List active public jobs (paginated)",
)
async def list_jobs(
    current_user: CurrentUserDep,
    db: DbSessionDep,
    page: Annotated[int, Query(ge=1, description="Page number (1-based)")] = 1,
    per_page: Annotated[
        int, Query(ge=1, le=100, description="Items per page (max 100)")
    ] = 20,
    language: Annotated[
        str | None, Query(description="Filter by BCP-47 language code, e.g. 'en'")
    ] = None,
) -> JobsListResponse:
    """Return a paginated list of active, non-deleted **public** jobs ordered by title ASC.

    Filters applied:
    - ``is_active = true``
    - ``deleted_at IS NULL``
    - ``created_by_user_id IS NULL``  — public/seeded jobs only; user-created
      practice jobs are excluded (they are accessed directly by UUID).
    - ``language = <param>`` (when provided)
    """
    base_filter = (
        Job.is_active.is_(True),
        Job.deleted_at.is_(None),
        Job.created_by_user_id.is_(None),
    )

    # Count query — separate from the data query to avoid N+1
    count_stmt = select(func.count()).select_from(Job).where(*base_filter)
    if language is not None:
        count_stmt = count_stmt.where(Job.language == language)

    total_result = await db.execute(count_stmt)
    total: int = total_result.scalar_one()

    # Data query with pagination
    offset = (page - 1) * per_page
    data_stmt = (
        select(Job)
        .where(*base_filter)
        .order_by(Job.title.asc())
        .limit(per_page)
        .offset(offset)
    )
    if language is not None:
        data_stmt = data_stmt.where(Job.language == language)

    rows = await db.execute(data_stmt)
    jobs = rows.scalars().all()

    log.info(
        "jobs.list",
        user_id=current_user.user_id,
        page=page,
        per_page=per_page,
        language=language,
        total=total,
        returned=len(jobs),
    )

    return JobsListResponse(
        items=[JobListItem.model_validate(j) for j in jobs],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=JobCreateResponse,
    summary="Create a custom practice job (self-serve)",
)
async def create_custom_job(
    body: JobCreateCustom,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> JobCreateResponse:
    """Create a user-owned practice job for a self-serve interview session.

    The new job row is stamped with ``created_by_user_id = <current user>``.
    It is excluded from ``GET /jobs`` (the public browse list) so it does not
    clutter other users' views.  The caller should address the job directly
    using the ``id`` returned in this response.

    ``description`` defaults to the ``title`` when the caller omits it, satisfying
    the NOT NULL constraint without burdening the form.

    PII note: ``jd_text`` may contain sensitive information — it is NOT logged.
    Only the job id, owning user id, and title length are emitted to the log.
    """
    now = datetime.now(UTC)
    owner_uuid = uuid.UUID(current_user.user_id)

    # description NOT NULL — default to title when the caller leaves it blank.
    effective_description = body.description.strip() or body.title

    job = Job(
        id=uuid.uuid4(),
        title=body.title,
        description=effective_description,
        level=body.level,
        language="en",  # self-serve jobs default to English (user changes via session)
        nos_codes=[],
        competencies={},
        is_active=True,
        created_at=now,
        updated_at=now,
        company_name=body.company_name or None,
        department=body.department or None,
        interview_type=body.interview_type,
        jd_text=body.jd_text or None,
        jd_s3_key=None,
        created_by_user_id=owner_uuid,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    log.info(
        "jobs.create_custom",
        job_id=str(job.id),
        user_id=current_user.user_id,
        title_length=len(body.title),
    )

    return JobCreateResponse(id=job.id, title=job.title)


@router.get(
    "/{job_id}",
    status_code=status.HTTP_200_OK,
    response_model=JobDetail,
    summary="Get a single job by UUID",
)
async def get_job(
    job_id: uuid.UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> JobDetail:
    """Return full job detail including NOS codes and competencies.

    Returns HTTP 404 if the job does not exist, is inactive, or is soft-deleted.
    """
    stmt = select(Job).where(
        Job.id == job_id,
        Job.is_active.is_(True),
        Job.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()

    if job is None:
        log.info("jobs.get.not_found", job_id=str(job_id), user_id=current_user.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found.",
        )

    log.info("jobs.get", job_id=str(job_id), user_id=current_user.user_id)
    return JobDetail.model_validate(job)
