"""Admin endpoints — three-tier hierarchy (HR workflow Phase 0+).

Two privileged tiers manage the tenant identity graph:

  PLATFORM OWNER ("super super admin" — the Intants core, support@intants.com):
    POST   /admin/companies                          — create a company
    GET    /admin/companies                           — list companies
    POST   /admin/companies/{company_id}/admin        — create the ONE company super admin
    GET    /admin/companies/{company_id}/admin         — get the company super admin
    GET    /admin/companies/{company_id}/hr-managers   — read-only view of a company's HRs
    GET/PUT /admin/feature-flags[...]                  — platform feature flags
    GET    /admin/audit-log                            — DPDP audit feed

  COMPANY SUPER ADMIN ("super admin" — one per company, company-scoped):
    GET    /admin/hr-managers                          — list HRs in the caller's company
    POST   /admin/hr-managers                          — create an HR in the caller's company

Accounts created with a (default) password get ``must_change_password=true`` so
they are forced to reset it on first login. A company has AT MOST ONE super admin.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, EmailStr, Field
from shared.auth.base import AuthProvider, User
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth_tokens import hash_token, mint_token, ttl_hours_for
from app.config import settings
from app.database import get_db_session
from app.dependencies import get_auth_provider_dep, require_role
from app.mailer import enqueue_email

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-hr"])

PlatformOwnerDep = Annotated[User, Depends(require_role("platform_owner"))]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
AuthProviderDep = Annotated[AuthProvider, Depends(get_auth_provider_dep)]


# ---------------------------------------------------------------------------
# Company-super-admin tenant context — resolves the caller's company_id.
# This is the isolation boundary: a company super admin can ONLY ever touch
# its own company's HR managers.
# ---------------------------------------------------------------------------
async def get_company_admin_ctx(
    user: Annotated[User, Depends(require_role("super_admin"))],
    db: DbSessionDep,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Return (admin_user_id, company_id). 403 if not assigned to a company."""
    try:
        uid = uuid.UUID(user.user_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user identity."
        ) from exc
    # Join companies + deleted_at so a super_admin of a soft-deleted tenant is
    # treated as unassigned (consistent with the platform-owner paths).
    company_id = await db.scalar(
        text(
            "SELECT u.company_id FROM users u "
            "JOIN companies c ON c.id = u.company_id AND c.deleted_at IS NULL "
            "WHERE u.id = :uid AND u.deleted_at IS NULL"
        ),
        {"uid": uid},
    )
    if company_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your super-admin account is not assigned to an active company.",
        )
    return uid, company_id


CompanyAdminCtxDep = Annotated[tuple[uuid.UUID, uuid.UUID], Depends(get_company_admin_ctx)]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreateCompanyBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, max_length=80)


class CompanyResponse(BaseModel):
    id: str
    name: str
    slug: str
    is_active: bool
    hr_count: int = 0
    has_admin: bool = False
    admin_email: str | None = None
    created_at: str


class CreateUserBody(BaseModel):
    """Shared body for creating a company super admin OR an HR manager."""

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    # A RANDOM bootstrap password is generated per account when none is supplied.
    # The user never uses it — a "set your password" link is emailed and they
    # choose their own (see _send_credentials_email). It only exists so the row
    # has a hash before that link is consumed. NEVER default to a known/published
    # value: with must_change_password=true an attacker who logs in first with a
    # known default (during the pre-reset window) takes over the account.
    password: str = Field(
        default_factory=lambda: secrets.token_urlsafe(16), min_length=8, max_length=128
    )


class CompanyAdminResponse(BaseModel):
    user_id: str
    email: str
    full_name: str
    company_id: str
    must_change_password: bool
    created_at: str


class HrManagerResponse(BaseModel):
    user_id: str
    email: str
    full_name: str
    company_id: str
    must_change_password: bool
    created_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "company"


async def _hash_password(password: str) -> str:
    rounds: int = settings.password_hash_rounds
    return await asyncio.to_thread(
        lambda: bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()
    )


async def _company_or_404(db: AsyncSession, company_id: uuid.UUID) -> None:
    exists = await db.scalar(
        text("SELECT 1 FROM companies WHERE id = :cid AND deleted_at IS NULL"),
        {"cid": company_id},
    )
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found.",
        )


async def _create_company_user(
    db: AsyncSession, *, company_id: uuid.UUID, body: CreateUserBody, role: str
) -> tuple[uuid.UUID, datetime]:
    """Insert a company-scoped user with the given role + must_change_password.

    Returns (user_id, created_at). Raises 409 on duplicate email.
    """
    # Resolve the role id up front so a missing role surfaces as a clear 500
    # rather than a NOT-NULL IntegrityError mislabelled as an email conflict.
    role_id = await db.scalar(
        text("SELECT id FROM roles WHERE name = :role"), {"role": role}
    )
    if role_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Role '{role}' is not provisioned — run database migrations.",
        )
    user_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    password_hash = await _hash_password(body.password)
    try:
        await db.execute(
            text(
                "INSERT INTO users "
                "(id, email, password_hash, full_name, company_id, "
                " must_change_password, created_at, updated_at) "
                "VALUES (:id, :email, :pw, :fn, :cid, true, :now, :now)"
            ),
            {
                "id": user_id, "email": str(body.email), "pw": password_hash,
                "fn": body.full_name, "cid": company_id, "now": now,
            },
        )
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id, assigned_at) "
                "VALUES (:uid, :rid, :now)"
            ),
            {"uid": user_id, "rid": role_id, "now": now},
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from exc

    # Best-effort: email the new user a secure "set your password" link (its own
    # transaction so a mail hiccup never fails account creation). We email a
    # password-set link rather than the bootstrap password — the user sets their
    # own password (which also verifies their email).
    await _send_credentials_email(
        db, user_id=user_id, email=str(body.email), full_name=body.full_name,
        role=role, company_id=company_id,
    )
    return user_id, now


# Human-readable role labels for the account-created email.
_ROLE_LABELS = {"super_admin": "company admin", "hr_manager": "HR manager"}


async def _send_credentials_email(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    email: str,
    full_name: str,
    role: str,
    company_id: uuid.UUID,
) -> None:
    """Mint a password-set link + enqueue the credentials email (best-effort)."""
    try:
        now = datetime.now(tz=UTC)
        raw = mint_token()
        await db.execute(
            text(
                "INSERT INTO auth_tokens (id, user_id, kind, token_hash, expires_at, created_at) "
                "VALUES (:id, :uid, 'password_reset', :th, :exp, :now)"
            ),
            {
                "id": uuid.uuid4(),
                "uid": user_id,
                "th": hash_token(raw, "password_reset"),
                "exp": now + timedelta(hours=ttl_hours_for("password_reset")),
                "now": now,
            },
        )
        company_name = await db.scalar(
            text("SELECT name FROM companies WHERE id = :cid"), {"cid": company_id}
        )
        await enqueue_email(
            db,
            to=email,
            template="hr_credentials",
            lang="en",
            ctx={
                "name": full_name,
                "role_label": _ROLE_LABELS.get(role, "team member"),
                "company": company_name,
                "set_url": f"{settings.app_base_url.rstrip('/')}/reset-password#{raw}",
                "login_url": settings.app_base_url,
            },
            to_user_id=user_id,
            company_id=company_id,
            related_kind="account_created",
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — credential email must never fail account creation
        await db.rollback()
        log.warning("admin_hr.credentials_email_failed", user_id=str(user_id))


async def _soft_delete_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Soft-delete a user: mark deleted, deactivate, and tombstone the email so
    the original address can be re-used for a brand-new account later.

    (Soft-delete matches the repo's deleted_at convention and preserves the row
    for audit/history; list queries already filter deleted_at IS NULL.)
    """
    await db.execute(
        text(
            "UPDATE users SET deleted_at = now(), is_active = false, "
            "email = email || '.deleted.' || id::text, updated_at = now() "
            "WHERE id = :uid AND deleted_at IS NULL"
        ),
        {"uid": user_id},
    )


async def _audit(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    details: dict[str, object] | None = None,
) -> None:
    """Append an admin action to the (append-only) DPDP audit_log.

    event_id + event_ts use server defaults; the append-only trigger blocks
    UPDATE/DELETE but permits INSERT.
    """
    await db.execute(
        text(
            "INSERT INTO audit_log "
            "(actor_id, actor_type, action, resource_type, resource_id, details) "
            "VALUES (:aid, 'admin', :action, :rtype, :rid, CAST(:details AS jsonb))"
        ),
        {
            "aid": actor_id, "action": action, "rtype": resource_type,
            "rid": resource_id, "details": json.dumps(details) if details else None,
        },
    )


# ===========================================================================
# PLATFORM OWNER — companies
# ===========================================================================


@router.post("/companies", status_code=status.HTTP_201_CREATED, response_model=CompanyResponse)
async def create_company(
    body: CreateCompanyBody, current_user: PlatformOwnerDep, db: DbSessionDep
) -> CompanyResponse:
    """Create a tenant company (platform_owner only)."""
    company_id = uuid.uuid4()
    slug = _slugify(body.slug or body.name)
    now = datetime.now(tz=UTC)
    try:
        await db.execute(
            text(
                "INSERT INTO companies "
                "(id, name, slug, created_by_user_id, created_at, updated_at) "
                "VALUES (:id, :name, :slug, :uid, :now, :now)"
            ),
            {
                "id": company_id,
                "name": body.name,
                "slug": slug,
                "uid": uuid.UUID(current_user.user_id),
                "now": now,
            },
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A company with slug '{slug}' already exists.",
        ) from exc
    log.info("admin_hr.company_created", company_id=str(company_id), slug=slug)
    return CompanyResponse(
        id=str(company_id), name=body.name, slug=slug, is_active=True, hr_count=0,
        has_admin=False, admin_email=None, created_at=now.isoformat(),
    )


@router.get("/companies", response_model=list[CompanyResponse])
async def list_companies(
    current_user: PlatformOwnerDep, db: DbSessionDep
) -> list[CompanyResponse]:
    """List companies with HR-manager counts + their super admin (platform_owner only)."""
    rows = (
        await db.execute(
            text(
                "SELECT c.id, c.name, c.slug, c.is_active, c.created_at, "
                "(SELECT count(*) FROM users u "
                " JOIN user_roles ur ON ur.user_id = u.id "
                " JOIN roles r ON r.id = ur.role_id AND r.name = 'hr_manager' "
                " WHERE u.company_id = c.id AND u.deleted_at IS NULL) AS hr_count, "
                "(SELECT u.email FROM users u "
                " JOIN user_roles ur ON ur.user_id = u.id "
                " JOIN roles r ON r.id = ur.role_id AND r.name = 'super_admin' "
                " WHERE u.company_id = c.id AND u.deleted_at IS NULL "
                " ORDER BY u.created_at ASC LIMIT 1) AS admin_email "
                "FROM companies c WHERE c.deleted_at IS NULL "
                "ORDER BY c.created_at DESC"
            )
        )
    ).fetchall()
    return [
        CompanyResponse(
            id=str(r[0]), name=r[1], slug=r[2], is_active=r[3],
            created_at=r[4].isoformat(), hr_count=int(r[5]),
            has_admin=r[6] is not None, admin_email=r[6],
        )
        for r in rows
    ]


@router.delete("/companies/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: uuid.UUID,
    current_user: PlatformOwnerDep,
    db: DbSessionDep,
    auth: AuthProviderDep,
) -> Response:
    """Soft-delete a company and ALL its member users — its super admin and HR
    managers (platform_owner only).

    The company's applicants / exams / interview records are retained in the DB
    but become inaccessible (the company and every login into it are gone).
    Member emails are released so they can be re-used for new accounts.

    All active sessions for every deleted member are immediately revoked via
    ``auth.logout_all`` so their tokens stop working within the access-token
    window rather than expiring naturally (up to 15 minutes later).
    """
    locked = await db.scalar(
        text("SELECT 1 FROM companies WHERE id = :cid AND deleted_at IS NULL FOR UPDATE"),
        {"cid": company_id},
    )
    if not locked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found.",
        )
    # Collect all active member user IDs before the soft-delete so we can
    # revoke their sessions after the DB commit (revocation is best-effort and
    # must not roll back the company deletion if it fails).
    member_rows = (
        await db.execute(
            text("SELECT id FROM users WHERE company_id = :cid AND deleted_at IS NULL"),
            {"cid": company_id},
        )
    ).fetchall()
    member_ids = [str(r[0]) for r in member_rows]

    # Soft-delete + tombstone every member user (super admin + HR managers).
    await db.execute(
        text(
            "UPDATE users SET deleted_at = now(), is_active = false, "
            "email = email || '.deleted.' || id::text, updated_at = now() "
            "WHERE company_id = :cid AND deleted_at IS NULL"
        ),
        {"cid": company_id},
    )
    await db.execute(
        text(
            "UPDATE companies SET deleted_at = now(), is_active = false, "
            "updated_at = now() WHERE id = :cid"
        ),
        {"cid": company_id},
    )
    await _audit(
        db, actor_id=uuid.UUID(current_user.user_id), action="delete_company",
        resource_type="company", resource_id=company_id,
    )
    await db.commit()

    # Revoke sessions for all former members (best-effort — individual failures
    # are logged but must not prevent the 204 response).
    for uid in member_ids:
        try:
            await auth.logout_all(uid)
        except Exception as exc:  # noqa: BLE001 — best-effort revocation
            log.warning(
                "admin_hr.company_deleted.session_revoke_failed",
                user_id=uid,
                error_type=type(exc).__name__,
            )

    log.info(
        "admin_hr.company_deleted",
        company_id=str(company_id),
        members_revoked=len(member_ids),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# PLATFORM OWNER — platform-wide stats (real counts, no dummy data)
# ===========================================================================


class PlatformStats(BaseModel):
    companies: int
    super_admins: int
    hr_managers: int
    candidates: int
    interviews_total: int
    interviews_30d: int


@router.get("/platform-stats", response_model=PlatformStats)
async def platform_stats(current_user: PlatformOwnerDep, db: DbSessionDep) -> PlatformStats:
    """Real platform-wide counts for the platform-owner dashboard tiles."""
    row = (
        await db.execute(
            text(
                "SELECT "
                " (SELECT count(*) FROM companies WHERE deleted_at IS NULL) AS companies, "
                " (SELECT count(DISTINCT u.id) FROM users u "
                "  JOIN user_roles ur ON ur.user_id = u.id "
                "  JOIN roles r ON r.id = ur.role_id AND r.name = 'super_admin' "
                "  WHERE u.deleted_at IS NULL) AS super_admins, "
                " (SELECT count(DISTINCT u.id) FROM users u "
                "  JOIN user_roles ur ON ur.user_id = u.id "
                "  JOIN roles r ON r.id = ur.role_id AND r.name = 'hr_manager' "
                "  WHERE u.deleted_at IS NULL) AS hr_managers, "
                " (SELECT count(DISTINCT u.id) FROM users u "
                "  JOIN user_roles ur ON ur.user_id = u.id "
                "  JOIN roles r ON r.id = ur.role_id AND r.name = 'candidate' "
                "  WHERE u.deleted_at IS NULL) AS candidates, "
                " (SELECT count(*) FROM sessions WHERE deleted_at IS NULL) AS interviews_total, "
                " (SELECT count(*) FROM sessions WHERE deleted_at IS NULL "
                "  AND created_at >= now() - interval '30 days') AS interviews_30d"
            )
        )
    ).fetchone()
    return PlatformStats(
        companies=int(row[0]), super_admins=int(row[1]), hr_managers=int(row[2]),
        candidates=int(row[3]), interviews_total=int(row[4]), interviews_30d=int(row[5]),
    )


# ===========================================================================
# PLATFORM OWNER — company super admins (one per company)
# ===========================================================================


@router.post(
    "/companies/{company_id}/admin",
    status_code=status.HTTP_201_CREATED,
    response_model=CompanyAdminResponse,
)
async def create_company_admin(
    company_id: uuid.UUID, body: CreateUserBody, current_user: PlatformOwnerDep, db: DbSessionDep
) -> CompanyAdminResponse:
    """Create the company super admin (platform_owner only).

    A company has at most ONE super admin — a second attempt returns 409. The
    account is created with a random bootstrap password (or a supplied one) and
    must_change_password=true; a "set your password" link is emailed, so the
    bootstrap value is never used or disclosed.

    Concurrency: the company row is locked FOR UPDATE before the existence
    check, so two simultaneous requests for the same company serialize — the
    second waits, then sees the first's super admin and gets a clean 409 (no
    duplicate-admin race).
    """
    locked = await db.scalar(
        text("SELECT 1 FROM companies WHERE id = :cid AND deleted_at IS NULL FOR UPDATE"),
        {"cid": company_id},
    )
    if not locked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found.",
        )
    existing = await db.scalar(
        text(
            "SELECT u.email FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles r ON r.id = ur.role_id AND r.name = 'super_admin' "
            "WHERE u.company_id = :cid AND u.deleted_at IS NULL LIMIT 1"
        ),
        {"cid": company_id},
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This company already has a super admin ({existing}).",
        )
    user_id, now = await _create_company_user(
        db, company_id=company_id, body=body, role="super_admin"
    )
    log.info("admin_hr.company_admin_created", user_id=str(user_id), company_id=str(company_id))
    return CompanyAdminResponse(
        user_id=str(user_id), email=str(body.email), full_name=body.full_name,
        company_id=str(company_id), must_change_password=True, created_at=now.isoformat(),
    )


@router.get("/companies/{company_id}/admin", response_model=CompanyAdminResponse)
async def get_company_admin(
    company_id: uuid.UUID, current_user: PlatformOwnerDep, db: DbSessionDep
) -> CompanyAdminResponse:
    """Get a company's super admin (platform_owner only). 404 if none yet."""
    await _company_or_404(db, company_id)
    row = (
        await db.execute(
            text(
                "SELECT u.id, u.email, u.full_name, u.must_change_password, u.created_at "
                "FROM users u "
                "JOIN user_roles ur ON ur.user_id = u.id "
                "JOIN roles r ON r.id = ur.role_id AND r.name = 'super_admin' "
                "WHERE u.company_id = :cid AND u.deleted_at IS NULL "
                "ORDER BY u.created_at ASC LIMIT 1"
            ),
            {"cid": company_id},
        )
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This company has no super admin yet.",
        )
    return CompanyAdminResponse(
        user_id=str(row[0]), email=row[1], full_name=row[2] or "",
        company_id=str(company_id), must_change_password=row[3],
        created_at=row[4].isoformat(),
    )


@router.delete(
    "/companies/{company_id}/admin", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_company_admin(
    company_id: uuid.UUID,
    current_user: PlatformOwnerDep,
    db: DbSessionDep,
    auth: AuthProviderDep,
) -> Response:
    """Remove a company's super admin (platform_owner only).

    Afterwards the company has no super admin and a fresh one can be created
    (this is also how you 'replace' a company's super admin). 404 if none.

    All active sessions for the removed admin are immediately revoked.
    """
    await _company_or_404(db, company_id)
    uid = await db.scalar(
        text(
            "SELECT u.id FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles r ON r.id = ur.role_id AND r.name = 'super_admin' "
            "WHERE u.company_id = :cid AND u.deleted_at IS NULL "
            "ORDER BY u.created_at ASC LIMIT 1"
        ),
        {"cid": company_id},
    )
    if uid is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This company has no super admin to remove.",
        )
    await _soft_delete_user(db, uid)
    await _audit(
        db, actor_id=uuid.UUID(current_user.user_id), action="delete_company_admin",
        resource_type="user", resource_id=uid,
    )
    await db.commit()

    # Revoke all sessions for the removed admin immediately (best-effort).
    try:
        await auth.logout_all(str(uid))
    except Exception as exc:  # noqa: BLE001 — best-effort revocation
        log.warning(
            "admin_hr.company_admin_deleted.session_revoke_failed",
            user_id=str(uid),
            error_type=type(exc).__name__,
        )

    log.info("admin_hr.company_admin_deleted", user_id=str(uid), company_id=str(company_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/companies/{company_id}/hr-managers", response_model=list[HrManagerResponse]
)
async def list_company_hr_managers(
    company_id: uuid.UUID, current_user: PlatformOwnerDep, db: DbSessionDep
) -> list[HrManagerResponse]:
    """Read-only view of a company's HR managers (platform_owner only)."""
    await _company_or_404(db, company_id)
    return await _query_hr_managers(db, company_id)


# ===========================================================================
# COMPANY SUPER ADMIN — HR managers (scoped to the caller's own company)
# ===========================================================================


async def _query_hr_managers(
    db: AsyncSession, company_id: uuid.UUID
) -> list[HrManagerResponse]:
    rows = (
        await db.execute(
            text(
                "SELECT u.id, u.email, u.full_name, u.must_change_password, u.created_at "
                "FROM users u "
                "JOIN user_roles ur ON ur.user_id = u.id "
                "JOIN roles r ON r.id = ur.role_id AND r.name = 'hr_manager' "
                "WHERE u.company_id = :cid AND u.deleted_at IS NULL "
                "ORDER BY u.created_at DESC"
            ),
            {"cid": company_id},
        )
    ).fetchall()
    return [
        HrManagerResponse(
            user_id=str(r[0]), email=r[1], full_name=r[2] or "",
            company_id=str(company_id), must_change_password=r[3],
            created_at=r[4].isoformat(),
        )
        for r in rows
    ]


@router.get("/hr-managers", response_model=list[HrManagerResponse])
async def list_my_hr_managers(
    ctx: CompanyAdminCtxDep, db: DbSessionDep
) -> list[HrManagerResponse]:
    """List HR managers in the caller's own company (super_admin only)."""
    _, company_id = ctx
    return await _query_hr_managers(db, company_id)


@router.post(
    "/hr-managers", status_code=status.HTTP_201_CREATED, response_model=HrManagerResponse
)
async def create_my_hr_manager(
    body: CreateUserBody, ctx: CompanyAdminCtxDep, db: DbSessionDep
) -> HrManagerResponse:
    """Create an HR manager in the caller's own company (super_admin only).

    The HR is created with a random bootstrap password (or a supplied one) and
    must_change_password=true; a "set your password" link is emailed, so the
    bootstrap value is never used or disclosed.
    """
    _, company_id = ctx
    user_id, now = await _create_company_user(
        db, company_id=company_id, body=body, role="hr_manager"
    )
    log.info("admin_hr.hr_created", user_id=str(user_id), company_id=str(company_id))
    return HrManagerResponse(
        user_id=str(user_id), email=str(body.email), full_name=body.full_name,
        company_id=str(company_id), must_change_password=True, created_at=now.isoformat(),
    )


@router.delete("/hr-managers/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_hr_manager(
    user_id: uuid.UUID,
    ctx: CompanyAdminCtxDep,
    db: DbSessionDep,
    auth: AuthProviderDep,
) -> Response:
    """Soft-delete an HR manager in the caller's OWN company (super_admin only).

    Tenant-scoped: the target must be an hr_manager belonging to the caller's
    company, else 404 — a super admin can never remove another company's HR.
    The HR's email is released so it can be re-used for a new account.

    All active sessions for the removed HR are immediately revoked so their
    tokens stop working without waiting for the natural 15-minute expiry.
    """
    caller_uid, company_id = ctx
    target = await db.scalar(
        text(
            "SELECT 1 FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles r ON r.id = ur.role_id AND r.name = 'hr_manager' "
            "WHERE u.id = :uid AND u.company_id = :cid AND u.deleted_at IS NULL"
        ),
        {"uid": user_id, "cid": company_id},
    )
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="HR manager not found in your company.",
        )
    await _soft_delete_user(db, user_id)
    await _audit(
        db, actor_id=caller_uid, action="delete_hr_manager",
        resource_type="user", resource_id=user_id,
    )
    await db.commit()

    # Revoke all sessions for the removed HR immediately (best-effort).
    try:
        await auth.logout_all(str(user_id))
    except Exception as exc:  # noqa: BLE001 — best-effort revocation
        log.warning(
            "admin_hr.hr_deleted.session_revoke_failed",
            user_id=str(user_id),
            error_type=type(exc).__name__,
        )

    log.info("admin_hr.hr_deleted", user_id=str(user_id), company_id=str(company_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# PLATFORM OWNER — DPDP audit log (read-only)
# ===========================================================================


class AuditEvent(BaseModel):
    ts: str
    kind: str  # admin_action | consent_granted | consent_denied | consent_revoked
    summary: str
    actor: str


@router.get("/audit-log", response_model=list[AuditEvent])
async def audit_log(
    current_user: PlatformOwnerDep,
    db: DbSessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[AuditEvent]:
    """Merged DPDP audit feed: append-only admin actions + consent ledger events."""
    rows = (
        await db.execute(
            text(
                "SELECT ts, kind, summary, actor FROM ("
                "  SELECT event_ts AS ts, 'admin_action' AS kind, "
                "         COALESCE(action,'') AS summary, COALESCE(actor_type,'system') AS actor "
                "  FROM audit_log WHERE event_ts IS NOT NULL "
                "  UNION ALL "
                "  SELECT granted_at AS ts, "
                "         CASE WHEN granted THEN 'consent_granted' ELSE 'consent_denied' END AS kind, "
                "         COALESCE(purpose, consent_type) AS summary, 'candidate' AS actor "
                "  FROM dpdp_consent_ledger "
                "  UNION ALL "
                "  SELECT revoked_at AS ts, 'consent_revoked' AS kind, "
                "         COALESCE(purpose, consent_type) AS summary, 'candidate' AS actor "
                "  FROM dpdp_consent_ledger WHERE revoked_at IS NOT NULL "
                ") e ORDER BY ts DESC LIMIT :lim"
            ),
            {"lim": limit},
        )
    ).fetchall()
    return [
        AuditEvent(ts=r[0].isoformat(), kind=r[1], summary=r[2] or "", actor=r[3])
        for r in rows
    ]


# ===========================================================================
# PLATFORM OWNER — feature flags
# ===========================================================================


class FeatureFlagOut(BaseModel):
    key: str
    label: str
    description: str | None
    enabled: bool
    updated_at: str | None


class FeatureFlagToggle(BaseModel):
    enabled: bool


@router.get("/feature-flags", response_model=list[FeatureFlagOut])
async def list_feature_flags(
    current_user: PlatformOwnerDep, db: DbSessionDep
) -> list[FeatureFlagOut]:
    """List platform feature flags (platform_owner only)."""
    rows = (
        await db.execute(
            text(
                "SELECT key, label, description, enabled, updated_at "
                "FROM feature_flags ORDER BY key"
            )
        )
    ).fetchall()
    return [
        FeatureFlagOut(
            key=r[0], label=r[1], description=r[2], enabled=r[3],
            updated_at=r[4].isoformat() if r[4] else None,
        )
        for r in rows
    ]


@router.put("/feature-flags/{key}", response_model=FeatureFlagOut)
async def toggle_feature_flag(
    key: str, body: FeatureFlagToggle, current_user: PlatformOwnerDep, db: DbSessionDep
) -> FeatureFlagOut:
    """Enable/disable a platform feature flag (platform_owner only)."""
    now = datetime.now(tz=UTC)
    row = (
        await db.execute(
            text(
                "UPDATE feature_flags SET enabled = :en, updated_at = :now, updated_by = :uid "
                "WHERE key = :key "
                "RETURNING key, label, description, enabled, updated_at"
            ),
            {"en": body.enabled, "now": now, "uid": uuid.UUID(current_user.user_id), "key": key},
        )
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Feature flag not found."
        )
    await db.commit()
    return FeatureFlagOut(
        key=row[0], label=row[1], description=row[2], enabled=row[3],
        updated_at=row[4].isoformat() if row[4] else None,
    )


# ===========================================================================
# PLATFORM OWNER — email delivery log (observability)
# ===========================================================================


class EmailEventOut(BaseModel):
    id: str
    template: str
    to_email: str  # masked (DPDP) — local part redacted
    status: str  # queued | sending | sent | failed | cancelled
    attempts: int
    subject: str
    last_error: str | None
    created_at: str
    sent_at: str | None


class EmailEventSummary(BaseModel):
    queued: int
    sent: int
    failed: int


def _mask_email(addr: str) -> str:
    """Redact the local part for the admin log: 'a***@example.com'."""
    if not addr or "@" not in addr:
        return "***"
    local, _, domain = addr.partition("@")
    head = local[0] if local else ""
    return f"{head}***@{domain}"


@router.get("/email-events", response_model=list[EmailEventOut])
async def list_email_events(
    current_user: PlatformOwnerDep,
    db: DbSessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[EmailEventOut]:
    """Recent transactional-email delivery log (platform_owner only).

    Backs an ops view of every email's lifecycle (sent / failed / pending). The
    recipient address is masked — the log is for delivery health, not PII export.
    """
    sql = (
        "SELECT id, template, to_email, status, attempts, subject, last_error, "
        "created_at, sent_at FROM email_events "
    )
    params: dict[str, object] = {"lim": limit}
    if status_filter:
        sql += "WHERE status = :st "
        params["st"] = status_filter
    sql += "ORDER BY created_at DESC LIMIT :lim"
    rows = (await db.execute(text(sql), params)).fetchall()
    return [
        EmailEventOut(
            id=str(r[0]), template=r[1], to_email=_mask_email(r[2]), status=r[3],
            attempts=int(r[4]), subject=r[5], last_error=r[6],
            created_at=r[7].isoformat(), sent_at=r[8].isoformat() if r[8] else None,
        )
        for r in rows
    ]


@router.get("/email-events/summary", response_model=EmailEventSummary)
async def email_events_summary(
    current_user: PlatformOwnerDep, db: DbSessionDep
) -> EmailEventSummary:
    """Counts by lifecycle bucket for the platform dashboard."""
    row = (
        await db.execute(
            text(
                "SELECT "
                " count(*) FILTER (WHERE status IN ('queued','sending')) AS queued, "
                " count(*) FILTER (WHERE status = 'sent') AS sent, "
                " count(*) FILTER (WHERE status = 'failed') AS failed "
                "FROM email_events"
            )
        )
    ).fetchone()
    return EmailEventSummary(
        queued=int(row[0]), sent=int(row[1]), failed=int(row[2])
    )
