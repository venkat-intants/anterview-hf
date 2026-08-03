"""Agent endpoints — console copilots and the assessment panel.

Three routes:

  POST /agent/chat                    talk to your console's copilot
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
    AgentMessage,
    ToolContext,
    UnknownConsoleError,
    assess_candidate,
    available_consoles,
    build_agent,
    run_agent,
)
from shared.auth.base import User
from sqlalchemy import text

from app.agents.evidence import ApplicantNotFoundError, gather_candidate_evidence
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
    company_id: str | None = None

    if role != "platform_owner":
        raw = await db.scalar(
            text("SELECT company_id FROM users WHERE id = CAST(:uid AS uuid) AND deleted_at IS NULL"),
            {"uid": user.user_id},
        )
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
        # Stated in the API so a client cannot present the copilot as more
        # capable than it is.
        "capabilities": ["read", "draft"],
        "note": "The assistant can read records and draft actions. Every action needs your approval.",
    }


@router.post("/chat", response_model=ChatOut)
async def agent_chat(body: ChatIn, user: UserDep, db: DbSessionDep) -> ChatOut:
    """Ask this console's copilot a question."""
    if not settings.agents_enabled:
        raise _AGENT_DISABLED

    ctx = await _agent_context(user, db)
    try:
        spec = build_agent(ctx.role, registry)
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
    if ctx.role not in ("hr_manager", "super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Candidate assessment is available to HR roles only.",
        )
    assert ctx.company_id is not None  # non-platform roles always carry one

    try:
        evidence = await gather_candidate_evidence(
            db, company_id=ctx.company_id, applicant_id=str(applicant_id)
        )
    except ApplicantNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Applicant not found.") from exc

    verdict = await assess_candidate(evidence, llm=build_panel_llm())

    log.info(
        "agent.panel",
        actor_id=ctx.actor_id,
        applicant_id=str(applicant_id),
        signals=sum(1 for s in verdict.signals if s.available),
        contradictions=len(verdict.contradictions),
        confidence=verdict.confidence,
    )
    return verdict.model_dump(mode="json")
