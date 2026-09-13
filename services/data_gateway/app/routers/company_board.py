"""The company super admin's hiring board — Group E, E3. READ-ONLY.

    GET /admin/hiring-board                          every open opening, with health
    GET /admin/requisitions/{id}/dashboard           one opening's dashboard, read-only

A company super admin manages the company's HR managers; until now they could
not see any hiring at all, because every requisition endpoint requires the HR
manager role. These two routes give them the view without the controls: there
is no write here, and nothing a super admin opens from the board can move a
candidate, change an opening or publish a workflow — those stay HR's.

Company-scoped through ``CompanyAdminCtxDep``: the company comes from the
caller's account, and another company's opening is a 404.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter

from app.company_board import hiring_board
from app.database import DbSessionDep
from app.routers.admin_hr import CompanyAdminCtxDep
from app.routers.hr_requisitions import build_requisition_dashboard

router = APIRouter(prefix="/admin", tags=["company-board"])


@router.get("/hiring-board", summary="Every open opening in the company, with its hiring health")
async def get_hiring_board(ctx: CompanyAdminCtxDep, db: DbSessionDep) -> dict[str, Any]:
    _admin_uid, company_id = ctx
    return await hiring_board(db, company_id=company_id)


@router.get(
    "/requisitions/{requisition_id}/dashboard",
    summary="One opening's dashboard, for the company super admin (read-only)",
)
async def get_requisition_dashboard_read_only(
    requisition_id: uuid.UUID, ctx: CompanyAdminCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """The same dashboard HR sees, built by the same function, without its controls."""
    _admin_uid, company_id = ctx
    return await build_requisition_dashboard(
        db, company_id=company_id, requisition_id=requisition_id
    )
