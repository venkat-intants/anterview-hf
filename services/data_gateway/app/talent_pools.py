"""Talent pools — PH5-E3.

A company's own named lists of candidates it already holds (manually curated,
or added from a rediscovery search) that it may want to contact about a FUTURE
opening. Raw parameterised SQL, no ORM models — the ``app/corpus.py`` /
``app/rediscovery.py`` choice, restated in the migration
(``e4a6c8b0d2f7``): nothing here is mapped in ``app/models.py``.

``hr_manager`` ONLY, never ``super_admin``
-------------------------------------------
A pool names candidates, so this is ``candidate_pii`` and
``shared/agents/schema.py::DATA_CLASS_ROLES`` gives that class to
``{hr_manager}`` alone. ``app/routers/hr_pools.py`` gates on ``HrCtxDep``,
which resolves ``hr_manager`` and nothing else — a company ``super_admin`` is
deliberately NOT a superset of HR (``CLAUDE.md``) and gets 403 at the router,
before this module is ever called. This module does not re-check the role for
that reason: there is exactly one caller and it is already narrowed.

ADDING A NAME DOES NOT NEED REDISCOVERY CONSENT; CONTACTING ONE DOES
---------------------------------------------------------------------
The purpose limitation design §4.3 states plainly: rediscovery consent governs
whether a candidate can be FOUND by a search and CONTACTED about a new
opening. It does not govern whether HR can see a name it already holds under
the application consent. So ``add_members`` never checks eligibility for a
``source="manual"`` add (HR is allowed to put any of its own applicants on its
own list), and DOES check it for ``source="rediscovery"`` (a result that was
never eligible should not quietly become a permanent pool row). ``invite_member``
— the one action that is genuinely "contact about a new opening" — checks
eligibility for EVERY member regardless of how they got into the pool, via
``rediscovery.eligibility_for_applicants``, the single shared definition
(``app/rediscovery.py::ELIGIBLE_CTE``), never re-derived here.

THE APPEND-ONLY TRAIL (criterion 16)
-------------------------------------
Every write here also inserts a ``talent_pool_events`` row. ``details`` carries
FACTS ONLY — action, ids, counts, freshness band, ``note_length`` — the same
rule ``app/corpus.py::_audit`` already states and the migration's docstring
restates for this table: never the note, never the removal reason's text,
never a name (a candidate's OR the pool's own free-text label — the stricter
``corpus.py`` practice, since neither is what an append-only trail exists to
hold), never CV or scorecard prose. The database enforces the append-only part
independently (a trigger, not only this module's discipline).

CRITERION 14 IS A CONTROL, NOT A SENTENCE (design §6.6 point 3)
-------------------------------------------------------------------
``invite_member`` computes the member's freshness band at READ TIME, from
``rediscovery.verified_evidence_freshness_for_applicant`` — never from
``talent_pool_members.evidence_freshness``, which is the band AS AT ADD TIME
and would let a member added fresh sail through unreviewed a year later.

WHOSE EVIDENCE COUNTS FOR THE GATE (independent acceptance pass, criterion 14
hole). The gate's band is computed over VERIFIED evidence only — a human
interviewer's submitted scorecard, a round result, an exam attempt — and
NEVER over the CV (``applicants.updated_at``) or the ``ai_interview`` fact.
A CV's upload date is candidate-authored and is not qualification: a
similarity-only match (``explained: false``, nothing to review) whose CV
happens to be recently uploaded must not sail through on that upload date
alone. Before this fix the gate used
``rediscovery.evidence_freshness_for_applicant``, which folds the CV in for
DISPLAY — so a similarity-only candidate with a fresh CV and zero assessment
history banded ``fresh`` and cleared with no acknowledgement, which is exactly
the overclaim design §6.3 refuses in words ("an unexplained match cannot be
invited without the acknowledgement, because there is nothing to review").
With no verified evidence at all, the gate's band is ``"none"`` — never
``"fresh"`` — so both a similarity-only match and a manually-added candidate
with no assessment history need one recorded review or acknowledgement, not a
hard block. The DISPLAY band (the per-item chips and the row header) still
legitimately includes the CV via ``evidence_freshness_for_applicant``, so the
two can and do disagree: a candidate with fresh verified evidence and a
long-stale CV shows a stale chip but clears this gate unacknowledged.

Anything other than ``fresh`` with nobody having CURRENTLY reviewed the
evidence refuses with 422 ``stale_evidence_unreviewed`` unless the caller
explicitly acknowledges — two ways through, and both leave a record: a review
stamps ``evidence_reviewed_at``/``_by``; an acknowledgement is written onto the
``member_invited`` event itself, facts only.

A REVIEW ITSELF EXPIRES (code review FIX 1). ``evidence_reviewed_at`` never
clearing was the same defect class this section already refuses two
paragraphs up, applied to the review instead of the evidence: a stored fact
about freshness that itself goes stale. ``_evidence_review_is_current`` bounds
a review's validity at ``rediscovery_review_valid_days`` (365 by default —
deliberately equal to ``rediscovery_stale_days`` today, but its own setting),
so a two-year-old "Mark evidence reviewed" click no longer clears the gate on
its own.

MATCH_REASON IS SANITISED ON THE WAY IN, NOT ONLY ON THE WAY OUT
-------------------------------------------------------------------
``rediscovery.freeze_match_reason`` already strips prose before the search
response is built, but that response left the server once and came back as
part of an HR-authored HTTP request body — a client bug or a hand-crafted call
could otherwise smuggle a CV snippet or the unexplained-match sentence into a
row this project repeatedly promises carries facts only.
``_sanitise_match_reason`` re-applies the exact same field allowlist
(``rediscovery.FREEZE_KEEP_ITEM_FIELDS``) at the boundary, rather than trusting
that whatever arrived was already frozen.

A RESULT CANNOT PRODUCE A HIRING OUTCOME
-------------------------------------------
``invite_member`` creates an ``enrolments`` row via
``app/workflow_runner.py::enrol_applicant`` — the same function every other
enrolment in this service goes through — and does nothing else: no stage past
``new``, no round result, no decision. There is no other write in this module
that touches ``enrolments``, ``round_results``, ``stage_transitions`` or
``interviewer_scorecards``.

Callers commit. Every function here does at most ``db.flush()`` (the
``app/job_tasks.py`` convention).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app import rediscovery
from app.config import settings
from app.interviewer_scorecards import RequestMeta
from app.models import AuditLog
from app.workflow_runner import enrol_applicant

log = structlog.get_logger(__name__)

MEMBER_SOURCES: tuple[str, ...] = ("manual", "rediscovery")
FRESHNESS_BANDS: tuple[str, ...] = ("fresh", "ageing", "stale", "unverifiable", "none")

#: Bounds on the frozen snapshot re-sanitised at write time — see the module
#: docstring. Generous enough for a real search result, tight enough that a
#: hand-crafted body cannot use this column as unbounded storage.
_MAX_MATCH_REASON_WHY = 20
_MAX_QUERY_TERMS = 20
_MAX_TERM_CHARS = 100


class PoolError(Exception):
    """Refused. Carries the HTTP status, a machine-readable ``code`` and a
    sentence for a person — the ``CorpusError``/``RediscoveryError`` shape.
    Rendered on the wire as ``{"detail": {"failure_code": .code, "message":
    .message}}`` by the router, matching ``app/routers/hr_corpus.py``."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _rowcount(result: Any) -> int:
    cursor: CursorResult[Any] = result
    return int(cursor.rowcount or 0)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _sanitise_match_reason(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Defence in depth on the way INTO the database — see the module
    docstring. Re-applies ``rediscovery.FREEZE_KEEP_ITEM_FIELDS`` to every
    ``why`` item regardless of what the caller actually sent, and bounds
    ``query_terms``/the number of ``why`` items so this column cannot become
    unbounded storage for a hand-crafted request."""
    if not raw or not isinstance(raw, dict):
        return None
    why: list[dict[str, Any]] = []
    for item in list(raw.get("why") or [])[:_MAX_MATCH_REASON_WHY]:
        if not isinstance(item, dict):
            continue
        frozen = {
            k: item[k] for k in rediscovery.FREEZE_KEEP_ITEM_FIELDS if item.get(k) is not None
        }
        citation = item.get("citation")
        if isinstance(citation, dict):
            frozen["citation"] = {
                "kind": citation.get("kind"), "id": citation.get("id"),
                "label": citation.get("label"),
            }
        if frozen:
            why.append(frozen)
    query_terms = raw.get("query_terms")
    breakdown = raw.get("breakdown") if isinstance(raw.get("breakdown"), dict) else None
    searched_at = raw.get("searched_at")
    return {
        "breakdown": breakdown,
        "why": why,
        "searched_at": searched_at if isinstance(searched_at, str) else None,
        "query_terms": (
            [str(t)[:_MAX_TERM_CHARS] for t in list(query_terms)[:_MAX_QUERY_TERMS]]
            if isinstance(query_terms, list) else []
        ),
    }


def _evidence_review_is_current(reviewed_at: datetime | None, *, now: datetime) -> bool:
    """Whether a past ``mark_evidence_reviewed`` action still clears the
    criterion-14 gate — code review FIX 1.

    A review is not forever: it records that a named person accepted the
    evidence AS IT STOOD on that day, and the evidence keeps ageing underneath
    it. Without an expiry, ``already_reviewed = row["evidence_reviewed_at"] is
    not None`` never becomes false again, so one review in 2026 would clear
    the gate for the rest of this member's life while the evidence it accepted
    goes on ageing — exactly the failure mode ``evidence_freshness`` (the
    frozen add-time column, §6.5) is deliberately NOT trusted for the gate,
    applied a second time to the review itself.

    ``settings.rediscovery_review_valid_days`` is the bound. ``None`` (never
    reviewed) is never current.
    """
    if reviewed_at is None:
        return False
    return (now - reviewed_at).days <= int(settings.rediscovery_review_valid_days)


def _match_reason_evidence_freshness(sanitised: dict[str, Any] | None) -> str | None:
    """The header band to freeze onto ``evidence_freshness`` at add time — the
    WORST band among the sanitised snapshot's own items, computed with
    ``rediscovery.worst_band`` (the one definition) rather than re-derived."""
    if not sanitised:
        return None
    bands = [item.get("freshness") for item in sanitised.get("why") or []]
    band = rediscovery.worst_band(bands)
    return None if band == "none" else band


async def _audit(
    db: AsyncSession, *, actor: uuid.UUID, action: str, resource_id: uuid.UUID,
    details: dict[str, Any], meta: RequestMeta,
) -> None:
    db.add(AuditLog(
        actor_id=actor, actor_type="user", action=action, resource_type="talent_pool",
        resource_id=resource_id, details=details, ip_address=meta.ip_address,
        user_agent=meta.user_agent, event_ts=datetime.now(tz=UTC),
    ))


async def _event(
    db: AsyncSession, *, company_id: uuid.UUID, pool_id: uuid.UUID,
    applicant_id: uuid.UUID | None, action: str, actor_user_id: uuid.UUID | None,
    actor_type: str = "user", details: dict[str, Any] | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO talent_pool_events"
            " (id, company_id, pool_id, applicant_id, action, actor_type, actor_user_id, details)"
            " VALUES (gen_random_uuid(), :c, :p, :a, :act, :at, :u, CAST(:j AS jsonb))"
        ),
        {"c": company_id, "p": pool_id, "a": applicant_id, "act": action, "at": actor_type,
         "u": actor_user_id, "j": json.dumps(details or {}, default=str)},
    )


# ---------------------------------------------------------------------------
# Pool rows
# ---------------------------------------------------------------------------
async def _pool_row(db: AsyncSession, *, company_id: uuid.UUID, pool_id: uuid.UUID) -> Any:
    return (
        await db.execute(
            text(
                "SELECT p.*, COALESCE(u.full_name, u.email) AS created_by_name,"
                " (SELECT count(*) FROM talent_pool_members m"
                "   WHERE m.pool_id = p.id AND m.removed_at IS NULL) AS member_count"
                "  FROM talent_pools p"
                "  LEFT JOIN users u ON u.id = p.created_by_user_id"
                " WHERE p.id = :i AND p.company_id = :c AND p.deleted_at IS NULL"
            ),
            {"i": pool_id, "c": company_id},
        )
    ).mappings().first()


def _pool_out(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "name": row["name"], "description": row["description"],
        "member_count": int(row["member_count"]), "created_by_name": row["created_by_name"],
        "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
        "archived_at": _iso(row["archived_at"]),
    }


async def list_pools(
    db: AsyncSession, *, company_id: uuid.UUID, include_archived: bool = False,
) -> dict[str, Any]:
    where = "" if include_archived else " AND p.archived_at IS NULL"
    rows = (
        await db.execute(
            text(
                "SELECT p.*, COALESCE(u.full_name, u.email) AS created_by_name,"
                " (SELECT count(*) FROM talent_pool_members m"
                "   WHERE m.pool_id = p.id AND m.removed_at IS NULL) AS member_count"
                "  FROM talent_pools p"
                "  LEFT JOIN users u ON u.id = p.created_by_user_id"
                " WHERE p.company_id = :c AND p.deleted_at IS NULL"
                + where  # nosec B608 -- one of two fixed literals chosen by an if, never input
                + " ORDER BY p.updated_at DESC"
            ),
            {"c": company_id},
        )
    ).mappings().all()
    return {
        "pools": [_pool_out(r) for r in rows],
        "limits": {
            "max_per_company": int(settings.talent_pool_max_per_company),
            "max_members": int(settings.talent_pool_max_members),
        },
    }


async def create_pool(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, name: str,
    description: str | None, meta: RequestMeta,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not 1 <= len(name) <= 120:
        raise PoolError(422, "invalid_name", "Give the pool a name of up to 120 characters.")
    description = (description or "").strip() or None
    if description and len(description) > 1000:
        raise PoolError(422, "invalid_name", "The description is too long (1000 characters).")

    count = await db.scalar(
        text("SELECT count(*) FROM talent_pools WHERE company_id = :c AND deleted_at IS NULL"),
        {"c": company_id},
    )
    if int(count or 0) >= settings.talent_pool_max_per_company:
        raise PoolError(
            409, "pool_limit_reached",
            f"Your company already has {settings.talent_pool_max_per_company} pools.",
        )
    taken = await db.scalar(
        text(
            "SELECT 1 FROM talent_pools"
            " WHERE company_id = :c AND lower(name) = lower(:n) AND deleted_at IS NULL"
        ),
        {"c": company_id, "n": name},
    )
    if taken:
        raise PoolError(409, "pool_name_taken", "A pool with this name already exists.")

    pool_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "INSERT INTO talent_pools (id, company_id, name, description, created_by_user_id,"
            " created_at, updated_at)"
            " VALUES (:i, :c, :n, :d, :u, :now, :now)"
        ),
        {"i": pool_id, "c": company_id, "n": name, "d": description, "u": actor, "now": now},
    )
    await _event(db, company_id=company_id, pool_id=pool_id, applicant_id=None,
                action="created", actor_user_id=actor,
                details={"name_length": len(name), "has_description": bool(description)})
    await _audit(db, actor=actor, action="pools.pool.created", resource_id=pool_id,
                details={"company_id": str(company_id)}, meta=meta)
    row = await _pool_row(db, company_id=company_id, pool_id=pool_id)
    return _pool_out(row)


async def get_pool_with_members(
    db: AsyncSession, *, company_id: uuid.UUID, pool_id: uuid.UUID,
) -> dict[str, Any] | None:
    row = await _pool_row(db, company_id=company_id, pool_id=pool_id)
    if row is None:
        return None
    members = await _list_members(db, company_id=company_id, pool_id=pool_id)
    return {"pool": _pool_out(row), "members": members}


async def update_pool(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, pool_id: uuid.UUID,
    name: str | None, description: str | None, archived: bool | None,
    name_given: bool, description_given: bool, meta: RequestMeta,
) -> dict[str, Any]:
    """Rename, re-describe, archive or restore — any combination in one call.

    ``name_given``/``description_given`` distinguish "not provided" from
    "provided as empty/None": the router passes these from
    ``model_fields_set`` so a PATCH that omits ``description`` never clears
    it, and one that explicitly sends ``description: null`` does.
    """
    row = await _pool_row(db, company_id=company_id, pool_id=pool_id)
    if row is None:
        raise PoolError(404, "pool_not_found", "Pool not found.")

    sets: list[str] = []
    params: dict[str, Any] = {"i": pool_id, "now": datetime.now(tz=UTC)}
    events: list[tuple[str, dict[str, Any]]] = []

    if name_given:
        new_name = (name or "").strip()
        if not 1 <= len(new_name) <= 120:
            raise PoolError(422, "invalid_name", "Give the pool a name of up to 120 characters.")
        if new_name.lower() != str(row["name"]).lower():
            taken = await db.scalar(
                text(
                    "SELECT 1 FROM talent_pools"
                    " WHERE company_id = :c AND lower(name) = lower(:n)"
                    "   AND deleted_at IS NULL AND id <> :i"
                ),
                {"c": company_id, "n": new_name, "i": pool_id},
            )
            if taken:
                raise PoolError(409, "pool_name_taken", "A pool with this name already exists.")
        if new_name != row["name"]:
            sets.append("name = :name")
            params["name"] = new_name
            events.append(("renamed", {"name_length": len(new_name)}))

    if description_given:
        new_description = (description or "").strip() or None
        if new_description and len(new_description) > 1000:
            raise PoolError(422, "invalid_name", "The description is too long (1000 characters).")
        if new_description != row["description"]:
            sets.append("description = :description")
            params["description"] = new_description
            events.append((
                "described",
                {"description_length": len(new_description) if new_description else 0},
            ))

    if archived is not None:
        currently_archived = row["archived_at"] is not None
        if archived and not currently_archived:
            sets.append("archived_at = :now")
            events.append(("archived", {}))
        elif not archived and currently_archived:
            sets.append("archived_at = NULL")
            events.append(("restored", {}))

    if sets:
        sets.append("updated_at = :now")
        await db.execute(
            text(f"UPDATE talent_pools SET {', '.join(sets)} WHERE id = :i"),  # nosec B608
            params,
        )
        for action, details in events:
            await _event(db, company_id=company_id, pool_id=pool_id, applicant_id=None,
                        action=action, actor_user_id=actor, details=details)
        await _audit(
            db, actor=actor, action="pools.pool.updated", resource_id=pool_id,
            details={"company_id": str(company_id), "changed": [a for a, _ in events]}, meta=meta,
        )

    updated = await _pool_row(db, company_id=company_id, pool_id=pool_id)
    return _pool_out(updated)


async def delete_pool(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, pool_id: uuid.UUID,
    meta: RequestMeta,
) -> dict[str, Any]:
    """Delete a pool and remove its members WITH it (soft on both): the
    contract's confirm dialog names how many members go, so the count this
    returns is what that dialog already showed, re-asserted server-side."""
    row = await _pool_row(db, company_id=company_id, pool_id=pool_id)
    if row is None:
        raise PoolError(404, "pool_not_found", "Pool not found.")
    now = datetime.now(tz=UTC)
    removed = await db.execute(
        text(
            "UPDATE talent_pool_members SET removed_at = :now, removed_by_user_id = :u"
            " WHERE pool_id = :p AND removed_at IS NULL"
        ),
        {"now": now, "u": actor, "p": pool_id},
    )
    members_removed = _rowcount(removed)
    await db.execute(
        text("UPDATE talent_pools SET deleted_at = :now, updated_at = :now WHERE id = :i"),
        {"now": now, "i": pool_id},
    )
    await _event(db, company_id=company_id, pool_id=pool_id, applicant_id=None,
                action="deleted", actor_user_id=actor,
                details={"members_removed": members_removed})
    await _audit(
        db, actor=actor, action="pools.pool.deleted", resource_id=pool_id,
        details={"company_id": str(company_id), "members_removed": members_removed}, meta=meta,
    )
    return {"deleted": True, "members_removed": members_removed}


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------
#: The base SELECT, with no ORDER BY and no member-id filter — both are
#: appended by the two callers below, and appending anything AFTER an
#: ORDER BY would be invalid SQL, which is why this is split rather than one
#: constant with a filter spliced onto its end.
_MEMBERS_SQL_BASE = (
    "SELECT m.id AS member_id, m.applicant_id, a.full_name, a.current_title,"
    " a.current_company, m.source, m.added_at,"
    " COALESCE(ub.full_name, ub.email) AS added_by_name, m.note, m.match_reason,"
    " m.evidence_freshness, m.evidence_reviewed_at,"
    " COALESCE(ur.full_name, ur.email) AS evidence_reviewed_by_name,"
    " (SELECT e.id FROM enrolments e"
    "   WHERE e.applicant_id = m.applicant_id AND e.company_id = m.company_id"
    "     AND e.deleted_at IS NULL"
    "  ORDER BY e.created_at DESC LIMIT 1) AS enrolment_id"
    "  FROM talent_pool_members m"
    "  JOIN applicants a ON a.id = m.applicant_id AND a.company_id = m.company_id"
    "  LEFT JOIN users ub ON ub.id = m.added_by_user_id"
    "  LEFT JOIN users ur ON ur.id = m.evidence_reviewed_by_user_id"
    " WHERE m.pool_id = :pool_id AND m.company_id = :company_id AND m.removed_at IS NULL"
)
_MEMBERS_SQL = _MEMBERS_SQL_BASE + " ORDER BY m.added_at DESC"


async def _list_members(
    db: AsyncSession, *, company_id: uuid.UUID, pool_id: uuid.UUID,
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(text(_MEMBERS_SQL), {"pool_id": pool_id, "company_id": company_id})
    ).mappings().all()
    elig = await rediscovery.eligibility_for_applicants(
        db, company_id=company_id, applicant_ids=[r["applicant_id"] for r in rows],
    )
    return [_member_out(r, elig.get(r["applicant_id"], _NOT_ELIGIBLE)) for r in rows]


#: The fallback for an applicant `eligibility_for_applicants` did not return at
#: all (belt-and-braces only; every live member's applicant_id is this
#: company's own, which the function's own WHERE already requires).
_NOT_ELIGIBLE: dict[str, Any] = {
    "eligible": False, "ineligible_reason": None, "opted_in_at": None, "expires_at": None,
}


def _member_out(row: Any, elig: dict[str, Any]) -> dict[str, Any]:
    return {
        "member_id": str(row["member_id"]), "applicant_id": str(row["applicant_id"]),
        "full_name": row["full_name"], "current_title": row["current_title"],
        "current_company": row["current_company"], "source": row["source"],
        "added_at": _iso(row["added_at"]), "added_by_name": row["added_by_name"],
        "note": row["note"], "match_reason": row["match_reason"],
        "evidence_freshness": row["evidence_freshness"],
        "evidence_reviewed_at": _iso(row["evidence_reviewed_at"]),
        "evidence_reviewed_by_name": row["evidence_reviewed_by_name"],
        "eligible": bool(elig["eligible"]),
        "ineligible_reason": elig["ineligible_reason"],
        "opted_in_at": _iso(elig["opted_in_at"]),
        "expires_at": _iso(elig["expires_at"]),
        "enrolment_id": str(row["enrolment_id"]) if row["enrolment_id"] else None,
    }


async def _member_row(
    db: AsyncSession, *, company_id: uuid.UUID, pool_id: uuid.UUID, member_id: uuid.UUID,
) -> Any:
    return (
        await db.execute(
            text(
                "SELECT m.* FROM talent_pool_members m"
                " WHERE m.id = :m AND m.pool_id = :p AND m.company_id = :c"
                "   AND m.removed_at IS NULL"
            ),
            {"m": member_id, "p": pool_id, "c": company_id},
        )
    ).mappings().first()


async def _one_member_out(
    db: AsyncSession, *, company_id: uuid.UUID, pool_id: uuid.UUID, member_id: uuid.UUID,
) -> dict[str, Any]:
    row = (
        await db.execute(
            # nosec B608 -- fixed appendage of a fixed base; :member_id is bound.
            text(_MEMBERS_SQL_BASE + " AND m.id = :member_id"),
            {"pool_id": pool_id, "company_id": company_id, "member_id": member_id},
        )
    ).mappings().first()
    if row is None:
        raise PoolError(404, "member_not_found", "Pool member not found.")
    elig = await rediscovery.eligibility_for_applicants(
        db, company_id=company_id, applicant_ids=[row["applicant_id"]],
    )
    return _member_out(row, elig.get(row["applicant_id"], _NOT_ELIGIBLE))


async def add_members(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, pool_id: uuid.UUID,
    applicant_ids: list[uuid.UUID], source: str, note: str | None,
    match_reason: dict[str, Any] | None, meta: RequestMeta,
) -> dict[str, Any]:
    pool = await _pool_row(db, company_id=company_id, pool_id=pool_id)
    if pool is None:
        raise PoolError(404, "pool_not_found", "Pool not found.")
    if source not in MEMBER_SOURCES:
        raise PoolError(422, "invalid_name", "Unknown member source.")
    note = (note or "").strip() or None
    if note and len(note) > 500:
        raise PoolError(422, "invalid_name", "The note is too long (500 characters).")
    sanitised = _sanitise_match_reason(match_reason)
    frozen_freshness = _match_reason_evidence_freshness(sanitised)

    ids = list(dict.fromkeys(applicant_ids))  # de-duplicated, order preserved

    company_rows = (
        await db.execute(
            text(
                "SELECT id FROM applicants"
                " WHERE id = ANY(CAST(:ids AS uuid[])) AND company_id = :c AND deleted_at IS NULL"
            ),
            {"ids": [str(i) for i in ids], "c": company_id},
        )
    ).scalars().all()
    this_company = set(company_rows)

    existing_rows = (
        await db.execute(
            text(
                "SELECT applicant_id FROM talent_pool_members"
                " WHERE pool_id = :p AND applicant_id = ANY(CAST(:ids AS uuid[]))"
                "   AND removed_at IS NULL"
            ),
            {"p": pool_id, "ids": [str(i) for i in ids]},
        )
    ).scalars().all()
    already_members = set(existing_rows)

    eligible_map: dict[uuid.UUID, dict[str, Any]] = {}
    if source == "rediscovery":
        candidates = [i for i in ids if i in this_company and i not in already_members]
        eligible_map = await rediscovery.eligibility_for_applicants(
            db, company_id=company_id, applicant_ids=candidates,
        )

    current_count = int(pool["member_count"])
    max_members = int(settings.talent_pool_max_members)

    added: list[str] = []
    skipped: list[dict[str, str]] = []
    now = datetime.now(tz=UTC)
    for applicant_id in ids:
        if applicant_id not in this_company:
            skipped.append({"applicant_id": str(applicant_id), "reason": "not_this_company"})
            continue
        if applicant_id in already_members:
            skipped.append({"applicant_id": str(applicant_id), "reason": "already_a_member"})
            continue
        if source == "rediscovery" and not eligible_map.get(applicant_id, {}).get("eligible"):
            skipped.append({"applicant_id": str(applicant_id), "reason": "not_eligible"})
            continue
        if current_count >= max_members:
            skipped.append({"applicant_id": str(applicant_id), "reason": "pool_full"})
            continue

        member_id = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO talent_pool_members"
                " (id, company_id, pool_id, applicant_id, source, added_by_user_id, added_at,"
                "  note, match_reason, evidence_freshness)"
                " VALUES (:i, :c, :p, :a, :src, :u, :now, :note, CAST(:mr AS jsonb), :fr)"
            ),
            {"i": member_id, "c": company_id, "p": pool_id, "a": applicant_id, "src": source,
             "u": actor, "now": now, "note": note,
             "mr": json.dumps(sanitised) if sanitised is not None else None,
             "fr": frozen_freshness},
        )
        await _event(
            db, company_id=company_id, pool_id=pool_id, applicant_id=applicant_id,
            action="member_added", actor_user_id=actor,
            details={
                "source": source, "note_length": len(note) if note else 0,
                "match_reason_present": sanitised is not None,
                "evidence_freshness": frozen_freshness,
            },
        )
        added.append(str(member_id))
        current_count += 1

    if added:
        await _audit(
            db, actor=actor, action="pools.member.added", resource_id=pool_id,
            details={
                "company_id": str(company_id), "source": source,
                "added": len(added), "skipped": len(skipped),
            },
            meta=meta,
        )
    return {"added": added, "skipped": skipped}


async def remove_member(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, pool_id: uuid.UUID,
    member_id: uuid.UUID, reason: str | None, meta: RequestMeta,
) -> dict[str, Any]:
    """Soft removal: ``removed_at`` is set, the row stays, and re-adding the
    same applicant is a NEW row — so the trail (who added them, why, when
    they were removed) survives across a remove/re-add cycle."""
    row = await _member_row(db, company_id=company_id, pool_id=pool_id, member_id=member_id)
    if row is None:
        raise PoolError(404, "member_not_found", "Pool member not found.")
    reason = (reason or "").strip() or None
    if reason and len(reason) > 200:
        raise PoolError(422, "invalid_name", "The reason is too long (200 characters).")
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE talent_pool_members SET removed_at = :now, removed_by_user_id = :u,"
            " removed_reason = :r WHERE id = :i"
        ),
        {"now": now, "u": actor, "r": reason, "i": member_id},
    )
    await _event(
        db, company_id=company_id, pool_id=pool_id, applicant_id=row["applicant_id"],
        action="member_removed", actor_user_id=actor,
        details={"reason_length": len(reason) if reason else 0},
    )
    await _audit(
        db, actor=actor, action="pools.member.removed", resource_id=pool_id,
        details={"company_id": str(company_id), "member_id": str(member_id)}, meta=meta,
    )
    return {"removed": True}


async def mark_evidence_reviewed(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, pool_id: uuid.UUID,
    member_id: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    """Records that a NAMED person accepted this member's evidence as it
    stands. Never changes ``evidence_freshness`` — the evidence is still as
    old as it was a second ago; this records that somebody looked, which is
    the criterion-14 control, not a claim that the evidence got fresher."""
    row = await _member_row(db, company_id=company_id, pool_id=pool_id, member_id=member_id)
    if row is None:
        raise PoolError(404, "member_not_found", "Pool member not found.")
    elig = await rediscovery.eligibility_for_applicants(
        db, company_id=company_id, applicant_ids=[row["applicant_id"]],
    )
    if not elig.get(row["applicant_id"], _NOT_ELIGIBLE)["eligible"]:
        # design §7 row 4 / §4.3: an ineligible member offers Remove only — no
        # evidence, no review, no invite.
        raise PoolError(409, "not_eligible", "This person is not currently eligible for contact.")
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE talent_pool_members SET evidence_reviewed_at = :now,"
            " evidence_reviewed_by_user_id = :u WHERE id = :i"
        ),
        {"now": now, "u": actor, "i": member_id},
    )
    await _event(
        db, company_id=company_id, pool_id=pool_id, applicant_id=row["applicant_id"],
        action="member_evidence_reviewed", actor_user_id=actor,
        details={"evidence_freshness": row["evidence_freshness"]},
    )
    await _audit(
        db, actor=actor, action="pools.member.evidence_reviewed", resource_id=pool_id,
        details={"company_id": str(company_id), "member_id": str(member_id)}, meta=meta,
    )
    return await _one_member_out(db, company_id=company_id, pool_id=pool_id, member_id=member_id)


async def invite_member(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, pool_id: uuid.UUID,
    member_id: uuid.UUID, requisition_id: uuid.UUID, acknowledged_stale: bool = False,
    meta: RequestMeta,
) -> dict[str, Any]:
    """Attach this pool member to an opening through ``enrolments`` — and
    nothing else. Idempotent per (applicant, requisition): a second call
    returns the existing enrolment rather than a duplicate, exactly the
    invariant ``enrol_applicant`` already gives every other caller.

    Re-checks eligibility HERE rather than trusting a list rendered a moment
    ago (design §4.3 point 3): contact is refused at the endpoint even if the
    screen has not refreshed.

    CRITERION 14 — A CONTROL, NOT A SENTENCE (design §6.6 point 3). Converting
    a pool member into an application on the strength of evidence nobody has
    looked at recently is exactly the overclaim this criterion exists to
    prevent. So: the freshness band is computed HERE, at read time, from the
    member's own VERIFIED evidence rows —
    ``rediscovery.verified_evidence_freshness_for_applicant`` — never the
    frozen ``evidence_freshness`` column, which is the band AS AT ADD TIME and
    would let a member added fresh sail through unreviewed a year later.

    "VERIFIED" excludes the CV and the AI-interview fact on purpose
    (independent acceptance pass, criterion 14 hole): a CV's upload date is
    candidate-authored and is not qualification, so a similarity-only match
    with a freshly-uploaded CV and no scorecard, round result or exam attempt
    must not clear this gate on the CV's date alone — its band is ``"none"``,
    which is not ``"fresh"``, so it still needs a review or an
    acknowledgement. See ``rediscovery.verified_evidence_freshness_for_applicant``
    for the full rationale and ``_VERIFIED_EVIDENCE_SIGNALS`` for exactly what
    counts. The DISPLAY band (the row's chips, which legitimately include the
    CV) is a different function, ``evidence_freshness_for_applicant``, and the
    two are expected to disagree.

    When that band is anything other than ``fresh`` AND nobody has CURRENTLY
    marked the evidence reviewed — a review itself expires after
    ``rediscovery_review_valid_days`` (FIX 1, ``_evidence_review_is_current``),
    for the same reason the frozen column above is not trusted — the invite
    refuses with 422 ``stale_evidence_unreviewed`` unless the caller explicitly
    acknowledges — two legitimate ways through (mark it reviewed, or
    acknowledge), and both leave a record: a review stamps
    ``evidence_reviewed_at``/``_by``: an acknowledgement is written onto the
    ``member_invited`` event itself.
    """
    row = await _member_row(db, company_id=company_id, pool_id=pool_id, member_id=member_id)
    if row is None:
        raise PoolError(404, "member_not_found", "Pool member not found.")
    applicant_id = row["applicant_id"]

    elig = await rediscovery.eligibility_for_applicants(
        db, company_id=company_id, applicant_ids=[applicant_id],
    )
    if not elig.get(applicant_id, _NOT_ELIGIBLE)["eligible"]:
        raise PoolError(409, "not_eligible", "This person is not currently eligible for contact.")

    band = await rediscovery.verified_evidence_freshness_for_applicant(
        db, company_id=company_id, applicant_id=applicant_id, viewer_user_id=actor,
    )
    # FIX 1 (code review): a review EXPIRES — see _evidence_review_is_current.
    # A review older than rediscovery_review_valid_days no longer clears the
    # gate, so a member reviewed once years ago is treated exactly like one
    # never reviewed at all: refused unless acknowledged.
    review_is_current = _evidence_review_is_current(
        row["evidence_reviewed_at"], now=datetime.now(tz=UTC),
    )
    if band != "fresh" and not review_is_current and not acknowledged_stale:
        raise PoolError(
            422, "stale_evidence_unreviewed",
            "This candidate's evidence has not been reviewed recently. Mark it "
            "reviewed, or confirm you want to invite them anyway.",
        )

    req = (
        await db.execute(
            text(
                "SELECT id, title, level, jd_text FROM job_requisitions"
                " WHERE id = :r AND company_id = :c AND status = 'open' AND deleted_at IS NULL"
            ),
            {"r": requisition_id, "c": company_id},
        )
    ).mappings().first()
    if req is None:
        raise PoolError(409, "requisition_not_open", "That opening is not open for applications.")

    applicant = (
        await db.execute(
            text("SELECT resume_s3_key FROM applicants WHERE id = :a AND company_id = :c"),
            {"a": applicant_id, "c": company_id},
        )
    ).mappings().first()

    outcome = await enrol_applicant(
        db, company_id=company_id, applicant_id=applicant_id, requisition_id=requisition_id,
        target_job_title=req["title"], target_level=req["level"], target_jd_text=req["jd_text"],
        actor_user_id=actor, reason="added from a talent pool",
        resume_s3_key=applicant["resume_s3_key"] if applicant is not None else None,
        source="internal",
    )
    already_enrolled = outcome.action == "noop"
    event_details: dict[str, Any] = {
        "requisition_id": str(requisition_id), "already_enrolled": already_enrolled,
    }
    if acknowledged_stale:
        # Facts only, as ever: THAT an override happened and what band it
        # overrode — never the evidence itself. An override that leaves no
        # trace is not a control (design §6.6 point 3).
        event_details["acknowledged_stale"] = True
        event_details["freshness"] = band
    await _event(
        db, company_id=company_id, pool_id=pool_id, applicant_id=applicant_id,
        action="member_invited", actor_user_id=actor, details=event_details,
    )
    await _audit(
        db, actor=actor, action="pools.member.invited", resource_id=pool_id,
        details={
            "company_id": str(company_id), "member_id": str(member_id),
            "requisition_id": str(requisition_id), "already_enrolled": already_enrolled,
        },
        meta=meta,
    )
    return {
        "enrolment_id": outcome.enrolment_id, "requisition_id": str(requisition_id),
        "already_enrolled": already_enrolled,
    }

