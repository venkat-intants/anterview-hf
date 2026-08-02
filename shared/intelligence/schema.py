"""Typed schema for the role-competency engine.

This module is the vocabulary the whole intelligence layer speaks. Every other
module here (taxonomy, derive, coverage, render) and every consumer
(interview_core worker + graph, feedback_billing scorer, exam generation)
reads and writes these types — so a role means exactly ONE thing platform-wide
instead of each feature re-guessing it from a free-text ``job_title``.

Dependency rule
---------------
stdlib + pydantic + structlog ONLY. ``shared/`` is copied verbatim into all
four service images (see each service Dockerfile: ``COPY shared /app/shared``
with ``PYTHONPATH=/app``) and those images have four *different* pinned
dependency sets. Anything heavier than pydantic here would break at least one
of them at import time.

Advisory-only guarantee
-----------------------
Nothing in this schema encodes a hire/reject verdict. A ``RoleProfile``
describes what a role *requires* and how to *evidence* it; the decision stays
with a human (see ``docs/`` DPDP notes on automated decision-making). The
``source`` and ``profile_id`` fields exist so any score can be traced back to
the exact rubric that produced it — which is the audit story we need for a
government bid.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Bump when a field changes meaning in a way that invalidates cached profiles.
# ``compute_profile_id`` folds this in, so a bump transparently misses every
# existing cache entry instead of serving a stale shape.
SCHEMA_VERSION: int = 1

# What kind of thing a competency measures. Drives (a) how the interviewer is
# told to probe it and (b) how it maps onto the four canonical scorecard axes
# (see ``render.axis_weights``).
CompetencyKind = Literal[
    "technical",  # role fundamentals / theory — "why does this work"
    "practical",  # hands-on execution — "can you actually do it"
    "domain",  # sector knowledge, standards, regulation
    "behavioural",  # collaboration, ownership, resilience
    "communication",  # clarity, listening, explaining to non-experts
]

Seniority = Literal["entry", "mid", "senior"]

# Where a profile came from. Recorded on every profile for audit + debugging:
#   taxonomy — deterministic baseline only (no LLM call, or the LLM failed)
#   llm      — the model produced the full profile and it validated
#   hybrid   — LLM output was partially usable; taxonomy filled the gaps
ProfileSource = Literal["taxonomy", "llm", "hybrid"]

# Competency ids are slugs: lowercase, digits, underscores. They end up in
# JSONB keys, prompt text and (eventually) analytics column aliases, so keep
# them boring and stable.
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

# Guard rails on how many competencies a profile may carry. Fewer than 3 and a
# 10-question interview has nothing to rotate across; more than 8 and each one
# gets under a question of airtime, which produces scores nobody can defend.
MIN_COMPETENCIES: int = 3
MAX_COMPETENCIES: int = 8


def _slugify(value: str) -> str:
    """Coerce free text into a valid competency id.

    Used on the LLM path, where the model is asked for a slug but occasionally
    returns ``"Structural Analysis"`` or ``"safety-compliance"``. Rejecting the
    whole profile over punctuation would drop us to the taxonomy baseline for a
    cosmetic problem, so we repair instead.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not cleaned:
        return "competency"
    if not cleaned[0].isalpha():
        cleaned = f"c_{cleaned}"
    return cleaned[:40]


class Anchors(BaseModel):
    """Behaviourally-anchored rating scale for one competency.

    Three bands, not five: interviewers (and LLM scorers) calibrate reliably
    across three clearly-separated descriptions and poorly across five
    adjacent ones. The 0-10 score is still emitted; these bands tell the
    scorer what each region of the scale actually looks like FOR THIS ROLE,
    which is the whole point — "technical: 7" means something different for a
    welder than for a backend engineer.
    """

    low: str = Field(description="0-3: cannot perform at this tier")
    mid: str = Field(description="4-7: approaching or meeting tier expectations")
    high: str = Field(description="8-10: exceeds tier expectations")


class Competency(BaseModel):
    """One thing this role is assessed on."""

    id: str
    name: str
    kind: CompetencyKind
    # Relative importance within the role. Normalised to sum to 1.0 across the
    # profile by RoleProfile's validator — callers may pass raw importance
    # numbers (or an LLM may emit weights that sum to 0.97) without care.
    weight: float = Field(gt=0.0, le=1.0)
    # Question stems the interviewer can adapt. NOT a script — the live worker
    # passes these as "shapes of question that would evidence this", so the
    # model still phrases naturally and follows the candidate's answers.
    probes: list[str] = Field(min_length=1, max_length=6)
    anchors: Anchors

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        return v if _SLUG_RE.match(v) else _slugify(v)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        name = v.strip()
        if not name:
            raise ValueError("competency name must not be empty")
        return name[:80]

    @field_validator("probes")
    @classmethod
    def _clean_probes(cls, v: list[str]) -> list[str]:
        """Drop blanks and cap length — probes ride along in EVERY turn's
        system prompt, so an unbounded probe list compounds token cost across
        the session (same reasoning as the resume/JD caps in graph/prompts.py).
        """
        cleaned = [p.strip()[:200] for p in v if p and p.strip()]
        if not cleaned:
            raise ValueError("competency needs at least one non-empty probe")
        return cleaned[:6]


class RoleProfile(BaseModel):
    """The single source of truth for what a role requires.

    Derived once per (title, JD, skills, level, department) tuple and cached by
    ``profile_id``. Consumed by the interviewer (question planning), the scorer
    (rubric + axis weights) and — next — exam generation, so all three finally
    agree on what the job is.
    """

    schema_version: int = SCHEMA_VERSION
    # Deterministic hash of the derivation inputs. Cache key AND audit handle:
    # a scorecard citing profile_id X can be replayed against exactly that
    # rubric years later. See ``derive.compute_profile_id``.
    profile_id: str
    job_title: str
    # Taxonomy family id (e.g. "civil_construction"). Always set, even on the
    # LLM path — it is the fallback rubric and the analytics grouping key.
    domain_family: str
    domain_label: str
    seniority: Seniority = "mid"
    # One or two sentences on what the person actually DOES day to day. Goes
    # into the interviewer prompt so the model has a concrete mental model of
    # the job rather than inferring one from the title.
    summary: str = ""
    competencies: list[Competency] = Field(
        min_length=MIN_COMPETENCIES, max_length=MAX_COMPETENCIES
    )
    # Role-specific signals worth noticing. Advisory ONLY — surfaced as
    # observations on the scorecard, never as an automatic rejection rule.
    red_flags: list[str] = Field(default_factory=list, max_length=8)
    # Topics the interviewer must not raise for THIS role, on top of the
    # platform-wide PII ban (which lives in the prompts and is not negotiable).
    # Example: a role advertised for a specific shift must not turn into
    # questions about childcare arrangements.
    avoid_topics: list[str] = Field(default_factory=list, max_length=8)
    source: ProfileSource = "taxonomy"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    @model_validator(mode="after")
    def _dedupe_and_normalise(self) -> RoleProfile:
        """Enforce unique ids and make weights sum to exactly 1.0.

        Both repairs rather than rejects. The LLM path routinely emits two
        competencies that slugify to the same id, or weights summing to 0.97 —
        neither is a reason to throw away an otherwise good role model and fall
        back to the generic baseline.
        """
        seen: dict[str, Competency] = {}
        for comp in self.competencies:
            if comp.id in seen:
                # Merge duplicates by keeping the heavier one — losing the
                # lighter duplicate is strictly better than carrying an id
                # collision into JSONB scorecard keys.
                if comp.weight > seen[comp.id].weight:
                    seen[comp.id] = comp
                continue
            seen[comp.id] = comp

        deduped = list(seen.values())
        if len(deduped) < MIN_COMPETENCIES:
            raise ValueError(
                f"profile needs >= {MIN_COMPETENCIES} distinct competencies, "
                f"got {len(deduped)}"
            )
        deduped = deduped[:MAX_COMPETENCIES]

        total = sum(c.weight for c in deduped)
        if total <= 0:
            raise ValueError("competency weights must sum to a positive number")
        for comp in deduped:
            comp.weight = round(comp.weight / total, 4)

        # Largest-remainder correction so the stored weights sum to exactly
        # 1.0 — otherwise a composite score computed from them drifts by a
        # fraction of a point and two identical interviews can disagree.
        drift = round(1.0 - sum(c.weight for c in deduped), 4)
        if drift:
            heaviest = max(deduped, key=lambda c: c.weight)
            heaviest.weight = round(heaviest.weight + drift, 4)

        object.__setattr__(self, "competencies", deduped)
        return self

    def competency(self, competency_id: str) -> Competency | None:
        """Look up one competency by id, or None."""
        for comp in self.competencies:
            if comp.id == competency_id:
                return comp
        return None

    @property
    def competency_ids(self) -> list[str]:
        return [c.id for c in self.competencies]


# ---------------------------------------------------------------------------
# Interview planning types
#
# These live here (not in coverage.py) so a consumer can type-annotate a plan
# without importing the planner's logic.
# ---------------------------------------------------------------------------

# What a given turn is FOR. The intro and wrap turns carry no competency:
#   intro — let the candidate introduce themselves (never scored on depth)
#   probe — evidence one specific competency
#   wrap  — warm close / candidate's own questions
TurnKind = Literal["intro", "probe", "wrap"]


class TurnPlan(BaseModel):
    """What the interviewer should be doing on one specific turn.

    Produced deterministically in code (``coverage.plan_interview``) rather
    than by asking the LLM to "pick the least-covered competency" in prose.
    That instruction was unverifiable and drifted — a model that decided to
    keep drilling one topic left three competencies unprobed and the scorer
    then guessed at them. Planning in code makes coverage a property of the
    system, not a hope.
    """

    turn_index: int = Field(ge=1, description="1-based candidate question number")
    kind: TurnKind
    competency_id: str | None = None
    competency_name: str | None = None
    # A probe stem drawn from the competency, rotated across turns so the same
    # stem is not reused if the competency gets several slots.
    probe_hint: str | None = None
    # Human-readable justification, logged and shown to HR on the scorecard so
    # the question plan is inspectable rather than a black box.
    rationale: str = ""


class CoverageRow(BaseModel):
    """How much airtime one competency was planned to get."""

    competency_id: str
    competency_name: str
    weight: float
    planned_turns: int
    # planned_turns as a share of all probe turns. Compared against ``weight``
    # to show HR where the plan had to round against a competency because a
    # 10-question interview cannot subdivide further.
    planned_share: float


class CoverageReport(BaseModel):
    """Plan-level coverage summary — attached to the scorecard as evidence.

    ``uncovered`` is the honest bit: with 10 questions and 7 competencies, some
    competencies get zero dedicated turns. Saying so explicitly is what stops a
    scorer from inventing a confident score for something never asked about.
    """

    total_turns: int
    probe_turns: int
    rows: list[CoverageRow]
    uncovered: list[str] = Field(default_factory=list)
