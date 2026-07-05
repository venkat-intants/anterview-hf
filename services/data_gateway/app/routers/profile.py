"""Read-only profile viewing for privileged roles.

  GET /users/{user_id}/profile  — HR managers and company super-admins can view
                                  any user in THEIR OWN company; platform admins
                                  and the platform owner can view any user.

This is the "click a candidate to see their details" surface. Editing one's own
profile lives in auth.py (PATCH /auth/me/profile); this router is view-only and
never returns secrets (no password hash, no resume text). Role-based access is
enforced by ``require_role`` — candidates cannot browse other users' profiles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from shared.auth.base import User
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import require_role

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/users", tags=["profiles"])

# Who may view another user's profile. Company-scoped roles (hr_manager,
# super_admin) only see users in THEIR OWN company (enforced in the handler);
# the platform roles (admin, platform_owner) may view any user.
ViewerDep = Annotated[
    User, Depends(require_role("hr_manager", "super_admin", "admin", "platform_owner"))
]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]

# Roles that see ALL users platform-wide; everyone else is company-scoped.
_GLOBAL_VIEW_ROLES = frozenset({"admin", "platform_owner"})


class PublicProfile(BaseModel):
    """A user's viewable profile (no secrets — never PII like resume text)."""

    user_id: str
    full_name: str | None
    email: str | None
    roles: list[str]
    avatar_url: str | None = None
    headline: str | None = None
    bio: str | None = None
    employment_status: str | None = None
    desired_roles: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    location: str | None = None
    phone: str | None = None
    official_email: str | None = None
    has_resume: bool = False
    company_name: str | None = None
    created_at: datetime | None = None


@router.get(
    "/{user_id}/profile",
    status_code=status.HTTP_200_OK,
    response_model=PublicProfile,
    summary="View another user's profile (own-company for HR/super-admin; any for admin/owner)",
)
async def get_user_profile(
    user_id: uuid.UUID,
    _viewer: ViewerDep,
    db: DbSessionDep,
) -> PublicProfile:
    """Return the public profile of ``user_id``. 404 if missing / soft-deleted.

    Tenant isolation: a company-scoped viewer (hr_manager / super_admin) may
    only view users in their OWN company. A target in another company (or a
    caller with no company) returns 404 — same as "not found", so the endpoint
    never confirms the existence of an out-of-tenant user.
    """
    result = await db.execute(
        text(
            "SELECT u.full_name, u.email, u.avatar_url, u.headline, u.bio,"
            " u.employment_status, u.desired_roles, u.linkedin_url, u.github_url,"
            " u.location, u.phone, u.official_email, u.resume_s3_key, u.created_at,"
            " c.name AS company_name, u.company_id,"
            " COALESCE(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL), '{}') AS roles"
            " FROM users u"
            " LEFT JOIN companies c ON c.id = u.company_id"
            " LEFT JOIN user_roles ur ON ur.user_id = u.id"
            " LEFT JOIN roles r ON r.id = ur.role_id"
            " WHERE u.id = :uid AND u.deleted_at IS NULL"
            " GROUP BY u.id, c.name"
        ),
        {"uid": user_id},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    # Tenant scoping for company-bound viewers.
    if not (_GLOBAL_VIEW_ROLES & set(_viewer.roles)):
        target_company_id = row[15]
        try:
            caller_uid = uuid.UUID(_viewer.user_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
            ) from exc
        if caller_uid != user_id:
            caller_company_id = await db.scalar(
                text("SELECT company_id FROM users WHERE id = :uid AND deleted_at IS NULL"),
                {"uid": caller_uid},
            )
            if caller_company_id is None or caller_company_id != target_company_id:
                # Out-of-tenant (or caller unassigned): behave as "not found".
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
                )

    log.info("profile.view", target=str(user_id))
    return PublicProfile(
        user_id=str(user_id),
        full_name=row[0],
        email=row[1],
        avatar_url=row[2],
        headline=row[3],
        bio=row[4],
        employment_status=row[5],
        desired_roles=row[6],
        linkedin_url=row[7],
        github_url=row[8],
        location=row[9],
        phone=row[10],
        official_email=row[11],
        has_resume=bool(row[12]),
        created_at=row[13],
        company_name=row[14],
        roles=list(row[16] or []),
    )
