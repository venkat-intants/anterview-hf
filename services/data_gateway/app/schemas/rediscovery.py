"""Pydantic models for talent-pool rediscovery — PH5-E3.

The response is the contract the Rediscovery screen is built from, so it is a
declared model rather than a bare ``dict``: a field the UI needs cannot then
quietly disappear from one branch of ``app/rediscovery.py::_assemble_result``.

ONE ``WhyItem`` FOR EVERY SIGNAL, with most fields optional. The alternative —
a discriminated union per signal — would be six models whose only difference
is which three fields are populated, and the renderer switches on ``signal``
either way. The route is served with ``response_model_exclude_none=True`` so a
scorecard item does not carry six empty exam fields over the wire.

WHAT IS NOT IN HERE, deliberately:
  * no ``why_sentence`` / ``summary`` — nothing on this response is written by a
    model. ``note`` on the similarity item is a FIXED sentence from
    ``app/rediscovery.py::SIMILARITY_NOTE``, the same for every row.
  * no hiring field. There is no ``status``, ``stage``, ``recommendation``,
    ``shortlist`` or ``decision`` anywhere in this file, and a rediscovery
    result has no way to express one.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Freshness = Literal["fresh", "ageing", "stale", "unverifiable"]
HeaderFreshness = Literal["fresh", "ageing", "stale", "unverifiable", "none"]
Signal = Literal[
    "resume_similarity", "resume_terms", "interviewer_scorecard", "round_result",
    "exam_attempt", "ai_interview",
]


class RediscoverySearchIn(BaseModel):
    """POST, not GET, and not because the body is large.

    A rediscovery search box is a place HR will type a candidate's name. A query
    string in a URL is recorded by every proxy, gateway and access log in the
    path, none of which this service controls — and the audit row this search
    writes deliberately carries only the query's LENGTH. Putting the query in a
    body keeps that decision meaningful.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    # The opening HR is searching FOR. Supplies the competency targets, and is
    # the ONLY thing that turns the evidence boost on; with no opening the rank
    # is pure relevance and evidence is still displayed.
    requisition_id: str | None = None
    limit: int = Field(default=20, ge=1, le=50)


class Citation(BaseModel):
    """Where a claim can be checked. ``href`` always comes from
    ``shared/agents/schema.py``'s route table, never from a path composed at a
    call site."""

    kind: str
    id: str
    label: str
    href: str | None = None


class WhyItem(BaseModel):
    """One contribution to one match, with whatever makes it checkable."""

    signal: Signal
    #: Percentage points of the final 0-100 score.
    contribution: int
    #: False for exactly one signal: ``resume_similarity``. A cosine similarity
    #: has no span to quote and no term to name, and the UI says so rather than
    #: narrating it.
    explainable: bool
    locator: str | None = None
    recorded_at: str | None = None
    freshness: Freshness | None = None
    #: Why there is no band — an undated item, or content that no longer exists.
    freshness_reason: str | None = None
    lifecycle: str | None = None
    produced_by: str | None = None
    #: The fixed sentence for the similarity leg. Never model output.
    note: str | None = None
    terms_matched: list[str] | None = None
    #: A bounded span of the CV around a lexical hit. Bracket markers, not HTML.
    snippet: str | None = None
    competency_id: str | None = None
    competency: str | None = None
    competency_ids: list[str] | None = None
    score: int | None = None
    of: int | None = None
    percent: float | None = None
    passed: bool | None = None
    round_title: str | None = None
    round_kind: str | None = None
    content_hidden_reason: str | None = None
    citation: Citation | None = None


class Eligibility(BaseModel):
    """When this person opted in, and when that lapses. Both read off the
    ledger row; ``expires_at`` is ``granted_at`` plus the window, never a stored
    value."""

    opted_in_at: str | None = None
    expires_at: str | None = None


class MatchWeights(BaseModel):
    semantic: float
    lexical: float
    evidence_max: float


class Breakdown(BaseModel):
    """Every number that produced the score, so it can be checked."""

    semantic: float
    lexical: float
    evidence_boost: float
    coverage: float
    freshness_factor: float
    covered_competencies: list[str] = Field(default_factory=list)
    semantic_available: bool
    weights: MatchWeights


class RediscoveryResult(BaseModel):
    applicant_id: str
    full_name: str
    current_title: str | None = None
    current_company: str | None = None
    years_experience: int | None = None
    score: int
    #: False when the only thing that caused this match was CV similarity. Such
    #: a row renders under its own heading, sorts below explained matches at
    #: equal score, and cannot be invited without an acknowledgement.
    explained: bool
    breakdown: Breakdown
    why: list[WhyItem]
    #: The WORST band among contributing evidence items — never the best, never
    #: a mean. ``none`` when there is no evidence at all.
    evidence_freshness: HeaderFreshness
    #: Computed from the bands and the explanation, never claimed.
    requires_review: bool
    review_reasons: list[str] = Field(default_factory=list)
    unexplained_note: str | None = None
    eligibility: Eligibility


class Universe(BaseModel):
    """How many of this company's applicants a rediscovery search can even see.

    Surfaced on every response because the honest answer is usually "very few":
    only candidates who opted in are here, and a historical candidate who never
    activated an account has no opt-in surface to reach at all.
    """

    eligible: int
    total: int


class TargetOpening(BaseModel):
    requisition_id: str
    title: str
    competency_ids: list[str] = Field(default_factory=list)
    competencies: int
    source: Literal["frozen_round_criteria", "none"]
    reason: str | None = None


class RediscoverySearchOut(BaseModel):
    #: False when the embedding service was unreachable: the results came from
    #: full text alone, and the screen must say so.
    semantic: bool
    universe: Universe
    #: How many eligible candidates had any signal at all, capped at
    #: ``rediscovery_search_limit``.
    matched: int
    returned: int
    weights: MatchWeights
    target: TargetOpening | None = None
    results: list[RediscoveryResult]


class UniverseOut(BaseModel):
    universe: Universe
    #: The window in months, so the screen's copy and the query cannot disagree.
    consent_months: int


def as_search_out(payload: dict[str, Any]) -> RediscoverySearchOut:
    """Validate the service layer's payload against the contract.

    Called on the way out of the router rather than trusted: this is where a
    field the frontend depends on going missing becomes a 500 in a test instead
    of an empty panel in production.
    """
    return RediscoverySearchOut.model_validate(payload)
