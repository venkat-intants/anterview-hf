"""Agent layer — copilots, collaborative assessment, and proactive watchers.

Three capabilities on one runtime:

* **Copilots** — a conversational agent per console (HR, super-admin, platform
  owner, analytics), each with a role-scoped toolset.
* **Panel** — four specialist agents read one candidate signal each, blind to
  one another, then a synthesizer reports where they disagree.
* **Watchers** — deterministic rules that run on a schedule and raise
  notifications about things nobody thought to ask.

The safety property everything rests on: **agents cannot mutate anything.**
There is no write tool, and ``ToolRegistry.register`` refuses to create one.
When an agent wants something to happen it emits a ``Proposal`` carrying the
exact API request; the frontend renders it for review and, if a human commits,
fires it with that human's own credentials through the normal authorised
endpoint. A proposal is inert data until someone clicks.

Wiring a service::

    registry = ToolRegistry()

    @registry.tool(name="list_applicants", description="...",
                   parameters={"type": "object", "properties": {...}},
                   allowed_roles=("hr_manager",))
    async def _list_applicants(args, ctx):
        rows = await db_query(ctx.company_id, ...)   # scope from ctx, never args
        return ToolOutput(data=rows, citations=[...])

    spec = build_agent("hr_manager", registry)
    run = await run_agent(spec, ctx, "who should I interview next?", llm=adapter)

Dependency rule: stdlib + pydantic + structlog only, so all four service images
can import it (same constraint as ``shared.intelligence``).
"""

from shared.agents.guardrails import (
    SAFETY_CLAUSE,
    UNTRUSTED_DATA_NOTICE,
    detect_injection,
    redact,
    with_safety_clause,
)
from shared.agents.panel import (
    CONTRADICTION_THRESHOLD,
    CandidateEvidence,
    PanelLLM,
    SignalEvidence,
    assess_candidate,
    detect_contradictions,
)
from shared.agents.registry import (
    ToolContext,
    ToolHandler,
    ToolOutput,
    ToolPermissionError,
    ToolRegistry,
)
from shared.agents.roster import (
    UnknownConsoleError,
    available_consoles,
    build_agent,
)
from shared.agents.runtime import (
    AgentBudget,
    AgentLLM,
    AgentSpec,
    build_wire_messages,
    run_agent,
)
from shared.agents.schema import (
    AgentMessage,
    AgentRun,
    AssistantStep,
    Citation,
    CommitSpec,
    Contradiction,
    PanelVerdict,
    Proposal,
    SignalAssessment,
    ToolCall,
    ToolResult,
    ToolSpec,
    WatcherFinding,
)
from shared.agents.watchers import (
    WATCHERS,
    ErasureRequest,
    FunnelRow,
    QuestionStat,
    StalledApplicant,
    WatcherInput,
    digest,
    run_watchers,
)

__all__ = [
    "CONTRADICTION_THRESHOLD",
    "SAFETY_CLAUSE",
    "UNTRUSTED_DATA_NOTICE",
    "WATCHERS",
    "AgentBudget",
    "AgentLLM",
    "AgentMessage",
    "AgentRun",
    "AgentSpec",
    "AssistantStep",
    "CandidateEvidence",
    "Citation",
    "CommitSpec",
    "Contradiction",
    "ErasureRequest",
    "FunnelRow",
    "PanelLLM",
    "PanelVerdict",
    "Proposal",
    "QuestionStat",
    "SignalAssessment",
    "SignalEvidence",
    "StalledApplicant",
    "ToolCall",
    "ToolContext",
    "ToolHandler",
    "ToolOutput",
    "ToolPermissionError",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "UnknownConsoleError",
    "WatcherFinding",
    "WatcherInput",
    "assess_candidate",
    "available_consoles",
    "build_agent",
    "build_wire_messages",
    "detect_contradictions",
    "detect_injection",
    "digest",
    "redact",
    "run_agent",
    "run_watchers",
    "with_safety_clause",
]
