"""Agent endpoints — console copilots and the assessment panel.

Three routes:

  POST /agent/chat                    talk to your console's copilot
                                      (optionally on a specialised surface)
  POST /agent/panel/{applicant_id}    run the specialist panel on a candidate
  GET  /agent/status                  is the assistant available at all

Authorisation is the ordinary dependency stack, not something the agent layer
reimplements: the caller's role comes from their verified token and their
company from the users table, and both are injected into ``ToolContext``. The
model never supplies either, so no prompt can widen scope.

Nothing here mutates. The copilot returns ``proposals``; the console renders
them for review and, on approval, fires each ``commit`` itself with the user's
own credentials against the normal HR endpoints — same authorisation, same
audit trail as doing it by hand.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from shared.agents import (
    CROSS_TENANT_ROLES,
    AgentMessage,
    ToolContext,
    UnknownConsoleError,
    assess_candidate,
    available_consoles,
    available_surfaces,
    build_agent,
    run_agent,
)
from shared.auth.base import User
from sqlalchemy import text

# Imported for its side effect: the module's @registry.tool decorators run on
# import, so without this line the workflow-builder surface gets a system
# prompt describing tools that were never registered. Imported here in the
# router that assembles the toolset rather than from a package __init__, where
# a reader looking for "what tools exist?" would not find it.
from app.agents import workflow_tools as _workflow_tools  # noqa: F401
from app.agents.evidence import (
    ApplicantNotFoundError,
    apply_document_warnings,
    gather_candidate_evidence,
)
from app.agents.llm import build_agent_llm, build_panel_llm, describe_availability
from app.agents.tools import registry
from app.config import settings
from app.database import get_db_session
from app.dependencies import get_current_user

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

DbSessionDep = Annotated[Any, Depends(get_db_session)]
UserDep = Annotated[User, Depends(get_current_user)]

# How much prior conversation the client may replay. Long histories are the
# main driver of agent cost, and beyond a handful of turns a console copilot is
# better served by the user restating what they want.
MAX_HISTORY_TURNS: int = 12
MAX_MESSAGE_CHARS: int = 2000

# Roles that live above the tenant boundary and therefore carry no company_id.
# `admin` is the platform ANALYTICS role, not a tenant one — seed_admin.py and
# grant_admin.py both create it with users.company_id NULL — so demanding a
# company for it made the analytics copilot 403 on every message and left
# get_score_distribution (its only tool, an un-scoped aggregate) unreachable.
#
# Sourced from shared/agents/schema.py rather than redeclared: the same set
# decides which tools are data_class="platform_aggregate", and a local copy
# that drifted from it would hand a tenant role a cross-tenant toolset.
PLATFORM_ROLES: frozenset[str] = CROSS_TENANT_ROLES

_AGENT_DISABLED = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="The assistant is disabled for this deployment.",
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class HistoryTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(max_length=MAX_MESSAGE_CHARS)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)
    # Which screen is asking. Selects a specialised system prompt; it does NOT
    # widen the toolset, which stays filtered by ToolSpec.allowed_roles against
    # the caller's real role.
    surface: str | None = Field(default=None, max_length=64)
    # What that screen is looking at — e.g. {"requisition_id": "..."} for the
    # workflow builder. Validated and injected into ToolContext.resources
    # below, so tools read the subject from ctx and the model cannot name a
    # different one.
    surface_context: dict[str, str] = Field(default_factory=dict)


class ChatOut(BaseModel):
    agent: str
    reply: str
    proposals: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    # Which tools ran, with no payloads — enough for the console to show "read
    # 12 applicants, 1 scorecard" without shipping candidate data twice.
    tools_used: list[dict[str, Any]]
    stop_reason: str


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


async def _agent_context(user: User, db: Any) -> ToolContext:
    """Build a ``ToolContext`` from the verified session.

    The company lookup is a fresh DB read rather than a token claim: a user
    moved between companies (or removed from one) must lose access on their
    next request, not whenever their token happens to expire.
    """
    role = _primary_role(user)

    # Read the company on EVERY path, including the platform roles. The old
    # version skipped the query for platform roles and simply assumed NULL,
    # which made "is this really a platform account?" an article of faith
    # rather than a check — and the platform tools are the un-scoped ones. A
    # company user who also held `admin` (a grant script away, and the kind of
    # thing that happens when someone is debugging) resolved to a NULL
    # company_id and could read every tenant's score data through
    # get_score_distribution.
    raw = await db.scalar(
        text("SELECT company_id FROM users WHERE id = CAST(:uid AS uuid) AND deleted_at IS NULL"),
        {"uid": user.user_id},
    )

    if role in PLATFORM_ROLES:
        if raw is not None:
            # A cross-tenant console demands an account that belongs to no
            # tenant. Fail closed rather than quietly downgrading to a
            # company-scoped context: the caller asked for a console their
            # account is not entitled to, and silently giving them a different
            # one hides a misconfigured grant instead of surfacing it.
            log.warning(
                "agent.context.platform_role_with_company",
                actor_id=str(user.user_id),
                role=role,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This console is for platform accounts. Your account "
                    "belongs to a company."
                ),
            )
        company_id: str | None = None
    else:
        if raw is None:
            # Fail closed. A scoped role with no company would otherwise reach
            # handlers with company_id=None, and any handler that failed to
            # filter would leak across tenants.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account is not assigned to a company.",
            )
        company_id = str(raw)

    return ToolContext(
        actor_id=str(user.user_id),
        role=role,
        company_id=company_id,
        resources={"db": db, "settings": settings},
    )


def _primary_role(user: User) -> str:
    """Pick the console this user's copilot should be.

    Most-privileged wins, matching how the frontend chooses a landing console.
    """
    roles = set(getattr(user, "roles", []) or [])
    for candidate in ("platform_owner", "super_admin", "hr_manager", "admin"):
        if candidate in roles:
            return candidate
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Your role does not have an assistant.",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status")
async def agent_status(user: UserDep) -> dict[str, Any]:
    """Whether the assistant is usable, and as which console."""
    availability = describe_availability()
    try:
        role = _primary_role(user)
    except HTTPException:
        role = ""
    # `enabled` must depend on the CALLER's role, not just on global config.
    # Without the role terms this reported the assistant as enabled for every
    # authenticated user, and the frontend gates purely on this flag
    # (CopilotLauncher.tsx: `if (!status?.enabled) return null`) while AppShell
    # mounts the launcher on candidate pages too — so a candidate saw an "Ask
    # assistant" button whose every message 403'd from _primary_role.
    has_console = bool(role) and role in available_consoles()
    return {
        "enabled": settings.agents_enabled and availability["configured"] and has_console,
        "model_configured": availability["configured"],
        "console": role,
        # Specialised screens this account may open a copilot on. The client
        # checks this before offering one, so a surface removed server-side
        # stops being offered rather than failing on first message.
        "surfaces": available_surfaces(role) if role else [],
        # Stated in the API so a client cannot present the copilot as more
        # capable than it is.
        "capabilities": ["read", "draft"],
        "note": "The assistant can read records and draft actions. Every action needs your approval.",
    }


async def _bind_surface(ctx: ToolContext, surface: str, given: dict[str, str], db: Any) -> None:
    """Validate what a specialised surface is looking at, and inject it.

    The subject of the conversation goes into ``ctx.resources``, NOT into tool
    arguments, and is checked against the caller's company before it gets
    there. That keeps the layer's central invariant intact — scope comes from
    the context, never from the model — and makes "design a workflow for
    somebody else's opening" unaskable rather than merely refused: there is no
    parameter through which another opening could be named.
    """
    if surface != "workflow_builder":
        return

    raw = (given.get("requisition_id") or "").strip()
    try:
        requisition_id = uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This assistant needs the opening it is designing for.",
        ) from exc

    owned = await db.scalar(
        text(
            "SELECT 1 FROM job_requisitions"
            " WHERE id = :r AND company_id = CAST(:c AS uuid) AND deleted_at IS NULL"
        ),
        {"r": requisition_id, "c": ctx.company_id},
    )
    if owned is None:
        # 404, not 403: another tenant's opening must be indistinguishable from
        # one that does not exist, exactly as the REST layer treats it.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Opening not found.")

    ctx.resources["requisition_id"] = str(requisition_id)


@router.post("/chat", response_model=ChatOut)
async def agent_chat(body: ChatIn, user: UserDep, db: DbSessionDep) -> ChatOut:
    """Ask this console's copilot a question."""
    if not settings.agents_enabled:
        raise _AGENT_DISABLED

    ctx = await _agent_context(user, db)

    if body.surface and body.surface not in available_surfaces(ctx.role):
        # The (role, surface) pair is checked here as well as in build_agent,
        # so a client naming a surface written for another console gets a plain
        # refusal rather than an UnknownConsoleError rendered as "your role has
        # no assistant", which is a different and misleading claim.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="That assistant is not available for your role.",
        )
    if body.surface:
        await _bind_surface(ctx, body.surface, body.surface_context, db)

    try:
        spec = build_agent(ctx.role, registry, body.surface)
    except UnknownConsoleError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role does not have an assistant.",
        ) from exc

    history = [
        AgentMessage(role=turn.role, text=turn.text)  # type: ignore[arg-type]  # pattern-validated
        for turn in body.history[-MAX_HISTORY_TURNS:]
    ]

    run = await run_agent(spec, ctx, body.message, llm=build_agent_llm(), history=history)

    log.info(
        "agent.chat",
        actor_id=ctx.actor_id,
        role=ctx.role,
        agent=run.agent,
        surface=body.surface,
        steps=run.steps_used,
        proposals=len(run.proposals),
        stop_reason=run.stop_reason,
        # NEVER log the message or the reply — both carry candidate PII.
    )

    return ChatOut(
        agent=run.agent,
        reply=run.reply,
        proposals=[p.model_dump(mode="json") for p in run.proposals],
        citations=[c.model_dump(mode="json") for c in run.citations],
        tools_used=[
            {"name": t.name, "ok": t.ok, "duration_ms": t.duration_ms} for t in run.trace
        ],
        stop_reason=run.stop_reason,
    )


@router.post("/watchers/run")
async def run_watchers_now(user: UserDep) -> dict[str, Any]:
    """Trigger the watcher sweep immediately.

    Platform-owner only. Exists so the nightly job can be verified after a
    deploy without waiting for the cron hour, and so an operator can force a
    re-check after fixing something. Suppression still applies, so calling it
    repeatedly does not spam anyone.
    """
    if _primary_role(user) != "platform_owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform owner role required.",
        )

    from app.agents.watch_runner import run_watcher_sweep

    totals = await run_watcher_sweep()
    log.info("agent.watchers.manual_run", actor_id=str(user.user_id), **totals)
    return totals


@router.post("/panel/{applicant_id}")
async def agent_panel(applicant_id: uuid.UUID, user: UserDep, db: DbSessionDep) -> dict[str, Any]:
    """Run the specialist panel on one candidate.

    Four specialists read one signal each, blind to one another, then a
    synthesizer reports where they disagree. Advisory only — the response has
    no field that can express a hire/reject verdict.
    """
    if not settings.agents_enabled:
        raise _AGENT_DISABLED

    ctx = await _agent_context(user, db)
    if ctx.role != "hr_manager":
        # This route reads the DENSEST candidate record in the platform —
        # resume, exam answers, coding submission and interview transcript, all
        # in one response. It is therefore candidate_pii in everything but
        # name, and belongs to the one console the access matrix
        # (shared/agents/schema.py DATA_CLASS_ROLES) trusts with that class.
        #
        # It used to admit super_admin as well, which made the matrix a
        # half-truth: every candidate TOOL was closed to that console while
        # this endpoint handed it a fuller record than any of them returned.
        # The REST layer already agrees — get_hr_company gates /hr/* on
        # hr_manager alone — so this is the agent surface catching up, not a
        # new restriction on anyone.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Candidate assessment is available to HR managers only.",
        )
    assert ctx.company_id is not None  # non-platform roles always carry one

    try:
        bundle = await gather_candidate_evidence(
            db, company_id=ctx.company_id, applicant_id=str(applicant_id)
        )
    except ApplicantNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Applicant not found.") from exc

    verdict = await assess_candidate(bundle.evidence, llm=build_panel_llm())
    apply_document_warnings(verdict, bundle.document_warnings)

    log.info(
        "agent.panel",
        actor_id=ctx.actor_id,
        applicant_id=str(applicant_id),
        signals=sum(1 for s in verdict.signals if s.available),
        contradictions=len(verdict.contradictions),
        confidence=verdict.confidence,
        document_warnings=len(bundle.document_warnings),
    )
    return verdict.model_dump(mode="json")
