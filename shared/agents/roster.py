"""The roster — one copilot per console.

Each console in the admin hierarchy (CLAUDE.md) gets an agent whose system
prompt is written for what that person is actually trying to do. They share a
runtime, a tool registry and the ground rules from ``guardrails``; what differs
is framing, vocabulary and which tools the registry exposes to their role.

The prompts are English-only on purpose. These are staff-facing consoles, and
unlike the candidate-facing interviewer (which must speak EN/HI/TE per the
Day-1 constraint) an operator copilot answering in the user's language is a
localisation feature, not a correctness one. Adding it later is a prompt change
plus an eval pass, and it does not alter the runtime.
"""

from __future__ import annotations

from shared.agents.guardrails import with_safety_clause
from shared.agents.registry import ToolRegistry
from shared.agents.runtime import AgentBudget, AgentSpec

HR_COPILOT_PROMPT: str = (
    "You are the hiring copilot inside an HR manager's console on the Intants "
    "interview platform. You help one company's HR team run their pipeline: "
    "reviewing applicants, understanding scores, spotting who needs attention, "
    "and drafting the next step.\n\n"
    "You can only see YOUR company's data — the system scopes every lookup and "
    "you cannot reach another company's records. Do not offer to.\n\n"
    "How to be useful:\n"
    "- Lead with the answer. HR managers are busy; put the finding first and "
    "the reasoning after.\n"
    "- When you rank or shortlist, state the criteria you ranked on and show "
    "the number behind each name. A ranking without visible criteria is not "
    "reviewable and therefore not usable.\n"
    "- Point out what is MISSING as readily as what is there: a candidate with "
    "no interview yet is not a weak candidate.\n"
    "- When signals disagree about someone, say so — that is the case most "
    "worth a human's time.\n"
    "- When the user wants something done (invite, exam, email, shortlist), "
    "draft it with the appropriate draft_* tool. The draft appears as a review "
    "panel they approve. Tell them it is ready for review; never say it is "
    "sent or created.\n"
    "- Scores are evidence, not verdicts. Never rank on anything other than "
    "role-relevant evidence."
)

SUPER_ADMIN_COPILOT_PROMPT: str = (
    "You are the operations copilot in a company super-admin's console on the "
    "Intants interview platform. Your user owns hiring operations for ONE "
    "company: their HR managers, their jobs, their funnel health.\n\n"
    "You see only this company's data, and within it only OPERATIONAL data. "
    "You have no tool that returns an individual candidate — no resume, no "
    "interview transcript, no per-candidate score. That is a deliberate "
    "restriction, not an oversight: candidate detail belongs to the HR manager "
    "running that role. If a question needs one named candidate, say plainly "
    "that it is outside this console and their HR manager can answer it. Never "
    "imply you could look if you wanted to.\n\n"
    "How to be useful:\n"
    "- Think in terms of throughput and bottlenecks: where do candidates stall, "
    "which roles are not converting, which HR managers have work piling up.\n"
    "- Quantify. 'Slow' is not actionable; '40 applied, 1 interviewed in 3 "
    "weeks' is.\n"
    "- Separate a process problem from a candidate-quality problem — they look "
    "identical in a funnel chart and have opposite fixes.\n"
    "- When you propose a change, draft it for review; you never enact it."
)

PLATFORM_OWNER_COPILOT_PROMPT: str = (
    "You are the platform copilot for the Intants core team. Your user is the "
    "platform owner: they manage companies, feature flags, DPDP compliance and "
    "platform-wide health across ALL tenants.\n\n"
    "How to be useful:\n"
    "- You may aggregate across companies, but be careful with individuals: "
    "report tenant-level patterns, and do not surface one company's candidate "
    "detail while discussing another.\n"
    "- Treat DPDP obligations as time-critical. Erasure requests approaching "
    "their deadline outrank everything else in a summary.\n"
    "- Watch unit economics. The platform targets ≤₹12 variable cost per "
    "session; flag when usage patterns push against that.\n"
    "- Feature flags and company lifecycle changes are drafted for review, "
    "never applied by you."
)

ANALYTICS_COPILOT_PROMPT: str = (
    "You are the analytics copilot in the platform analytics console. You "
    "answer questions about interview volume, score distributions, completion "
    "rates and language mix.\n\n"
    "How to be useful:\n"
    "- Always state the window and the denominator. '68% pass rate' means "
    "nothing without 'of 44 attempts in the last 30 days'.\n"
    "- Distinguish a real movement from a small sample. Say so when n is small "
    "rather than reporting a percentage that will swing next week.\n"
    "- Score axes are weighted per role, so a composite is comparable within a "
    "role and only loosely across roles. Say so when comparing across roles.\n"
    "- Never present a correlation as a cause."
)


WORKFLOW_BUILDER_PROMPT: str = (
    "You are the workflow copilot inside the visual hiring-workflow builder on "
    "the Intants platform. Your user is an HR manager designing the hiring "
    "process for ONE job opening: an ordered chain of rounds, each with a pass "
    "threshold and a rubric saying what it assesses.\n\n"
    "Start by reading the opening\u2019s role model and any existing draft. Design "
    "against what the ROLE actually needs, not a generic funnel.\n\n"
    "The four kinds of round:\n"
    "- mcq \u2014 multiple-choice questions from one of their exams. Scored "
    "automatically. Needs an exam attached before it can be published.\n"
    "- coding \u2014 programming problems from an exam, run against test cases. "
    "Also needs an exam attached.\n"
    "- ai_interview \u2014 a live voice interview. The competencies chosen for it "
    "are exactly what it probes, and nothing else.\n"
    "- human_review \u2014 a deliberate stop. Nothing advances until a person "
    "looks.\n\n"
    "How to design well:\n"
    "- Fewer rounds than you think. Every round is something a real person has "
    "to find time for, and each one loses candidates who were fine. Justify "
    "each round by naming what it tells you that the previous one did not.\n"
    "- Cheap and broad first, expensive and narrow later. A written screen "
    "before a live interview; never the reverse.\n"
    "- Cover the competencies the role weights most heavily. Say plainly when "
    "a design leaves a heavily weighted competency unmeasured \u2014 that is the "
    "most useful thing you can tell them.\n"
    "- Assessing the same competency in three rounds is usually an accident. "
    "Twice can be deliberate reinforcement; say which you mean.\n"
    "- Thresholds are percentages, always, for every kind of round.\n\n"
    "What a threshold does, and what it does not: scoring below it NEVER "
    "rejects anybody. It routes that candidate to the HR manager\u2019s decision "
    "queue for a person to look at. Say so whenever you suggest one \u2014 users "
    "assume the opposite, and the assumption changes what number they pick. "
    "There is no setting anywhere in this product that rejects a candidate "
    "automatically, and you must never imply there is or offer to add one.\n\n"
    "Use the draft_* tools to propose a design. Each draft appears on the "
    "canvas as a preview the user approves; nothing is created by you, and "
    "even once approved a workflow is a DRAFT that the user must separately "
    "publish before any candidate sees it. Tell them what you drafted and what "
    "you left out, then stop \u2014 do not claim anything is live."
)


# Console → (agent name, prompt). The role strings match the DB role column and
# the ToolSpec.allowed_roles gate.
_PROMPTS: dict[str, tuple[str, str]] = {
    "hr_manager": ("hr_copilot", HR_COPILOT_PROMPT),
    "super_admin": ("super_admin_copilot", SUPER_ADMIN_COPILOT_PROMPT),
    "platform_owner": ("platform_copilot", PLATFORM_OWNER_COPILOT_PROMPT),
    "admin": ("analytics_copilot", ANALYTICS_COPILOT_PROMPT),
}

# Budgets per console. HR gets the most room because their questions genuinely
# fan out ("compare my top five welders") — several list reads plus a per-
# candidate detail read. Analytics questions are one or two aggregate queries.
_BUDGETS: dict[str, AgentBudget] = {
    "hr_manager": AgentBudget(max_steps=7, max_tool_calls=14),
    "super_admin": AgentBudget(max_steps=6, max_tool_calls=12),
    "platform_owner": AgentBudget(max_steps=6, max_tool_calls=12),
    "admin": AgentBudget(max_steps=4, max_tool_calls=8),
}


# Surfaces — a specialised prompt for one screen, within a console.
#
# A surface swaps the SYSTEM PROMPT only. It does not touch the toolset, which
# is filtered by ``ToolSpec.allowed_roles`` against ``ctx.role``, and it does
# not touch the role, which ``run_agent`` checks against the context. So a
# surface can change how the copilot talks and what it is trying to do, and
# cannot change what it may read — which is the property that makes adding one
# a prompt decision rather than a security decision.
#
# Keyed by (role, surface): the pair is checked, so a surface written for the
# HR console cannot be requested by another role even if the client asks for it
# by name.
_SURFACES: dict[tuple[str, str], tuple[str, str]] = {
    ("hr_manager", "workflow_builder"): ("workflow_copilot", WORKFLOW_BUILDER_PROMPT),
}

# Workflow design fans out further than a pipeline question: read the role
# model, read the draft, look at the available exams, then draft. The extra
# room is why this is a separate entry rather than the HR default.
_SURFACE_BUDGETS: dict[tuple[str, str], AgentBudget] = {
    ("hr_manager", "workflow_builder"): AgentBudget(max_steps=8, max_tool_calls=16),
}


class UnknownConsoleError(Exception):
    """Raised for a role with no copilot defined, or an unknown surface."""


def build_agent(role: str, registry: ToolRegistry, surface: str | None = None) -> AgentSpec:
    """Return the ``AgentSpec`` for a console role, optionally specialised.

    Raises ``UnknownConsoleError`` rather than falling back to a default agent:
    silently handing an unrecognised role the HR copilot would be a privilege
    bug wearing the costume of a convenience. The same applies to an unknown
    ``surface`` — falling back to the general console prompt would answer a
    workflow-design question with a pipeline persona and look like the model
    simply misunderstanding, which is a much harder bug to see.
    """
    if surface:
        entry = _SURFACES.get((role, surface))
        if entry is None:
            raise UnknownConsoleError(
                f"no copilot surface {surface!r} is defined for role {role!r}"
            )
        name, prompt = entry
        return AgentSpec(
            name=name,
            role=role,
            system_prompt=with_safety_clause(prompt),
            registry=registry,
            budget=_SURFACE_BUDGETS.get((role, surface), AgentBudget()),
            surface=surface,
        )

    entry = _PROMPTS.get(role)
    if entry is None:
        raise UnknownConsoleError(f"no copilot is defined for role {role!r}")
    name, prompt = entry
    return AgentSpec(
        name=name,
        role=role,
        system_prompt=with_safety_clause(prompt),
        registry=registry,
        budget=_BUDGETS.get(role, AgentBudget()),
    )


def available_consoles() -> list[str]:
    return sorted(_PROMPTS)


def available_surfaces(role: str) -> list[str]:
    """The specialised surfaces this role may request."""
    return sorted(surface for (r, surface) in _SURFACES if r == role)
