"""The governed metric registry — PH5-C2.

ONE SOURCE OF TRUTH for what a hiring metric means. Every consumer (the HR
analytics endpoints, the requisition dashboard, the copilot's funnel tool, the
nightly watcher) computes through :mod:`app.metrics.compute`, which reads
nothing but the ``Flag``/``Measure``/``Metric`` objects defined here — so two
consumers can never quietly disagree about what "interviewed" means, because
there is only one definition of it to read.

WHY THIS EXISTS (the defects it structurally closes)
Before this module, "interviewed" meant three different things depending on
which screen you were looking at (an AI scorecard existing, the ledger's
``interviewed`` status — which actually means "finished the workflow, awaiting
a decision" — or a human interviewer scorecard, counted nowhere); "hired"
excluded a reversed offer in some places and not others; and a stage-to-stage
rate could exceed 100% because a "conversion" was computed as one snapshot
count divided by another. None of that is a comment asking the next author to
be careful; it is closed structurally, here:

* **Flags** are versioned, named boolean facts about ONE application
  (``Flag(name, version, description, sql)``), each evaluated once in the
  single per-application CTE :mod:`app.metrics.compute` builds. ``sql`` is a
  real SQL predicate, not a placeholder — it references the base-table lateral
  aliases that CTE's fixed FROM/JOIN skeleton always provides (documented at
  the top of ``compute.py``), so a flag is exactly as inspectable as the query
  it becomes.
* **Metrics** combine flags with AND only (never OR, never NOT — an OR belongs
  inside one flag's own SQL). A rate's numerator flag set is always a SUPERSET
  of its denominator's, so ``count(A AND B) / count(A)`` can never exceed
  100%; that is enforced at import (:func:`validate_registry`), not left to a
  reviewer to notice.
* **The human-readable formula is generated from the spec** (:func:`_formula`),
  never hand-written, so the formula shown in the UI cannot drift from what is
  actually computed.
* **Everything is versioned with an effective date and a change note.** A new
  definition is a NEW version with a later ``effective_from``; the old version
  stays computable forever. ``published.lock.json`` freezes every published
  version's canonical spec by hash — see that file and
  ``ops/ci/check_metric_lock.py`` — so a definition already shipped cannot be
  edited in place, only superseded.
* **The lock also covers what gives a flag's SQL its meaning, not just the SQL
  itself** (evidence-audit finding, C2-8). A flag's ``sql`` is a predicate
  fragment, not a complete query — it means nothing except evaluated inside
  :mod:`app.metrics.compute`'s fixed FROM/JOIN skeleton, filtered to a cohort
  window predicate picked by ``CohortBasis``. Before this fix, editing that
  skeleton or a cohort window changed every published figure under an
  UNCHANGED lock and hash. :class:`EngineFragment` is the same
  ``{sha256, effective_from, added_on}`` shape as a Flag/Measure/Metric, keyed
  ``engine:name@version`` in the SAME lock file — but the SQL text lives in
  ``compute.py`` (where the skeleton and cohort predicates actually are), not
  here, so :func:`engine_fragment_lock_entries` is exported for
  ``app.metrics.compute.build_engine_lock_entries`` to call rather than this
  module importing ``compute.py`` (which already imports this one — that
  would be circular). See ``compute.py``'s module docstring for exactly which
  constants are locked this way, and what is NOT: the per-metric aggregation
  shape :func:`app.metrics.compute._plan_metric` generates (``count(*) FILTER
  (WHERE ...)`` vs ``percentile_cont`` vs ``avg`` vs a bucketed ``FILTER``,
  chosen from a metric's ``kind``) and the suppression control flow in
  :func:`app.metrics.compute._read_metric_value` are Python branches, not
  string constants — there is nothing to hash that is not already the source
  code itself. Both are exercised end-to-end by the integration suite (every
  flag/cohort/suppression test in ``tests/integration/test_ph5_w1_metrics.py``
  would fail if either changed the wrong value), but neither failure would, by
  itself, trip ``check_metric_lock.py`` the way an edited skeleton or cohort
  predicate now does.

VALIDATION AT IMPORT
:func:`validate_registry` runs once, at the bottom of this module, over the
real registry — so an invalid definition fails the service at startup and
fails CI at collection, the same guarantee ``shared/agents/schema.py``'s
``ToolSpec`` gives the agent tool registry. It is also exported and unit
tested directly against hand-built BAD registries (see
``tests/unit/test_ph5_w1_metrics_definitions.py``), because the real registry
is, by construction, always valid — a test that only imports this module
would never see a validation failure fire.

THIS MODULE NEVER WRITES. It has no import of a session/engine/connection
type and defines no SQL statement that is not a SELECT fragment; the AST test
in ``tests/unit/test_ph5_w1_metrics_definitions.py``
(``test_metrics_package_has_no_write_statements``) asserts nothing under
``app/metrics/`` contains an INSERT/UPDATE/DELETE keyword or imports a writer.
That is C1's "no AI-generated metric or recommendation can modify candidate
status", restated for the metric layer itself rather than for the agents that
read it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypeVar

CohortBasis = Literal["application", "decision", "hire"]
Dimension = Literal["source", "requisition", "interviewer_score_band"]
MetricKind = Literal["count", "rate", "median", "mean", "distribution"]

COHORT_BASES: frozenset[str] = frozenset({"application", "decision", "hire"})
#: ``interviewer_score_band`` added in PH5-E4 (Wave 2) — a hires' band on the
#: mean CURRENT human interviewer scorecard score. Added to the SAME
#: ``DIMENSIONS_REGISTRY``/``DIMENSIONS`` a metric's ``dimensions`` tuple is
#: validated against; a ``Metric``'s hash EXCLUDES ``dimensions`` (see
#: ``_canonical_spec``), so widening ``_HIRE_DIMS``/``_PIPELINE_DIMS`` below to
#: include it does not move any already-locked metric to a new version.
DIMENSIONS: frozenset[str] = frozenset({"source", "requisition", "interviewer_score_band"})
METRIC_KINDS: frozenset[str] = frozenset({"count", "rate", "median", "mean", "distribution"})

#: The date every version-1 definition in this file became effective. A new
#: version added later gets its OWN, later ``effective_from`` — this constant
#: is deliberately not reused for anything but the initial set.
_V1_DATE = date(2026, 9, 22)
#: PH5-E4 (Wave 2): when the calibration spec and the new score-band
#: dimension became effective — later than every V1 definition, since they
#: are new registry KINDS added after Wave 1 shipped, not part of what V1
#: meant.
_V2_DATE = date(2026, 9, 23)


class MetricDefinitionError(ValueError):
    """An invalid, duplicate or conflicting metric/flag/measure definition.

    Raised at import (see the bottom of this module) so a bad definition
    fails the service at startup and fails CI at collection — never reaches a
    dashboard silently wrong.
    """


# ---------------------------------------------------------------------------
# The offer-outcome vocabulary that means a recorded hire did not stand.
# ---------------------------------------------------------------------------
#: Mirrors ``OUTCOMES``/``REVERSED_OFFER_OUTCOMES`` in migrations
#: ``b9d1f3a5c7e2`` (offers) and ``8f41cb18a299`` (hire_checkins) — three
#: literals that must never drift apart, pinned by
#: ``test_reversed_offer_outcomes_matches_the_migrations``.
REVERSED_OFFER_OUTCOMES: tuple[str, ...] = ("offer_declined", "offer_expired", "offer_withdrawn")

#: "The hire stands" — ONE fragment, evaluated over an ``enrolments`` alias
#: ``e``. Every reader of "is this hire real" (the ``hired`` flag here, the
#: ``checkin_due`` flag's employment-start fallback, ``hr_pipeline``,
#: ``requisition_dashboard`` and ``company_board``'s existing inline copies,
#: and the ``hire_checkins_lifecycle`` trigger's INSERT check) must agree with
#: this text. The trigger is PL/pgSQL and spells the same three literals as
#: ``<> ALL(ARRAY[...])`` rather than ``NOT IN (...)`` — a unit test asserts
#: the trigger's copy has not silently picked up a fourth outcome instead of
#: asserting byte-identical SQL, since the two are different syntax for the
#: same rule. See that test for exactly what is and is not compared, and why.
HIRE_STANDS_SQL: str = (
    "e.status = 'hired' AND COALESCE(e.offer_outcome, '') NOT IN "
    "('offer_declined', 'offer_expired', 'offer_withdrawn')"
)

#: An interviewer's CURRENT scorecard — "the live one, or, when a correction
#: was withdrawn with its interviewer's removal, the submitted original it
#: superseded, which is still that person's last word" — taken verbatim from
#: ``app.interviewer_scorecards.summary_for_enrolments`` (minus its
#: assigned/late counting, which this layer has no use for). Evaluated over an
#: ``interviewer_scorecards`` alias ``s``, LEFT JOINed to a second alias
#: ``nxt`` on ``nxt.id = s.superseded_by_id``. Used by the ``interviewed``
#: flag's human-scorecard arm and by the ``hire_interviewer_score`` measure —
#: NOT by ``calibration_core.py`` or ``panel_workload.py``, whose own copies
#: ``test_ph4_wave3.py`` asserts the exact SQL text of and which move in Wave 2.
CURRENT_SCORECARD_SQL: str = (
    "s.status = 'submitted' AND (s.superseded_at IS NULL OR nxt.status = 'withdrawn')"
)

#: Flags that read a 90-day check-in — coverage (operational: has one been
#: recorded at all) and outcome (what it says). A metric that uses ANY of
#: these must stay within :data:`CHECKIN_SAFE_DIMENSIONS`, and the copilot
#: (PH5-C1 "check-in data never reaches a model") is filtered to metrics that
#: use NONE of them — see :func:`uses_checkin_data`.
CHECKIN_OPERATIONAL_FLAGS: frozenset[str] = frozenset({"checkin_due", "checkin_recorded"})
#: The check-in flags that reveal what a check-in actually SAID (as opposed to
#: merely that one exists) — a DPDP-sensitive outcome about a named former
#: candidate, never a decision, but not something a rate's numerator or a
#: drill-down should surface below the suppression floor either.
CHECKIN_OUTCOME_FLAGS: frozenset[str] = frozenset(
    {"retained_90d", "perf_below", "perf_meets", "perf_exceeds"}
)
CHECKIN_FLAGS: frozenset[str] = CHECKIN_OPERATIONAL_FLAGS | CHECKIN_OUTCOME_FLAGS

#: A metric that reads any :data:`CHECKIN_FLAGS` member may only be grouped by
#: these dimensions — validated at import (:func:`validate_registry`), the
#: same "raise before publication" precedent as ``DATA_CLASS_ROLES`` in
#: ``shared/agents/schema.py``. Wave 2 extends this set deliberately with
#: ``interviewer_score_band`` — a hires' band on the mean CURRENT human
#: interviewer scorecard score, never a candidate-authored or free-text
#: field — validated (:func:`_validate_score_band_dimension`) to derive from
#: nothing but ``hire_interviewer_score@1``.
CHECKIN_SAFE_DIMENSIONS: frozenset[str] = frozenset(
    {"source", "requisition", "interviewer_score_band"}
)

#: The interviewer_score_band bucketing (PH5-E4) — fixed, anchored edges on
#: the governed 1-5 scale (never a quantile, which would be data-dependent
#: and could not be governed the same way): below_3 [1,3), 3_to_4 [3,4),
#: 4_plus [4,5], or none (no CURRENT human interviewer scorecard at all).
#: References ONLY ``m_hire_interviewer_score`` — the ``app_facts`` output
#: column :mod:`app.metrics.compute` builds for the ``hire_interviewer_score@1``
#: measure — never a raw lateral alias (this SQL is evaluated OUTSIDE
#: ``app_base``, over ``app_facts``'s own columns, the same scope
#: ``_COHORT_PREDICATES`` and the ``source``/``requisition`` dimensions'
#: ``group_sql`` already run in). :func:`_validate_score_band_dimension`
#: checks this structurally at import.
INTERVIEWER_SCORE_BAND_SQL: str = (
    "(CASE WHEN m_hire_interviewer_score IS NULL THEN 'none' "
    "WHEN m_hire_interviewer_score < 3 THEN 'below_3' "
    "WHEN m_hire_interviewer_score < 4 THEN '3_to_4' "
    "ELSE '4_plus' END)"
)

#: The single measure :data:`INTERVIEWER_SCORE_BAND_SQL` may reference —
#: pinned so a future edit cannot quietly widen what a check-in-safe
#: grouping is allowed to depend on.
INTERVIEWER_SCORE_BAND_MEASURE: str = "hire_interviewer_score@1"

#: Display labels for the band dimension's values, in the fixed order the
#: outcome-signals API reports them (below_3, 3_to_4, 4_plus, none) — never
#: derived from data, since the edges themselves are not.
INTERVIEWER_SCORE_BAND_LABELS: dict[str, str] = {
    "below_3": "Below 3",
    "3_to_4": "3 to under 4",
    "4_plus": "4 and above",
    "none": "No human scorecard",
}


# ---------------------------------------------------------------------------
# Flag / Measure / Metric
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Flag:
    """A named, versioned boolean fact about ONE application.

    ``sql`` is a real SQL predicate, evaluated in the per-application CTE
    :mod:`app.metrics.compute` builds — it references that CTE's documented
    lateral aliases (``e``, ``a``, ``r``, ``la``, ``st``, ``ex``, ``rr``,
    ``iv``, ``hsc``, ``off``, ``hc``; see ``compute.py``'s module docstring),
    never a view, a function, or another flag's already-computed column
    (Postgres does not let one SELECT-list expression see another's alias).
    """

    name: str
    version: int
    effective_from: date
    description: str
    sql: str

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class Measure:
    """A named, versioned NUMERIC fact about one application (e.g. a day count).

    ``sql`` is a scalar SQL expression over the same lateral aliases a
    :class:`Flag` may use. It is free to evaluate to ``NULL`` for a row the
    measure does not apply to (a non-hire has no ``days_to_hire``) — the
    metric that reads it supplies the population filter (its ``numerator``
    flags), so the measure itself does not need to.
    """

    name: str
    version: int
    effective_from: date
    description: str
    sql: str

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class Metric:
    """A governed, versioned metric: a count, a rate, or an aggregate over a measure.

    ``numerator``/``denominator``/``buckets`` are tuples of ``"flag@version"``
    keys, combined with AND only. A ``rate``'s ``numerator`` is always a
    SUPERSET of its ``denominator`` — ``count(A AND B) / count(A)`` — which is
    what makes a stage-to-stage conversion structurally unable to exceed 100%.
    """

    name: str
    version: int
    effective_from: date
    kind: MetricKind
    label: str
    description: str
    cohort_bases: tuple[CohortBasis, ...]
    dimensions: tuple[Dimension, ...]
    numerator: tuple[str, ...] = ()
    denominator: tuple[str, ...] = ()
    measure: str | None = None
    buckets: tuple[str, ...] | None = None
    change_note: str = ""

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def formula(self) -> str:
        return _formula(self)


@dataclass(frozen=True)
class MetricDimension:
    """A governed grouping — ``source`` or ``requisition`` today.

    Versioned like everything else here, and locked the same way, but NOT
    part of a :class:`Metric`'s hashed spec (see ``_canonical_spec``): adding
    a way to GROUP a metric never changes the metric's own number, so it
    cannot be what makes a metric's version bump.
    """

    name: str
    version: int
    effective_from: date
    label: str
    description: str
    group_sql: str

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class Rule:
    """A locked, named CONFIGURATION VALUE the compute layer reads — e.g. the
    small-cell suppression floor — so a DPDP-governed threshold is a registry
    entry with a hash and a lock, not a bare constant a diff could change
    unnoticed."""

    name: str
    version: int
    effective_from: date
    description: str
    value: Any

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class CalibrationSpec:
    """A governed, versioned calibration METHOD — PH5-E4 — locked the same way
    a :class:`Metric` is (``calibration:name@version``, in the SAME
    ``published.lock.json``), so a threshold ``app.calibration_core.calibrate``
    reads cannot silently change under an unchanged ``registry_hash``.

    NOT a :class:`Metric`: its unit of analysis is one JUDGEMENT (an
    enrolment x round x competency x interviewer row), never one application,
    so it is not expressible as flags/measures over the ``app_facts`` cohort
    engine — see ``app.calibration_core`` and ``app.panel_workload`` for
    where it is actually computed. ``thresholds`` is a flat, JSON-serialisable
    mapping (every value already the exact type the API echoes back as
    ``rules``), hashed as part of this spec's canonical form like every other
    registry entry.
    """

    name: str
    version: int
    effective_from: date
    description: str
    method: str
    unit: str
    cohort_bases: tuple[str, ...]
    thresholds: dict[str, Any]
    change_note: str = ""

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class EngineFragment:
    """A locked, named SQL constant OUTSIDE the Flag/Measure/Metric registry
    that every flag's and measure's SQL is evaluated INSIDE — the FROM/JOIN
    skeleton :mod:`app.metrics.compute` builds ``app_base`` from, and the
    per-``CohortBasis`` window predicate ``app_facts`` filters it by (C2-8,
    evidence audit). Neither is a fact about one application (nothing here
    ever appears in a ``numerator``/``denominator``/``buckets`` tuple), so it
    is not a :class:`Flag`; it is what gives every flag's ``sql`` its
    MEANING. Locked the same ``{sha256, effective_from, added_on}`` shape,
    keyed ``engine:name@version`` in the SAME ``published.lock.json`` — see
    :func:`engine_fragment_lock_entries`, and this module's own docstring for
    why the SQL text itself lives in ``compute.py`` rather than here.
    """

    name: str
    version: int
    effective_from: date
    description: str
    sql: str

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


def engine_fragment_lock_entries(
    fragments: tuple[EngineFragment, ...],
) -> dict[str, dict[str, Any]]:
    """The lock entries a tuple of :class:`EngineFragment` implies —
    ``{"engine:name@version": {sha256, effective_from, added_on}}`` — the same
    shape :func:`build_lock_entries` produces for a Flag/Measure/Metric/
    Dimension/Rule, so both merge into one ``published.lock.json`` under one
    hashing rule and one append-only check (``ops/ci/check_metric_lock.py``
    needs no change: it compares opaque JSON keys, never parses the
    ``kind:name@version`` structure).

    A free function taking the fragments as an argument, not a global this
    module owns, because the fragments THEMSELVES are ``compute.py``'s module
    constants (the FROM/JOIN skeleton, the cohort predicates) — this module
    importing ``compute.py`` to reach them would be circular, since
    ``compute.py`` already imports ``app.metrics.definitions``. Call this from
    ``app.metrics.compute.build_engine_lock_entries()`` instead.
    """
    entries: dict[str, dict[str, Any]] = {}
    for item in fragments:
        spec = {
            "name": item.name,
            "version": item.version,
            "effective_from": item.effective_from.isoformat(),
            "description": item.description,
            "sql": item.sql,
            "_type": "EngineFragment",
        }
        entries[f"engine:{item.key}"] = {
            "sha256": _sha256_of(spec),
            "effective_from": item.effective_from.isoformat(),
            "added_on": item.effective_from.isoformat(),
        }
    return entries


def _bare(flag_key: str) -> str:
    """``"hired@1"`` -> ``"hired"`` — for a formula a person reads."""
    return flag_key.split("@", 1)[0]


def _formula(m: Metric) -> str:
    """The formula shown in the UI, generated from the spec so it cannot drift
    from what :mod:`app.metrics.compute` actually runs."""
    numerator_names = " AND ".join(_bare(k) for k in m.numerator)
    if m.kind == "count":
        return f"count({numerator_names})"
    if m.kind == "rate":
        denominator_names = " AND ".join(_bare(k) for k in m.denominator)
        return f"count({numerator_names}) / count({denominator_names})"
    if m.kind in ("median", "mean"):
        measure_name = _bare(m.measure) if m.measure else "?"
        return f"{m.kind}({measure_name}) over applications where {numerator_names}"
    if m.kind == "distribution":
        bucket_names = ", ".join(_bare(k) for k in (m.buckets or ()))
        return f"share of [{bucket_names}] over applications where {numerator_names}"
    raise MetricDefinitionError(f"{m.key}: unknown kind {m.kind!r}")  # unreachable post-validation


T = TypeVar("T", Flag, Measure, MetricDimension, Rule, CalibrationSpec)


def _index(items: tuple[T, ...]) -> dict[str, T]:
    """Key a tuple of Flags/Measures/Dimensions/Rules/CalibrationSpecs by
    ``name@version``, raising on a duplicate."""
    out: dict[str, T] = {}
    for item in items:
        if item.key in out:
            raise MetricDefinitionError(f"duplicate definition: {item.key}")
        out[item.key] = item
    return out


def _index_metrics(items: tuple[Metric, ...]) -> dict[str, Metric]:
    out: dict[str, Metric] = {}
    for item in items:
        if item.key in out:
            raise MetricDefinitionError(f"duplicate definition: {item.key}")
        out[item.key] = item
    return out


# ---------------------------------------------------------------------------
# Flags, version 1 — per application (enrolment ``e``), base tables only.
#
# See ``compute.py``'s module docstring for exactly what each lateral alias
# below (``la``, ``st``, ``ex``, ``rr``, ``iv``, ``hsc``, ``off``, ``hc``)
# selects and from which base tables. Nothing here reads a view.
# ---------------------------------------------------------------------------
FLAGS: dict[str, Flag] = _index((
    Flag("applied", 1, _V1_DATE, "Every application in the cohort.", "TRUE"),
    Flag(
        "screened", 1, _V1_DATE,
        "The stage ledger ever moved this application to 'shortlisted', or to "
        "any status that comes after it (interviewed, hired) — the human "
        "screen, or anything beyond it. A straight new -> rejected never "
        "passed a screen, which is deliberate: HR can reject before shortlisting.",
        "COALESCE(st.screened, FALSE)",
    ),
    Flag(
        "assessed", 1, _V1_DATE,
        "A submitted exam attempt attributable to this application (by its "
        "assignment's enrolment_id, or — for an attempt recorded before that "
        "link existed — because this person has exactly one application at "
        "the company), or any round_results row recorded directly against it.",
        "(COALESCE(ex.legacy_or_direct, FALSE) OR COALESCE(rr.has_result, FALSE))",
    ),
    Flag(
        "interviewed", 1, _V1_DATE,
        "A completed AI interview session, or a submitted and non-superseded "
        "human interviewer scorecard, attributable to this application. "
        "Deliberately NOT the ledger's 'interviewed' status, which means "
        "'finished the workflow, awaiting a decision'.",
        "(COALESCE(iv.ai_completed, FALSE) OR COALESCE(hsc.submitted, FALSE))",
    ),
    Flag(
        "selected", 1, _V1_DATE,
        "The company chose this candidate: an offer reached them (sent, "
        "accepted, declined or expired), or the ledger ever recorded a hire "
        "— even one later reversed, which is still a company having chosen.",
        "(COALESCE(off.reached, FALSE) OR COALESCE(st.hired_ever, FALSE))",
    ),
    Flag("hired", 1, _V1_DATE, "A hire that stands (not declined, expired or "
         "withdrawn after the fact).", HIRE_STANDS_SQL),
    Flag("rejected", 1, _V1_DATE, "The application's current status is 'rejected'.",
         "e.status = 'rejected'"),
    Flag("offer_sent", 1, _V1_DATE, "An offer for this application reached the candidate.",
         "COALESCE(off.reached, FALSE)"),
    Flag("offer_accepted", 1, _V1_DATE, "An offer for this application was accepted.",
         "COALESCE(off.accepted, FALSE)"),
    Flag(
        "checkin_due", 1, _V1_DATE,
        "A hire that stands, whose employment start (the accepted offer's "
        "start date, else the ledger's first move into hired) was at least "
        "90 days ago. A check-in can only be recorded up to 180 days after "
        "the start date and is deleted 24 months after it was first "
        "recorded, so coverage is low for hires from before check-ins "
        "existed, and for hires more than about two years ago.",
        f"(({HIRE_STANDS_SQL}) AND COALESCE(off.accepted_start, st.hired_at::date) "
        "<= (CURRENT_DATE - INTERVAL '90 days'))",
    ),
    Flag("checkin_recorded", 1, _V1_DATE,
         "A live (not superseded) 90-day check-in exists for this hire.",
         "COALESCE(hc.recorded, FALSE)"),
    Flag("retained_90d", 1, _V1_DATE,
         "The live 90-day check-in says the hire is still employed.",
         "COALESCE(hc.retained, FALSE)"),
    Flag("perf_below", 1, _V1_DATE,
         "The live 90-day check-in rates performance below expectations.",
         "COALESCE(hc.perf_below, FALSE)"),
    Flag("perf_meets", 1, _V1_DATE,
         "The live 90-day check-in rates performance as meeting expectations.",
         "COALESCE(hc.perf_meets, FALSE)"),
    Flag("perf_exceeds", 1, _V1_DATE,
         "The live 90-day check-in rates performance as exceeding expectations.",
         "COALESCE(hc.perf_exceeds, FALSE)"),
))

# ---------------------------------------------------------------------------
# Measures, version 1.
# ---------------------------------------------------------------------------
MEASURES: dict[str, Measure] = _index((
    Measure(
        "days_to_hire", 1, _V1_DATE,
        "Days from the application landing to the ledger's first move into "
        "hired. NULL for an application never hired.",
        "EXTRACT(EPOCH FROM (st.hired_at - e.created_at)) / 86400.0",
    ),
    Measure(
        "hire_interviewer_score", 1, _V1_DATE,
        "The mean of the CURRENT (see CURRENT_SCORECARD_SQL — the live "
        "scorecard, or a withdrawn correction's submitted original) HUMAN "
        "interviewer scorecard scores for this application, excluding any "
        "criterion marked not-assessed. Never an AI interview score — those "
        "are purged 90 days after the session and are not a quality-of-hire "
        "input.",
        "hsc.mean_score",
    ),
))

# ---------------------------------------------------------------------------
# Dimensions, version 1 — governed groupings. NOT part of a metric's hashed
# spec (see ``_canonical_spec``): a dimension is how a metric's population is
# split, never a change to what is being counted.
# ---------------------------------------------------------------------------
DIMENSIONS_REGISTRY: dict[str, MetricDimension] = _index((
    MetricDimension(
        "source", 1, _V1_DATE, "Source",
        "The acquisition channel recorded on the application (PH3-B1's closed "
        "vocabulary); 'unknown' shown as Unknown / untracked.",
        "source",
    ),
    MetricDimension(
        "requisition", 1, _V1_DATE, "Opening",
        "The job requisition (opening) the application belongs to.",
        "requisition_id",
    ),
    MetricDimension(
        "interviewer_score_band", 1, _V2_DATE, "Interviewer score band",
        "The hires' band on the mean CURRENT (see CURRENT_SCORECARD_SQL) "
        "human interviewer scorecard score: below_3 [1,3), 3_to_4 [3,4), "
        "4_plus [4,5], or none (no current human scorecard at all). Fixed, "
        "anchored edges on the governed 1-5 scale, not quantiles, so the "
        "boundary is itself governed rather than data-dependent. New in "
        "PH5-E4.",
        INTERVIEWER_SCORE_BAND_SQL,
    ),
))

# ---------------------------------------------------------------------------
# Rules, version 1 — locked configuration values, not bare constants.
# ---------------------------------------------------------------------------
RULES: dict[str, Rule] = _index((
    Rule(
        "min_cell", 1, _V1_DATE,
        "The smallest population a rate's denominator, or a median/mean/"
        "distribution's population, may have before `suppressed` is set. For "
        "a metric that reads a check-in flag (coverage or outcome), the "
        "VALUE is also nulled below this floor — and, for a check-in OUTCOME "
        "flag specifically, the numerator too — a DPDP small-cell control on "
        "a signal about named former employees. For every OTHER metric (the "
        "ordinary hiring-pipeline rates, time_to_hire_days, "
        "hire_interviewer_score), `suppressed` is advisory only: the real "
        "value and numerator are still shown, so a small company's numbers "
        "are never reported as 'no data' when there is data.",
        5,
    ),
))

# ---------------------------------------------------------------------------
# Calibration specs, version 1 — PH5-E4. NOT a Flag/Measure/Metric: its unit
# of analysis is one judgement, not one application. Locked in the SAME
# published.lock.json as ``calibration:name@version``.
# ---------------------------------------------------------------------------
CALIBRATION_SPECS: dict[str, CalibrationSpec] = _index((
    CalibrationSpec(
        "interviewer_calibration", 1, _V2_DATE,
        "Whether an interviewer scores the SAME candidates differently from "
        "the rest of the panel, on a frozen criterion (round_id, "
        "competency_id) — see app.calibration_core for the paired-panel-mean "
        "method this spec parameterises, and app.panel_workload for where it "
        "is computed. Thresholds only; the method itself is code, not data.",
        method="paired_panel_mean", unit="judgement",
        cohort_bases=("scorecard_submitted",),
        thresholds={
            "min_candidates": 5,
            "min_pairs": 5,
            "meaningful_delta": 0.75,
            "min_same_direction_share": 0.7,
            "min_interviewers_for_baseline": 2,
            "wide_disagreement_range": 1.5,
            "min_span_days": 7,
            "max_span_days": 366,
            "scale": "1-5",
        },
        change_note="New in PH5-E4: governs the thresholds "
        "app.calibration_core previously held as bare module constants "
        "(MIN_PAIRS, MEANINGFUL_DELTA, MIN_CANDIDATES), and adds "
        "min_same_direction_share (a sign-consistency guard: one wild score "
        "no longer trips the flag on its own) and "
        "min_interviewers_for_baseline (a panel baseline needs more than one "
        "person's scores).",
    ),
))


def current_calibration_specs(as_of: date | None = None) -> dict[str, CalibrationSpec]:
    return _current(CALIBRATION_SPECS.values(), as_of or date.today())


#: The one calibration spec this codebase currently computes —
#: ``app.calibration_core``/``app.panel_workload`` read this rather than
#: re-deriving the name.
CALIBRATION_SPEC_NAME = "interviewer_calibration"


def calibration_spec(as_of: date | None = None) -> CalibrationSpec:
    """The CURRENT ``interviewer_calibration`` spec — thresholds and metadata,
    never re-derived by a caller. Raises ``KeyError`` if ``as_of`` predates
    every published version, which cannot happen for ``None`` (today)."""
    return current_calibration_specs(as_of)[CALIBRATION_SPEC_NAME]


# ---------------------------------------------------------------------------
# Metrics, version 1, effective 2026-09-22.
# ---------------------------------------------------------------------------
_PIPELINE_COHORTS: tuple[CohortBasis, ...] = ("application", "decision")
#: PH5-E4 adds ``interviewer_score_band`` — harmless for the metrics that
#: never use check-in data (their DPDP validation never even looks at
#: ``dimensions``), and required for ``application_to_hire``, which the
#: outcome-signals "decision" cohort DOES group this way (see
#: ``routers/hr_metrics.py``). Excluded from a Metric's hash (see
#: ``_canonical_spec``), so this changes no already-locked metric's version.
_PIPELINE_DIMS: tuple[Dimension, ...] = ("source", "requisition", "interviewer_score_band")
_HIRE_COHORTS: tuple[CohortBasis, ...] = ("hire",)
#: PH5-E4: the outcome-signals "hire" cohort groups every one of these five
#: metrics by ``interviewer_score_band`` (routers/hr_metrics.py). Three of
#: them (checkin_coverage, retention_90d, performance_90d) read check-in
#: flags, which is exactly why CHECKIN_SAFE_DIMENSIONS had to grow to include
#: it — validated at import, below.
_HIRE_DIMS: tuple[Dimension, ...] = ("source", "requisition", "interviewer_score_band")

METRICS: dict[str, Metric] = _index_metrics((
    Metric("applications", 1, _V1_DATE, "count", "Applications",
           "Every application in the cohort.", _PIPELINE_COHORTS, _PIPELINE_DIMS,
           numerator=("applied@1",),
           change_note="New in PH5-C2."),
    Metric("screened", 1, _V1_DATE, "count", "Screened",
           "Applications the ledger ever moved to shortlisted or beyond.",
           _PIPELINE_COHORTS, _PIPELINE_DIMS, numerator=("screened@1",),
           change_note="New in PH5-C2."),
    Metric("assessed", 1, _V1_DATE, "count", "Assessed",
           "Applications with a submitted exam attempt or a recorded round result.",
           _PIPELINE_COHORTS, _PIPELINE_DIMS, numerator=("assessed@1",),
           change_note="New in PH5-C2. Previously (HrConversion.ever_sat_exam) an "
           "attempt on ANY application by the same person counted for all of "
           "them; this no longer bleeds across applications."),
    Metric("interviewed", 1, _V1_DATE, "count", "Interviewed",
           "Applications with a completed AI interview or a submitted human "
           "interviewer scorecard.", _PIPELINE_COHORTS, _PIPELINE_DIMS,
           numerator=("interviewed@1",),
           change_note="New in PH5-C2. Previously (HrConversion.ever_interviewed) "
           "this was the ledger's 'interviewed' status, which means 'finished "
           "the workflow, awaiting a decision' rather than 'was interviewed', "
           "and never counted a human interviewer scorecard."),
    Metric("selected", 1, _V1_DATE, "count", "Selected",
           "Applications the company chose: an offer reached the candidate, "
           "or the ledger ever recorded a hire.", _PIPELINE_COHORTS, _PIPELINE_DIMS,
           numerator=("selected@1",), change_note="New in PH5-C2."),
    # `hires` carries THREE cohort bases (application/decision, per the
    # pipeline section, AND hire, since it is also the population every
    # hire-cohort metric is measured over) — the one metric in this file that
    # is not `_PIPELINE_COHORTS`.
    Metric("hires", 1, _V1_DATE, "count", "Hires",
           "Applications with a hire that stands.",
           ("application", "decision", "hire"), _PIPELINE_DIMS,
           numerator=("hired@1",),
           change_note="New in PH5-C2. Previously (HrFunnel.hired / "
           "HrConversion.ever_hired) computed independently in three places; "
           "now the same flag everywhere (see the cross-consumer consistency "
           "test)."),
    Metric("rejections", 1, _V1_DATE, "count", "Rejections",
           "Applications currently rejected.", _PIPELINE_COHORTS, _PIPELINE_DIMS,
           numerator=("rejected@1",), change_note="New in PH5-C2."),

    Metric("application_to_screen", 1, _V1_DATE, "rate", "Application → screen",
           "Share of applications ever screened.", _PIPELINE_COHORTS, _PIPELINE_DIMS,
           numerator=("applied@1", "screened@1"), denominator=("applied@1",),
           change_note="New in PH5-C2."),
    Metric("application_to_assess", 1, _V1_DATE, "rate", "Application → assessment",
           "Share of applications ever assessed.", _PIPELINE_COHORTS, _PIPELINE_DIMS,
           numerator=("applied@1", "assessed@1"), denominator=("applied@1",),
           change_note="New in PH5-C2."),
    Metric("application_to_interview", 1, _V1_DATE, "rate", "Application → interview",
           "Share of applications ever interviewed.", _PIPELINE_COHORTS, _PIPELINE_DIMS,
           numerator=("applied@1", "interviewed@1"), denominator=("applied@1",),
           change_note="New in PH5-C2. Read from application_progress by the "
           "copilot and the watcher before this; both now agree with this "
           "definition (see the cross-consumer consistency test)."),
    Metric("application_to_hire", 1, _V1_DATE, "rate", "Application → hire",
           "Share of applications that end in a hire that stands.",
           _PIPELINE_COHORTS, _PIPELINE_DIMS,
           numerator=("applied@1", "hired@1"), denominator=("applied@1",),
           change_note="New in PH5-C2. Previously (HrConversion.pct_hired) "
           "counted any ledger move to hired, including one later reversed."),
    Metric("screen_to_interview", 1, _V1_DATE, "rate", "Screen → interview",
           "Of applications screened, the share that reached interview.",
           _PIPELINE_COHORTS, _PIPELINE_DIMS,
           numerator=("screened@1", "interviewed@1"), denominator=("screened@1",),
           change_note="New in PH5-C2. Structurally cannot exceed 100% — the "
           "numerator flag set is a superset of the denominator's."),
    Metric("interview_to_hire", 1, _V1_DATE, "rate", "Interview → hire",
           "Of applications interviewed, the share that end in a hire that stands.",
           _PIPELINE_COHORTS, _PIPELINE_DIMS,
           numerator=("interviewed@1", "hired@1"), denominator=("interviewed@1",),
           change_note="New in PH5-C2."),
    Metric("offer_acceptance", 1, _V1_DATE, "rate", "Offer acceptance",
           "Of offers that reached a candidate, the share accepted.",
           _PIPELINE_COHORTS, _PIPELINE_DIMS,
           numerator=("offer_sent@1", "offer_accepted@1"), denominator=("offer_sent@1",),
           change_note="New in PH5-C2."),

    Metric("time_to_hire_days", 1, _V1_DATE, "median", "Time to hire (days)",
           "Median days from application to hire, for hires in the cohort.",
           _HIRE_COHORTS, _HIRE_DIMS, numerator=("hired@1",), measure="days_to_hire@1",
           change_note="New in PH5-C2. NOT the same measure HrVelocity used, "
           "though both read the stage ledger rather than updated_at: "
           "HrVelocity.median_time_to_hire_days started the clock at the "
           "ledger's first move to 'new' (MIN(occurred_at) FILTER (to_status "
           "= 'new'), joined from stage_transitions — silently excluding any "
           "enrolment with no such row at all); days_to_hire@1 starts it at "
           "enrolments.created_at, which every enrolment has. Now "
           "cohort-filterable and shared with every other consumer."),
    Metric("checkin_coverage", 1, _V1_DATE, "rate", "Check-in coverage",
           "Of hires due a 90-day check-in, the share that have one recorded. "
           "A check-in can only be recorded up to 180 days after the start "
           "date and is deleted 24 months after it was first recorded, so "
           "coverage is low for hires from before check-ins existed, and "
           "for hires more than about two years ago.",
           _HIRE_COHORTS, _HIRE_DIMS,
           numerator=("checkin_due@1", "checkin_recorded@1"), denominator=("checkin_due@1",),
           change_note="New in PH5-C2 / D5-2."),
    Metric("retention_90d", 1, _V1_DATE, "rate", "90-day retention",
           "Of hires with a recorded check-in, the share still employed. A "
           "SIGNAL about past hires, never a decision about any candidate. "
           "A check-in can only be recorded up to 180 days after the start "
           "date and is deleted 24 months after it was first recorded, so "
           "coverage is low for hires from before check-ins existed, and "
           "for hires more than about two years ago.",
           _HIRE_COHORTS, _HIRE_DIMS,
           numerator=("checkin_recorded@1", "retained_90d@1"),
           denominator=("checkin_recorded@1",), change_note="New in PH5-C2 / D5-2."),
    Metric("performance_90d", 1, _V1_DATE, "distribution", "90-day performance mix",
           "Of hires with a recorded check-in, the performance rating split. "
           "A SIGNAL about past hires, never a decision about any candidate.",
           _HIRE_COHORTS, _HIRE_DIMS, numerator=("checkin_recorded@1",),
           buckets=("perf_below@1", "perf_meets@1", "perf_exceeds@1"),
           change_note="New in PH5-C2 / D5-2."),
    Metric("hire_interviewer_score", 1, _V1_DATE, "mean", "Hires' interviewer score",
           "Mean submitted human interviewer scorecard score of hires in the "
           "cohort. Built from interviewer_scorecard_scores ONLY — never an "
           "AI interview score. A SIGNAL about past hires, never a decision.",
           _HIRE_COHORTS, _HIRE_DIMS, numerator=("hired@1",),
           measure="hire_interviewer_score@1",
           change_note="New in PH5-C2 / C1: human scorecard results feed "
           "quality-of-hire for the first time."),
))


# ---------------------------------------------------------------------------
# Check-in data governance — PH5-C1 "check-in data never reaches a model".
# ---------------------------------------------------------------------------
def _referenced_flag_names(metric: Metric) -> frozenset[str]:
    return frozenset(
        _bare(k) for k in (*metric.numerator, *metric.denominator, *(metric.buckets or ()))
    )


def uses_checkin_data(metric: Metric) -> bool:
    """True if this metric reads ANY 90-day check-in flag, coverage or outcome.

    The copilot tool ``get_funnel_analytics`` and the nightly watcher's
    ``funnel_health`` input are both filtered through this — check-in data,
    which is about a NAMED former candidate's post-hire outcome, must never
    reach an LLM prompt.
    """
    return bool(_referenced_flag_names(metric) & CHECKIN_FLAGS)


def uses_checkin_outcome(metric: Metric) -> bool:
    """True if this metric's numerator or buckets read what a check-in SAID
    (as opposed to merely that one was recorded). Used for response-shape
    suppression (numerator also nulled) and for the members drill-down gate."""
    names = frozenset(_bare(k) for k in (*metric.numerator, *(metric.buckets or ())))
    return bool(names & CHECKIN_OUTCOME_FLAGS)


def checkin_outcome_drilldown_blocked(metric: Metric, part: str) -> bool:
    """True when a members drill-down for ``metric``/``part`` would show
    individually-identifying check-in OUTCOME data rather than an aggregate.

    Coverage (``checkin_due``/``checkin_recorded``) is operational — who has,
    or has not yet had, a check-in recorded — and is always allowed
    (``checkin_coverage``, both parts). ``retention_90d`` is allowed only for
    its DENOMINATOR (who was covered, not what they said).
    ``performance_90d`` has no denominator at all (a distribution), so it has
    no safe part and is always blocked.
    """
    if not uses_checkin_outcome(metric):
        return False
    return not (metric.name == "retention_90d" and part == "denominator")


def drillable(metric: Metric) -> dict[str, bool]:
    """Which parts of this metric ``GET /hr/analytics/members`` will drill
    into — ``{"numerator": bool, "denominator": bool}``, shown on
    ``GET /hr/metrics/definitions`` so the web screen never hard-codes this.

    Derived from :func:`checkin_outcome_drilldown_blocked` — the SAME rule
    the members route uses to refuse a request — so the advertised
    capability and the enforced one cannot disagree. A ``count`` metric has
    only ever had one population (its numerator; the route ignores ``part``
    for it) and is always drillable both ways by convention, matching how
    the route already treats it.
    """
    if metric.kind == "count":
        return {"numerator": True, "denominator": True}
    return {
        "numerator": not checkin_outcome_drilldown_blocked(metric, "numerator"),
        "denominator": not checkin_outcome_drilldown_blocked(metric, "denominator"),
    }


# ---------------------------------------------------------------------------
# Validation — runs at import over the real registry, and is exported for
# tests to run again over hand-built BAD registries.
# ---------------------------------------------------------------------------
def _flag_ref(key: str, flags: dict[str, Flag], owner: str) -> Flag:
    if "@" not in key:
        raise MetricDefinitionError(f"{owner}: unversioned flag reference {key!r}")
    if key not in flags:
        raise MetricDefinitionError(f"{owner}: unknown flag reference {key!r}")
    return flags[key]


def validate_registry(
    flags: dict[str, Flag], measures: dict[str, Measure], metrics: dict[str, Metric]
) -> None:
    """Raise :class:`MetricDefinitionError` on any invalid or conflicting definition.

    Checked, over the registry passed in (never a global — so a test can call
    this with a deliberately broken registry without touching the real one):

    * every flag reference (numerator, denominator, measure, bucket) names a
      real, versioned ``Flag``/``Measure``;
    * ``cohort_bases``, ``dimensions`` and ``kind`` are each a member of the
      closed set this module recognises;
    * a ``count`` metric has a numerator and no denominator; a ``rate`` has
      both, and its numerator flag set is a SUPERSET of its denominator's
      (never just overlapping — that is the 175%-conversion fix, structural);
      ``median``/``mean`` name a ``measure`` and no denominator; a
      ``distribution`` names ``buckets`` (each a flag) and no denominator;
    * for one metric NAME, versions are strictly increasing together with
      ``effective_from`` — no two versions share or reverse an effective date.
    """
    for metric in metrics.values():
        if not metric.cohort_bases or not set(metric.cohort_bases) <= COHORT_BASES:
            raise MetricDefinitionError(
                f"{metric.key}: cohort_bases must be a non-empty subset of {sorted(COHORT_BASES)}"
            )
        if not set(metric.dimensions) <= DIMENSIONS:
            raise MetricDefinitionError(
                f"{metric.key}: dimensions must be a subset of {sorted(DIMENSIONS)}"
            )
        if metric.kind not in METRIC_KINDS:
            raise MetricDefinitionError(f"{metric.key}: unknown kind {metric.kind!r}")

        for key in (*metric.numerator, *metric.denominator):
            _flag_ref(key, flags, metric.key)

        if metric.kind == "count":
            if not metric.numerator or metric.denominator or metric.measure or metric.buckets:
                raise MetricDefinitionError(
                    f"{metric.key}: a count metric needs a numerator and nothing else"
                )
        elif metric.kind == "rate":
            if not metric.numerator or not metric.denominator or metric.measure or metric.buckets:
                raise MetricDefinitionError(
                    f"{metric.key}: a rate metric needs a numerator AND a denominator, "
                    "and nothing else"
                )
            if not set(metric.denominator) <= set(metric.numerator):
                raise MetricDefinitionError(
                    f"{metric.key}: a rate's numerator must be a superset of its "
                    "denominator (count(A AND B) / count(A)), or the rate could "
                    "exceed 100%"
                )
        elif metric.kind in ("median", "mean"):
            if not metric.numerator or metric.denominator or not metric.measure or metric.buckets:
                raise MetricDefinitionError(
                    f"{metric.key}: a {metric.kind} metric needs a numerator (the "
                    "population) and a measure, and nothing else"
                )
            if "@" not in metric.measure or metric.measure not in measures:
                raise MetricDefinitionError(
                    f"{metric.key}: unknown or unversioned measure reference "
                    f"{metric.measure!r}"
                )
        elif metric.kind == "distribution":
            if not metric.numerator or metric.denominator or metric.measure or not metric.buckets:
                raise MetricDefinitionError(
                    f"{metric.key}: a distribution metric needs a numerator (the "
                    "population) and buckets, and nothing else"
                )
            for bucket in metric.buckets:
                _flag_ref(bucket, flags, metric.key)
        else:  # pragma: no cover — unreachable, `kind` already checked above
            raise MetricDefinitionError(f"{metric.key}: unknown kind {metric.kind!r}")

        # PH5-C1 "check-in data never reaches a model" / "check-in-safe
        # grouping": a metric that reads a 90-day check-in flag may only be
        # grouped by a dimension in CHECKIN_SAFE_DIMENSIONS. Validated here,
        # at import — the ToolSpec/DATA_CLASS_ROLES precedent — rather than
        # trusted to every call site that ever adds a dimension.
        if uses_checkin_data(metric) and not set(metric.dimensions) <= CHECKIN_SAFE_DIMENSIONS:
            raise MetricDefinitionError(
                f"{metric.key}: uses check-in data, so its dimensions must be "
                f"within {sorted(CHECKIN_SAFE_DIMENSIONS)} (got "
                f"{sorted(metric.dimensions)})"
            )

    for name, versions in _group_by_name(metrics.values()).items():
        _assert_no_overlap(name, versions)
    for name, versions in _group_by_name(flags.values()).items():
        _assert_no_overlap(name, versions)
    for name, versions in _group_by_name(measures.values()).items():
        _assert_no_overlap(name, versions)


def _group_by_name(
    items: Any,
) -> dict[str, list[Flag | Measure | Metric]]:
    out: dict[str, list[Flag | Measure | Metric]] = {}
    for item in items:
        out.setdefault(item.name, []).append(item)
    return out


def _assert_no_overlap(name: str, versions: list[Flag | Measure | Metric]) -> None:
    ordered = sorted(versions, key=lambda v: v.version)
    for prior, current in zip(ordered, ordered[1:], strict=False):
        if current.version <= prior.version or current.effective_from <= prior.effective_from:
            raise MetricDefinitionError(
                f"{name}: version {current.version} (effective {current.effective_from}) "
                f"does not come strictly after version {prior.version} "
                f"(effective {prior.effective_from})"
            )


validate_registry(FLAGS, MEASURES, METRICS)


# ---------------------------------------------------------------------------
# PH5-E4: the score-band dimension derives from nothing but
# hire_interviewer_score@1 — structural, not a comment.
# ---------------------------------------------------------------------------
def _validate_score_band_dimension(dimensions: dict[str, MetricDimension]) -> None:
    """Raise unless ``interviewer_score_band@1``'s ``group_sql`` references
    ``m_hire_interviewer_score`` (the ``app_facts`` output column for
    :data:`INTERVIEWER_SCORE_BAND_MEASURE`) and NOTHING else that looks like
    another flag or measure column (``f_*``/``m_*``). The same "raise before
    publication" precedent as :func:`validate_registry`'s check-in-safe
    dimensions check and ``shared/agents/schema.py``'s ``DATA_CLASS_ROLES``.

    A plain substring/regex check, not a SQL parse — proportionate to what
    this guards: nobody can widen the band's dependency to another flag or
    measure without this failing at import, which is the whole point.
    """
    key = "interviewer_score_band@1"
    if key not in dimensions:
        raise MetricDefinitionError(f"{key}: dimension is not registered")
    sql = dimensions[key].group_sql
    expected_column = f"m_{_bare(INTERVIEWER_SCORE_BAND_MEASURE)}"
    if expected_column not in sql:
        raise MetricDefinitionError(f"{key}: group_sql must reference {expected_column}")
    referenced = set(re.findall(r"\b[mf]_[a-z][a-z0-9_]*\b", sql))
    extra = referenced - {expected_column}
    if extra:
        raise MetricDefinitionError(
            f"{key}: group_sql must derive from {expected_column} alone, "
            f"found extra reference(s) {sorted(extra)}"
        )


_validate_score_band_dimension(DIMENSIONS_REGISTRY)


# ---------------------------------------------------------------------------
# "Current" resolution — the latest version of each name whose effective_from
# is on or before ``as_of`` (default today). Every consumer that does not ask
# for a specific historical version gets this.
# ---------------------------------------------------------------------------
def current_flags(as_of: date | None = None) -> dict[str, Flag]:
    return _current(FLAGS.values(), as_of or date.today())


def current_measures(as_of: date | None = None) -> dict[str, Measure]:
    return _current(MEASURES.values(), as_of or date.today())


def current_metrics(as_of: date | None = None) -> dict[str, Metric]:
    return _current(METRICS.values(), as_of or date.today())


def current_dimensions(as_of: date | None = None) -> dict[str, MetricDimension]:
    return _current(DIMENSIONS_REGISTRY.values(), as_of or date.today())


def current_rules(as_of: date | None = None) -> dict[str, Rule]:
    return _current(RULES.values(), as_of or date.today())


def min_cell_size(as_of: date | None = None) -> int:
    """The ``rule:min_cell`` suppression floor — read from the registry, not
    a bare constant, so a DPDP-governed threshold has a hash and a lock."""
    return int(current_rules(as_of)["min_cell"].value)


def _current(items: Any, as_of: date) -> dict[str, Any]:
    best: dict[str, Any] = {}
    for item in items:
        if item.effective_from > as_of:
            continue
        prior = best.get(item.name)
        if prior is None or item.version > prior.version:
            best[item.name] = item
    return best


# ---------------------------------------------------------------------------
# published.lock.json — canonical spec + hash, for the append-only freeze.
# ---------------------------------------------------------------------------
def _canonical_spec(
    item: Flag | Measure | Metric | MetricDimension | Rule | CalibrationSpec,
) -> dict[str, Any]:
    """A JSON-serialisable, order-independent spec for hashing.

    Excludes computed properties (``key``, ``formula``) — hashing the formula
    would make the hash change if the formula GENERATOR changes wording
    without the underlying definition changing, which is not what "the
    definition changed" means.

    Excludes a :class:`Metric`'s ``dimensions``: a metric's hash is about what
    it COUNTS, and adding a way to GROUP it never changes that number. A
    dimension is itself governed and locked (``dimension:name@version``,
    above) — this is what keeps "add a grouping" from forcing every metric
    that could use it into a new version.
    """
    spec = dict(vars(item))
    spec["effective_from"] = spec["effective_from"].isoformat()
    spec["_type"] = type(item).__name__
    if isinstance(item, Metric):
        spec.pop("dimensions", None)
    return spec


def _sha256_of(spec: dict[str, Any]) -> str:
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_lock_entries() -> dict[str, dict[str, Any]]:
    """The lock entries the current registry implies: ``{"kind:name@version": {...}}``.

    Used to (re)generate ``published.lock.json`` for a NEW entry and, in
    tests, to assert every existing lock entry's hash still matches its
    definition. Never called to overwrite an EXISTING entry outside a
    deliberate, reviewed regeneration — ``ops/ci/check_metric_lock.py`` is
    what actually stops that in CI.
    """
    entries: dict[str, dict[str, Any]] = {}
    for prefix, registry in (
        ("flag", FLAGS), ("measure", MEASURES), ("metric", METRICS),
        ("dimension", DIMENSIONS_REGISTRY), ("rule", RULES),
        ("calibration", CALIBRATION_SPECS),
    ):
        for item in registry.values():
            spec = _canonical_spec(item)
            entries[f"{prefix}:{item.key}"] = {
                "sha256": _sha256_of(spec),
                "effective_from": item.effective_from.isoformat(),
                "added_on": item.effective_from.isoformat(),
            }
    return entries


# ---------------------------------------------------------------------------
# The lock file itself, and the ONE hash every computed result carries.
# ---------------------------------------------------------------------------
LOCK_PATH: Path = Path(__file__).with_name("published.lock.json")


def load_lock() -> dict[str, dict[str, Any]]:
    """The committed lock file, parsed. Raises if it is missing or not valid JSON
    — a metric layer with no lock file is not "unlocked", it is unbuilt."""
    with LOCK_PATH.open(encoding="utf-8") as fh:
        data: dict[str, dict[str, Any]] = json.load(fh)
    return data


def _registry_hash() -> str:
    """sha256 over the WHOLE lock file (sorted-keys canonical JSON).

    Definitions are frozen; this hash is not — it changes the moment a new
    version is added, and every computed result carries it so a screenshot
    from before and after a definition change is distinguishable even when
    the number shown happens to be the same.
    """
    lock = load_lock()
    canonical = json.dumps(lock, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


REGISTRY_HASH: str = _registry_hash()
