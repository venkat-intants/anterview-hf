"""Self-serve onboarding + the personal practice plan.

The SELF-SERVE journey: a student practising on their own, as opposed to an
applicant an HR manager invited. They register, tell us who they are and what
they are aiming for, and their dashboard becomes a practice plan built from
the role engine's competencies for that role.

  GET   /users/me/onboarding       — status + answers (drives the wizard)
  POST  /users/me/onboarding       — save answers, mark complete
  POST  /users/me/onboarding/skip  — dismiss; dashboard shows a nudge instead
  GET   /users/me/practice-plan    — competencies for their target role, with
                                     their own progress across past mocks

Mounted under ``/users/me`` rather than a new ``/me`` prefix on purpose: the
Space's Caddy already routes ``/users/*`` to data_gateway and the self-service
resume endpoints already live at ``/users/me/resume``. A fresh top-level
prefix would need a new reverse-proxy rule in TWO Caddyfiles, and forgetting
one silently serves the SPA's HTML to a JSON fetch — a failure this project
has already had once.

Scope: every endpoint acts on ``current_user`` only. There is no user_id
parameter anywhere in this router, so it cannot be pointed at somebody else.

The practice plan is the payoff for the whole intelligence layer on the
consumer side: the same ``RoleProfile`` that plans the interview questions and
weights the scorecard is what the student sees themselves being measured
against.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator
from shared.auth.base import User
from shared.intelligence import (
    RoleProfile,
    baseline_profile,
    compute_profile_id,
    plan_interview,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/users/me", tags=["onboarding"])

CurrentUserDep = Annotated[User, Depends(get_current_user)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]

# Why they are practising. Shapes the dashboard's nudge copy, and is worth
# collecting because "campus placement in 3 weeks" and "switching fields" want
# very different encouragement. Stored as an opaque string; unknown values are
# rejected so the UI can rely on the set.
GOALS: frozenset[str] = frozenset(
    {
        "campus_placement",
        "first_job",
        "switching_field",
        "interview_soon",
        "general_practice",
    }
)

LEVELS: frozenset[str] = frozenset({"entry", "mid", "senior"})
LANGUAGES: frozenset[str] = frozenset({"en", "hi", "te"})

# Roles that never see the wizard. HR managers and admins have their own
# consoles; interrupting them with "what job are you practising for?" would be
# nonsense. Checked against the user's role set, not their company, because a
# platform owner has no company_id.
_PRIVILEGED_ROLES: frozenset[str] = frozenset(
    {"hr_manager", "super_admin", "platform_owner", "admin"}
)

# How many past mock interviews feed the plan. Enough to show a trend without
# an unbounded scan for a heavy user.
_MAX_SESSIONS_SCANNED: int = 50


def _is_self_serve(user: User) -> bool:
    """True when this user should be offered the wizard."""
    return not (set(getattr(user, "roles", []) or []) & _PRIVILEGED_ROLES)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class OnboardingStatus(BaseModel):
    """What the wizard needs to decide whether to show, and how to prefill."""

    # False for HR/admin — the frontend never routes them to the wizard.
    applicable: bool
    # True once completed OR skipped. The first-login redirect fires only when
    # this is False, so the wizard interrupts exactly once.
    seen: bool
    completed: bool
    skipped: bool
    full_name: str | None = None
    goal: str | None = None
    target_role: str | None = None
    target_level: str | None = None
    preferred_language: str = "en"


class OnboardingSubmit(BaseModel):
    """Wizard answers. Everything except the role is optional/defaulted."""

    full_name: str | None = Field(default=None, max_length=120)
    goal: str | None = Field(default=None, max_length=32)
    target_role: str = Field(min_length=2, max_length=200)
    target_level: str = Field(default="entry", max_length=16)
    preferred_language: str = Field(default="en", max_length=8)

    @field_validator("target_role")
    @classmethod
    def _clean_role(cls, v: str) -> str:
        role = " ".join(v.split()).strip()
        if not role:
            raise ValueError("target_role must not be blank")
        return role

    @field_validator("goal")
    @classmethod
    def _validate_goal(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in GOALS:
            raise ValueError(f"goal must be one of {sorted(GOALS)}")
        return v

    @field_validator("target_level")
    @classmethod
    def _validate_level(cls, v: str) -> str:
        return v if v in LEVELS else "entry"

    @field_validator("preferred_language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        return v if v in LANGUAGES else "en"


class CompetencyProgress(BaseModel):
    """One competency on the plan, with this user's own history against it."""

    id: str
    name: str
    kind: str
    # Share of the interview this competency gets for this role.
    weight: float
    # What "adequate" looks like — shown so the target is concrete rather than
    # a bare number the student cannot act on.
    target: str
    # Mean of every score this user has received on this competency, 0-10.
    # None when no past interview ever probed it — NOT zero. "Never practised"
    # and "practised badly" are different facts and must look different.
    score: float | None = None
    attempts: int = 0
    # Most recent score minus the one before it. None with fewer than two.
    trend: float | None = None


class PracticePlan(BaseModel):
    """The self-serve dashboard's centrepiece."""

    # False when they have not set a target role yet — the frontend shows the
    # "personalize your practice" nudge instead of an empty plan.
    ready: bool
    target_role: str | None = None
    target_level: str | None = None
    domain_family: str | None = None
    domain_label: str | None = None
    what_this_person_does: str | None = None
    competencies: list[CompetencyProgress] = Field(default_factory=list)
    # Weighted mean of the competencies that have a score, 0-100. None until
    # they have completed at least one mock interview.
    readiness: float | None = None
    interviews_completed: int = 0
    last_interview_at: datetime | None = None
    # The lowest-scoring competency that has been probed — what to practise
    # next. None when nothing has been scored yet.
    focus_competency_id: str | None = None
    focus_competency_name: str | None = None
    # Competencies the plan covers that no interview has touched yet.
    never_probed: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_row(db: AsyncSession, user_id: str) -> Any:
    return (
        await db.execute(
            text(
                """
                SELECT full_name, preferred_language, onboarding_goal,
                       target_role, target_level,
                       onboarding_completed_at, onboarding_skipped_at
                FROM users
                WHERE id = CAST(:uid AS uuid) AND deleted_at IS NULL
                """
            ),
            {"uid": user_id},
        )
    ).first()


def _profile_for(target_role: str, target_level: str) -> RoleProfile:
    """Deterministic role model for a practice plan.

    Taxonomy baseline only — no LLM call. This runs on every dashboard load,
    so an LLM round-trip would put a multi-second stall in front of the
    student's home screen for a refinement they cannot see. The interview
    itself still derives the refined profile when a key is configured.
    """
    level = target_level if target_level in LEVELS else "entry"
    return baseline_profile(
        profile_id=compute_profile_id(job_title=target_role, seniority=level),
        job_title=target_role,
        seniority=level,  # type: ignore[arg-type]
    )


async def _competency_history(
    db: AsyncSession, user_id: str
) -> tuple[dict[str, list[float]], int, datetime | None]:
    """Per-competency score history for one user, oldest first.

    Reads the ``_competencies`` breakdown the scorer writes into
    ``scorecards.rationale`` (nested JSONB — see scorer.py). Sessions are the
    join: a self-serve mock interview is a ``sessions`` row owned by the user.

    Returns (scores_by_competency_id, interviews_completed, last_interview_at).
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT sc.rationale, sc.created_at
                FROM sessions s
                JOIN scorecards sc ON sc.session_id = s.id
                WHERE s.user_id = CAST(:uid AS uuid)
                ORDER BY sc.created_at ASC
                LIMIT :limit
                """
            ),
            {"uid": user_id, "limit": _MAX_SESSIONS_SCANNED},
        )
    ).all()

    history: dict[str, list[float]] = {}
    last_at: datetime | None = None
    for row in rows:
        last_at = row.created_at
        rationale = row.rationale
        if not isinstance(rationale, dict):
            continue
        comps = rationale.get("_competencies")
        if not isinstance(comps, dict):
            # Either a scorecard written before the intelligence layer, or one
            # whose interview had no role profile. Counts as an interview but
            # contributes no per-competency evidence.
            continue
        for cid, entry in comps.items():
            if not isinstance(entry, dict):
                continue
            # Convert BEFORE inserting. setdefault() first would leave an empty
            # list behind for an unparseable score, which then reads downstream
            # as "this competency exists in the history with no attempts" —
            # harmless today but exactly the kind of phantom key that makes a
            # later aggregation quietly wrong.
            try:
                score = float(entry.get("score", 0))
            except (TypeError, ValueError):
                continue
            history.setdefault(str(cid), []).append(score)

    return history, len(rows), last_at


def _build_plan(
    profile: RoleProfile,
    history: dict[str, list[float]],
    *,
    target_role: str,
    target_level: str,
    interviews: int,
    last_at: datetime | None,
) -> PracticePlan:
    """Merge the role model with the user's own history. Pure — easy to test."""
    competencies: list[CompetencyProgress] = []
    never: list[str] = []

    for comp in profile.competencies:
        scores = history.get(comp.id, [])
        mean = round(sum(scores) / len(scores), 1) if scores else None
        trend = round(scores[-1] - scores[-2], 1) if len(scores) >= 2 else None
        if not scores:
            never.append(comp.name)
        competencies.append(
            CompetencyProgress(
                id=comp.id,
                name=comp.name,
                kind=comp.kind,
                weight=comp.weight,
                target=comp.anchors.mid,
                score=mean,
                attempts=len(scores),
                trend=trend,
            )
        )

    scored = [c for c in competencies if c.score is not None]
    readiness: float | None = None
    if scored:
        # Re-normalise across only the SCORED competencies. Weighting against
        # the full set would drag readiness down purely because some
        # competencies have not come up yet, which reads as "you got worse"
        # when the student has simply not practised them.
        total_weight = sum(c.weight for c in scored) or 1.0
        readiness = round(
            sum((c.score or 0) * c.weight for c in scored) / total_weight * 10.0, 1
        )

    focus = min(scored, key=lambda c: c.score or 0.0) if scored else None

    return PracticePlan(
        ready=True,
        target_role=target_role,
        target_level=target_level,
        domain_family=profile.domain_family,
        domain_label=profile.domain_label,
        what_this_person_does=profile.summary,
        competencies=competencies,
        readiness=readiness,
        interviews_completed=interviews,
        last_interview_at=last_at,
        focus_competency_id=focus.id if focus else None,
        focus_competency_name=focus.name if focus else None,
        never_probed=never,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/onboarding",
    response_model=OnboardingStatus,
    summary="Whether to show the onboarding wizard, and what to prefill",
)
async def get_onboarding(user: CurrentUserDep, db: DbSessionDep) -> OnboardingStatus:
    row = await _load_row(db, str(user.user_id))
    applicable = _is_self_serve(user)

    if row is None:
        # Authenticated but no row (soft-deleted mid-session). Report "seen" so
        # the frontend does not trap them in a wizard that cannot save.
        return OnboardingStatus(applicable=False, seen=True, completed=False, skipped=False)

    completed = row.onboarding_completed_at is not None
    skipped = row.onboarding_skipped_at is not None
    return OnboardingStatus(
        applicable=applicable,
        seen=completed or skipped,
        completed=completed,
        skipped=skipped,
        full_name=row.full_name,
        goal=row.onboarding_goal,
        target_role=row.target_role,
        target_level=row.target_level,
        preferred_language=row.preferred_language or "en",
    )


@router.post(
    "/onboarding",
    response_model=OnboardingStatus,
    status_code=status.HTTP_200_OK,
    summary="Save onboarding answers and mark it complete",
)
async def submit_onboarding(
    body: OnboardingSubmit, user: CurrentUserDep, db: DbSessionDep
) -> OnboardingStatus:
    """Persist the wizard answers.

    Re-runnable: a user editing their target role later hits this same
    endpoint, which re-stamps completed_at and clears any prior skip. That is
    intentional — "I skipped, then changed my mind" must land in the same
    state as "I completed it first time".
    """
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            """
            UPDATE users SET
                full_name = COALESCE(:full_name, full_name),
                preferred_language = :language,
                onboarding_goal = :goal,
                target_role = :role,
                target_level = :level,
                onboarding_completed_at = :now,
                onboarding_skipped_at = NULL,
                updated_at = :now
            WHERE id = CAST(:uid AS uuid) AND deleted_at IS NULL
            """
        ),
        {
            "full_name": body.full_name,
            "language": body.preferred_language,
            "goal": body.goal,
            "role": body.target_role,
            "level": body.target_level,
            "now": now,
            "uid": str(user.user_id),
        },
    )
    await db.commit()

    # Log the CLASSIFICATION, never the raw role text — a free-text field a
    # user typed is their data, and the family is what we actually want for
    # product analytics.
    profile = _profile_for(body.target_role, body.target_level)
    log.info(
        "onboarding.completed",
        user_id=str(user.user_id),
        goal=body.goal,
        domain_family=profile.domain_family,
        level=body.target_level,
        language=body.preferred_language,
    )

    return await get_onboarding(user, db)


@router.post(
    "/onboarding/skip",
    response_model=OnboardingStatus,
    status_code=status.HTTP_200_OK,
    summary="Dismiss onboarding; the dashboard shows a nudge card instead",
)
async def skip_onboarding(user: CurrentUserDep, db: DbSessionDep) -> OnboardingStatus:
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            """
            UPDATE users
            SET onboarding_skipped_at = :now, updated_at = :now
            WHERE id = CAST(:uid AS uuid) AND deleted_at IS NULL
              AND onboarding_completed_at IS NULL
            """
        ),
        {"now": now, "uid": str(user.user_id)},
    )
    await db.commit()
    log.info("onboarding.skipped", user_id=str(user.user_id))
    return await get_onboarding(user, db)


@router.get(
    "/practice-plan",
    response_model=PracticePlan,
    summary="Personal practice plan: role competencies + this user's progress",
)
async def get_practice_plan(user: CurrentUserDep, db: DbSessionDep) -> PracticePlan:
    """Return the plan, or ``ready=False`` when there is no target role yet.

    Deliberately useful on day zero: before any mock interview the plan still
    lists every competency with ``score=None`` and ``attempts=0``, so a brand
    new student can see exactly what they are about to be assessed on. An
    empty dashboard would waste the one moment they are most motivated.
    """
    row = await _load_row(db, str(user.user_id))
    if row is None or not row.target_role:
        return PracticePlan(ready=False)

    profile = _profile_for(row.target_role, row.target_level or "entry")
    history, interviews, last_at = await _competency_history(db, str(user.user_id))

    plan = _build_plan(
        profile,
        history,
        target_role=row.target_role,
        target_level=row.target_level or "entry",
        interviews=interviews,
        last_at=last_at,
    )
    log.info(
        "practice_plan.served",
        user_id=str(user.user_id),
        domain_family=profile.domain_family,
        interviews=interviews,
        readiness=plan.readiness,
    )
    return plan


@router.get(
    "/practice-plan/preview",
    response_model=PracticePlan,
    summary="Preview the plan for a role without saving it (wizard step 3)",
)
async def preview_practice_plan(
    user: CurrentUserDep,
    target_role: str,
    target_level: str = "entry",
) -> PracticePlan:
    """What the plan WOULD look like for a role.

    Powers the live "→ Mechanical & Manufacturing ✓" confirmation as the
    student types their target role, so they can tell immediately whether we
    understood the job. Pure computation — no DB read, no write, no history.
    """
    role = " ".join(target_role.split()).strip()
    if len(role) < 2:
        return PracticePlan(ready=False)

    profile = _profile_for(role, target_level)
    return _build_plan(
        profile,
        {},
        target_role=role,
        target_level=target_level if target_level in LEVELS else "entry",
        interviews=0,
        last_at=None,
    )


@router.get(
    "/practice-plan/next-interview",
    summary="The question plan the next mock interview would follow",
)
async def next_interview_shape(user: CurrentUserDep, db: DbSessionDep) -> dict[str, Any]:
    """A preview of what the next mock interview will ask about.

    Shown on the dashboard so practice never feels like a black box: the
    student can see the competency behind each of the ten questions before
    they start.
    """
    row = await _load_row(db, str(user.user_id))
    if row is None or not row.target_role:
        return {"ready": False, "turns": []}

    profile = _profile_for(row.target_role, row.target_level or "entry")
    plans = plan_interview(profile, 10)
    return {
        "ready": True,
        "target_role": row.target_role,
        "turns": [
            {
                "turn": p.turn_index,
                "kind": p.kind,
                "competency": p.competency_name,
            }
            for p in plans
        ],
    }
