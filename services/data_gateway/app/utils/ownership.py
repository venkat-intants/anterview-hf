"""The multi-tenant isolation boundary, in one place (DG-5).

Seven ``_get_owned_*`` helpers across five HR routers used to implement this
independently — applicant, exam, question, coding question, invite, round,
section. Each one had to remember the same three things, and any one of them
forgetting is a silent cross-tenant read:

1. **``company_id`` predicate.** The caller's company comes from the
   authenticated session (``get_hr_company``), never from the request body. This
   is the predicate that makes the endpoint multi-tenant safe.
2. **``deleted_at IS NULL``.** Everything here is soft-deleted. Without this a
   deleted row is still readable and still writable.
3. **404, not 403.** A 403 on another tenant's id confirms the id EXISTS. Over
   many probes that enumerates a competitor's applicant, exam and invite ids.
   404 makes "not yours" and "not there" indistinguishable.

The seven named helpers survive as thin wrappers so no call site changed and
each router keeps its own error noun ("Exam not found.", "Round not found.").
What changed is that there is now one query to review instead of seven.
"""

from __future__ import annotations

import uuid
from typing import Any, TypeVar

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Bound to Any rather than to the declarative Base: the models module builds its
# Base dynamically, and importing it here would put app.models in the import
# graph of every module that only wants the query shape.
ModelT = TypeVar("ModelT")


async def get_owned(
    db: AsyncSession,
    model: type[ModelT],
    company_id: uuid.UUID,
    obj_id: uuid.UUID,
    *,
    noun: str,
    **extra: Any,
) -> ModelT:
    """Fetch *obj_id* scoped to *company_id*, or raise 404.

    Args:
        db: The request-scoped session.
        model: The declarative model to select.
        company_id: The caller's company, from the authenticated session.
        obj_id: The ``id`` being fetched.
        noun: Capitalised subject of the 404 detail, e.g. ``"Exam"`` gives
            ``"Exam not found."``. Kept per-call so the messages the console
            already shows do not change.
        **extra: Additional equality predicates on *model*, given as
            ``column_name=value`` — e.g. ``exam_id=exam_id`` for rows that must
            also belong to a specific parent. These NARROW the query; they can
            never widen it past the ``company_id`` filter.

    Raises:
        HTTPException: 404 when no live row in this company matches.
    """
    stmt = select(model).where(
        model.id == obj_id,  # type: ignore[attr-defined]
        model.company_id == company_id,  # type: ignore[attr-defined]
        model.deleted_at.is_(None),  # type: ignore[attr-defined]
    )
    for column, value in extra.items():
        stmt = stmt.where(getattr(model, column) == value)

    row: ModelT | None = await db.scalar(stmt)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{noun} not found."
        )
    return row
