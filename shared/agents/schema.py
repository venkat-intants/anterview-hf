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

from pydantic import BaseModel, Field, field_validator, model_validator

# What a tool does to the world. There is deliberately no "write" member:
# adding one would be the single change that breaks the guarantee above, so it
# has to be a visible, reviewable edit to this literal rather than a new
# argument someone passes at a call site.
ToolEffect = Literal["read", "draft"]

# Console personas. Mirrors the admin hierarchy in CLAUDE.md — every tool
# declares which of these may call it, and the registry filters the tool list
# per caller so an HR manager's agent never even SEES a platform-owner tool.
AgentRole = Literal["hr_manager", "super_admin", "platform_owner", "admin"]

# What KIND of data a tool returns. Declared per tool and checked against
# ``DATA_CLASS_ROLES`` below, so "which console may read candidate PII" is one
# reviewable table rather than a role tuple retyped at nine call sites.
#
#   candidate_pii      identifies or describes a NAMED candidate — resume text,
#                      transcripts, per-axis scores, anything that reads as a
#                      person rather than a number.
#   company_scoped     aggregates inside one company. Counts, rates, per-role
#                      roll-ups. No individual candidate is identifiable.
#   company_staff      the company's own STAFF records (HR managers, their
#                      workload). Employee data, not candidate data.
#   platform_aggregate crosses tenants. Aggregate-only, always.
ToolDataClass = Literal[
    "candidate_pii",
    "company_scoped",
    "company_staff",
    "platform_aggregate",
]

# The access matrix. A tool may name only roles listed for its data class, and
# ``ToolSpec`` rejects anything wider at construction time — which means an
# import of the tools module is enough to fail, so a mis-scoped tool cannot
# reach a running console.
#
# Read this as the platform's data-minimisation policy in executable form:
#
#   * Only ``hr_manager`` ever sees a named candidate. A company super admin
#     runs hiring OPERATIONS — throughput, bottlenecks, who on their team is
#     overloaded — and none of that requires reading one applicant's resume.
#     Removing that reach is the point: it is the difference between a super
#     admin who cannot look up a candidate and one who merely should not.
#   * ``platform_owner`` and the ``admin`` analytics console are cross-tenant
#     by design, and therefore aggregate-only. A platform operator debugging
#     tenant health has no route to a company's applicants through a copilot.
#   * No role appears in more than one tenancy scope. Nothing here grants a
#     company role a cross-tenant read, or a platform role a tenant read.
DATA_CLASS_ROLES: dict[str, frozenset[str]] = {
    "candidate_pii": frozenset({"hr_manager"}),
    "company_scoped": frozenset({"hr_manager", "super_admin"}),
    "company_staff": frozenset({"super_admin"}),
    "platform_aggregate": frozenset({"platform_owner", "admin"}),
}

# Roles whose reads are NOT confined to a single company. Kept next to the
# matrix because the router uses it to decide whether a ToolContext may carry a
# NULL company_id, and the two must not drift.
CROSS_TENANT_ROLES: frozenset[str] = frozenset({"platform_owner", "admin"})

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
    # PH5-E5: a HUMAN interviewer's scorecard (never reuse "scorecard", which
    # means the AI one) and a hire/reject decision — nothing represented the
    # latter before. Kept in sync with web/src/api/agent.ts's Citation union
    # by test_evidence_graph_citation_kind_parity.
    "interviewer_scorecard",
    "decision",
    # PH5-E2: a company's own reference document (policy, handbook, process
    # note) in the HR document corpus. Added here, ahead of that tool landing,
    # so CITATION_ROUTES/CITATION_MIN_ROLES stay a complete table over every
    # member of this Literal from day one, and the corpus tool needs only to
    # USE the kind, never to add it.
    "document",
]

# Frontend route per citation kind, one table rather than a href string typed
# out at every call site. ``None`` means "no single record to open" — an
# analytics aggregate or a role model computed on the fly — and the renderer
# shows an unlinked chip rather than a dead link. Keyed by ``str`` rather than
# ``CitationKind`` so the coverage test below can compare key sets against
# ``typing.get_args`` without a type-checker complaint about a non-Literal key.
#
# The id substituted into ``{id}`` is whatever the ROUTE needs, not necessarily
# ``Citation.id`` — a ``scorecard`` citation is identified by the scorecard's
# own id but links through the APPLICANT page, because there is no standalone
# scorecard route. Callers use ``citation_href`` below rather than formatting
# this by hand, so that distinction is made once instead of at every call site.
#
# This table plus ``CITATION_VIEWS`` is the WHOLE vocabulary of citation hrefs:
# no citation-producing module formats one itself, which
# ``test_citation_contract_sweep.py`` enforces structurally. It was decoration
# until PH5 Wave 3's acceptance pass — roughly two of twenty call sites used it,
# and the rest agreed with it only by luck.
CITATION_ROUTES: dict[str, str | None] = {
    "applicant": "/hr/applicants/{id}",
    "scorecard": "/hr/applicants/{id}",
    "exam": "/hr/exams/{id}",
    # Served by ``web/src/pages/hr/ExamAttemptRedirect.tsx``, which resolves the
    # owning exam and forwards to the attempt detail screen. That resolver costs
    # ONE REQUEST PER EXAM until it finds the attempt, because there is no
    # ``GET /hr/exams/attempts/{id}`` to ask directly — deliberately not added
    # yet (this is a rescue path off a citation chip, not a hot route). The cheap
    # fix, for whoever needs it: one endpoint returning the attempt's exam_id,
    # and the resolver becomes a single call.
    "exam_attempt": "/hr/exams/attempts/{id}",
    "interview": "/hr/interviews/{id}",
    "job": "/hr/requisitions/{id}",
    "interviewer_scorecard": "/hr/enrolments/{id}/evidence",
    "decision": "/hr/enrolments/{id}/evidence?decision={id}",
    # PH5-E2 coordination: moved from /hr/documents/{id} — that path was
    # shared with an unrelated entity (candidate_documents/preboarding),
    # differing only by verb. The corpus's own routes live under /hr/library.
    "document": "/hr/library/{id}",
    "role_profile": None,
    "analytics": None,
    "audit": None,
}


# Sub-views a citation may legitimately point at INSTEAD of the record's own
# page, keyed by ``(kind, view)``. The table above answers "where does this
# record live"; this one answers the cases where that is the wrong place to land:
#
#   * the workflow copilot cites the opening it is designing — the canvas is
#     the page the user is on and the one the answer is about, not the
#     requisition dashboard;
#   * two watchers cite an opening because a QUEUE on it needs working;
#   * an ``analytics`` citation has no record at all (hence ``None`` above) but
#     does have a console dashboard that shows the aggregate, and which console
#     that is depends on who asked;
#   * a ``document`` lives in TWO consoles (the HR library and the super admin's
#     copy of the same screen) and a role may only enter its own, so the view is
#     the console — see ``CITATION_CONSOLE_VIEW`` and ``citation_href_for_role``.
#
# This exists because the alternative was each of those call sites formatting a
# path inline, which is how ``CITATION_ROUTES`` came to describe only about two
# of twenty emitted hrefs. A ``(kind, view)`` pair that is not declared here
# RAISES rather than falling back to the base route: a typo'd view must be a
# loud failure in a test, not a chip that silently lands somewhere else.
#
# Python-side only, and deliberately not mirrored in ``web/src/api/agent.ts``:
# the renderer (``components/agent/CitationChips.tsx``) navigates to the
# ``href`` the SERVER sent, so the TS ``CITATION_ROUTES`` copy exists for the
# kind-coverage parity test, not to build links. Adding a new CitationKind does
# need the TS change; adding a view does not.
CITATION_VIEWS: dict[tuple[str, str], str] = {
    ("job", "workflow"): "/hr/requisitions/{id}/workflow",
    ("job", "decisions"): "/hr/requisitions/{id}/decisions",
    ("analytics", "hr"): "/hr/analytics",
    ("analytics", "company"): "/superadmin",
    ("analytics", "platform"): "/platform",
    # One document, two consoles. ``/hr/*`` admits ``hr_manager`` only, so a
    # super admin — who legitimately RECEIVES document citations, since
    # CITATION_MIN_ROLES["document"] is company_scoped — was handed a link their
    # own route guard bounced to /superadmin. The record is the same; the door is
    # not. The base route above stays the HR one, which is what hr_manager gets.
    ("document", "hr"): "/hr/library/{id}",
    ("document", "company"): "/superadmin/library/{id}",
}

# Which console a staff role is allowed to walk into. Used ONLY for the kinds
# that exist in more than one console (``document`` today): a citation's kind
# says what the record is, and this says where that particular reader can open
# it. On the server because the server is the only side that knows both — the
# alternative was the client rewriting a path it was handed, which is a second
# copy of the route table in the place least able to keep it honest.
#
# Roles absent here (``platform_owner``, ``admin``, ``interviewer``) get the
# kind's base route: they are never offered a company-scoped document tool, and
# inventing a console path for them would be a guess.
CITATION_CONSOLE_VIEW: dict[str, str] = {
    "hr_manager": "hr",
    "super_admin": "company",
}


def citation_href(kind: str, route_id: str = "", *, view: str | None = None) -> str | None:
    """Build an href from ``CITATION_ROUTES`` (or ``CITATION_VIEWS``) for a record.

    Not every citation's own ``id`` is the id its route needs (see the
    ``scorecard`` note on ``CITATION_ROUTES`` above), so callers pass whichever
    id the ROUTE wants, explicitly, rather than this function guessing from a
    ``Citation`` instance.

    *view* selects a declared sub-view instead of the record's own page — see
    ``CITATION_VIEWS``. An undeclared ``(kind, view)`` raises ``KeyError``; so
    does a route that needs an id it was not given, because "/hr/requisitions/"
    is a dead link that no test would notice.
    """
    if view is not None:
        try:
            template: str | None = CITATION_VIEWS[(kind, view)]
        except KeyError:
            raise KeyError(
                f"no citation view {view!r} declared for kind {kind!r}; add it to "
                "CITATION_VIEWS in shared/agents/schema.py rather than formatting "
                "the path at the call site"
            ) from None
    else:
        template = CITATION_ROUTES.get(kind)
    if template is None:
        return None
    if "{id}" in template and not route_id:
        raise KeyError(f"citation route {template!r} needs an id and was given none")
    return template.format(id=route_id)


def citation_href_for_role(kind: str, route_id: str, *, role: str) -> str | None:
    """``citation_href`` for a kind whose page lives in more than one console.

    Falls back to the kind's base route whenever this kind has no per-console
    view for this role, so it is safe to use anywhere and only changes the answer
    where a table entry says it should. That fallback is why it does not raise
    like ``view=`` does: a role with no console view is the normal case, not a
    typo — the loud failure that matters (a view named but not declared) is still
    ``citation_href``'s.
    """
    view = CITATION_CONSOLE_VIEW.get(role)
    if view is not None and (kind, view) in CITATION_VIEWS:
        return citation_href(kind, route_id, view=view)
    return citation_href(kind, route_id)


# Minimum roles that may be HANDED a citation of this kind at all — the answer
# to "can the caller open this?", checked by ``ToolRegistry.invoke`` on every
# citation a tool returns, independently of whether the TOOL itself was one
# this role may call. A ``company_scoped`` tool is offered to both
# ``hr_manager`` and ``super_admin``, but if it happens to surface an
# ``applicant`` citation (candidate_pii, hr_manager only), a super admin must
# never receive it — the access matrix in ``DATA_CLASS_ROLES`` says a company
# super admin has no route to a named candidate, and a citation is exactly
# such a route if nothing here stops it.
#
# Derived from ``DATA_CLASS_ROLES`` wherever the design ties a citation kind to
# an existing data class, rather than retyped, so the two tables cannot drift.
CITATION_MIN_ROLES: dict[str, frozenset[str]] = {
    "applicant": DATA_CLASS_ROLES["candidate_pii"],
    "scorecard": DATA_CLASS_ROLES["candidate_pii"],
    "exam_attempt": DATA_CLASS_ROLES["candidate_pii"],
    "interviewer_scorecard": DATA_CLASS_ROLES["candidate_pii"],
    "decision": DATA_CLASS_ROLES["candidate_pii"],
    "interview": DATA_CLASS_ROLES["candidate_pii"],
    "exam": DATA_CLASS_ROLES["company_scoped"],
    "job": DATA_CLASS_ROLES["company_scoped"],
    "document": DATA_CLASS_ROLES["company_scoped"],
    "role_profile": DATA_CLASS_ROLES["company_scoped"],
    "analytics": DATA_CLASS_ROLES["company_scoped"] | DATA_CLASS_ROLES["platform_aggregate"],
    "audit": DATA_CLASS_ROLES["platform_aggregate"],
}


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
    # Run-scoped reference handle ("S1", "S2", …) a model can write inline
    # after a claim. ASSIGNED BY THE RUNTIME ONLY — never set by a tool, so a
    # handler cannot forge a ref that collides with, or pre-empts, the one the
    # run assigns. Empty until ``run_agent`` stamps it.
    ref: str = ""
    # Where inside the source the evidence sits — "page 4", "v3 · §2.1 Leave
    # policy", "criterion Communication", "workload across 6 HR managers".
    # Optional: most citations point at a whole record and need no locator.
    locator: str | None = None

    @field_validator("href")
    @classmethod
    def _must_be_relative_app_path(cls, v: str | None) -> str | None:
        """Refuse an absolute URL, mirroring ``CommitSpec._must_be_relative_api_path``.

        Unlike a commit, nothing FIRES a request at a citation's href — a click
        just navigates the SPA. But the renderer today is a plain ``<a href=…>``
        (``CopilotPanel.tsx``), and a tool that (by bug, or by a document that
        got as far as instruction-following text) emitted an absolute URL would
        turn a "see the source" chip into a link off our own site. ``None`` is
        still legal and means "no single record to open".
        """
        if v is None:
            return v
        if not v.startswith("/") or v.startswith("//"):
            raise ValueError("citation href must be a relative app path, or None")
        return v


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
    # What this tool returns. Required — there is deliberately no default,
    # because the safe-looking default ("aggregate") is exactly the one a new
    # tool that actually returns PII would inherit by accident.
    data_class: ToolDataClass
    # Which consoles may see and call this tool. Required and non-empty: an
    # empty tuple used to mean "every role", which is the wrong default for a
    # surface that reads candidate data. Every tool now states its audience,
    # and ``_roles_match_data_class`` checks that audience against the matrix.
    allowed_roles: tuple[AgentRole, ...] = Field(min_length=1)
    # Which specialised SCREEN this tool belongs to, if any. Empty (the
    # default) means the general console copilot, which is where almost every
    # tool belongs.
    #
    # This is presentation, not authorisation — ``allowed_roles`` is the
    # security boundary and is checked independently on every invocation. What
    # this does is keep a tool out of a prompt that has no use for it: a
    # workflow-authoring tool offered to someone asking about their pipeline is
    # prompt bloat that invites a confused call and a refusal the user cannot
    # interpret. An empty tuple is deliberately the permissive default, because
    # the property being managed here is relevance rather than access.
    surfaces: tuple[str, ...] = ()

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

    @model_validator(mode="after")
    def _roles_match_data_class(self) -> ToolSpec:
        """Reject a tool offered to a role its data class does not permit.

        This is the enforcement point for the access matrix. It runs at
        construction, so a mis-scoped tool raises during module import and the
        service fails to start rather than serving one company's candidates to
        somebody who should never have been shown them.

        Widening access therefore cannot be done at a call site. It requires
        editing ``DATA_CLASS_ROLES`` — a visible, reviewable change, in the
        same spirit as ``ToolEffect`` having no "write" member.
        """
        permitted = DATA_CLASS_ROLES[self.data_class]
        overreach = tuple(r for r in self.allowed_roles if r not in permitted)
        if overreach:
            raise ValueError(
                f"tool {self.name!r} is data_class={self.data_class!r} but names "
                f"role(s) {overreach!r}, which that class does not permit "
                f"(allowed: {sorted(permitted)}). Either the data class is "
                f"wrong or this is a privilege widening — change "
                f"DATA_CLASS_ROLES deliberately if the latter."
            )
        return self

    def permits(self, role: str) -> bool:
        """Whether this role may see and call the tool.

        No empty-means-everyone case: ``allowed_roles`` is validated non-empty,
        so an unlisted role is always a denial.
        """
        return role in self.allowed_roles

    def on_surface(self, surface: str | None) -> bool:
        """Whether this tool belongs in the toolset for a given screen.

        A tool with no declared surfaces is general and appears everywhere,
        including on specialised screens — the workflow copilot can still look
        up a role model. A tool WITH surfaces appears only on those, so it does
        not clutter the general console.
        """
        return not self.surfaces or surface in self.surfaces


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
    # Hiring-workflow authoring. Three kinds rather than one because the
    # builder renders them differently: a whole process is previewed as ghost
    # rounds on the canvas, a single round attaches to an existing draft, and
    # settings change no rounds at all. Note that none of these reaches a
    # candidate — they author a process, and the process is not live until the
    # human publishes it, which is a separate act from committing the proposal.
    "workflow",
    "workflow_round",
    "workflow_settings",
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
    "role_mismatch",  # spec role and caller role disagreed — refused, see runtime
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
    # Refs (e.g. "S1") the reply actually cited, after ``bind_refs`` has removed
    # any the model invented. Empty is normal — many good answers cite nothing
    # inline even when evidence_used is true (a table, say, rather than prose).
    cited_refs: list[str] = Field(default_factory=list)
    # How many inline markers ``bind_refs`` had to strip because they named no
    # citation the run actually produced. Never surfaced to the user — logged —
    # because the FIX is silent (the marker is simply removed); this count is
    # what tells an operator a console or a GROQ_MODEL is bad at the convention.
    invented_refs: int = 0
    # Computed, never claimed: true when at least one successful tool result in
    # ``trace`` carried a citation. The console renders this — not the model's
    # say-so — as "answered from your records" vs "no records were read".
    evidence_used: bool = False


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
