"""The public careers page — one company's open roles. NO LOGIN.

    GET /careers/{slug}   the board: company header, filter options, job cards

Until now an opening was reachable only by its requisition UUID. Unguessable,
but explicitly not a secret — and that made the only route in a link somebody
had to send you. This is the front door: a stranger arrives, filters, and picks
a role worth reading.

PER COMPANY, NOT PLATFORM-WIDE
------------------------------
Addressed by company slug, so one tenant's board shows one tenant's roles and
nothing else. That is a product decision (recorded as decision 4), and it is
the conservative one: a per-company board is a strict subset of a cross-tenant
one, so starting here costs nothing if the platform later wants a single board,
whereas launching cross-tenant and retreating is a conversation with customers.
It also means there is no ranking question — the only ordering that exists is
"newest", and no company is being placed above another.

WHAT MAKES A ROLE VISIBLE
-------------------------
Three conditions, all required: ``public_apply_enabled`` (HR opted this opening
in), ``status = 'open'``, and a closing date that has not passed. Exactly the
predicate ``public_apply.py`` already uses to decide whether an application may
be submitted, and that is not a coincidence — a board that lists a role the
apply endpoint then refuses is worse than a board that omits it.

WHAT A CARD DOES NOT CARRY
--------------------------
No ``jd_text``. The spec is right that a board is for deciding whether to open
a role, not for reading it — and shipping every description in a list response
would make the payload grow with the length of the prose rather than the number
of roles. The detail page (``GET /apply/{id}``) has the full advert.

No applicant counts, no owner, no internal state. Same rule as the posting
endpoint: this is served to anyone at all, and "37 people have applied" is the
company's information, not the visitor's.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.database import DbSessionDep
from app.rate_limit import rate_limit
from app.utils.sql_like import LIKE_ESCAPE, like_literal

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/careers", tags=["careers"])

# A board page. Large enough that most companies fit on one, small enough that
# the response stays a list rather than a download.
_DEFAULT_PER_PAGE = 20
_MAX_PER_PAGE = 60

# Skills shown on a card. The full list is on the detail page; a card is a
# glance, and eight tags is already more than anyone reads in a grid.
_CARD_SKILLS = 6

# Every visible role must satisfy this, and it is deliberately the same
# predicate the apply endpoint enforces. Listing a role that cannot be applied
# to is the one failure a board must not have.
_VISIBLE = (
    " r.deleted_at IS NULL"
    " AND r.status = 'open'"
    " AND r.public_apply_enabled"
    " AND (r.closes_at IS NULL OR r.closes_at > :now)"
)

_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="No careers page found at that address.",
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class JobCard(BaseModel):
    """One role, as a card. Enough to decide whether to open it."""

    requisition_id: str
    title: str
    level: str
    department: str | None = None
    location: str | None = None
    employment_type: str | None = None
    experience_min_years: int | None = None
    experience_max_years: int | None = None
    # Trimmed to the first few. A card is a glance.
    skills: list[str] = Field(default_factory=list)
    posted_at: str
    # Present only when the opening publishes its band — the same gate the
    # detail endpoint applies, enforced again here rather than trusted, because
    # a list query is exactly where a WHERE clause gets forgotten.
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None


class BoardFilters(BaseModel):
    """What this company actually has, so a dropdown offers only real choices.

    Computed across every visible role, NOT across the filtered result — a
    department list that shrinks as you filter cannot be used to change your
    mind, which is the main thing a filter is for.
    """

    departments: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)


class CareersBoard(BaseModel):
    company_name: str
    company_slug: str
    total: int
    page: int
    per_page: int
    filters: BoardFilters
    items: list[JobCard] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
async def _company(db: DbSessionDep, slug: str) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT id, name, slug FROM companies"
                " WHERE lower(slug) = lower(:s) AND is_active AND deleted_at IS NULL"
            ),
            {"s": slug},
        )
    ).mappings().first()
    if row is None:
        raise _NOT_FOUND
    return dict(row)


def _card(row: Any) -> JobCard:
    shows_salary = bool(row["salary_visible"])
    skills = list(row["required_skills"] or [])[:_CARD_SKILLS]
    return JobCard(
        requisition_id=str(row["id"]),
        title=row["title"],
        level=row["level"],
        department=row["department"],
        location=row["location"],
        employment_type=row["employment_type"],
        experience_min_years=row["experience_min_years"],
        experience_max_years=row["experience_max_years"],
        skills=skills,
        posted_at=row["created_at"].isoformat(),
        salary_min=row["salary_min"] if shows_salary else None,
        salary_max=row["salary_max"] if shows_salary else None,
        salary_currency=row["salary_currency"] if shows_salary else None,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@router.get(
    "/{slug}",
    response_model=CareersBoard,
    summary="A company's open roles",
    # Generous: this is a browse surface a visitor paginates and re-filters,
    # and throttling it like a write would make the page feel broken. Still
    # capped, because it is unauthenticated and reachable by anyone.
    dependencies=[rate_limit("careers_board", 120)],
)
async def get_board(
    slug: str,
    db: DbSessionDep,
    q: Annotated[str | None, Query(max_length=120)] = None,
    department: Annotated[str | None, Query(max_length=120)] = None,
    location: Annotated[str | None, Query(max_length=200)] = None,
    employment_type: Annotated[str | None, Query(max_length=40)] = None,
    # "I have this much experience" — matches roles asking for no more than
    # that. Phrased around the candidate rather than the posting: somebody with
    # four years wants roles they can apply to, not roles whose minimum happens
    # to be four.
    max_experience_years: Annotated[int | None, Query(ge=0, le=60)] = None,
    # "Pays at least this much." Same shape as max_experience_years and for the
    # same reason: phrased around what the candidate wants, not around the
    # posting's fields.
    min_salary: Annotated[int | None, Query(ge=0, le=1_000_000_000)] = None,
    sort: Annotated[str, Query(pattern="^(newest|relevance)$")] = "newest",
    page: Annotated[int, Query(ge=1, le=500)] = 1,
    per_page: Annotated[int, Query(ge=1, le=_MAX_PER_PAGE)] = _DEFAULT_PER_PAGE,
) -> CareersBoard:
    """The board. Public, unauthenticated, one company."""
    company = await _company(db, slug)
    now = datetime.now(tz=UTC)
    params: dict[str, Any] = {"c": company["id"], "now": now}

    where = [_VISIBLE, "r.company_id = :c"]
    if department:
        where.append("r.department = :dept")
        params["dept"] = department
    if location:
        where.append("r.location = :loc")
        params["loc"] = location
    if employment_type:
        where.append("r.employment_type = :etype")
        params["etype"] = employment_type
    if max_experience_years is not None:
        # A role with no stated minimum is open to everyone, so it stays in.
        where.append(
            "(r.experience_min_years IS NULL OR r.experience_min_years <= :maxexp)"
        )
        params["maxexp"] = max_experience_years
    if min_salary is not None:
        # A role that does not PUBLISH a salary stays in. Absence is not a
        # mismatch — same rule as the experience filter above — and hiding every
        # unpublished role behind a salary preference would empty most boards.
        # salary_max is the top of the advertised band, so "could pay this".
        where.append(
            "(NOT r.salary_visible OR r.salary_max IS NULL OR r.salary_max >= :minsal)"
        )
        params["minsal"] = min_salary
    if q and q.strip():
        # Title and skills. Escaped so a visitor typing '%' searches for a
        # percent sign rather than matching every role — the filter must not
        # widen the set it was asked to narrow.
        where.append(
            "(r.title ILIKE :q ESCAPE :esc"
            " OR r.required_skills::text ILIKE :q ESCAPE :esc"
            " OR r.nice_to_have_skills::text ILIKE :q ESCAPE :esc)"
        )
        params["q"] = f"%{like_literal(q.strip())}%"
        params["esc"] = LIKE_ESCAPE
    clause = " AND ".join(where)

    # Relevance only means anything when there is a search term; without one it
    # would be an arbitrary permutation presented as a ranking, so it falls back
    # to newest. The expression is built from a fixed set of in-code strings —
    # `sort` is constrained to two values by the route's own pattern and never
    # reaches SQL as text.
    if sort == "relevance" and q and q.strip():
        # Title beats skills beats everything else, then newest inside a band.
        order_by = (
            "CASE WHEN r.title ILIKE :q ESCAPE :esc THEN 0"
            "      WHEN r.required_skills::text ILIKE :q ESCAPE :esc THEN 1"
            "      ELSE 2 END, r.created_at DESC"
        )
    else:
        order_by = "r.created_at DESC"

    total = await db.scalar(
        text(f"SELECT count(*) FROM job_requisitions r WHERE {clause}"), params
    )

    rows = (
        await db.execute(
            text(
                "SELECT r.id, r.title, r.level, r.department, r.location,"
                "       r.employment_type, r.experience_min_years, r.experience_max_years,"
                "       r.required_skills, r.salary_min, r.salary_max, r.salary_currency,"
                "       r.salary_visible, r.created_at"
                "  FROM job_requisitions r"
                f" WHERE {clause}"
                f" ORDER BY {order_by}"
                " LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": per_page, "off": (page - 1) * per_page},
        )
    ).mappings().all()

    # Facets over everything visible, not over the filtered set — see BoardFilters.
    facet_rows = (
        await db.execute(
            text(
                "SELECT DISTINCT r.department, r.location, r.employment_type"
                "  FROM job_requisitions r"
                f" WHERE {_VISIBLE} AND r.company_id = :c"
            ),
            {"c": company["id"], "now": now},
        )
    ).mappings().all()

    return CareersBoard(
        company_name=company["name"],
        company_slug=company["slug"],
        total=int(total or 0),
        page=page,
        per_page=per_page,
        filters=BoardFilters(
            departments=sorted({r["department"] for r in facet_rows if r["department"]}),
            locations=sorted({r["location"] for r in facet_rows if r["location"]}),
            employment_types=sorted(
                {r["employment_type"] for r in facet_rows if r["employment_type"]}
            ),
        ),
        items=[_card(r) for r in rows],
    )
