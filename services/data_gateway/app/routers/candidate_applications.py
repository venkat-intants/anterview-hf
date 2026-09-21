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

WHICH CREDENTIAL MAY READ IT
----------------------------
The account's own, and not an interview link. ``get_current_user`` verifies a
JWT and returns its roles; it does not check them. Redeeming an interview
invitation issues a real access token whose ``sub`` is the applicant's
``user_id`` — after activation, their actual account id — with the role
``guest_candidate``. So for any route that authenticates with
``get_current_user`` alone, holding a forwarded interview link is holding the
candidate's account: their applications at every company, and the ability to
rotate their links.

Two comments elsewhere asserted this was already prevented ("rejected by
candidate/HR routes", "every candidate route rejects guest_candidate"). Only
the HR routes rejected it, because only they require a role; that is why
nobody noticed. It refuses a role rather than requiring one deliberately — see
``dependencies.reject_role``.

The gate below covers THIS router. It is not a property of the ``/users/me``
prefix: FastAPI binds router dependencies to a router's own routes, and six
routers are mounted there (tasks, offers, interview scheduling, onboarding,
resumes and this one), each carrying its own copy. The first version of this
fix gated one of them and said "so a candidate surface added later inherits
it" — true within this file, and read by the next person as true of the
prefix, which is the same mistake that let the hole live. What holds the line
across all six is ``test_candidate_route_authz``, which walks the mounted
application and asserts the gate for every route under ``/users/me``.

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

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from shared.auth.base import User
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.dependencies import get_current_user, reject_role
from app.exam_link import hash_exam_token, mint_exam_token
from app.interview_link import hash_interview_token, mint_interview_token
from app.publishing import visible_sql
from app.rate_limit import rate_limit_actor

log = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/users/me",
    tags=["candidate-applications"],
    # On the router, not on each route, so a route added to THIS file inherits
    # it. A new /users/me router needs its own copy — see the module docstring.
    dependencies=[Depends(reject_role("guest_candidate", "service"))],
)

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
    # A live interview invitation, if one is waiting. Carries no token: the raw
    # invite token is never stored (only its HMAC), so a link cannot be rebuilt
    # here — the candidate asks for a fresh one, which is a separate, audited
    # act rather than something a list endpoint hands out on every render.
    interview_invite_id: str | None = None
    interview_scheduled_at: str | None = None
    # PH4-D4: an open job_simulation/portfolio submission, if the candidate is
    # currently sitting on one — the task equivalent of interview_invite_id.
    # Carries no token, the same reason: the raw link is never stored.
    task_submission_id: str | None = None
    task_due_at: str | None = None
    task_status: str | None = None
    # A live assessment for THIS application, if one is waiting or under way.
    # The same rule as the invite above: an id to ask for a fresh link with,
    # never the link itself. `exam_in_progress` lets the page say "resume"
    # rather than "start" once the candidate has begun.
    exam_assignment_id: str | None = None
    exam_in_progress: bool = False
    exam_expires_at: str | None = None
    # Set only for a scheduled round, and then it is the date that matters —
    # the round opens then and closes a short window later, both well before
    # `exam_expires_at`. A page that shows the link's expiry for a scheduled
    # round tells the candidate a deadline that is not the one they have.
    exam_scheduled_at: str | None = None


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
         WHERE x.workflow_id = e.workflow_id AND x.deleted_at IS NULL) AS total_rounds,
       -- A live interview invitation for this applicant, if there is one.
       --
       -- Without this the page was actively misleading: an applicant who had
       -- been invited still read "Shortlisted · watch your email", because the
       -- invite does not move the enrolment status. The invitation existed only
       -- in an email, so a candidate who missed it had no way to learn of it.
       --
       -- 'invited' only — deliberately. A 'consumed' invite means the interview
       -- has already been started, and offering to start it again from here
       -- would be a second, misleading entry point; that path resumes from the
       -- candidate's own link or resume cookie.
       (SELECT i.id FROM interview_invites i
         WHERE i.applicant_id = a.id
           AND i.deleted_at IS NULL
           AND i.status = 'invited'
           AND i.expires_at > now()
         ORDER BY i.created_at DESC LIMIT 1) AS invite_id,
       (SELECT i.scheduled_at FROM interview_invites i
         WHERE i.applicant_id = a.id
           AND i.deleted_at IS NULL
           AND i.status = 'invited'
           AND i.expires_at > now()
         ORDER BY i.created_at DESC LIMIT 1) AS invite_scheduled_at,
       -- PH4-D4: an open task submission (job_simulation/portfolio), the
       -- task equivalent of the interview invite above.
       (SELECT t.id FROM task_submissions t
         WHERE t.enrolment_id = e.id AND t.superseded_at IS NULL
           AND t.status IN ('assigned', 'in_progress')
         ORDER BY t.created_at DESC LIMIT 1) AS task_id,
       (SELECT t.due_at FROM task_submissions t
         WHERE t.enrolment_id = e.id AND t.superseded_at IS NULL
           AND t.status IN ('assigned', 'in_progress')
         ORDER BY t.created_at DESC LIMIT 1) AS task_due_at,
       (SELECT t.status FROM task_submissions t
         WHERE t.enrolment_id = e.id AND t.superseded_at IS NULL
           AND t.status IN ('assigned', 'in_progress')
         ORDER BY t.created_at DESC LIMIT 1) AS task_status,
       exa.id           AS exam_assignment_id,
       exa.status       AS exam_status,
       exa.expires_at   AS exam_expires_at,
       exa.scheduled_at AS exam_scheduled_at
  FROM applicants a
  JOIN enrolments e     ON e.applicant_id = a.id AND e.deleted_at IS NULL
  JOIN companies c      ON c.id = e.company_id AND c.deleted_at IS NULL
  JOIN job_requisitions r ON r.id = e.requisition_id AND r.deleted_at IS NULL
  LEFT JOIN workflow_rounds wr
         ON wr.id = e.current_round_id AND wr.deleted_at IS NULL
  -- A live assessment for THIS application: matched on the enrolment, not the
  -- applicant, because one person applying to two openings can hold an exam
  -- for each, and each belongs on its own card.
  --
  -- 'invited' and 'started' both qualify. An exam, unlike an interview, is
  -- safe to re-enter mid-way: /exam/start resumes the attempt already open and
  -- keeps its original deadline, so a fresh link cannot reset anyone's clock.
  -- The round must be published, the exam not closed, and a scheduled round
  -- inside its join window — ALL the gates the exam page applies, so the page
  -- never offers a button that leads nowhere. That last one was missing, and
  -- the cost of missing it is not cosmetic: pressing the button rotates the
  -- token, and only the HMAC is stored, so an offer the exam page then refuses
  -- destroys the emailed link and hands back nothing.
  --
  -- The join window gates only the FIRST start (exam_take.start_attempt returns
  -- early for an attempt already open), so an in-progress attempt is exempt
  -- from it here too, exactly as it is there.
  --
  -- Matched on the enrolment, and corroborated on the applicant and the
  -- company. `enrolment_id` is a soft column behind a single-column FK with
  -- nothing tying it to an enrolment of the same person; the one writer sets
  -- both from the same row, so this is belt to that brace, for a future
  -- writer, a data repair or an applicant merge.
  LEFT JOIN LATERAL (
       SELECT ea.id, ea.status, ea.expires_at, ea.scheduled_at
         FROM exam_assignments ea
         JOIN exam_rounds er ON er.id = ea.round_id
                            AND er.status = 'published' AND er.deleted_at IS NULL
         JOIN exams ex       ON ex.id = ea.exam_id
                            AND ex.status <> 'closed' AND ex.deleted_at IS NULL
        WHERE ea.enrolment_id = e.id
          AND ea.applicant_id = a.id
          AND ea.company_id = e.company_id
          AND ea.deleted_at IS NULL
          AND ea.status IN ('invited', 'started')
          AND ea.expires_at > now()
          AND (ea.scheduled_at IS NULL
               OR (now() >= ea.scheduled_at
                   AND now() <= ea.scheduled_at + make_interval(mins => :join_window))
               OR EXISTS (SELECT 1 FROM exam_attempts at2
                           WHERE at2.round_id = ea.round_id
                             AND at2.applicant_id = ea.applicant_id
                             AND at2.company_id = ea.company_id
                             AND at2.status = 'in_progress'
                             AND at2.deleted_at IS NULL))
        ORDER BY ea.created_at DESC LIMIT 1
  ) exa ON true
 WHERE a.user_id = :uid
   AND a.deleted_at IS NULL
   {extra}
 ORDER BY e.created_at DESC
 LIMIT :lim
"""


def _next_step_for(row: Any, status_: str) -> str:
    """What the candidate should do, with a waiting invitation taking priority.

    A pending invite outranks the status wording because it is the one thing on
    this page the candidate can act on. Without it the row read "You are through
    to the assessment stage. Watch your email…" to somebody whose interview was
    already waiting — advice that was both stale and, if the mail never arrived,
    a dead end.
    """
    if getattr(row, "invite_id", None):
        return "Your interview is ready. Start it from here."
    if getattr(row, "task_id", None):
        return "Your task is ready. Open it from here."
    # The same reasoning for an assessment: "Watch your email for a link" is the
    # shortlisted wording, and a candidate reading it next to a working button
    # would be told to wait for something they can already do.
    if getattr(row, "exam_assignment_id", None):
        if getattr(row, "exam_status", None) == "started":
            return "Your assessment is in progress. Resume it from here."
        return "Your assessment is ready. Start it from here."
    return _NEXT_STEPS.get(status_, "The hiring team is reviewing your application.")


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
        "next_step": _next_step_for(row, status_),
        "closed": status_ in _CLOSED,
        "current_round_title": row.round_title,
        "current_round_kind": row.round_kind,
        "round_number": number,
        "total_rounds": row.total_rounds or None,
        "interview_invite_id": str(row.invite_id) if row.invite_id else None,
        "interview_scheduled_at": (
            row.invite_scheduled_at.isoformat() if row.invite_scheduled_at else None
        ),
        "task_submission_id": str(row.task_id) if row.task_id else None,
        "task_due_at": row.task_due_at.isoformat() if row.task_due_at else None,
        "task_status": row.task_status,
        "exam_assignment_id": str(row.exam_assignment_id) if row.exam_assignment_id else None,
        "exam_in_progress": row.exam_status == "started",
        "exam_expires_at": row.exam_expires_at.isoformat() if row.exam_expires_at else None,
        "exam_scheduled_at": (
            row.exam_scheduled_at.isoformat() if row.exam_scheduled_at else None
        ),
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
            {
                "uid": uuid.UUID(user.user_id),
                "lim": _MAX_APPLICATIONS,
                "join_window": settings.exam_join_window_minutes,
            },
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
            {
                "uid": uuid.UUID(user.user_id),
                "eid": application_id,
                "lim": 1,
                "join_window": settings.exam_join_window_minutes,
            },
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
_OPEN_ROLES_TEMPLATE = """
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
 WHERE {visible}
 ORDER BY r.created_at DESC
 LIMIT :lim
"""

# SAFE: the only substitution is visible_sql("r"), which returns a predicate
# assembled from module-level literals in app/publishing.py. Nothing a caller
# supplies reaches the statement text; every value below is a bound parameter.
_OPEN_ROLES_SQL = _OPEN_ROLES_TEMPLATE.format(visible=visible_sql("r"))  # nosec B608


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


# ---------------------------------------------------------------------------
# Getting into the interview from inside the account
# ---------------------------------------------------------------------------
class InterviewLinkOut(BaseModel):
    """A freshly minted link for an invitation this candidate owns."""

    interview_url: str
    expires_at: str


_ROTATE_SQL = """
UPDATE interview_invites i
   SET token_hash = :th, updated_at = now()
  FROM applicants a
 WHERE i.id = :invite_id
   AND i.applicant_id = a.id
   AND a.user_id = :uid
   AND a.deleted_at IS NULL
   AND i.deleted_at IS NULL
   AND i.status = 'invited'
   AND i.expires_at > now()
RETURNING i.expires_at
"""


@router.post(
    "/interviews/{invite_id}/link",
    response_model=InterviewLinkOut,
    summary="Mint a fresh link for an interview this candidate was invited to",
    # Every other credential-issuing route in this service carries one; these
    # two did not. Each call writes a new token_hash and retires the previous
    # link, so unbounded rotation is a cheap way to keep someone out of their
    # own interview. Note the limiter fails open on a Redis error by design —
    # a control to add, not a boundary to lean on.
    dependencies=[rate_limit_actor("candidate_interview_link", 6)],
)
async def mint_my_interview_link(
    invite_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> InterviewLinkOut:
    """Give the signed-in candidate a working link to their own interview.

    WHY THIS EXISTS. The invitation used to reach the candidate only as an
    email. When that email was filtered, mistyped, or caught by a local mail
    sink, the interview was unreachable — while looking perfectly healthy from
    the HR side, because the invite really had been minted and really had been
    queued for delivery. This is the path that does not depend on mail.

    WHY IT ROTATES THE TOKEN. Only the HMAC of the token is stored, never the
    token itself, so an existing link cannot be re-read and shown again. That
    property is worth keeping, so instead of weakening it the invite gets a NEW
    token and the old link stops working. One invitation, one live link.

    WHY THIS IS NOT A SECOND REDEMPTION PATH. It hands back a link and nothing
    else. Redemption — the join window, consent, guest provisioning, the
    reconnect branch, the row lock — stays in `interview_take.redeem_invite`,
    unchanged and still the only way in. Two code paths into an interview is how
    they drift apart, and one of them ends up missing a check.

    The UPDATE authorises in its own WHERE clause: it matches only when the
    invite belongs to an applicant carrying this user's id. A 404 covers "no
    such invite", "not yours", "already started" and "expired" alike, so it
    cannot be used to discover which invites exist.
    """
    row = (
        await db.execute(
            text(_ROTATE_SQL),
            {
                "th": hash_interview_token(raw := mint_interview_token(), settings.interview_link_secret),
                "invite_id": invite_id,
                "uid": user.user_id,
            },
        )
    ).first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No interview invitation is waiting on this application.",
        )

    # The same immutable record as the exam mint below, and for the same
    # reason: a credential was issued for a scored round, and "who asked for
    # it" must not be answerable only by a log line a retention sweep removes.
    await db.execute(
        text(
            "INSERT INTO audit_log "
            "(actor_id, actor_type, action, resource_type, resource_id, details) "
            "VALUES (:aid, 'candidate', 'candidate.interview_link.minted',"
            " 'interview_invite', :rid, NULL)"
        ),
        {"aid": uuid.UUID(user.user_id), "rid": invite_id},
    )
    await db.commit()
    # The actor too: without it a log line cannot say who minted.
    log.info(
        "candidate.interview_link.minted", invite_id=str(invite_id), user_id=user.user_id
    )

    base = settings.interview_link_base_url.rstrip("/")
    return InterviewLinkOut(
        # Fragment, not query string: the token never reaches a server log,
        # a proxy, or a Referer header. Same shape the emailed link uses.
        interview_url=f"{base}/interview-invite#{raw}",
        expires_at=row.expires_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Getting into the assessment from inside the account
# ---------------------------------------------------------------------------
class ExamLinkIn(BaseModel):
    """What the candidate is confirming, if anything.

    ``resume_anyway`` is their answer to the one question this endpoint cannot
    answer for them: whether the assessment open in another tab is one they
    still want. Minting rotates the token, so it silently kills that tab —
    every call there 404s from then on while the clock keeps running. Default
    false, so a stray second press, a back button or a second tab on this page
    cannot cost someone a timed assessment.
    """

    resume_anyway: bool = False


class ExamLinkOut(BaseModel):
    """A freshly minted link for an assessment this candidate owns."""

    exam_url: str
    expires_at: str


# Read-only, and run BEFORE the rotation. The rule it exists to keep: never
# rotate ahead of the gate that decides whether the new link will work. Only
# the HMAC is stored, so a rotation cannot be undone — handing out a link the
# exam page then refuses costs the candidate the emailed one and gives them
# nothing back.
#
# It carries the same gates as the UPDATE below, which remains the authority:
# authorisation lives in that WHERE, and this decides only which refusal the
# candidate reads.
_EXAM_LINK_PRECHECK_SQL = """
SELECT ea.id,
       ea.scheduled_at,
       (ea.scheduled_at IS NULL
        OR (now() >= ea.scheduled_at
            AND now() <= ea.scheduled_at + make_interval(mins => :join_window))) AS in_window,
       EXISTS (SELECT 1 FROM exam_attempts at2
                WHERE at2.round_id = ea.round_id
                  AND at2.applicant_id = ea.applicant_id
                  AND at2.company_id = ea.company_id
                  AND at2.status = 'in_progress'
                  AND at2.deleted_at IS NULL) AS attempt_open
  FROM exam_assignments ea
  JOIN applicants a ON a.id = ea.applicant_id AND a.deleted_at IS NULL
 WHERE ea.id = :assignment_id
   AND a.user_id = :uid
   AND ea.deleted_at IS NULL
   AND ea.status IN ('invited', 'started')
   AND ea.expires_at > now()
   AND EXISTS (SELECT 1 FROM exam_rounds er
                WHERE er.id = ea.round_id
                  AND er.status = 'published' AND er.deleted_at IS NULL)
   AND EXISTS (SELECT 1 FROM exams ex
                WHERE ex.id = ea.exam_id
                  AND ex.status <> 'closed' AND ex.deleted_at IS NULL)
"""


# Authorises in its own WHERE, as _ROTATE_SQL does for interviews: it matches
# only an assignment whose applicant carries this user's id. The round, exam
# and join-window gates repeat the list query's, so a button the page offers
# and a link this hands out cannot disagree about whether the assessment is
# open.
_ROTATE_EXAM_SQL = """
UPDATE exam_assignments ea
   SET token_hash = :th, updated_at = now()
  FROM applicants a
 WHERE ea.id = :assignment_id
   AND ea.applicant_id = a.id
   AND a.user_id = :uid
   AND a.deleted_at IS NULL
   AND ea.deleted_at IS NULL
   AND ea.status IN ('invited', 'started')
   AND ea.expires_at > now()
   AND (ea.scheduled_at IS NULL
        OR (now() >= ea.scheduled_at
            AND now() <= ea.scheduled_at + make_interval(mins => :join_window))
        OR EXISTS (SELECT 1 FROM exam_attempts at2
                    WHERE at2.round_id = ea.round_id
                      AND at2.applicant_id = ea.applicant_id
                      AND at2.company_id = ea.company_id
                      AND at2.status = 'in_progress'
                      AND at2.deleted_at IS NULL))
   AND EXISTS (SELECT 1 FROM exam_rounds er
                WHERE er.id = ea.round_id
                  AND er.status = 'published' AND er.deleted_at IS NULL)
   AND EXISTS (SELECT 1 FROM exams ex
                WHERE ex.id = ea.exam_id
                  AND ex.status <> 'closed' AND ex.deleted_at IS NULL)
RETURNING ea.expires_at
"""


@router.post(
    "/exams/{assignment_id}/link",
    response_model=ExamLinkOut,
    summary="Mint a fresh link for an assessment this candidate was sent",
    dependencies=[rate_limit_actor("candidate_exam_link", 6)],
)
async def mint_my_exam_link(
    assignment_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
    body: ExamLinkIn | None = None,
) -> ExamLinkOut:
    """Give the signed-in candidate a working link to their own assessment.

    WHY THIS EXISTS. Exactly the gap the interview link above closed: the
    assessment reached the candidate only as an email, so one that was filtered
    or mistyped left an exam that existed, showed on this page by name, and
    could not be opened. This is the path that does not depend on mail.

    WHY IT ROTATES THE TOKEN. Only the HMAC is stored, so the emailed link
    cannot be read back and shown again. The assignment gets a new token and the
    old link stops working: one assessment, one live link.

    WHY 'started' IS ALLOWED HERE WHEN THE INTERVIEW VERSION REFUSES IT. An
    interview is a live session with a join window and a resume cookie; an
    assessment is an attempt that /exam/start RESUMES — same attempt, original
    deadline. So a candidate who began from the email and lost the tab can come
    back here without gaining a minute.

    AND WHY THAT NEEDS CONFIRMING. Rotating kills whatever tab already holds
    the old token: every call from it 404s while the attempt's clock keeps
    running, so a late submit grades as expired. Losing a timed assessment must
    not be one stray press away, and a second tab on this page, a back button
    or a double click would all have been exactly that. So an assessment with
    an attempt already open returns 409 unless the caller says
    ``resume_anyway``, which the page asks as a question first.

    NOT A SECOND WAY IN. It hands back a link and nothing else. The attempt
    rules and the grading stay in exam_take, unchanged and still the only way
    in. The gates it can be refused by — published round, open exam, live
    assignment, and the join window for a scheduled round — are checked here
    too, and deliberately BEFORE the rotation: only the HMAC is stored, so a
    rotation cannot be undone, and handing out a link the exam page then
    refuses would cost the candidate their emailed one for nothing.

    One thing exam_take does NOT do, despite what this said before: enforce
    consent. The assessment's consent tick is browser-only — ``/exam/start``
    takes no consent argument and writes no ledger row. That is a pre-existing
    gap, not one this route opens (minting a link collects nothing), but it is
    not a gate to point at.

    One 404 for "no such assignment", "not yours", "finished", "expired" and
    "not open", so it cannot be used to discover which assessments exist. The
    409 and the closed-window 403 are reachable only by the owner, who has
    already been matched on ``applicants.user_id``.
    """
    body = body or ExamLinkIn()
    params: dict[str, Any] = {
        "assignment_id": assignment_id,
        "uid": user.user_id,
        "join_window": settings.exam_join_window_minutes,
    }

    check = (await db.execute(text(_EXAM_LINK_PRECHECK_SQL), params)).first()
    if check is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment is waiting on this application.",
        )

    # The join window gates the FIRST start only, so an attempt already open is
    # exempt — the same rule, and the same order, as exam_take.start_attempt.
    if not check.in_window and not check.attempt_open:
        sched = check.scheduled_at
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This round opens at {sched.isoformat()}."
                if sched and datetime.now(tz=UTC) < sched
                else "The scheduled join window for this round has closed."
            ),
        )

    if check.attempt_open and not body.resume_anyway:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This assessment is already open in another tab. Opening it here "
                "will close it there — your answers so far and your time remaining "
                "are unaffected."
            ),
        )

    row = (
        await db.execute(
            text(_ROTATE_EXAM_SQL),
            {
                **params,
                "th": hash_exam_token(raw := mint_exam_token(), settings.exam_link_secret),
            },
        )
    ).first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment is waiting on this application.",
        )

    # Append-only, and written in the same transaction as the rotation. A
    # credential was issued for a scored assessment: after an incident there
    # has to be a record of who asked for it and when, and a log line that a
    # retention sweep can remove is not that record.
    await db.execute(
        text(
            "INSERT INTO audit_log "
            "(actor_id, actor_type, action, resource_type, resource_id, details) "
            "VALUES (:aid, 'candidate', 'candidate.exam_link.minted',"
            " 'exam_assignment', :rid, CAST(:details AS jsonb))"
        ),
        {
            "aid": uuid.UUID(user.user_id),
            "rid": assignment_id,
            # No token, raw or hashed. `resumed` records that the candidate was
            # told an attempt was open and chose to take it over anyway.
            "details": json.dumps({"resumed": bool(check.attempt_open)}),
        },
    )
    await db.commit()
    # The actor and the assignment — never the token, raw or hashed. Without
    # the actor a log line cannot say who minted, which is the one question an
    # incident asks first.
    log.info(
        "candidate.exam_link.minted",
        assignment_id=str(assignment_id),
        user_id=user.user_id,
        resumed=bool(check.attempt_open),
    )

    base = settings.exam_link_base_url.rstrip("/")
    return ExamLinkOut(
        # Fragment, not query string, for the same reason as the interview link.
        exam_url=f"{base}/exam#{raw}",
        expires_at=row.expires_at.isoformat(),
    )
