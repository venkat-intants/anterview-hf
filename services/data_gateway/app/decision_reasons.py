"""Structured decision reason codes — PH4-O4.

Every final decision carries a free-text reason, recorded against the person who
made it. This adds a structured category beside it, so "why do we reject?" can be
answered across a thousand decisions without reading a thousand sentences.

WHAT THIS CANNOT DO
Choose, suggest or infer a decision. A reason is only ever attached by the human
recording the decision, through the same guarded writers as before
(``record_final_decision`` and the pipeline board). Nothing here changes who may
decide, or triggers anything when a reason is chosen.

THE TAXONOMY
Per company. Each company starts with the defaults below — seeded idempotently
on first read, so a company created tomorrow gets them without a migration —
and can add its own or retire any. Nothing is deleted: a retired reason stays
readable on every decision that used it.

THE DECISION SNAPSHOTS THE LABEL
The ledger row stores the code AND the label as chosen. Renaming or retiring a
reason later cannot rewrite what a historical decision says.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

log = structlog.get_logger(__name__)

APPLIES_TO: frozenset[str] = frozenset({"hired", "rejected", "both"})
#: A reason flagged requires_explanation needs at least this much free text.
EXPLANATION_MIN_CHARS = 10
MAX_REASONS_PER_COMPANY = 50

#: (code, label, applies_to, requires_explanation, position)
DEFAULTS: tuple[tuple[str, str, str, bool, int], ...] = (
    ("skills_fit", "Skills / competency fit", "both", False, 10),
    ("experience", "Experience", "both", False, 20),
    ("role_fit", "Role fit", "both", False, 30),
    ("interview_performance", "Interview performance", "both", False, 40),
    ("compensation_availability", "Compensation / availability", "both", False, 50),
    ("position_closed", "Position closed", "rejected", False, 60),
    ("other", "Other", "both", True, 90),
)


class ReasonError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ResolvedReason:
    code: str
    label: str
    requires_explanation: bool


async def ensure_defaults(db: AsyncSession, *, company_id: uuid.UUID) -> None:
    """Give a company the default reasons it does not already have. Idempotent.

    ON CONFLICT DO NOTHING, so a default the company RETIRED is not revived —
    retiring sets ``active = false``, it never deletes, so the row still exists
    and the insert is skipped.
    """
    for code, label, applies_to, explain, position in DEFAULTS:
        await db.execute(
            text(
                "INSERT INTO decision_reasons (id, company_id, code, label, applies_to,"
                " requires_explanation, is_default, active, position, created_at, updated_at)"
                " VALUES (:id, :c, :code, :label, :applies, :explain, true, true, :pos, now(), now())"
                " ON CONFLICT (company_id, code) DO NOTHING"
            ),
            {"id": uuid.uuid4(), "c": company_id, "code": code, "label": label,
             "applies": applies_to, "explain": explain, "pos": position},
        )


def _row(r: Any, *, include_admin: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "code": r["code"],
        "label": r["label"],
        "applies_to": r["applies_to"],
        "requires_explanation": r["requires_explanation"],
    }
    if include_admin:
        out["active"] = r["active"]
        out["is_default"] = r["is_default"]
    return out


async def list_reasons(
    db: AsyncSession, *, company_id: uuid.UUID, include_retired: bool = False
) -> list[dict[str, Any]]:
    await ensure_defaults(db, company_id=company_id)
    # Two whole statements rather than one with a spliced-in clause: bandit
    # flags SQL assembled by concatenation even from constants, and a nosec is a
    # standing exception the next author copies onto a string that is not.
    sql = (
        "SELECT code, label, applies_to, requires_explanation, active, is_default"
        "  FROM decision_reasons WHERE company_id = :c ORDER BY position, label"
        if include_retired else
        "SELECT code, label, applies_to, requires_explanation, active, is_default"
        "  FROM decision_reasons WHERE company_id = :c AND active ORDER BY position, label"
    )
    rows = (await db.execute(text(sql), {"c": company_id})).mappings().all()
    return [_row(r, include_admin=include_retired) for r in rows]


async def resolve(
    db: AsyncSession, *, company_id: uuid.UUID, code: str | None, decision: str, reason: str
) -> ResolvedReason:
    """Validate a chosen reason for a decision, or refuse in words."""
    if not code:
        raise ReasonError(422, "Choose a reason category for this decision.")
    await ensure_defaults(db, company_id=company_id)
    row = (
        await db.execute(
            text(
                "SELECT code, label, applies_to, requires_explanation, active"
                "  FROM decision_reasons WHERE company_id = :c AND code = :code"
            ),
            {"c": company_id, "code": code},
        )
    ).mappings().first()
    if row is None or not row["active"]:
        raise ReasonError(422, "That reason category is not available.")
    if row["applies_to"] not in ("both", decision):
        raise ReasonError(
            422, f"'{row['label']}' is not a reason for a {decision} decision."
        )
    if row["requires_explanation"] and len((reason or "").strip()) < EXPLANATION_MIN_CHARS:
        raise ReasonError(
            422, f"'{row['label']}' needs an explanation of at least "
                 f"{EXPLANATION_MIN_CHARS} characters."
        )
    return ResolvedReason(
        code=row["code"], label=row["label"], requires_explanation=row["requires_explanation"]
    )


def _code_from_label(label: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if not base or not base[0].isalpha():
        base = f"r_{base}".strip("_")
    return base[:48] or "reason"


async def create_reason(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    actor: uuid.UUID,
    label: str,
    applies_to: str,
    requires_explanation: bool,
) -> dict[str, Any]:
    """Add a company-specific reason. Caller commits."""
    clean = " ".join(label.split())
    if not 2 <= len(clean) <= 120:
        raise ReasonError(422, "A reason label is 2 to 120 characters.")
    if applies_to not in APPLIES_TO:
        raise ReasonError(422, "applies_to must be hired, rejected or both.")
    await ensure_defaults(db, company_id=company_id)
    count = await db.scalar(
        text("SELECT count(*) FROM decision_reasons WHERE company_id = :c"), {"c": company_id}
    )
    if int(count or 0) >= MAX_REASONS_PER_COMPANY:
        raise ReasonError(
            409, f"A company can have at most {MAX_REASONS_PER_COMPANY} reasons; retire one first."
        )
    duplicate = await db.scalar(
        text(
            "SELECT 1 FROM decision_reasons WHERE company_id = :c"
            " AND lower(btrim(label)) = lower(:l)"
        ),
        {"c": company_id, "l": clean},
    )
    if duplicate:
        raise ReasonError(409, f"A reason called '{clean}' already exists.")

    base = _code_from_label(clean)
    code = base
    suffix = 2
    while await db.scalar(
        text("SELECT 1 FROM decision_reasons WHERE company_id = :c AND code = :code"),
        {"c": company_id, "code": code},
    ):
        code = f"{base[:44]}_{suffix}"
        suffix += 1

    await db.execute(
        text(
            "INSERT INTO decision_reasons (id, company_id, code, label, applies_to,"
            " requires_explanation, is_default, active, position, created_at, updated_at)"
            " VALUES (:id, :c, :code, :label, :applies, :explain, false, true, 80, now(), now())"
        ),
        {"id": uuid.uuid4(), "c": company_id, "code": code, "label": clean,
         "applies": applies_to, "explain": requires_explanation},
    )
    _audit(db, actor=actor, company_id=company_id, action="decision_reason.created",
           details={"code": code, "label": clean, "applies_to": applies_to,
                    "requires_explanation": requires_explanation})
    return {"code": code, "label": clean, "applies_to": applies_to,
            "requires_explanation": requires_explanation, "active": True, "is_default": False}


async def update_reason(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    actor: uuid.UUID,
    code: str,
    active: bool | None,
    label: str | None,
) -> dict[str, Any]:
    """Rename or retire/restore a reason. Caller commits.

    Safe for history by construction: decisions already recorded hold their own
    copy of the label, so neither a rename nor a retirement reaches them.
    """
    await ensure_defaults(db, company_id=company_id)
    row = (
        await db.execute(
            text(
                "SELECT code, label, applies_to, requires_explanation, active, is_default"
                "  FROM decision_reasons WHERE company_id = :c AND code = :code FOR UPDATE"
            ),
            {"c": company_id, "code": code},
        )
    ).mappings().first()
    if row is None:
        raise ReasonError(404, "Reason not found.")

    new_label = row["label"]
    if label is not None:
        new_label = " ".join(label.split())
        if not 2 <= len(new_label) <= 120:
            raise ReasonError(422, "A reason label is 2 to 120 characters.")
    new_active = row["active"] if active is None else active
    if new_active is False and row["code"] == "other":
        # Without a catch-all, a decision that fits no category has nowhere to go
        # and HR would pick the nearest wrong one — which corrupts the analytics
        # this exists to make possible.
        raise ReasonError(409, "'Other' cannot be retired; it is the catch-all.")

    await db.execute(
        text(
            "UPDATE decision_reasons SET label = :l, active = :a, updated_at = now()"
            " WHERE company_id = :c AND code = :code"
        ),
        {"l": new_label, "a": new_active, "c": company_id, "code": code},
    )
    _audit(db, actor=actor, company_id=company_id, action="decision_reason.updated",
           details={"code": code, "label_before": row["label"], "label_after": new_label,
                    "active_before": row["active"], "active_after": new_active})
    return {"code": code, "label": new_label, "applies_to": row["applies_to"],
            "requires_explanation": row["requires_explanation"], "active": new_active,
            "is_default": row["is_default"]}


def _audit(
    db: AsyncSession, *, actor: uuid.UUID, company_id: uuid.UUID, action: str,
    details: dict[str, Any],
) -> None:
    db.add(
        AuditLog(
            actor_id=actor,
            actor_type="user",
            action=action,
            resource_type="decision_reason",
            resource_id=company_id,
            details={"company_id": str(company_id), **details},
            event_ts=datetime.now(tz=UTC),
        )
    )
