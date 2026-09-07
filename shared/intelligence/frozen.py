"""Rebuild a RoleProfile from a workflow round's frozen criteria — Group C, C8.

The problem
-----------
``derive_role_profile`` produces a profile from a title and a JD, optionally
refining it with an LLM call. That is right at authoring time and wrong at
scoring time: two derivations of the same role can differ (the model refines
differently, the taxonomy is updated), so a candidate scored today would be
measured against a rubric that is not the one their workflow was published with.

A published workflow stores its own rubric — competency id, name, kind, weight,
behavioural anchors and probe stems — copied at authoring time. This module
turns that stored copy back into a ``RoleProfile``, so every existing consumer
(``plan_interview``, ``render_scoring_rubric_block``, ``axis_weights``,
``render_competency_output_spec``) works unchanged against a frozen rubric
instead of a freshly derived one.

Two properties worth stating
----------------------------
**No network call.** Scoring a round reads its criteria and rebuilds the
profile locally. That removes a dependency, a latency and a failure mode from
the scoring path.

**Weights are renormalised, deliberately.** A round assessing three of a role's
six competencies gets weights summing to 1.0 across those three, not 0.5. The
round's own scale is what its threshold was set against; leaving the weights at
their role-wide values would silently halve every score the round produces.
"""

from __future__ import annotations

from typing import Any

from shared.intelligence.schema import (
    MAX_COMPETENCIES,
    MIN_COMPETENCIES,
    Anchors,
    Competency,
    RoleProfile,
)

# ``round_criteria.competency_kind`` is free text in the database, so a stored
# value can be one the current schema does not recognise — a kind from an older
# taxonomy, or simply NULL on a round authored before the field existed. A
# frozen rubric has to survive that: refusing would make one stale string break
# scoring for an entire round, months after anyone could fix it.
_VALID_KINDS: frozenset[str] = frozenset(
    {"technical", "practical", "domain", "behavioural", "communication"}
)
_KIND_FALLBACK = "technical"

# Used when a round was authored before anchors were frozen (they are nullable
# for exactly that reason). Deliberately generic: inventing role-specific band
# descriptions nobody wrote would be worse than admitting we do not have them,
# because a scorer would treat the invention as the employer's standard.
_GENERIC_ANCHORS = Anchors(
    low="Little or no evidence of this in the candidate's answers.",
    mid="Some evidence, at roughly the level the role expects.",
    high="Clear, specific evidence that exceeds what the role expects.",
)
_GENERIC_PROBE = "Ask for a concrete example that would evidence this."

# A frozen rubric with only one or two criteria is legitimate — a focused round
# may assess exactly one thing — but RoleProfile requires at least three. Padding
# would fabricate competencies, so the profile is built with what is there and
# the caller is told the shape differs. See ``profile_from_round_criteria``.
_MIN_FOR_PROFILE = MIN_COMPETENCIES


class FrozenRubricError(Exception):
    """The stored criteria cannot form a usable rubric."""


def _coerce_kind(value: Any) -> str:
    """Map a stored kind onto one the schema accepts, never raising."""
    kind = str(value or "").strip().lower()
    return kind if kind in _VALID_KINDS else _KIND_FALLBACK


def _to_competency(row: dict[str, Any], weight: float) -> Competency:
    anchors_raw = row.get("anchors") or {}
    anchors = (
        Anchors(
            low=str(anchors_raw.get("low") or _GENERIC_ANCHORS.low),
            mid=str(anchors_raw.get("mid") or _GENERIC_ANCHORS.mid),
            high=str(anchors_raw.get("high") or _GENERIC_ANCHORS.high),
        )
        if anchors_raw
        else _GENERIC_ANCHORS
    )
    probes = [str(p) for p in (row.get("probes") or []) if str(p).strip()]
    return Competency(
        id=row["competency_id"],
        name=row.get("competency_name") or row["competency_id"],
        # 'kind' drives axis affinity in render.axis_weights. Coerced rather
        # than trusted: see _VALID_KINDS above.
        kind=_coerce_kind(row.get("competency_kind")),  # type: ignore[arg-type]
        weight=weight,
        probes=probes or [_GENERIC_PROBE],
        anchors=anchors,
    )


def renormalise(weights: list[float]) -> list[float]:
    """Scale weights to sum to 1.0, preserving their relative proportions.

    A round assessing a subset of the role's competencies must be scored on its
    own scale. Falls back to an equal split when the stored weights sum to zero,
    which can only happen if the data is corrupt — equal is the least wrong
    assumption available and is better than dividing by zero.
    """
    total = sum(weights)
    if total <= 0:
        return [1.0 / len(weights)] * len(weights) if weights else []
    return [w / total for w in weights]


def profile_from_round_criteria(
    criteria: list[dict[str, Any]],
    *,
    job_title: str,
    round_title: str,
    workflow_id: str,
    round_id: str,
    domain_family: str | None = None,
    domain_label: str | None = None,
    seniority: str = "mid",
) -> RoleProfile:
    """Rebuild the exact rubric a round was published with.

    ``criteria`` are rows from ``round_criteria`` — dicts carrying at least
    ``competency_id``, ``competency_name``, ``weight`` and, where the round was
    authored after the rubric freeze, ``anchors`` and ``probes``.

    ``profile_id`` is deliberately derived from the workflow and round rather
    than hashed from a title and JD like a normal profile's. It says where the
    rubric came from, which is the question an audit of a stored scorecard
    actually asks.
    """
    rows = [c for c in criteria if c.get("competency_id")]
    if not rows:
        raise FrozenRubricError(f"{round_title}: no criteria stored for this round.")
    if len(rows) > MAX_COMPETENCIES:
        # More than the schema allows: keep the heaviest, since dropping the
        # lightest changes the score least.
        rows = sorted(rows, key=lambda c: float(c.get("weight") or 0), reverse=True)[
            :MAX_COMPETENCIES
        ]

    weights = renormalise([float(c.get("weight") or 0) for c in rows])
    competencies = [_to_competency(r, w) for r, w in zip(rows, weights, strict=True)]

    # RoleProfile requires at least MIN_COMPETENCIES. A focused round assessing
    # one or two things is legitimate, so pad the *shape* with the round's own
    # heaviest competency repeated? No — that would double-count it. Instead the
    # profile is built at the real length and the schema floor is respected by
    # refusing, so the caller can fall back to whole-role scoring rather than
    # silently scoring against a distorted rubric.
    if len(competencies) < _MIN_FOR_PROFILE:
        raise FrozenRubricError(
            f"{round_title}: only {len(competencies)} criterion(s) stored; "
            f"a rubric needs at least {_MIN_FOR_PROFILE}. Score against the "
            "whole role model instead."
        )

    return RoleProfile(
        profile_id=f"frozen:{workflow_id}:{round_id}",
        job_title=job_title,
        domain_family=domain_family or "frozen_rubric",
        domain_label=domain_label or round_title,
        seniority=seniority,  # type: ignore[arg-type]
        summary=(
            f"Rubric frozen for the '{round_title}' round. Scores are produced "
            "against the competencies this round was published to assess."
        ),
        competencies=competencies,
        # 'frozen' is not one of the derivation sources, so it is reported as
        # taxonomy-derived with the provenance carried in profile_id. A scorecard
        # citing frozen:<workflow>:<round> can be traced to the exact published
        # rubric without guessing.
        source="taxonomy",
    )


def has_frozen_anchors(criteria: list[dict[str, Any]]) -> bool:
    """True when every criterion carries its own behavioural bands.

    Rounds authored before the rubric freeze do not, and fall back to generic
    bands. Worth surfacing in the builder: a rubric without role-specific
    anchors still works, but it measures less precisely than one with them.
    """
    return bool(criteria) and all(
        isinstance(c.get("anchors"), dict) and c["anchors"].get("mid") for c in criteria
    )


def composite_from_criteria(breakdown: dict[str, dict[str, Any]]) -> float | None:
    """Weighted 0-10 score across a round's own criteria — the upper layer (C8).

    This is what a workflow round's advance threshold is compared against, and
    it is deliberately NOT the four-axis composite. The axes are the
    *comparison* layer, frozen so a score means the same thing across roles and
    cohorts (D-02); this is the *evaluation* layer, which varies by what the
    round set out to measure. Both come out of the same model call, so the two
    layers cost nothing extra per session.

    Lives in ``shared`` because both sides need it: ``feedback_billing``
    produces the per-criterion scores, and the ``data_gateway`` runner compares
    the result against the round's threshold.

    Weights arrive already renormalised to sum to 1.0 across the round's
    criteria (see :func:`renormalise`), so this is a plain weighted mean rather
    than a second normalisation.
    """
    if not breakdown:
        return None
    total_weight = sum(float(v.get("weight") or 0) for v in breakdown.values())
    if total_weight <= 0:
        return None
    weighted = sum(
        float(v.get("score") or 0) * float(v.get("weight") or 0)
        for v in breakdown.values()
    )
    return round(weighted / total_weight, 2)
