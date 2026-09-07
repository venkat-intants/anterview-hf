"""What a candidate can see about their own applications.

The gap this closes: an applicant who used the public form got email and
nothing else. Their enrolment status, the round they are on and every stage
transition were all recorded — and none of it was ever shown to the person it
was about. HR could see the whole funnel; the candidate could see none of it.

    GET  /users/me/applications        every opening this person applied to
    GET  /users/me/applications/{id}   one of them, with its stage history
    GET  /users/me/open-roles          what they could apply to right now

Scope is the signed-in user and nothing else. There is no applicant_id,
company_id or user_id parameter anywhere in this router, so it cannot be
pointed at somebody else — the same rule ``onboarding.py`` follows, and it
matters more here, because unlike a practice plan these rows belong to a
company's hiring process.

A candidate may hold applications at SEVERAL companies, so this is the one
candidate-facing surface that deliberately reads across tenants. It is safe in
the one direction that counts: the join starts from ``applicants.user_id`` and
can only ever reach rows that name this user.

WHAT IS DELIBERATELY NOT RETURNED
---------------------------------
Any evaluation of the person. No ATS score, no breakdown, no strengths or
concerns, no recommendation, no round score, no pass threshold, no decision
rationale, and nothing about any other candidate. Two reasons, and they point
the same way:

  * DPDP Act 2023 scrutinises automated decision-making. Showing a candidate a
    machine-generated score against their name, on a page that also shows their
    application progressing or not, is exactly the inference we have designed
    the rest of the system to avoid making.
  * ``held`` is reported to the candidate as "under review", not as the
    below-threshold state it is internally. That is not evasion: under D-05 a
    threshold decides advancement and never the outcome, a person decides the
    outcome, and at the moment the candidate reads this the truthful statement
    is that a human has yet to decide. Telling them they fell short of a bar
    that cannot reject them would be both unkind and inaccurate.

Progress is fair game — which round, how many rounds, what happens next. That
is what someone refreshing this page actually wants to know.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from shared.auth.base import User
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/users/me", tags=["candidate-applications"])

CurrentUserDep = Annotated[User, Depends(get_current_user)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]

# One application per opening is already enforced in the database; this is a
# sanity bound on the response, not a paging scheme. Somebody with more than
# this many live applications is a case to look at, not to paginate.
_MAX_APPLICATIONS = 100

# A console panel, not a board. Past a couple of dozen a candidate wants a
# careers page with filters, which already exists.
_MAX_OPEN_ROLES = 24


# ---------------------------------------------------------------------------
# Candidate-facing vocabulary
# ---------------------------------------------------------------------------
# The internal status set is {new, shortlisted, interviewed, held, hired,
# rejected}. These are the words the candidate reads. Mapped in one place
# rather than in the frontend so the reasoning above lives next to the strings
# it produced, and so a new internal status cannot leak out untranslated.
_STAGE_LABELS: dict[str, str] = {
    "new": "Application received",
    "shortlisted": "Shortlisted",
    "interviewed": "Interview complete",
    "held": "Under review",
    "hired": "Selected",
    "rejected": "Not progressing",
}

# What the candidate should do next, if anything. Every value here is either an
# action they can take or an honest statement that the ball is not in their
# court. None of them implies an outcome.
_NEXT_STEPS: dict[str, str] = {
    "new": "The hiring team is reviewing applications. Nothing to do for now.",
    "shortlisted": "You are through to the assessment stage. Watch your email for a link.",
    "interviewed": "Your interview is complete and with the hiring team.",
    "held": "The hiring team is reviewing your application. Nothing to do for now.",
    "hired": "The hiring team will be in touch about next steps.",
    "rejected": "This application has closed. You are welcome to apply for other roles.",
}

_CLOSED = frozenset({"hired", "rejected"})


class ApplicationOut(BaseModel):
    """One application, from the candidate's side of the screen."""

    id: str
    job_title: str
    company_name: str
    applied_at: str
    updated_at: str
    stage: str  # candidate-facing label, never the raw status
    next_step: str
    closed: bool
    # Progress through the workflow, when the opening has one published and the
    # candidate has actually started it. All three are null before the first
    # round is assigned, which is the common case while HR is still reviewing.
    current_round_title: str | None = None
    current_round_kind: str | None = None
    round_number: int | None = None
    total_rounds: int | None = None


class StageEventOut(BaseModel):
    """One step in this application's history, as the candidate may see it."""

    stage: str
    occurred_at: str
    by_a_person: bool


class ApplicationDetailOut(ApplicationOut):
    history: list[StageEventOut]


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
# Reaches enrolments only through an applicant row that names this user, and
# LEFT JOINs the workflow round so an application still lists while HR is
# reviewing it (current_round_id is NULL for most of an application's life).
_LIST_SQL = """
SELECT e.id,
       e.status,
       e.target_job_title,
       e.created_at,
       e.updated_at,
       c.name                AS company_name,
       r.title               AS requisition_title,
       wr.title              AS round_title,
       wr.kind               AS round_kind,
       wr.position           AS round_position,
       (SELECT count(*) FROM workflow_rounds x
         WHERE x.workflow_id = e.workflow_id AND x.deleted_at IS NULL) AS total_rounds
  FROM applicants a
  JOIN enrolments e     ON e.applicant_id = a.id AND e.deleted_at IS NULL
  JOIN companies c      ON c.id = e.company_id AND c.deleted_at IS NULL
  JOIN job_requisitions r ON r.id = e.requisition_id AND r.deleted_at IS NULL
  LEFT JOIN workflow_rounds wr
         ON wr.id = e.current_round_id AND wr.deleted_at IS NULL
 WHERE a.user_id = :uid
   AND a.deleted_at IS NULL
   {extra}
 ORDER BY e.created_at DESC
 LIMIT :lim
"""


def _to_out(row: Any) -> dict[str, Any]:
    status_ = row.status
    # position is 0-based in the table; humans count rounds from one.
    number = (row.round_position + 1) if row.round_position is not None else None
    return {
        "id": str(row.id),
        # The requisition's current title wins over the copy frozen onto the
        # enrolment: a candidate reading this should see the role as it is
        # named today, not as it was worded the day they applied.
        "job_title": row.requisition_title or row.target_job_title,
        "company_name": row.company_name,
        "applied_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "stage": _STAGE_LABELS.get(status_, "In progress"),
        "next_step": _NEXT_STEPS.get(status_, "The hiring team is reviewing your application."),
        "closed": status_ in _CLOSED,
        "current_round_title": row.round_title,
        "current_round_kind": row.round_kind,
        "round_number": number,
        "total_rounds": row.total_rounds or None,
    }


@router.get(
    "/applications",
    response_model=list[ApplicationOut],
    summary="Every opening the signed-in candidate has applied to",
)
async def list_my_applications(
    user: CurrentUserDep, db: DbSessionDep
) -> list[ApplicationOut]:
    """The candidate's own applications, newest first.

    An empty list is the correct answer for a practice-only account, and for an
    applicant whose account has not been linked to their application yet — see
    the activation flow in ``public_apply``.
    """
    rows = (
        await db.execute(
            text(_LIST_SQL.format(extra="")),
            {"uid": uuid.UUID(user.user_id), "lim": _MAX_APPLICATIONS},
        )
    ).all()
    return [ApplicationOut(**_to_out(r)) for r in rows]


@router.get(
    "/applications/{application_id}",
    response_model=ApplicationDetailOut,
    summary="One application, with its stage history",
)
async def get_my_application(
    application_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> ApplicationDetailOut:
    """One of the candidate's own applications.

    404 — not 403 — when the application belongs to somebody else, because a
    403 would confirm that the id names a real application. The candidate has
    no way to enumerate ids anyway; the uniform answer keeps it that way.
    """
    row = (
        await db.execute(
            text(_LIST_SQL.format(extra="AND e.id = :eid")),
            {"uid": uuid.UUID(user.user_id), "eid": application_id, "lim": 1},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Application not found."
        )

    # The ledger records who moved a candidate and whether it was automated.
    # 'reason' is NOT selected: HR writes it for HR, and it is where a frank
    # internal note about a person would sit.
    events = (
        await db.execute(
            text(
                "SELECT to_status, occurred_at, automated FROM stage_transitions"
                " WHERE enrolment_id = :eid ORDER BY occurred_at ASC LIMIT 50"
            ),
            {"eid": application_id},
        )
    ).all()

    return ApplicationDetailOut(
        **_to_out(row),
        history=[
            StageEventOut(
                stage=_STAGE_LABELS.get(e.to_status, "In progress"),
                occurred_at=e.occurred_at.isoformat(),
                by_a_person=not e.automated,
            )
            for e in events
        ],
    )


# ---------------------------------------------------------------------------
# What this candidate could apply to
#
# WHY THIS IS CROSS-TENANT WHEN THE PUBLIC BOARD IS NOT
#
# ``/careers/{slug}`` is deliberately one company's roles: a public marketing
# page, addressed by that company, where putting a competitor's opening
# alongside would be a decision neither of them made.
#
# This is a different surface with a different reader. A candidate with an
# ACCOUNT has chosen to be on the platform, and a candidate account whose job
# list is empty unless somebody sends them a link is not much of an account.
# The exposure is the same rows the public board already serves — every one of
# them is open to the whole internet by an explicit ``public_apply_enabled``
# opt-in — so this shows nothing that was not already public. What it adds is
# the finding.
#
# Ordering is newest-first and nothing else. No relevance score, no promotion,
# no company ranked above another: the moment a feed ranks openings it is
# making a commercial decision, and that is not one to arrive at as a side
# effect of sorting.
# ---------------------------------------------------------------------------
_OPEN_ROLES_SQL = """
SELECT r.id, r.title, r.level, r.department, r.location, r.employment_type,
       r.experience_min_years, r.experience_max_years,
       r.required_skills, r.salary_min, r.salary_max, r.salary_currency,
       r.salary_visible, r.created_at,
       c.name AS company_name, c.slug AS company_slug,
       -- Whether this candidate is already in this opening's funnel. A feed
       -- that offers somebody a role they applied to last week, with no sign
       -- that they did, is worse than not listing it.
       EXISTS (
           SELECT 1 FROM enrolments e
             JOIN applicants a ON a.id = e.applicant_id
            WHERE e.requisition_id = r.id
              AND a.user_id = :uid
              AND e.deleted_at IS NULL
              AND a.deleted_at IS NULL
       ) AS already_applied
  FROM job_requisitions r
  JOIN companies c ON c.id = r.company_id AND c.is_active AND c.deleted_at IS NULL
 WHERE r.deleted_at IS NULL
   AND r.status = 'open'
   AND r.public_apply_enabled
   AND (r.closes_at IS NULL OR r.closes_at > :now)
 ORDER BY r.created_at DESC
 LIMIT :lim
"""


class OpenRole(BaseModel):
    """One opening, as a signed-in candidate sees it in their own console."""

    requisition_id: str
    title: str
    company_name: str
    company_slug: str
    level: str
    department: str | None = None
    location: str | None = None
    employment_type: str | None = None
    experience_min_years: int | None = None
    experience_max_years: int | None = None
    skills: list[str] = Field(default_factory=list)
    posted_at: str
    # Same gate as everywhere else, applied again rather than trusted: a list
    # query is exactly where a WHERE clause gets forgotten.
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    # So the card can say "applied" instead of inviting a second application.
    already_applied: bool = False


@router.get(
    "/open-roles",
    response_model=list[OpenRole],
    summary="Roles this candidate can apply to right now",
)
async def list_open_roles(user: CurrentUserDep, db: DbSessionDep) -> list[OpenRole]:
    """Every opening currently accepting public applications, newest first.

    Bounded rather than paged: this is a console panel, not a job board, and a
    candidate who wants to browse properly is better served by a company's
    careers page than by paging through a feed.
    """
    rows = (
        await db.execute(
            text(_OPEN_ROLES_SQL),
            {
                "uid": uuid.UUID(user.user_id),
                "now": datetime.now(tz=UTC),
                "lim": _MAX_OPEN_ROLES,
            },
        )
    ).mappings().all()

    out: list[OpenRole] = []
    for r in rows:
        shows_salary = bool(r["salary_visible"])
        out.append(
            OpenRole(
                requisition_id=str(r["id"]),
                title=r["title"],
                company_name=r["company_name"],
                company_slug=r["company_slug"],
                level=r["level"],
                department=r["department"],
                location=r["location"],
                employment_type=r["employment_type"],
                experience_min_years=r["experience_min_years"],
                experience_max_years=r["experience_max_years"],
                skills=list(r["required_skills"] or [])[:6],
                posted_at=r["created_at"].isoformat(),
                salary_min=r["salary_min"] if shows_salary else None,
                salary_max=r["salary_max"] if shows_salary else None,
                salary_currency=r["salary_currency"] if shows_salary else None,
                already_applied=bool(r["already_applied"]),
            )
        )
    return out
