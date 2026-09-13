"""Starter workflow templates, seeded from the opening's role model (D4).

These used to be client-side presets — a title, a round type and a threshold
per step, sent as a chain of addRound calls. That produced the right SHAPE and
nothing else: no competencies on any round, no time limits, no deadlines, no
settings, and the same template for a welder as for a backend engineer. HR then
had to fill in every round by hand, which is the work a template exists to save.

Here a template is built against the requisition's RoleProfile:

* round types, order, thresholds, time limits and deadlines are the template's;
* each round's competencies come from the role model, chosen by what KIND of
  thing the round can observe — a multiple-choice test can check theory and
  domain knowledge, a coding test checks practical execution, a conversation
  hears communication and behaviour, a person reviews behaviour;
* anything the role needs that no round would otherwise assess goes to the AI
  interview, because a conversation can probe anything — so a fresh template
  starts with no coverage gaps;
* automation settings are the recommended defaults.

It uses the RoleProfile vocabulary as-is (ids, weights, anchors, probes), so a
templated round's rubric is frozen exactly as if HR had picked each competency
in the panel. And it only ever produces a DRAFT, through the same one-transaction
path the copilot uses — nothing here can touch a published workflow.

What a template cannot do: attach questions. An MCQ or coding round needs an
exam before it can be published, and that stays an explicit step; the builder
flags it on the round.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Families where a coding round is a sensible assessment. Kept in step with the
# generator's family gate (feedback_billing exam_generator._CODING_FAMILIES).
CODING_FAMILIES: frozenset[str] = frozenset({"software_it", "data_analytics"})

# Matches RoundIn's criteria cap and the web builder's MAX_CRITERIA_PER_ROUND.
MAX_CRITERIA_PER_ROUND = 8


@dataclass(frozen=True)
class Stage:
    title: str
    kind: str
    pass_threshold: float | None
    time_limit_seconds: int | None
    deadline_days: int
    # Which competency kinds this round can actually observe.
    observes: tuple[str, ...]


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    description: str
    stages: tuple[Stage, ...]


def _aptitude() -> Stage:
    return Stage("Aptitude", "mcq", 60, 30 * 60, 5, ("technical", "domain"))


def _interview(observes: tuple[str, ...]) -> Stage:
    # No time limit: an AI interview has its own length, set by the interviewer.
    return Stage("AI Interview", "ai_interview", 60, None, 7, observes)


def _human_review(observes: tuple[str, ...]) -> Stage:
    return Stage("Human Review", "human_review", None, None, 5, observes)


TEMPLATES: dict[str, Template] = {
    "technical": Template(
        key="technical",
        name="Technical",
        description="Aptitude, then code, then a conversation, then your own review.",
        stages=(
            _aptitude(),
            Stage("Coding", "coding", 50, 60 * 60, 5, ("practical",)),
            _interview(("communication", "behavioural", "technical")),
            _human_review(("behavioural",)),
        ),
    ),
    "non_technical": Template(
        key="non_technical",
        name="Non-technical",
        description="Aptitude, then a conversation, then your own review.",
        stages=(
            _aptitude(),
            _interview(("practical", "communication", "behavioural")),
            _human_review(("behavioural",)),
        ),
    ),
    "interview_only": Template(
        key="interview_only",
        name="Interview only",
        description="For roles where a written test tells you little. Straight to the conversation.",
        stages=(
            _interview(("technical", "practical", "domain", "communication", "behavioural")),
            _human_review(("behavioural", "communication")),
        ),
    ),
}

TEMPLATE_ORDER: tuple[str, ...] = ("technical", "non_technical", "interview_only")

# The recommended defaults (C9). Nothing here can end a candidacy — there is no
# such setting (D-05).
DEFAULT_SETTINGS: dict[str, Any] = {
    "auto_score_on_apply": True,
    "auto_assign_first_round": True,
    "auto_advance_rounds": True,
    "reminders_enabled": True,
}


def recommended_template(domain_family: str | None) -> str:
    """Technical for families where coding is a real assessment, else non-technical."""
    return "technical" if domain_family in CODING_FAMILIES else "non_technical"


def _criterion(comp: Any) -> dict[str, Any]:
    """A competency as the round's frozen rubric entry — the whole thing, not a label."""
    return {
        "id": comp.id,
        "name": comp.name,
        "kind": comp.kind,
        "weight": comp.weight,
        "anchors": {"low": comp.anchors.low, "mid": comp.anchors.mid, "high": comp.anchors.high},
        "probes": list(comp.probes)[:6],
    }


def assign_competencies(template: Template, competencies: list[Any]) -> list[list[Any]]:
    """Which competencies each stage assesses. Pure; exposed for tests.

    1. Each stage takes the competencies of the kinds it can observe.
    2. Anything left unassessed goes to the AI interview.
    3. A test round with nothing of its kinds takes the two heaviest competencies,
       and an interview with nothing takes the three heaviest — an empty test
       generates nothing useful, and an AI round cannot publish without criteria.
    4. Each stage is capped at MAX_CRITERIA_PER_ROUND, heaviest first. A role with
       more competencies than that can end with one unassessed; the coverage
       check says so rather than this silently dropping it.
    """
    by_weight = sorted(competencies, key=lambda c: (-c.weight, c.id))
    stages = template.stages
    picked: list[list[Any]] = [
        [c for c in by_weight if c.kind in stage.observes] for stage in stages
    ]

    interview = next((i for i, s in enumerate(stages) if s.kind == "ai_interview"), None)
    if interview is not None:
        covered = {c.id for chosen in picked for c in chosen}
        picked[interview].extend(c for c in by_weight if c.id not in covered)

    for i, stage in enumerate(stages):
        if not picked[i] and stage.kind in ("mcq", "coding"):
            picked[i] = by_weight[:2]
        if not picked[i] and stage.kind == "ai_interview":
            picked[i] = by_weight[:3]
        unique: dict[str, Any] = {}
        for c in picked[i]:
            unique.setdefault(c.id, c)
        picked[i] = sorted(unique.values(), key=lambda c: (-c.weight, c.id))[
            :MAX_CRITERIA_PER_ROUND
        ]
    return picked


def build_template(key: str, profile: Any) -> dict[str, Any]:
    """The ApplyDraftIn payload for one template against one role profile.

    Raises KeyError for an unknown template — the router validates the key first.
    """
    template = TEMPLATES[key]
    picked = assign_competencies(template, list(profile.competencies))
    return {
        "name": template.name,
        "rounds": [
            {
                "title": stage.title,
                "kind": stage.kind,
                "pass_threshold": stage.pass_threshold,
                "time_limit_seconds": stage.time_limit_seconds,
                "deadline_days": stage.deadline_days,
                "criteria": [_criterion(c) for c in chosen],
            }
            for stage, chosen in zip(template.stages, picked, strict=True)
        ],
        "settings": dict(DEFAULT_SETTINGS),
    }


def template_summaries(profile: Any) -> list[dict[str, Any]]:
    """What each template would create for THIS role — the picker's preview."""
    recommended = recommended_template(getattr(profile, "domain_family", None))
    out = []
    for key in TEMPLATE_ORDER:
        payload = build_template(key, profile)
        template = TEMPLATES[key]
        out.append({
            "key": key,
            "name": template.name,
            "description": template.description,
            "recommended": key == recommended,
            "rounds": [
                {
                    "title": r["title"],
                    "kind": r["kind"],
                    "pass_threshold": r["pass_threshold"],
                    "time_limit_seconds": r["time_limit_seconds"],
                    "deadline_days": r["deadline_days"],
                    "competencies": [c["name"] for c in r["criteria"]],
                }
                for r in payload["rounds"]
            ],
        })
    return out
