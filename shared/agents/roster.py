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
    "You see only this company's data.\n\n"
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


class UnknownConsoleError(Exception):
    """Raised for a role with no copilot defined."""


def build_agent(role: str, registry: ToolRegistry) -> AgentSpec:
    """Return the ``AgentSpec`` for a console role.

    Raises ``UnknownConsoleError`` rather than falling back to a default agent:
    silently handing an unrecognised role the HR copilot would be a privilege
    bug wearing the costume of a convenience.
    """
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
