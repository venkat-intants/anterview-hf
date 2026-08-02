"""Typed vocabulary for the agent layer.

This is the contract between three parties that must never disagree: the agent
runtime (which plans), the concrete tools (which read the database), and the
frontend (which renders results and commits proposals).

The load-bearing idea — no write tools
--------------------------------------
Agents cannot mutate anything. There is no write tool to call, so no prompt
injection, no model mistake, and no jailbreak can cause one. When an agent
wants something to happen it emits a ``Proposal`` containing the exact HTTP
request that would do it. The frontend renders that as a review panel; if a
human clicks commit, the FRONTEND fires the request with the USER'S own
credentials, through the same authorised endpoint and the same audit trail as
if they had filled the form by hand.

That inverts the usual agent-safety problem. Instead of trying to constrain
what an agent may do after giving it power, it is never given the power — the
proposal is inert data until a human acts on it. ``ToolRegistry`` enforces this
structurally by refusing to register any tool whose declared effect is not
``read`` or ``draft``.

Dependency rule: stdlib + pydantic + structlog only, same as
``shared.intelligence`` — this package is imported by services with four
different pinned dependency sets.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# What a tool does to the world. There is deliberately no "write" member:
# adding one would be the single change that breaks the guarantee above, so it
# has to be a visible, reviewable edit to this literal rather than a new
# argument someone passes at a call site.
ToolEffect = Literal["read", "draft"]

# Console personas. Mirrors the admin hierarchy in CLAUDE.md — every tool
# declares which of these may call it, and the registry filters the tool list
# per caller so an HR manager's agent never even SEES a platform-owner tool.
AgentRole = Literal["hr_manager", "super_admin", "platform_owner", "admin"]

# Where a piece of evidence came from. The frontend maps these to deep links,
# so an HR user can click any claim an agent makes and land on the record.
CitationKind = Literal[
    "applicant",
    "scorecard",
    "exam",
    "exam_attempt",
    "interview",
    "job",
    "analytics",
    "audit",
    "role_profile",
]


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


class Citation(BaseModel):
    """A pointer to the record backing a claim.

    Every factual statement an agent makes should carry one. This is the
    difference between a copilot that is useful in a hiring decision and one
    that is a liability: an HR manager must be able to check the source, and a
    DPDP audit must be able to reconstruct what the agent was looking at.
    """

    kind: CitationKind
    id: str
    label: str
    # Frontend route, e.g. "/hr/applicants/<id>". Optional because analytics
    # aggregates have no single record to link to.
    href: str | None = None


class ToolSpec(BaseModel):
    """Declaration of a tool the agent may call.

    ``parameters`` is a JSON Schema object passed to the model's function-calling
    API verbatim, so tool definitions live in one place instead of being
    duplicated between the prompt and the handler.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    description: str
    parameters: dict[str, Any]
    effect: ToolEffect
    # Empty means "every role". Most read tools are company-scoped at the
    # handler level anyway, so the role gate is about which CONSOLE surfaces
    # the capability, not about tenancy (tenancy is enforced separately and
    # unconditionally — see ToolContext).
    allowed_roles: tuple[AgentRole, ...] = ()

    @field_validator("parameters")
    @classmethod
    def _must_be_object_schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Reject a non-object schema early.

        Function-calling APIs require a top-level object; a malformed schema
        fails at the provider with an opaque 400 that is painful to trace back
        to the tool that caused it.
        """
        if v.get("type") != "object":
            raise ValueError("tool parameters must be a JSON Schema object")
        v.setdefault("properties", {})
        return v

    def permits(self, role: str) -> bool:
        return not self.allowed_roles or role in self.allowed_roles


class ToolCall(BaseModel):
    """A model's request to invoke one tool."""

    id: str = Field(default_factory=_new_id)
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """The outcome of one tool invocation, fed back to the model.

    ``content`` is what the model sees (JSON text). ``citations`` and
    ``proposals`` are lifted out for the frontend — the model does not need to
    reproduce them in prose, which stops it from garbling an id.
    """

    call_id: str
    name: str
    ok: bool = True
    content: str = ""
    citations: list[Citation] = Field(default_factory=list)
    proposals: list[Proposal] = Field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0


class CommitSpec(BaseModel):
    """The exact request a human would fire to enact a proposal.

    The agent never sends this. It is data describing a call the FRONTEND makes
    with the signed-in user's credentials, against an endpoint that already
    enforces that user's role and company scope. So a proposal cannot widen
    anyone's authority: if the human could not do it manually, committing the
    proposal fails exactly the same way.
    """

    method: Literal["POST", "PATCH", "PUT", "DELETE"]
    path: str
    body: dict[str, Any] = Field(default_factory=dict)
    # Button copy, e.g. "Send 5 interview invites".
    label: str

    @field_validator("path")
    @classmethod
    def _must_be_relative_api_path(cls, v: str) -> str:
        """Refuse absolute URLs.

        A proposal whose path pointed at another host would turn the frontend's
        commit button into an exfiltration primitive — the user's session
        firing their own data at an attacker's server. Relative paths can only
        ever hit our own API.
        """
        if not v.startswith("/") or v.startswith("//"):
            raise ValueError("commit path must be a relative API path")
        return v


ProposalKind = Literal[
    "interview_invite",
    "exam",
    "exam_questions",
    "email",
    "job_description",
    "shortlist",
    "pipeline_decision",
    "note",
]


class Proposal(BaseModel):
    """A drafted action awaiting a human decision.

    Inert. Nothing happens until someone clicks. ``risk_note`` is required for
    anything that reaches a candidate, so the review panel can say plainly what
    the human is about to do to a real person.
    """

    id: str = Field(default_factory=_new_id)
    kind: ProposalKind
    title: str
    summary: str
    commit: CommitSpec
    rationale: str = ""
    citations: list[Citation] = Field(default_factory=list)
    # Set whenever committing sends email or is otherwise visible to a
    # candidate. Surfaced prominently in the review UI.
    risk_note: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class AgentMessage(BaseModel):
    """One entry in the running conversation."""

    role: Literal["user", "assistant", "tool"]
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)


class AssistantStep(BaseModel):
    """What the model produced on one turn of the agent loop."""

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    prompt_tokens: int = 0
    output_tokens: int = 0


StopReason = Literal[
    "completed",  # the model answered with no further tool calls
    "max_steps",  # hit the step budget mid-plan
    "token_budget",  # hit the token budget
    "llm_error",  # provider failed and could not be recovered
    "no_llm",  # no provider configured
]


class AgentRun(BaseModel):
    """The full result of one agent invocation.

    ``trace`` is retained so the console can show what the agent looked at.
    That is not a debugging nicety — an HR manager acting on an agent's ranking
    needs to see which records it read, and a DPDP audit needs the same.
    """

    agent: str
    reply: str = ""
    proposals: list[Proposal] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    trace: list[ToolResult] = Field(default_factory=list)
    steps_used: int = 0
    stop_reason: StopReason = "completed"
    prompt_tokens: int = 0
    output_tokens: int = 0


# ---------------------------------------------------------------------------
# Collaborative assessment (the specialist panel)
# ---------------------------------------------------------------------------

# The independent signals a candidate accumulates. Each gets its OWN specialist
# agent that sees ONLY that signal — the whole point of a panel. One agent
# shown all four anchors hard on whichever it read first and then rationalises
# the rest.
SignalName = Literal["resume", "exam", "coding", "interview"]


class SignalAssessment(BaseModel):
    """One specialist's read of one signal."""

    signal: SignalName
    # False when the candidate has not taken that round yet. An absent signal
    # is reported as absent, never scored as zero — "did not sit the exam" and
    # "failed the exam" are completely different facts about a person.
    available: bool
    score_0_100: float | None = None
    # How much weight a human should put on this read: a 3-question exam and a
    # 10-question interview do not deserve equal confidence.
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class Contradiction(BaseModel):
    """A disagreement between two signals worth a human's attention.

    The most valuable output of the panel. A candidate who scores 91 on a
    take-home coding test and 52 on the technical interview is the single most
    important case for a human to look at, and no per-signal view surfaces it.
    """

    between: tuple[SignalName, SignalName]
    detail: str
    # What a human could do to resolve it. Advisory — never auto-executed.
    suggested_check: str
    severity: Literal["low", "medium", "high"] = "medium"


class PanelVerdict(BaseModel):
    """The synthesised, evidence-backed view of one candidate.

    Explicitly NOT a decision. ``suggested_next_step`` is a recommendation a
    human may act on via a ``Proposal``; nothing here advances or rejects
    anyone, and there is no field that could be read as a verdict.
    """

    applicant_id: str
    applicant_label: str = ""
    signals: list[SignalAssessment] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    summary: str = ""
    # Signals with no data yet, and role competencies never probed. Stated
    # plainly so nobody reads confidence into a gap.
    coverage_gaps: list[str] = Field(default_factory=list)
    suggested_next_step: str = ""
    # Overall confidence in the picture as a whole, driven by how many signals
    # exist and how much they agree.
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # Fixed string, not an enum with a "reject" member. The panel has no
    # authority to conclude anything else, and the type says so.
    decision_authority: Literal["human_only"] = "human_only"
    citations: list[Citation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Proactive watchers
# ---------------------------------------------------------------------------

WatcherSeverity = Literal["info", "warning", "critical"]


class WatcherFinding(BaseModel):
    """Something a background watcher noticed without being asked.

    Deliberately a plain finding, not an action: a watcher that could act would
    need write authority, which nothing in this layer has. Findings become
    notifications, and the human decides.
    """

    watcher: str
    severity: WatcherSeverity
    title: str
    body: str
    # Frontend route to the thing that needs attention.
    link: str | None = None
    # Stable identity for the finding, so re-running a watcher nightly does not
    # notify the same person about the same stalled candidate every day.
    dedupe_key: str = ""
    citations: list[Citation] = Field(default_factory=list)


# Proposal is referenced by ToolResult before it is defined; rebuild so the
# forward reference resolves.
ToolResult.model_rebuild()
