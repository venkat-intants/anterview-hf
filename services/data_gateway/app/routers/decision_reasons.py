"""Decision reason categories — PH4-O4.

HR reads the active categories to record a decision; the company's super admin
configures them. Both are company-scoped from the session.

Configuring a category never touches a recorded decision: each decision holds
its own copy of the label it was recorded with.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import DbSessionDep
from app.decision_reasons import ReasonError, create_reason, list_reasons, update_reason
from app.dependencies import HrCtxDep, SuperAdminCtxDep

hr_router = APIRouter(prefix="/hr", tags=["decision-reasons"])
admin_router = APIRouter(prefix="/admin", tags=["decision-reasons"])


class ReasonCreateIn(BaseModel):
    label: str = Field(min_length=2, max_length=120)
    applies_to: str = Field(pattern="^(hired|rejected|both)$")
    requires_explanation: bool = False


class ReasonUpdateIn(BaseModel):
    active: bool | None = None
    label: str | None = Field(default=None, min_length=2, max_length=120)


@hr_router.get("/decision-reasons")
async def hr_decision_reasons(ctx: HrCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    """Active categories, in display order. Seeds the defaults on first use."""
    _uid, company_id = ctx
    out = await list_reasons(db, company_id=company_id)
    await db.commit()  # persists a first-use seed
    return out


@admin_router.get("/decision-reasons")
async def admin_decision_reasons(ctx: SuperAdminCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    """Every category, retired ones included."""
    _uid, company_id = ctx
    out = await list_reasons(db, company_id=company_id, include_retired=True)
    await db.commit()
    return out


@admin_router.post("/decision-reasons", status_code=201)
async def admin_create_reason(
    body: ReasonCreateIn, ctx: SuperAdminCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        out = await create_reason(
            db, company_id=company_id, actor=uid, label=body.label,
            applies_to=body.applies_to, requires_explanation=body.requires_explanation,
        )
    except ReasonError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await db.commit()
    return out


@admin_router.patch("/decision-reasons/{code}")
async def admin_update_reason(
    code: str, body: ReasonUpdateIn, ctx: SuperAdminCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        out = await update_reason(
            db, company_id=company_id, actor=uid, code=code,
            active=body.active, label=body.label,
        )
    except ReasonError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await db.commit()
    return out
