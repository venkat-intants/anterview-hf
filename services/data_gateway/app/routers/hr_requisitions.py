"""HR requisition + enrolment endpoints — Group B.

    GET    /hr/requisitions                     list this company's openings
    POST   /hr/requisitions                     create one
    GET    /hr/requisitions/{id}                one opening, with its funnel
    PATCH  /hr/requisitions/{id}                edit title/level/JD/target/close date
    POST   /hr/requisitions/{id}/status         open | paused | closed
    GET    /hr/requisitions/{id}/enrolments     who is in this opening
    POST   /hr/enrolments/{id}/status           move one candidate, audited
    GET    /hr/requisitions/review              backfill review: merges + backfilled reqs
    POST   /hr/applicants/merge                 fold duplicate applicants together
    POST   /hr/requisitions/{id}/split          take a mis-grouped opening apart

MULTI-TENANT: every query is company-scoped through ``HrCtxDep``, and the
composite foreign keys make a cross-tenant enrolment impossible at the database
level rather than only in these handlers.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text

from app.application_questions import answers_for
from app.database import DbSessionDep
from app.dependencies import HrCtxDep
from app.models import AuditLog
from app.requisitions import (
    TERMINAL_STATUSES,
    VALID_STATUSES,
    merge_applicants,
    merge_candidates,
    normalise_title,
    record_transition,
    split_requisition,
)
from app.utils.request_ip import extract_client_ip, extract_user_agent
from app.workflow_runner import on_shortlisted

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/hr", tags=["hr-requisitions"])

_VALID_REQ_STATUS = {"open", "paused", "closed"}
_MAX_PAGE = 200


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
# Employment types the board can filter on. Mirrors ck_job_requisitions_employment_type
# — the database is the authority, this is the friendly refusal in front of it.
EMPLOYMENT_TYPES = ("full_time", "part_time", "contract", "internship", "temporary")

# jsonb columns — these bind as JSON text with an explicit cast, never as a
# Python list, which asyncpg would send as a Postgres array.
_JSON_LIST_FIELDS = frozenset(
    {"responsibilities", "required_skills", "nice_to_have_skills"}
)

# Per list, and per entry. A responsibilities list is a job advert, not a
# document: past roughly twenty bullets nobody reads it and the board card
# cannot show it, so the cap is a product decision as much as a size guard.
_MAX_LIST_ITEMS = 20
_MAX_ITEM_CHARS = 300


def _clean_list(items: list[str] | None) -> list[str] | None:
    """Trim, drop blanks, keep order.

    Returns None for None so a PATCH that does not mention a list leaves it
    alone — distinct from an empty list, which deliberately clears it.

    The COUNT limit is not applied here. pydantic's ``max_length`` runs before
    this validator, so an over-long list is already a 422 naming the field by
    the time we get here — which is the behaviour we want. Silently keeping the
    first twenty of somebody's thirty bullets would lose content without
    telling anyone, and a form that reports "at most 20" still holds what they
    typed while they cut it down.
    """
    if items is None:
        return None
    out: list[str] = []
    for raw in items:
        cleaned = " ".join(str(raw).split())[:_MAX_ITEM_CHARS]
        if cleaned:
            out.append(cleaned)
    return out


class PostingFields(BaseModel):
    """The advert half of an opening — shared by create and patch.

    Every field optional, because these arrived after several thousand openings
    already existed and a create that demanded them would break the existing
    flow to serve a board that is not built yet.
    """

    department: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=40)
    experience_min_years: int | None = Field(default=None, ge=0, le=60)
    experience_max_years: int | None = Field(default=None, ge=0, le=60)
    salary_min: int | None = Field(default=None, ge=0, le=1_000_000_000)
    salary_max: int | None = Field(default=None, ge=0, le=1_000_000_000)
    salary_currency: str | None = Field(default=None, max_length=8)
    salary_visible: bool | None = None
    responsibilities: list[str] | None = Field(default=None, max_length=_MAX_LIST_ITEMS)
    required_skills: list[str] | None = Field(default=None, max_length=_MAX_LIST_ITEMS)
    nice_to_have_skills: list[str] | None = Field(default=None, max_length=_MAX_LIST_ITEMS)

    @field_validator("employment_type")
    @classmethod
    def _known_employment_type(cls, v: str | None) -> str | None:
        if v is not None and v not in EMPLOYMENT_TYPES:
            raise ValueError(f"employment_type must be one of {sorted(EMPLOYMENT_TYPES)}")
        return v

    @field_validator("responsibilities", "required_skills", "nice_to_have_skills")
    @classmethod
    def _tidy(cls, v: list[str] | None) -> list[str] | None:
        return _clean_list(v)

    @model_validator(mode="after")
    def _ranges_make_sense(self) -> PostingFields:
        """Refuse an inverted range here so the caller gets a field-level 422.

        The database refuses it too — that check constraint is what protects the
        copilot commit path and any future import. This exists so a person
        filling in a form is told which field is wrong instead of seeing a 500.
        """
        lo, hi = self.experience_min_years, self.experience_max_years
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("experience_min_years cannot exceed experience_max_years")
        lo, hi = self.salary_min, self.salary_max
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("salary_min cannot exceed salary_max")
        return self


class RequisitionIn(PostingFields):
    title: str = Field(min_length=2, max_length=200)
    level: str = Field(default="mid", max_length=40)
    jd_text: str | None = Field(default=None, max_length=40_000)
    target_hires: int | None = Field(default=None, gt=0, le=10_000)
    closes_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not normalise_title(v):
            raise ValueError("title cannot be blank")
        return v.strip()


class RequisitionPatch(PostingFields):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    level: str | None = Field(default=None, max_length=40)
    jd_text: str | None = Field(default=None, max_length=40_000)
    target_hires: int | None = Field(default=None, gt=0, le=10_000)
    closes_at: datetime | None = None
    owner_user_id: uuid.UUID | None = None
    # Whether the open web may apply. Off until someone turns it on — see the
    # note in public_apply.py on why a requisition id is not a secret.
    public_apply_enabled: bool | None = None


class FunnelStage(BaseModel):
    status: str
    count: int


class RequisitionOut(BaseModel):
    id: str
    title: str
    level: str
    status: str
    jd_text: str | None = None
    target_hires: int | None = None
    closes_at: str | None = None
    owner_user_id: str | None = None
    from_backfill: bool
    public_apply_enabled: bool = False
    created_at: str
    department: str | None = None
    location: str | None = None
    employment_type: str | None = None
    experience_min_years: int | None = None
    experience_max_years: int | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_visible: bool = False
    responsibilities: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    # Rollup — what the board and the dashboard both need.
    total_enrolments: int = 0
    hired: int = 0
    awaiting_decision: int = 0
    funnel: list[FunnelStage] = Field(default_factory=list)


class EnrolmentOut(BaseModel):
    id: str
    applicant_id: str
    full_name: str
    email: str | None = None
    status: str
    target_job_title: str
    ats_overall: int | None = None
    ats_recommendation: str | None = None
    days_in_stage: float | None = None
    created_at: str
    # Which round they are actually sitting on. Absent until now, which meant
    # the candidate could see their own position (via /users/me/applications)
    # and the hiring team could not — and "shortlisted" alone cannot answer
    # whether the workflow started, which is the question the runner wiring
    # made worth asking. NULL before the shortlist gate, and for an enrolment
    # whose opening has no published workflow.
    current_round_id: str | None = None
    current_round_title: str | None = None


class StatusIn(BaseModel):
    status: str
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("status")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}")
        return v


class MergeIn(BaseModel):
    survivor_id: uuid.UUID
    absorbed_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)


class SplitIn(BaseModel):
    """Which of a folded opening's source titles belong in an opening of their own."""

    source_titles: list[str] = Field(min_length=1, max_length=50)
    new_title: str = Field(min_length=2, max_length=200)
    level: str | None = Field(default=None, max_length=40)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _owned(db: DbSessionDep, company_id: uuid.UUID, req_id: uuid.UUID) -> dict[str, Any]:
    """Fetch a requisition or 404. Cross-tenant reads are indistinguishable from
    missing ones — never 403, which would confirm the row exists."""
    row = (
        await db.execute(
            text(
                "SELECT id, title, level, status, jd_text, target_hires, closes_at,"
                "       owner_user_id, from_backfill, public_apply_enabled, created_at,"
                "       department, location, employment_type,"
                "       experience_min_years, experience_max_years,"
                "       salary_min, salary_max, salary_currency, salary_visible,"
                "       responsibilities, required_skills, nice_to_have_skills"
                "  FROM job_requisitions"
                " WHERE id = :i AND company_id = :c AND deleted_at IS NULL"
            ),
            {"i": req_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Requisition not found.")
    return dict(row)


def _to_out(row: dict[str, Any], counts: dict[str, int] | None = None) -> RequisitionOut:
    counts = counts or {}
    return RequisitionOut(
        id=str(row["id"]),
        title=row["title"],
        level=row["level"],
        status=row["status"],
        jd_text=row.get("jd_text"),
        target_hires=row.get("target_hires"),
        closes_at=row["closes_at"].isoformat() if row.get("closes_at") else None,
        owner_user_id=str(row["owner_user_id"]) if row.get("owner_user_id") else None,
        from_backfill=bool(row["from_backfill"]),
        public_apply_enabled=bool(row.get("public_apply_enabled")),
        created_at=row["created_at"].isoformat(),
        department=row.get("department"),
        location=row.get("location"),
        employment_type=row.get("employment_type"),
        experience_min_years=row.get("experience_min_years"),
        experience_max_years=row.get("experience_max_years"),
        salary_min=row.get("salary_min"),
        salary_max=row.get("salary_max"),
        salary_currency=row.get("salary_currency"),
        salary_visible=bool(row.get("salary_visible")),
        # `or []` rather than a default: the column is NOT NULL with a '[]'
        # default, but _to_out is also handed rows from the list query and from
        # tests, and a None there would 422 the response model on a read.
        responsibilities=row.get("responsibilities") or [],
        required_skills=row.get("required_skills") or [],
        nice_to_have_skills=row.get("nice_to_have_skills") or [],
        total_enrolments=sum(counts.values()),
        hired=counts.get("hired", 0),
        awaiting_decision=sum(
            v for k, v in counts.items() if k not in TERMINAL_STATUSES
        ),
        funnel=[FunnelStage(status=k, count=v) for k, v in sorted(counts.items())],
    )


async def _counts(db: DbSessionDep, req_ids: list[uuid.UUID]) -> dict[str, dict[str, int]]:
    """Per-requisition status histogram in one query, so listing N openings is
    two round trips rather than N+1."""
    if not req_ids:
        return {}
    rows = (
        await db.execute(
            text(
                "SELECT requisition_id, status, count(*) AS n FROM enrolments"
                " WHERE requisition_id = ANY(:ids) AND deleted_at IS NULL"
                " GROUP BY requisition_id, status"
            ),
            {"ids": req_ids},
        )
    ).mappings().all()
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        out.setdefault(str(r["requisition_id"]), {})[r["status"]] = int(r["n"])
    return out


# ---------------------------------------------------------------------------
# Requisitions
# ---------------------------------------------------------------------------
@router.get("/requisitions", response_model=list[RequisitionOut])
async def list_requisitions(
    ctx: HrCtxDep,
    db: DbSessionDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RequisitionOut]:
    _hr_uid, company_id = ctx
    if status_filter is not None and status_filter not in _VALID_REQ_STATUS:
        raise HTTPException(
            status_code=400, detail=f"status must be one of {sorted(_VALID_REQ_STATUS)}"
        )
    rows = (
        await db.execute(
            text(
                "SELECT id, title, level, status, jd_text, target_hires, closes_at,"
                "       owner_user_id, from_backfill, public_apply_enabled, created_at,"
                "       department, location, employment_type,"
                "       experience_min_years, experience_max_years,"
                "       salary_min, salary_max, salary_currency, salary_visible,"
                "       responsibilities, required_skills, nice_to_have_skills"
                "  FROM job_requisitions"
                " WHERE company_id = :c AND deleted_at IS NULL"
                # CAST is required: asyncpg cannot infer the type of a
                # parameter used only as a bare NULL, so the unfiltered call
                # raised AmbiguousParameterError before this.
                "   AND (CAST(:s AS text) IS NULL OR status = CAST(:s AS text))"
                " ORDER BY (status = 'open') DESC, created_at DESC"
                " LIMIT :lim OFFSET :off"
            ),
            {"c": company_id, "s": status_filter, "lim": limit, "off": offset},
        )
    ).mappings().all()
    counts = await _counts(db, [r["id"] for r in rows])
    return [_to_out(dict(r), counts.get(str(r["id"]), {})) for r in rows]


@router.post("/requisitions", status_code=status.HTTP_201_CREATED, response_model=RequisitionOut)
async def create_requisition(
    body: RequisitionIn, ctx: HrCtxDep, db: DbSessionDep
) -> RequisitionOut:
    hr_uid, company_id = ctx
    now = datetime.now(tz=UTC)
    req_id = uuid.uuid4()
    try:
        await db.execute(
            text(
                "INSERT INTO job_requisitions (id, company_id, title, level, jd_text,"
                " target_hires, closes_at, owner_user_id, created_by_user_id, status,"
                " from_backfill, created_at, updated_at,"
                " department, location, employment_type,"
                " experience_min_years, experience_max_years,"
                " salary_min, salary_max, salary_currency, salary_visible,"
                " responsibilities, required_skills, nice_to_have_skills)"
                " VALUES (:i,:c,:t,:l,:jd,:th,:ca,:o,:o,'open',false,:n,:n,"
                "         :dept,:loc,:etype,:exmin,:exmax,"
                "         :smin,:smax,:scur,:svis,"
                "         CAST(:resp AS jsonb), CAST(:req AS jsonb), CAST(:nice AS jsonb))"
            ),
            {"i": req_id, "c": company_id, "t": body.title, "l": body.level,
             "jd": body.jd_text, "th": body.target_hires, "ca": body.closes_at,
             "o": hr_uid, "n": now,
             "dept": body.department, "loc": body.location,
             "etype": body.employment_type,
             "exmin": body.experience_min_years, "exmax": body.experience_max_years,
             "smin": body.salary_min, "smax": body.salary_max,
             "scur": body.salary_currency,
             # Not `or False`: the column is NOT NULL and an unset optional is
             # None, which would violate it.
             "svis": bool(body.salary_visible),
             "resp": json.dumps(body.responsibilities or []),
             "req": json.dumps(body.required_skills or []),
             "nice": json.dumps(body.nice_to_have_skills or [])},
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — the partial unique index is the guard
        await db.rollback()
        # Surfaced as 409 with the existing title rather than a 500: two
        # recruiters opening "Python Developer" on the same morning is normal,
        # and the right answer is to point them at the one that exists.
        if "uq_job_requisitions_company_title" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An open requisition for '{body.title}' already exists.",
            ) from exc
        log.error("hr.requisition.create_failed", error_type=type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not create the requisition.") from exc

    log.info("hr.requisition.created", requisition_id=str(req_id), company_id=str(company_id))
    return _to_out(await _owned(db, company_id, req_id))


@router.get("/requisitions/review")
async def review_backfill(ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    """What the Group B backfill inferred, for a human to confirm.

    Two lists, because the backfill makes two kinds of guess. Requisitions it
    minted by grouping on a normalised title — those may have merged two
    openings that only *look* alike. And applicant rows sharing an email, which
    D-06 says are one person but which this deliberately did not merge, because
    merging moves someone's assessment history and cannot be undone.
    """
    _hr_uid, company_id = ctx
    reqs = (
        await db.execute(
            text(
                "SELECT r.id, r.title, r.status, r.created_at,"
                "       count(e.id) FILTER (WHERE e.deleted_at IS NULL) AS enrolments,"
                "       count(DISTINCT e.target_job_title) AS distinct_titles,"
                "       array_agg(DISTINCT e.target_job_title) AS titles"
                "  FROM job_requisitions r"
                "  LEFT JOIN enrolments e ON e.requisition_id = r.id"
                " WHERE r.company_id = :c AND r.deleted_at IS NULL AND r.from_backfill"
                " GROUP BY r.id, r.title, r.status, r.created_at"
                " ORDER BY count(DISTINCT e.target_job_title) DESC, r.title"
            ),
            {"c": company_id},
        )
    ).mappings().all()
    dupes = await merge_candidates(db, company_id)
    return {
        "backfilled_requisitions": [
            {
                "id": str(r["id"]),
                "title": r["title"],
                "status": r["status"],
                "enrolments": int(r["enrolments"] or 0),
                # >1 means several spellings were folded together. Usually right,
                # occasionally two real openings — which is the whole point of
                # showing it rather than assuming.
                "distinct_source_titles": int(r["distinct_titles"] or 0),
                "source_titles": [t for t in (r["titles"] or []) if t],
            }
            for r in reqs
        ],
        "merge_candidates": [c.as_dict() for c in dupes],
    }


@router.get("/requisitions/{requisition_id}", response_model=RequisitionOut)
async def get_requisition(
    requisition_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> RequisitionOut:
    _hr_uid, company_id = ctx
    row = await _owned(db, company_id, requisition_id)
    counts = await _counts(db, [requisition_id])
    return _to_out(row, counts.get(str(requisition_id), {}))


@router.patch("/requisitions/{requisition_id}", response_model=RequisitionOut)
async def update_requisition(
    requisition_id: uuid.UUID, body: RequisitionPatch, ctx: HrCtxDep, db: DbSessionDep
) -> RequisitionOut:
    _hr_uid, company_id = ctx
    await _owned(db, company_id, requisition_id)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return await get_requisition(requisition_id, ctx, db)
    if "title" in fields and not normalise_title(fields["title"] or ""):
        raise HTTPException(status_code=422, detail="title cannot be blank")

    # The three list columns are jsonb. Bound as a plain Python list, asyncpg
    # sends a Postgres ARRAY and the UPDATE fails on a type it cannot cast — so
    # they go over as JSON text with an explicit cast, like every other jsonb
    # write in this service.
    sets = ", ".join(
        f"{k} = CAST(:{k} AS jsonb)" if k in _JSON_LIST_FIELDS else f"{k} = :{k}"
        for k in fields
    )
    params: dict[str, Any] = {
        k: (json.dumps(v) if k in _JSON_LIST_FIELDS else v) for k, v in fields.items()
    }
    params |= {"i": requisition_id, "c": company_id, "n": datetime.now(tz=UTC)}
    try:
        await db.execute(
            text(
                f"UPDATE job_requisitions SET {sets}, updated_at = :n"
                " WHERE id = :i AND company_id = :c"
            ),
            params,
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        if "uq_job_requisitions_company_title" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another open requisition already uses that title.",
            ) from exc
        raise HTTPException(status_code=503, detail="Could not update the requisition.") from exc
    # Editing the title makes the grouping deliberate rather than inferred, so
    # the row no longer belongs in the review queue.
    if "title" in fields:
        await db.execute(
            text("UPDATE job_requisitions SET from_backfill = false WHERE id = :i"),
            {"i": requisition_id},
        )
        await db.commit()
    return await get_requisition(requisition_id, ctx, db)


@router.post("/requisitions/{requisition_id}/status", response_model=RequisitionOut)
async def set_requisition_status(
    requisition_id: uuid.UUID,
    body: dict[str, str],
    request: Request,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> RequisitionOut:
    """Open, pause or close an opening.

    Closing does NOT touch the candidates in it. Under D-05 nothing but a person
    ends a candidacy, so an opening that closes with people still in it reports
    them as unresolved rather than quietly rejecting them; the response carries
    that count so the console can prompt for the decisions.
    """
    hr_uid, company_id = ctx
    new_status = (body or {}).get("status", "")
    if new_status not in _VALID_REQ_STATUS:
        raise HTTPException(
            status_code=400, detail=f"status must be one of {sorted(_VALID_REQ_STATUS)}"
        )
    row = await _owned(db, company_id, requisition_id)
    now = datetime.now(tz=UTC)
    await db.execute(
        text("UPDATE job_requisitions SET status = :s, updated_at = :n WHERE id = :i"),
        {"s": new_status, "n": now, "i": requisition_id},
    )
    db.add(
        AuditLog(
            actor_id=hr_uid,
            actor_type="user",
            action=f"requisition.status.{new_status}",
            resource_type="job_requisition",
            resource_id=requisition_id,
            details={"company_id": str(company_id), "previous_status": row["status"],
                     "title": row["title"]},
            ip_address=extract_client_ip(request),
            user_agent=extract_user_agent(request),
            event_ts=now,
        )
    )
    await db.commit()
    counts = await _counts(db, [requisition_id])
    out = _to_out({**row, "status": new_status}, counts.get(str(requisition_id), {}))
    log.info(
        "hr.requisition.status",
        requisition_id=str(requisition_id), status=new_status,
        unresolved=out.awaiting_decision,
    )
    return out


# ---------------------------------------------------------------------------
# Enrolments
# ---------------------------------------------------------------------------
@router.get("/requisitions/{requisition_id}/enrolments", response_model=list[EnrolmentOut])
async def list_enrolments(
    requisition_id: uuid.UUID,
    ctx: HrCtxDep,
    db: DbSessionDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[EnrolmentOut]:
    _hr_uid, company_id = ctx
    await _owned(db, company_id, requisition_id)
    if status_filter is not None and status_filter not in VALID_STATUSES:
        raise HTTPException(
            status_code=400, detail=f"status must be one of {sorted(VALID_STATUSES)}"
        )
    rows = (
        await db.execute(
            text(
                "SELECT e.id, e.applicant_id, a.full_name, a.email, e.status,"
                "       e.target_job_title, e.ats_overall, e.ats_recommendation, e.created_at,"
                "       e.current_round_id, wr.title AS current_round_title,"
                # Days since the last recorded move — the number that makes a
                # stall visible. Derived from the ledger, not from updated_at.
                "       EXTRACT(EPOCH FROM (now() - COALESCE("
                "           (SELECT max(st.occurred_at) FROM stage_transitions st"
                "             WHERE st.enrolment_id = e.id), e.created_at))) / 86400 AS days"
                "  FROM enrolments e"
                "  JOIN applicants a ON a.id = e.applicant_id AND a.deleted_at IS NULL"
                "  LEFT JOIN workflow_rounds wr ON wr.id = e.current_round_id"
                "                             AND wr.deleted_at IS NULL"
                " WHERE e.requisition_id = :r AND e.company_id = :c AND e.deleted_at IS NULL"
                "   AND (CAST(:s AS text) IS NULL OR e.status = CAST(:s AS text))"
                " ORDER BY e.ats_overall DESC NULLS LAST, e.created_at"
                " LIMIT :lim OFFSET :off"
            ),
            {"r": requisition_id, "c": company_id, "s": status_filter,
             "lim": limit, "off": offset},
        )
    ).mappings().all()
    return [
        EnrolmentOut(
            id=str(r["id"]),
            applicant_id=str(r["applicant_id"]),
            full_name=r["full_name"],
            email=r["email"],
            status=r["status"],
            target_job_title=r["target_job_title"],
            current_round_id=str(r["current_round_id"]) if r["current_round_id"] else None,
            current_round_title=r["current_round_title"],
            ats_overall=r["ats_overall"],
            ats_recommendation=r["ats_recommendation"],
            days_in_stage=round(float(r["days"]), 2) if r["days"] is not None else None,
            created_at=r["created_at"].isoformat(),
        )
        for r in rows
    ]


@router.post("/enrolments/{enrolment_id}/status", response_model=EnrolmentOut)
async def set_enrolment_status(
    enrolment_id: uuid.UUID,
    body: StatusIn,
    request: Request,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> EnrolmentOut:
    """Move one candidate, recording who moved them and why.

    ``automated=False`` unconditionally: this endpoint is only ever reached by a
    signed-in human. The workflow runner writes its own transitions through
    ``record_transition`` directly and marks them automated, so the ledger can
    always answer "did a person decide this?" — which is the question a DPDP
    audit actually asks.
    """
    hr_uid, company_id = ctx
    row = (
        await db.execute(
            text(
                "SELECT e.requisition_id FROM enrolments e"
                " WHERE e.id = :e AND e.company_id = :c AND e.deleted_at IS NULL"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Enrolment not found.")

    previous = await record_transition(
        db,
        enrolment_id=enrolment_id,
        company_id=company_id,
        to_status=body.status,
        actor_user_id=hr_uid,
        automated=False,
        reason=body.reason,
    )
    # The shortlist gate. Recording the status was only ever half of it: this is
    # the moment the workflow is supposed to start, and until now nothing called
    # the runner, so a shortlisted candidate sat at current_round_id = NULL
    # forever and no exam link was ever sent.
    #
    # Same transaction as the transition, on purpose. "Shortlisted, but round 1
    # was never assigned" is precisely the state this endpoint existed to
    # prevent, so the two either happen together or neither does.
    #
    # on_shortlisted is idempotent and refuses politely — no workflow attached,
    # already in a round, auto-assign switched off — so a re-save or a workflow
    # that is not ready is a no-op rather than an error.
    if body.status == "shortlisted" and previous != "shortlisted":
        outcome = await on_shortlisted(
            db, enrolment_id=enrolment_id, actor_user_id=hr_uid
        )
        log.info(
            "hr.enrolment.shortlisted",
            enrolment_id=str(enrolment_id),
            action=outcome.action,
            to_round=outcome.to_round,
            reason=outcome.reason,
        )
    if body.status in TERMINAL_STATUSES:
        now = datetime.now(tz=UTC)
        db.add(
            AuditLog(
                actor_id=hr_uid,
                actor_type="user",
                action=f"enrolment.decision.{body.status}",
                resource_type="enrolment",
                resource_id=enrolment_id,
                details={"company_id": str(company_id), "previous_status": previous,
                         "reason": body.reason},
                ip_address=extract_client_ip(request),
                user_agent=extract_user_agent(request),
                event_ts=now,
            )
        )
    await db.commit()

    found = await list_enrolments(row[0], ctx, db, None, _MAX_PAGE, 0)
    for e in found:
        if e.id == str(enrolment_id):
            return e
    raise HTTPException(status_code=404, detail="Enrolment not found.")


@router.get("/enrolments/{enrolment_id}/answers")
async def get_application_answers(
    enrolment_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> list[dict[str, Any]]:
    """What this applicant said to the opening's questions.

    Per enrolment rather than per applicant, because the questions belong to an
    opening: the same person applying twice answered two different sets, and
    merging them would attribute an answer to a question it was not given to.

    Retired questions are included. Somebody's answer does not stop existing
    because the company stopped asking, and hiding it would leave HR reading a
    decision that was partly based on something they can no longer see.
    """
    _hr_uid, company_id = ctx
    owned = await db.scalar(
        text(
            "SELECT 1 FROM enrolments"
            " WHERE id = :e AND company_id = :c AND deleted_at IS NULL"
        ),
        {"e": enrolment_id, "c": company_id},
    )
    if not owned:
        raise HTTPException(status_code=404, detail="Enrolment not found.")

    rows = await answers_for(db, enrolment_id=enrolment_id)
    return [
        {
            "question_id": str(r["question_id"]),
            "prompt": r["prompt"],
            "kind": r["kind"],
            "retired": bool(r["retired"]),
            "answer": r["answer"],
        }
        for r in rows
    ]


@router.post("/applicants/merge")
async def merge_duplicate_applicants(
    body: MergeIn, request: Request, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """Fold duplicate applicant rows into one person (D-06).

    Irreversible: it repoints exam and interview history onto the survivor and
    soft-deletes the rest. Audited for that reason, and refused outright when
    both rows are enrolled in the same opening, because choosing which of two
    applications to keep is a judgement about someone's candidacy.
    """
    hr_uid, company_id = ctx
    try:
        moved = await merge_applicants(
            db,
            company_id=company_id,
            survivor_id=body.survivor_id,
            absorbed_ids=body.absorbed_ids,
            actor_user_id=hr_uid,
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    db.add(
        AuditLog(
            actor_id=hr_uid,
            actor_type="user",
            action="applicant.merged",
            resource_type="applicant",
            resource_id=body.survivor_id,
            details={"company_id": str(company_id),
                     "absorbed": [str(i) for i in body.absorbed_ids], "moved": moved},
            ip_address=extract_client_ip(request),
            user_agent=extract_user_agent(request),
            event_ts=datetime.now(tz=UTC),
        )
    )
    await db.commit()
    return {"survivor_id": str(body.survivor_id), "absorbed": len(body.absorbed_ids),
            "moved": moved}


@router.post("/requisitions/{requisition_id}/split")
async def split_backfilled_requisition(
    requisition_id: uuid.UUID,
    body: SplitIn,
    request: Request,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> dict[str, Any]:
    """Take a mis-grouped opening apart (the review screen's other half).

    The backfill grouped applicants by normalised title, which is right almost
    always and occasionally folds two real jobs into one. Merge fixes duplicate
    *people*; this fixes duplicate *openings*, moving the misfiled candidates
    into a requisition of their own.

    Audited alongside merge because it moves people's applications between
    openings. It is far less destructive than a merge — nothing is soft-deleted
    and the candidates keep every assessment they have — but "which opening am
    I actually applying to?" is still a question about someone's candidacy, so
    the ledger should be able to answer who changed it.
    """
    hr_uid, company_id = ctx
    await _owned(db, company_id, requisition_id)
    try:
        out = await split_requisition(
            db,
            company_id=company_id,
            requisition_id=requisition_id,
            source_titles=body.source_titles,
            new_title=body.new_title,
            level=body.level,
            actor_user_id=hr_uid,
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    db.add(
        AuditLog(
            actor_id=hr_uid,
            actor_type="user",
            action="requisition.split",
            resource_type="job_requisition",
            resource_id=uuid.UUID(out["requisition_id"]),
            details={"company_id": str(company_id), "split_from": str(requisition_id),
                     "source_titles": body.source_titles, "moved": out["moved"]},
            ip_address=extract_client_ip(request),
            user_agent=extract_user_agent(request),
            event_ts=datetime.now(tz=UTC),
        )
    )
    await db.commit()
    log.info("hr.requisition.split", source=str(requisition_id),
             created=out["requisition_id"], moved=out["moved"])
    return out


@router.get("/requisitions/{requisition_id}/dashboard")
async def requisition_dashboard(
    requisition_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """Everything about one opening on one screen — Group E, E1.

    The list view answers "which openings need me?"; this answers "what is
    actually happening inside this one?". Three things the list cannot show:

    * WHERE candidates are, per round rather than per status. A status of
      'shortlisted' is the same word whether someone is waiting for round one
      or sitting between rounds three and four, and those are different
      problems.
    * WHERE THEY STOP. Per-round drop-off is the only view that distinguishes a
      hard round from a broken one — a round nobody clears is usually the
      second, not the first.
    * WHERE THEY CAME FROM. Public applications and HR uploads behave
      differently enough (volume, quality, consent basis) that a single total
      hides which lever is working.
    """
    _hr_uid, company_id = ctx
    req = await _owned(db, company_id, requisition_id)

    by_status = (
        await db.execute(
            text(
                "SELECT status, count(*) AS n FROM enrolments"
                " WHERE requisition_id = :r AND company_id = :c AND deleted_at IS NULL"
                " GROUP BY status"
            ),
            {"r": requisition_id, "c": company_id},
        )
    ).mappings().all()

    # Per round of the LIVE workflow. Archived versions are deliberately left
    # out: candidates still finishing an old version are counted in the status
    # totals above, and mixing two versions' rounds into one funnel would
    # produce a chart of a process that never existed.
    rounds = (
        await db.execute(
            text(
                "SELECT wr.id, wr.position, wr.title, wr.kind,"
                "       (SELECT count(*) FROM enrolments e"
                "         WHERE e.current_round_id = wr.id AND e.deleted_at IS NULL) AS at_round,"
                "       (SELECT count(*) FROM round_results rr"
                "         WHERE rr.round_id = wr.id AND rr.superseded_at IS NULL) AS attempted,"
                "       (SELECT count(*) FROM round_results rr"
                "         WHERE rr.round_id = wr.id AND rr.superseded_at IS NULL"
                "           AND rr.passed) AS passed"
                "  FROM workflow_rounds wr"
                "  JOIN workflows w ON w.id = wr.workflow_id"
                " WHERE w.requisition_id = :r AND w.status = 'published'"
                "   AND w.deleted_at IS NULL AND wr.deleted_at IS NULL"
                " ORDER BY wr.position"
            ),
            {"r": requisition_id},
        )
    ).mappings().all()

    # Median, not mean: one candidate parked for six months would drag a mean
    # into uselessness, and the question being asked is "how long does this
    # normally take?".
    timing = (
        await db.execute(
            text(
                "SELECT percentile_cont(0.5) WITHIN GROUP ("
                "         ORDER BY EXTRACT(EPOCH FROM (NOW() - e.updated_at)) / 86400.0"
                "       ) AS median_days_in_stage,"
                "       count(*) FILTER (WHERE a.pending_enrichment) AS still_being_read,"
                "       count(*) FILTER (WHERE a.created_by_user_id IS NULL) AS unattributed"
                "  FROM enrolments e"
                "  JOIN applicants a ON a.id = e.applicant_id AND a.deleted_at IS NULL"
                " WHERE e.requisition_id = :r AND e.company_id = :c AND e.deleted_at IS NULL"
                "   AND e.status NOT IN ('hired','rejected')"
            ),
            {"r": requisition_id, "c": company_id},
        )
    ).mappings().first()

    counts = {r["status"]: int(r["n"]) for r in by_status}
    return {
        "requisition": _to_out(req, counts).model_dump(),
        "rounds": [
            {
                "round_id": str(r["id"]),
                "position": r["position"],
                "title": r["title"],
                "kind": r["kind"],
                "at_this_round": int(r["at_round"] or 0),
                "attempted": int(r["attempted"] or 0),
                "passed": int(r["passed"] or 0),
                # None rather than 0 when nobody has sat it: "0% pass rate" and
                # "nobody has taken it yet" look identical on a chart and mean
                # opposite things.
                "pass_rate": (
                    round(int(r["passed"]) / int(r["attempted"]) * 100)
                    if int(r["attempted"] or 0)
                    else None
                ),
            }
            for r in rounds
        ],
        "has_published_workflow": bool(rounds),
        "median_days_in_stage": (
            round(float(timing["median_days_in_stage"]), 1)
            if timing and timing["median_days_in_stage"] is not None
            else None
        ),
        # Resumes stored but not yet scored. Shown so an empty ATS column reads
        # as "not read yet" rather than "scored zero".
        "still_being_read": int(timing["still_being_read"] or 0) if timing else 0,
    }
