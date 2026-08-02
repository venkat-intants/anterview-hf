"""Reusable competency archetypes — the building blocks of every role baseline.

Why archetypes instead of writing every family's competencies out longhand:
the taxonomy covers ~18 domain families and each needs 4-6 competencies. Spelt
out in full that is ~90 near-duplicate blocks of "collaboration" and "safety",
which rot independently the moment anyone edits one. Instead each family
composes archetypes and overrides only the parts that are genuinely
role-specific — usually the display name and the probes.

Each archetype also declares ``axis_affinity``: how it maps onto the four
canonical scorecard axes (communication / technical / problem_solving /
confidence). Those four axes are frozen — they are persisted in the
``scorecards.scores`` JSONB, aggregated by the admin analytics SQL
(``avg_problem_solving`` et al) and typed in the frontend. What the role
engine changes is not WHICH axes exist but how they are WEIGHTED and what each
one MEANS for this role. ``render.axis_weights`` walks these affinities to turn
a role's competency mix into per-role axis weights.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shared.intelligence.schema import Anchors, Competency, CompetencyKind

# The four canonical scorecard axes. Frozen contract — see module docstring.
CANONICAL_AXES: tuple[str, ...] = (
    "communication",
    "technical",
    "problem_solving",
    "confidence",
)


@dataclass(frozen=True)
class Archetype:
    """A competency template, before a family specialises it."""

    id: str
    name: str
    kind: CompetencyKind
    probes: tuple[str, ...]
    anchors: Anchors
    # Maps onto CANONICAL_AXES; values need not sum to 1 (they are normalised
    # downstream), but should reflect relative contribution.
    axis_affinity: dict[str, float] = field(default_factory=dict)

    def to_competency(
        self,
        *,
        weight: float,
        name: str | None = None,
        probes: tuple[str, ...] | None = None,
    ) -> Competency:
        """Materialise this archetype as a concrete ``Competency``.

        ``name`` / ``probes`` overrides let a family say "for a nursing role,
        'safety and compliance' is 'Patient Safety & Infection Control' and the
        probes are about hand hygiene, not lockout-tagout" without redefining
        the archetype.
        """
        return Competency(
            id=self.id,
            name=name or self.name,
            kind=self.kind,
            weight=weight,
            probes=list(probes or self.probes),
            anchors=self.anchors,
        )


def _a(low: str, mid: str, high: str) -> Anchors:
    return Anchors(low=low, mid=mid, high=high)


# ---------------------------------------------------------------------------
# The archetype library.
#
# Anchors are written to be role-agnostic on purpose — they describe the SHAPE
# of weak/adequate/strong evidence, and the role's own summary + probes supply
# the subject matter. A family that needs sharper anchors overrides the whole
# competency in taxonomy.py.
# ---------------------------------------------------------------------------

DOMAIN_FUNDAMENTALS = Archetype(
    id="domain_fundamentals",
    name="Core Fundamentals",
    kind="technical",
    probes=(
        "Ask them to explain a core concept of this work in their own words.",
        "Ask why a standard practice in this field is done the way it is.",
        "Ask them to compare two common approaches and say when each applies.",
    ),
    anchors=_a(
        low="Recites terms without meaning; cannot explain why anything is done.",
        mid="Explains the common cases correctly; hesitant at the edges.",
        high="Explains the reasoning behind the practice and where it breaks down.",
    ),
    axis_affinity={"technical": 0.8, "problem_solving": 0.2},
)

HANDS_ON_EXECUTION = Archetype(
    id="hands_on_execution",
    name="Hands-on Execution",
    kind="practical",
    probes=(
        "Ask them to walk through how they actually performed a recent task, step by step.",
        "Ask what they do when the standard procedure does not fit the situation in front of them.",
        "Ask about a time their work had to be redone, and what they changed.",
    ),
    anchors=_a(
        low="Describes work only in the abstract; no evidence of doing it themselves.",
        mid="Can describe their own steps on familiar work; thin on non-routine cases.",
        high="Concrete, sequenced account of their own hands; adapts confidently off-script.",
    ),
    axis_affinity={"technical": 0.5, "problem_solving": 0.3, "confidence": 0.2},
)

TOOLS_AND_SYSTEMS = Archetype(
    id="tools_and_systems",
    name="Tools & Systems",
    kind="technical",
    probes=(
        "Ask which tools or systems they use daily and what they use each one for.",
        "Ask how they picked a tool for a specific job over the alternatives.",
        "Ask what they do when their usual tool or system is unavailable.",
    ),
    anchors=_a(
        low="Names tools but cannot say what they are for.",
        mid="Fluent with their usual toolset; limited beyond it.",
        high="Chooses tools deliberately and can justify the trade-off.",
    ),
    axis_affinity={"technical": 0.75, "problem_solving": 0.25},
)

SAFETY_AND_COMPLIANCE = Archetype(
    id="safety_and_compliance",
    name="Safety & Compliance",
    kind="domain",
    probes=(
        "Ask what the key safety or compliance requirements of this work are.",
        "Ask about a time they saw a rule being cut short, and what they did.",
        "Ask how they confirm a job is safe to start.",
    ),
    anchors=_a(
        low="Treats rules as paperwork; no examples of applying them.",
        mid="Knows the main rules and follows them when reminded.",
        high="Owns safety proactively; has stopped or corrected unsafe work themselves.",
    ),
    axis_affinity={"technical": 0.5, "confidence": 0.2, "problem_solving": 0.3},
)

QUALITY_AND_ACCURACY = Archetype(
    id="quality_and_accuracy",
    name="Quality & Accuracy",
    kind="practical",
    probes=(
        "Ask how they check their own work before calling it done.",
        "Ask about an error that reached the next person, and what changed afterwards.",
        "Ask how they keep quality up when the deadline is tight.",
    ),
    anchors=_a(
        low="No self-check habit; treats errors as someone else's catch.",
        mid="Has a checking routine; applies it inconsistently under pressure.",
        high="Systematic self-verification; treats an escaped error as a process fix.",
    ),
    axis_affinity={"technical": 0.4, "problem_solving": 0.4, "confidence": 0.2},
)

PROBLEM_SOLVING = Archetype(
    id="problem_solving",
    name="Problem Solving",
    kind="technical",
    probes=(
        "Give them a realistic problem from this role and ask how they would approach it.",
        "Ask about the hardest problem they solved and how they narrowed it down.",
        "Ask what they do when the obvious fix does not work.",
    ),
    anchors=_a(
        low="Jumps to a guess; no method for narrowing the problem.",
        mid="Has a workable method; needs prompting to consider alternatives.",
        high="Structures the problem, states assumptions, and tests them in order.",
    ),
    axis_affinity={"problem_solving": 0.85, "technical": 0.15},
)

CUSTOMER_INTERACTION = Archetype(
    id="customer_interaction",
    name="Customer Interaction",
    kind="behavioural",
    probes=(
        "Ask how they handled an unhappy customer or client.",
        "Ask how they explain something technical to someone who does not share their background.",
        "Ask how they say no to a customer without losing them.",
    ),
    anchors=_a(
        low="Blames the customer; no de-escalation instinct.",
        mid="Stays polite and resolves routine friction.",
        high="De-escalates deliberately, protects the relationship and the outcome.",
    ),
    axis_affinity={"communication": 0.6, "confidence": 0.25, "problem_solving": 0.15},
)

COMMERCIAL_ACUMEN = Archetype(
    id="commercial_acumen",
    name="Commercial Acumen",
    kind="domain",
    probes=(
        "Ask how they identify who is actually worth their time.",
        "Ask what numbers they were measured on and how they tracked them.",
        "Ask about a deal or target they missed and what they learnt.",
    ),
    anchors=_a(
        low="No sense of what drives the numbers.",
        mid="Knows their targets; reactive about influencing them.",
        high="Connects daily activity to the commercial outcome with real figures.",
    ),
    axis_affinity={"technical": 0.4, "problem_solving": 0.35, "communication": 0.25},
)

COLLABORATION = Archetype(
    id="collaboration",
    name="Collaboration & Teamwork",
    kind="behavioural",
    probes=(
        "Ask about a disagreement with a colleague and how it was resolved.",
        "Ask what they owned personally versus what the team owned on a recent piece of work.",
        "Ask how they handle someone who is not pulling their weight.",
    ),
    anchors=_a(
        low="Describes team work only in 'we' terms; no personal ownership or conflict handling.",
        mid="Works well with willing colleagues; avoids friction rather than resolving it.",
        high="Names their own contribution clearly and handles disagreement directly and fairly.",
    ),
    axis_affinity={"communication": 0.5, "confidence": 0.3, "problem_solving": 0.2},
)

COMMUNICATION_CLARITY = Archetype(
    id="communication_clarity",
    name="Communication Clarity",
    kind="communication",
    probes=(
        "Ask them to explain their most recent work to someone outside their field.",
        "Ask how they escalate bad news upward.",
        "Ask how they make sure instructions they give are understood.",
    ),
    anchors=_a(
        low="Rambling or so terse the point is lost; no structure.",
        mid="Understandable with effort; structure emerges when prompted.",
        high="Structured, audience-aware, checks understanding without being asked.",
    ),
    axis_affinity={"communication": 0.85, "confidence": 0.15},
)

OWNERSHIP_AND_LEARNING = Archetype(
    id="ownership_and_learning",
    name="Ownership & Learning",
    kind="behavioural",
    probes=(
        "Ask about something they got wrong and what they did next.",
        "Ask what they have taught themselves recently and why.",
        "Ask how they got up to speed the last time they were thrown into unfamiliar work.",
    ),
    anchors=_a(
        low="Deflects responsibility; learning happens only when instructed.",
        mid="Accepts responsibility after the fact; learns on demand.",
        high="Owns outcomes unprompted and drives their own learning with a clear reason.",
    ),
    axis_affinity={"confidence": 0.5, "communication": 0.2, "problem_solving": 0.3},
)

PLANNING_AND_COORDINATION = Archetype(
    id="planning_and_coordination",
    name="Planning & Coordination",
    kind="behavioural",
    probes=(
        "Ask how they sequence work when several things are urgent at once.",
        "Ask how they handle a dependency on someone who is running late.",
        "Ask how they keep everyone informed of where a job stands.",
    ),
    anchors=_a(
        low="Works first-in-first-out; surprised by predictable clashes.",
        mid="Prioritises sensibly; coordination is reactive.",
        high="Plans against constraints, surfaces clashes early, keeps stakeholders ahead of them.",
    ),
    axis_affinity={"problem_solving": 0.5, "communication": 0.35, "confidence": 0.15},
)

DATA_REASONING = Archetype(
    id="data_reasoning",
    name="Data & Measurement",
    kind="technical",
    probes=(
        "Ask how they know whether something they did actually worked.",
        "Ask about a time the numbers contradicted what everyone expected.",
        "Ask what they check before trusting a figure they have been handed.",
    ),
    anchors=_a(
        low="No measurement instinct; asserts results without evidence.",
        mid="Uses the numbers given to them; limited scrutiny of them.",
        high="Defines the measure, questions the source, and acts on what it says.",
    ),
    axis_affinity={"problem_solving": 0.5, "technical": 0.4, "communication": 0.1},
)

PEOPLE_HANDLING = Archetype(
    id="people_handling",
    name="People Handling",
    kind="behavioural",
    probes=(
        "Ask how they handled someone who was upset or distressed.",
        "Ask how they give difficult feedback.",
        "Ask how they build trust with someone who did not want to deal with them.",
    ),
    anchors=_a(
        low="Treats people problems as obstacles; no empathy evidenced.",
        mid="Handles routine situations kindly; struggles with charged ones.",
        high="Reads the situation, adapts their approach, and keeps the relationship intact.",
    ),
    axis_affinity={"communication": 0.55, "confidence": 0.3, "problem_solving": 0.15},
)

ROLE_MOTIVATION = Archetype(
    id="role_motivation",
    name="Role Fit & Motivation",
    kind="behavioural",
    probes=(
        "Ask what draws them to this specific role rather than a similar one.",
        "Ask what they think the hardest part of the day-to-day will be.",
        "Ask what would make them consider this a good year.",
    ),
    anchors=_a(
        low="Generic answers; no understanding of what the job involves.",
        mid="Plausible motivation; understanding of the role is surface-level.",
        high="Specific, realistic reasons grounded in an accurate picture of the work.",
    ),
    axis_affinity={"communication": 0.4, "confidence": 0.45, "problem_solving": 0.15},
)

# Registry — every archetype must be reachable by id so taxonomy entries can
# reference them as data.
ARCHETYPES: dict[str, Archetype] = {
    a.id: a
    for a in (
        DOMAIN_FUNDAMENTALS,
        HANDS_ON_EXECUTION,
        TOOLS_AND_SYSTEMS,
        SAFETY_AND_COMPLIANCE,
        QUALITY_AND_ACCURACY,
        PROBLEM_SOLVING,
        CUSTOMER_INTERACTION,
        COMMERCIAL_ACUMEN,
        COLLABORATION,
        COMMUNICATION_CLARITY,
        OWNERSHIP_AND_LEARNING,
        PLANNING_AND_COORDINATION,
        DATA_REASONING,
        PEOPLE_HANDLING,
        ROLE_MOTIVATION,
    )
}


def get_archetype(archetype_id: str) -> Archetype:
    """Return an archetype by id.

    Raises KeyError on an unknown id — taxonomy entries are static data in this
    repo, so an unknown id is a programming error that should fail loudly at
    import/test time, not degrade silently in production.
    """
    return ARCHETYPES[archetype_id]


def affinity_for(competency_id: str, kind: CompetencyKind) -> dict[str, float]:
    """Best-effort axis affinity for an arbitrary competency.

    The LLM path invents competency ids we have never seen (``"tolerancing"``,
    ``"triage_under_load"``). When the id matches a known archetype we reuse
    its affinity; otherwise we fall back to a per-kind default. Without this,
    an LLM-derived profile could not be converted into axis weights at all and
    the scorer would silently drop back to the flat defaults.
    """
    known = ARCHETYPES.get(competency_id)
    if known is not None:
        return dict(known.axis_affinity)

    defaults: dict[CompetencyKind, dict[str, float]] = {
        "technical": {"technical": 0.8, "problem_solving": 0.2},
        "practical": {"technical": 0.5, "problem_solving": 0.3, "confidence": 0.2},
        "domain": {"technical": 0.6, "problem_solving": 0.3, "communication": 0.1},
        "behavioural": {"confidence": 0.45, "communication": 0.4, "problem_solving": 0.15},
        "communication": {"communication": 0.85, "confidence": 0.15},
    }
    return dict(defaults[kind])
