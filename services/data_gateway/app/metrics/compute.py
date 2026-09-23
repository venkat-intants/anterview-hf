"""The one calculation service every governed-metric consumer calls — PH5-C2.

ONE QUERY SHAPE. Every call builds the same two-CTE statement:

* ``app_base`` — one row per LIVE enrolment (application) for the company,
  every :class:`~app.metrics.definitions.Flag`/:class:`Measure` evaluated as a
  plain column, from BASE TABLES ONLY (never ``application_progress`` or any
  other view/function whose meaning can change without a lock-file hash
  change). The lateral aliases every flag/measure ``sql`` fragment may
  reference, and exactly what each selects:

  - ``e`` / ``a`` / ``r`` — the ``enrolments`` row, its live ``applicants``
    row, and its ``job_requisitions`` row (LEFT, may be absent).
  - ``la.n`` — how many LIVE enrolments this applicant holds at the company
    (the "exactly one application" legacy-attribution rule).
  - ``st.screened`` / ``st.hired_ever`` / ``st.hired_at`` / ``st.decided_at``
    — from ``stage_transitions``: whether the ledger ever recorded a move to
    shortlisted-or-later / to hired; the first ``occurred_at`` of a move to
    hired; the first ``occurred_at`` of a move to hired OR rejected.
  - ``ex.legacy_or_direct`` — a submitted ``exam_attempts`` row attributable
    to this application (by its assignment's ``enrolment_id``, or — when the
    attempt predates that link — because ``la.n <= 1``).
  - ``rr.has_result`` — any ``round_results`` row for this enrolment.
  - ``iv.ai_completed`` — a completed AI interview (``interview_invites`` join
    ``sessions``) attributable to this application, same attribution rule.
  - ``hsc.submitted`` / ``hsc.mean_score`` — a submitted, non-superseded human
    ``interviewer_scorecards`` row for this enrolment, and the mean of its
    ``interviewer_scorecard_scores.score`` (excluding ``not_assessed``).
  - ``off.reached`` / ``off.accepted`` / ``off.accepted_start`` — any
    ``offers`` row for this enrolment that reached the candidate
    (sent/accepted/declined/expired); whether one was accepted; the accepted
    offer's ``start_date``.
  - ``hc.recorded`` / ``hc.retained`` / ``hc.perf_below`` / ``hc.perf_meets``
    / ``hc.perf_exceeds`` — a live (``superseded_at IS NULL``) 90-day
    ``hire_checkins`` row for this enrolment, kind ``90_day``, and its
    employment/performance reading. Reads ONLY the columns migration
    ``8f41cb18a299`` actually has: there is no ``note``, ``applicant_id`` or
    ``redacted_at`` on this table — a check-in carries nothing to redact and
    is deleted outright by retention and by erasure (see
    ``app.hire_checkins``'s module docstring).

* ``app_facts`` — ``app_base`` narrowed to the requested cohort window and
  filters (``requisition_id``, ``source``), referencing ``app_base``'s own
  OUTPUT columns (a plain column reference across a CTE boundary, not the
  same-SELECT alias visibility Postgres refuses).

Every metric is then one or two ``count(*) FILTER (WHERE ...)`` /
``percentile_cont``/``avg`` expressions over ``app_facts``, aggregated once
overall (the "All" group) and, if requested, once more grouped by
``source`` or ``requisition``.

SQL ASSEMBLY. Every fragment placed into the statement text comes from a
closed, module-constant source: the FROM/JOIN skeleton below, or a
``Flag``/``Measure``/``Metric`` object from the validated registry — never a
request value. Every VALUE (company_id, dates, filters, the metric-population
FILTER's bound literals are not literals at all, they are column
references) is a bound parameter. Each assembled ``text(...)`` therefore
carries ``# nosec B608`` with that one-line justification, per file policy
(``docs/ACCEPTED-RISKS.md`` catalogues nothing here — this is the documented,
reviewed exception the bandit gate expects, the same pattern already used in
``app/jd_versions.py`` and ``app/offers.py``).

THE SKELETON AND THE COHORT WINDOWS ARE THEMSELVES LOCKED (C2-8, evidence
audit). A Flag's ``sql`` means nothing except evaluated inside
``_APP_BASE_HEAD`` + ``_APP_BASE_TAIL`` (the FROM/JOIN skeleton every lateral
alias comes from) and filtered by one ``_COHORT_PREDICATES`` entry — so
editing either used to change every published figure under an unchanged lock
and an unchanged ``registry_hash``, which is exactly what
``published.lock.json`` exists to make impossible. :func:`build_engine_lock_entries`
locks both, as ``engine:app_facts_skeleton@1``, ``engine:app_facts_filters@1``
(the ``requisition_id``/``source`` filters every cohort query also applies)
and ``engine:cohort_application@1`` / ``engine:cohort_decision@1`` /
``engine:cohort_hire@1`` — the SAME lock file
:mod:`app.metrics.definitions` freezes Flags/Measures/Metrics in (see
:class:`app.metrics.definitions.EngineFragment` for why the class lives
there but the SQL text stays here). NOT locked, because there is no string
constant to hash that is not the source code itself: the per-metric
aggregation shape :func:`_plan_metric` picks from a metric's ``kind`` (
``count(*) FILTER (...)`` / ``percentile_cont`` / ``avg`` / a bucketed
``FILTER``) and the suppression branching in :func:`_read_metric_value`. Both
are Python control flow, not data, and both are exercised end-to-end by
``tests/integration/test_ph5_w1_metrics.py``'s per-flag, per-cohort and
per-suppression assertions — which would fail immediately if either produced
the wrong number — but neither would, by itself, trip
``ops/ci/check_metric_lock.py`` the way an edited skeleton or cohort
predicate now does. That gap is disclosed here rather than left implicit.

THIS MODULE NEVER WRITES (except the one exception the caller controls: the
drill-down audit row, which lives in the ROUTER, not here — see
``app/routers/hr_metrics.py``). ``SET LOCAL statement_timeout`` bounds every
read to 10 seconds so a pathological filter combination cannot hang a
connection — and is explicitly RESTORED after a successful read
(``_set_timeout``/``_restore_timeout``; a failed read leaves the transaction
aborted, and the caller's rollback discards the setting), because ``SET LOCAL``'s
own scope is "until this transaction ends", not "until this function
returns": every caller here (the requisition dashboard, a copilot turn, the
watcher loop) keeps the same session open across further queries, which a
leaked override would silently re-time too.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application_source import SOURCE_LABELS
from app.metrics.definitions import (
    CURRENT_SCORECARD_SQL,
    INTERVIEWER_SCORE_BAND_LABELS,
    REGISTRY_HASH,
    CohortBasis,
    EngineFragment,
    Metric,
    current_dimensions,
    current_flags,
    current_measures,
    current_metrics,
    engine_fragment_lock_entries,
    min_cell_size,
    uses_checkin_data,
    uses_checkin_outcome,
)

CompanyId = uuid.UUID
#: PH5-E4 adds ``interviewer_score_band`` — grouped from
#: ``current_dimensions()["interviewer_score_band"].group_sql`` (see
#: :func:`compute_funnel`), never a hardcoded copy of that SQL here.
GroupBy = Literal["source", "requisition", "interviewer_score_band"] | None

#: The drill-down is capped, newest first — an unbounded members list is
#: exactly the "200 applicants pushes the real question out" problem
#: ``app/agents/tools.py`` already documents for the copilot, applied here.
MEMBERS_LIMIT = 200


class MetricComputeError(ValueError):
    """A caller asked for something the registry does not support (an unknown
    metric name, or a cohort the metric is not defined over). The router turns
    this into a 400/404 — never a 500 — since it is a request-shape problem,
    not a database failure."""


def _bare(flag_key: str) -> str:
    return flag_key.split("@", 1)[0]


# ---------------------------------------------------------------------------
# The fixed FROM/JOIN skeleton — a module constant, never touched by a request.
# ---------------------------------------------------------------------------
_APP_BASE_HEAD = """\
    SELECT
        e.id                AS enrolment_id,
        e.company_id        AS company_id,
        e.applicant_id      AS applicant_id,
        e.requisition_id    AS requisition_id,
        r.title             AS requisition_title,
        e.source            AS source,
        e.status            AS status,
        e.created_at        AS created_at,
        st.decided_at       AS decided_at,
        st.hired_at         AS hired_at,
"""

_APP_BASE_TAIL_TEMPLATE = """\
    FROM enrolments e
    JOIN applicants a
      ON a.id = e.applicant_id AND a.company_id = e.company_id AND a.deleted_at IS NULL
    LEFT JOIN job_requisitions r
      ON r.id = e.requisition_id AND r.company_id = e.company_id
    LEFT JOIN LATERAL (
        SELECT count(*) AS n
          FROM enrolments n2
         WHERE n2.applicant_id = e.applicant_id AND n2.company_id = e.company_id
           AND n2.deleted_at IS NULL
    ) la ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            bool_or(t.to_status IN ('shortlisted', 'interviewed', 'hired')) AS screened,
            bool_or(t.to_status = 'hired')                                 AS hired_ever,
            min(t.occurred_at) FILTER (WHERE t.to_status = 'hired')        AS hired_at,
            min(t.occurred_at)
                FILTER (WHERE t.to_status IN ('hired', 'rejected'))        AS decided_at
          FROM stage_transitions t
         WHERE t.enrolment_id = e.id AND t.company_id = e.company_id
    ) st ON TRUE
    LEFT JOIN LATERAL (
        SELECT bool_or(
                   g.enrolment_id = e.id OR (g.enrolment_id IS NULL AND la.n <= 1)
               ) AS legacy_or_direct
          FROM exam_attempts x
          LEFT JOIN exam_assignments g ON g.id = x.assignment_id
         WHERE x.applicant_id = e.applicant_id AND x.company_id = e.company_id
           AND x.status = 'submitted' AND x.deleted_at IS NULL
    ) ex ON TRUE
    LEFT JOIN LATERAL (
        SELECT (count(*) > 0) AS has_result
          FROM round_results rr2
         WHERE rr2.enrolment_id = e.id AND rr2.company_id = e.company_id
    ) rr ON TRUE
    LEFT JOIN LATERAL (
        SELECT bool_or(
                   s.status = 'completed'
                   AND (i.enrolment_id = e.id OR (i.enrolment_id IS NULL AND la.n <= 1))
               ) AS ai_completed
          FROM interview_invites i
          JOIN sessions s ON s.id = i.session_id
         WHERE i.applicant_id = e.applicant_id AND i.company_id = e.company_id
           AND i.deleted_at IS NULL
    ) iv ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            (count(DISTINCT s.id) > 0)                                       AS submitted,
            avg(sc.score)
                FILTER (WHERE sc.score IS NOT NULL AND NOT sc.not_assessed)   AS mean_score
          FROM interviewer_scorecards s
          LEFT JOIN interviewer_scorecards nxt ON nxt.id = s.superseded_by_id
          LEFT JOIN interviewer_scorecard_scores sc ON sc.scorecard_id = s.id
         WHERE s.enrolment_id = e.id AND s.company_id = e.company_id
           AND ({current_scorecard})
    ) hsc ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            bool_or(o.status IN ('sent', 'accepted', 'declined', 'expired')) AS reached,
            bool_or(o.status = 'accepted')                                  AS accepted,
            max(o.start_date) FILTER (WHERE o.status = 'accepted')          AS accepted_start
          FROM offers o
         WHERE o.enrolment_id = e.id AND o.company_id = e.company_id
    ) off ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            (count(*) > 0)                        AS recorded,
            bool_or(hc2.employment = 'employed')   AS retained,
            bool_or(hc2.performance = 'below')     AS perf_below,
            bool_or(hc2.performance = 'meets')     AS perf_meets,
            bool_or(hc2.performance = 'exceeds')   AS perf_exceeds
          FROM hire_checkins hc2
         WHERE hc2.enrolment_id = e.id AND hc2.company_id = e.company_id
           AND hc2.kind = '90_day' AND hc2.superseded_at IS NULL
    ) hc ON TRUE
    WHERE e.company_id = :company_id AND e.deleted_at IS NULL
"""

# `.replace(...)`, not an f-string or `.format()`: bandit's B608 heuristic
# flags both of those unconditionally on any SQL-shaped string regardless of
# what is interpolated, and the module docstring's `# nosec B608` convention
# only reads cleanly on a SINGLE line — this template spans ~80. The value
# substituted is CURRENT_SCORECARD_SQL, a module constant from the validated
# registry, never a request value.
_APP_BASE_TAIL = _APP_BASE_TAIL_TEMPLATE.replace("{current_scorecard}", CURRENT_SCORECARD_SQL)

#: Cohort predicates, keyed by the closed ``CohortBasis`` enum — picked in
#: Python by a validated literal, never interpolated from a request string.
#: Each references ``app_base``'s OWN output columns from the enclosing
#: ``app_facts`` query, which is a normal FROM-clause column reference (not
#: the same-SELECT alias visibility Postgres refuses).
#: ``CAST(:name AS type)`` rather than ``:name::type`` — the latter, repeated
#: with the SAME bind name in one statement (unavoidable here: ``from_ts``
#: guards two comparisons), left SQLAlchemy's positional-param rewriter for
#: asyncpg failing to substitute the first occurrence at all (a literal
#: ``:from_ts::timestamptz`` reached Postgres and it raised a syntax error on
#: the bare colon). ``CAST(...)`` is also this codebase's existing convention
#: (``app/agents/watch_runner.py``'s ``CAST(:cid AS uuid)`` and others).
_COHORT_PREDICATES: dict[CohortBasis, str] = {
    "application": (
        "(CAST(:from_ts AS timestamptz) IS NULL OR created_at >= CAST(:from_ts AS timestamptz)) "
        "AND (CAST(:to_ts AS timestamptz) IS NULL OR created_at < CAST(:to_ts AS timestamptz))"
    ),
    "decision": (
        "decided_at IS NOT NULL "
        "AND (CAST(:from_ts AS timestamptz) IS NULL OR decided_at >= CAST(:from_ts AS timestamptz)) "
        "AND (CAST(:to_ts AS timestamptz) IS NULL OR decided_at < CAST(:to_ts AS timestamptz))"
    ),
    "hire": (
        "f_hired AND hired_at IS NOT NULL "
        "AND (CAST(:from_ts AS timestamptz) IS NULL OR hired_at >= CAST(:from_ts AS timestamptz)) "
        "AND (CAST(:to_ts AS timestamptz) IS NULL OR hired_at < CAST(:to_ts AS timestamptz))"
    ),
}

_REQUISITION_FILTER = (
    "(CAST(:requisition_id AS uuid) IS NULL OR requisition_id = CAST(:requisition_id AS uuid))"
)
_SOURCE_FILTER = "(CAST(:source_filter AS text) IS NULL OR source = CAST(:source_filter AS text))"

#: When these engine fragments became effective — the same date every v1
#: flag/measure/metric in ``app.metrics.definitions`` did, since they were
#: already part of what v1 meant; this closes a lock GAP, not a definition
#: change (nothing here was ever a separately-versioned thing before).
_ENGINE_V1_DATE = date(2026, 9, 22)

#: What C2-8 (evidence audit) found missing from the lock: the FROM/JOIN
#: skeleton every flag/measure is evaluated inside, the requisition/source
#: filters every cohort query applies, and each cohort basis's own window
#: predicate. None of these is a Flag or a Measure — nothing here is a fact
#: about one application — so they are locked as
#: :class:`~app.metrics.definitions.EngineFragment`, ``engine:name@version``,
#: in the SAME ``published.lock.json``. See :func:`build_engine_lock_entries`
#: and this module's docstring ("THE SKELETON AND THE COHORT WINDOWS ARE
#: THEMSELVES LOCKED") for what this does and does not cover.
_ENGINE_FRAGMENTS: tuple[EngineFragment, ...] = (
    EngineFragment(
        "app_facts_skeleton", 1, _ENGINE_V1_DATE,
        "The FROM/JOIN skeleton every flag/measure column is selected "
        "alongside: which base tables app_base joins, on what keys, and "
        "which lateral columns (la, st, ex, rr, iv, hsc, off, hc — see this "
        "module's docstring) a flag or measure's own SQL may reference. "
        "Locked as _APP_BASE_HEAD + _APP_BASE_TAIL (CURRENT_SCORECARD_SQL "
        "already substituted in) — NOT the per-flag/measure SELECT list "
        "_app_base_select_list() appends, since THAT is assembled from "
        "flags/measures the registry already locks individually.",
        _APP_BASE_HEAD + _APP_BASE_TAIL,
    ),
    EngineFragment(
        "app_facts_filters", 1, _ENGINE_V1_DATE,
        "The requisition_id/source filters app_facts applies in every "
        "cohort, in addition to its cohort-window predicate.",
        _REQUISITION_FILTER + _SOURCE_FILTER,
    ),
    EngineFragment(
        "cohort_application", 1, _ENGINE_V1_DATE,
        "The 'application' cohort-window predicate: e.created_at in "
        "[from, to).",
        _COHORT_PREDICATES["application"],
    ),
    EngineFragment(
        "cohort_decision", 1, _ENGINE_V1_DATE,
        "The 'decision' cohort-window predicate: the first ledger move of "
        "e into hired or rejected is in [from, to).",
        _COHORT_PREDICATES["decision"],
    ),
    EngineFragment(
        "cohort_hire", 1, _ENGINE_V1_DATE,
        "The 'hire' cohort-window predicate: a hire that stands, whose "
        "first move into hired is in [from, to).",
        _COHORT_PREDICATES["hire"],
    ),
)


def build_engine_lock_entries() -> dict[str, dict[str, Any]]:
    """This module's own share of ``published.lock.json`` —
    ``engine:app_facts_skeleton@1``, ``engine:app_facts_filters@1`` and
    ``engine:cohort_<basis>@1`` — merged with
    :func:`app.metrics.definitions.build_lock_entries` (by whatever caller is
    regenerating or checking the lock; see
    ``tests/unit/test_ph5_w1_metrics_definitions.py``) to form the complete,
    published lock. A free function rather than a global :mod:`app.metrics.definitions`
    reaches into, because ``definitions.py`` cannot import this module
    (``compute.py`` already imports ``definitions.py`` — that would be
    circular).
    """
    return engine_fragment_lock_entries(_ENGINE_FRAGMENTS)


def _app_base_select_list() -> str:
    """The flag/measure columns appended to :data:`_APP_BASE_HEAD`.

    Built from the CURRENT version of every flag/measure in the validated
    registry — module-constant text, not request input.
    """
    lines = []
    for flag in sorted(current_flags().values(), key=lambda f: f.name):
        lines.append(f"        ({flag.sql}) AS f_{flag.name},\n")
    measures = sorted(current_measures().values(), key=lambda m: m.name)
    for i, measure in enumerate(measures):
        comma = "," if i < len(measures) - 1 else ""
        lines.append(f"        ({measure.sql}) AS m_{measure.name}{comma}\n")
    if not measures:
        # Drop the trailing comma on the last flag column instead.
        lines[-1] = lines[-1].rstrip(",\n") + "\n"
    return "".join(lines)


def _with_clause(cohort_basis: CohortBasis) -> str:
    """``WITH app_base AS (...), app_facts AS (...)`` — shared by every caller.

    # nosec B608 — every fragment concatenated here is a module constant (this
    # skeleton, or a Flag/Measure's `.sql` from the validated registry); every
    # VALUE is a bound parameter (`:company_id`, `:from_ts`, `:to_ts`,
    # `:requisition_id`, `:source_filter`). No request value is ever
    # interpolated into this text.
    """
    app_base = _APP_BASE_HEAD + _app_base_select_list() + _APP_BASE_TAIL
    cohort_predicate = _COHORT_PREDICATES[cohort_basis]
    return (
        # nosec B608 — every piece interpolated here is a module constant
        # picked by a closed enum (cohort_basis) or built from the validated
        # registry (app_base); every VALUE is a bound parameter. See the
        # module docstring's "SQL ASSEMBLY" note.
        f"WITH app_base AS (\n{app_base}\n),\n"  # nosec B608
        f"app_facts AS (\n"
        f"    SELECT * FROM app_base\n"
        f"    WHERE {cohort_predicate}\n"
        f"      AND {_REQUISITION_FILTER}\n"
        f"      AND {_SOURCE_FILTER}\n"
        f")\n"
    )


def _population_sql(flag_keys: tuple[str, ...]) -> str:
    if not flag_keys:
        return "TRUE"
    return " AND ".join(f"f_{_bare(k)}" for k in flag_keys)


def _bucket_label(flag_name: str) -> str:
    return flag_name.removeprefix("perf_")


@dataclass(frozen=True)
class _MetricPlan:
    metric: Metric
    select_sql: list[str]
    columns: dict[str, str]


def _plan_metric(metric: Metric) -> _MetricPlan:
    alias = metric.name
    exprs: list[str] = []
    cols: dict[str, str] = {}

    if metric.kind == "count":
        pop = _population_sql(metric.numerator)
        col = f"{alias}_value"
        exprs.append(f"count(*) FILTER (WHERE {pop}) AS {col}")
        cols["value"] = col

    elif metric.kind == "rate":
        num_col, den_col = f"{alias}_num", f"{alias}_den"
        exprs.append(f"count(*) FILTER (WHERE {_population_sql(metric.numerator)}) AS {num_col}")
        exprs.append(f"count(*) FILTER (WHERE {_population_sql(metric.denominator)}) AS {den_col}")
        cols["num"] = num_col
        cols["den"] = den_col

    elif metric.kind in ("median", "mean"):
        assert metric.measure is not None  # guaranteed by validate_registry
        pop = _population_sql(metric.numerator)
        measure_col = f"m_{_bare(metric.measure)}"
        agg = (
            f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {measure_col})"
            if metric.kind == "median"
            else f"avg({measure_col})"
        )
        value_col, n_col = f"{alias}_value", f"{alias}_n"
        exprs.append(f"{agg} FILTER (WHERE {pop}) AS {value_col}")
        exprs.append(f"count(*) FILTER (WHERE {pop}) AS {n_col}")
        cols["value"] = value_col
        cols["n"] = n_col

    elif metric.kind == "distribution":
        assert metric.buckets is not None  # guaranteed by validate_registry
        pop = _population_sql(metric.numerator)
        n_col = f"{alias}_n"
        exprs.append(f"count(*) FILTER (WHERE {pop}) AS {n_col}")
        cols["n"] = n_col
        for bucket_key in metric.buckets:
            bucket_name = _bare(bucket_key)
            bcol = f"{alias}_bucket_{bucket_name}"
            exprs.append(f"count(*) FILTER (WHERE {pop} AND f_{bucket_name}) AS {bcol}")
            cols[f"bucket:{bucket_name}"] = bcol
    else:  # pragma: no cover — unreachable, kind validated at import
        raise MetricComputeError(f"unknown metric kind {metric.kind!r}")

    return _MetricPlan(metric=metric, select_sql=exprs, columns=cols)


def _read_metric_value(row: Any, plan: _MetricPlan) -> dict[str, Any]:
    """Read one metric's aggregate columns off a result row.

    SUPPRESSION IS A DPDP RULING FOR CHECK-IN DATA — NOT A GENERAL DISPLAY
    CHOICE (lead policy decision, C2-13). Below
    :func:`~app.metrics.definitions.min_cell_size`, ``suppressed`` is ALWAYS
    set — an advisory flag the UI reads for "too few to compare" — but only a
    metric that reads a check-in flag (:func:`~app.metrics.definitions
    .uses_checkin_data`: ``checkin_coverage``, ``retention_90d``,
    ``performance_90d``) has its ``value`` NULLED as well. For an OUTCOME flag
    specifically (``retention_90d``, ``performance_90d`` —
    :func:`~app.metrics.definitions.uses_checkin_outcome`), ``numerator`` is
    ALSO nulled. Every OTHER metric — the ordinary hiring-pipeline rates,
    ``time_to_hire_days``, ``hire_interviewer_score`` — keeps its real value
    and numerator even when small: a small-but-real hiring number is not the
    same DPDP concern as a post-hire outcome about a named former employee,
    and nulling it made `/hr/analytics` report "no data" for small companies
    that had real data (C2-13). ``denominator`` and ``n`` are always shown —
    they say how much data exists, not what it says. Counts are never
    suppressed.
    """
    m = plan.metric
    out: dict[str, Any] = {"metric": m.name, "version": m.version, "kind": m.kind}
    floor = min_cell_size()
    hides_value = uses_checkin_data(m)
    if m.kind == "count":
        out["value"] = int(row[plan.columns["value"]] or 0)
        return out
    if m.kind == "rate":
        num = int(row[plan.columns["num"]] or 0)
        den = int(row[plan.columns["den"]] or 0)
        suppressed = den < floor
        hide_now = suppressed and hides_value
        out["numerator"] = None if (hide_now and uses_checkin_outcome(m)) else num
        out["denominator"] = den
        out["value"] = None if hide_now else (round(100.0 * num / den, 1) if den else None)
        out["suppressed"] = suppressed
        return out
    if m.kind in ("median", "mean"):
        n = int(row[plan.columns["n"]] or 0)
        raw = row[plan.columns["value"]]
        suppressed = n < floor
        hide_now = suppressed and hides_value
        out["value"] = None if hide_now else (round(float(raw), 1) if raw is not None else None)
        out["n"] = n
        out["suppressed"] = suppressed
        return out
    if m.kind == "distribution":
        n = int(row[plan.columns["n"]] or 0)
        suppressed = n < floor
        hide_now = suppressed and hides_value
        dist: dict[str, int] | None = None
        if not hide_now:
            dist = {}
            for part, col in plan.columns.items():
                if part.startswith("bucket:"):
                    dist[_bucket_label(part.split(":", 1)[1])] = int(row[col] or 0)
        out["value"] = dist
        out["n"] = n
        out["suppressed"] = suppressed
        return out
    raise MetricComputeError(f"unknown metric kind {m.kind!r}")  # pragma: no cover


@dataclass(frozen=True)
class CohortWindow:
    basis: CohortBasis
    from_: date | None = None
    to_: date | None = None


@dataclass(frozen=True)
class FunnelFilters:
    requisition_id: uuid.UUID | None = None
    source: str | None = None


@dataclass
class FunnelGroup:
    key: str | None
    label: str
    in_progress: int
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class FunnelResult:
    registry_hash: str
    cohort: CohortWindow
    filters: FunnelFilters
    group_by: GroupBy
    groups: list[FunnelGroup]


def _bound_params(
    company_id: CompanyId, cohort: CohortWindow, filters: FunnelFilters
) -> dict[str, Any]:
    return {
        "company_id": company_id,
        "from_ts": datetime.combine(cohort.from_, time.min) if cohort.from_ else None,
        "to_ts": datetime.combine(cohort.to_, time.min) if cohort.to_ else None,
        "requisition_id": filters.requisition_id,
        "source_filter": filters.source,
    }


async def _set_timeout(db: AsyncSession) -> str:
    """``SET LOCAL statement_timeout``, returning the PRIOR value.

    ``SET LOCAL`` is scoped to "until this transaction ends", not "until this
    function returns" — and every caller here (the requisition dashboard, a
    copilot turn, the watcher loop) keeps the same session/transaction open
    across many more queries afterwards. Left unrestored, a single funnel
    read would silently re-time every later query sharing that transaction.
    The caller must pair this with :func:`_restore_timeout` (both
    ``compute_funnel`` and ``compute_members`` do), and only on SUCCESS: when a
    query here fails, the transaction is aborted and the caller must roll it
    back, which discards the ``SET LOCAL`` by itself. A restore attempted in
    that state raises "current transaction is aborted" and would replace the
    real error (a statement timeout, say) with a misleading one.
    """
    previous = await db.scalar(
        text("SELECT current_setting('statement_timeout')")
    )  # nosec B608 — fixed literal, no interpolation
    await db.execute(text("SET LOCAL statement_timeout = '10s'"))  # nosec B608
    return str(previous) if previous is not None else "0"


async def _restore_timeout(db: AsyncSession, previous: str) -> None:
    """Undo :func:`_set_timeout`'s override. ``set_config(..., true)``'s
    third argument is LOCAL scope — the same scope ``SET LOCAL`` used — so
    this restore cannot itself leak past the caller's transaction either."""
    await db.execute(
        text("SELECT set_config('statement_timeout', :value, true)"), {"value": previous},
    )  # nosec B608 — fixed literal; :value is a bound parameter


def _metrics_for_cohort(cohort_basis: CohortBasis) -> list[Metric]:
    return sorted(
        (m for m in current_metrics().values() if cohort_basis in m.cohort_bases),
        key=lambda m: m.name,
    )


async def compute_funnel(
    db: AsyncSession,
    *,
    company_id: CompanyId,
    cohort: CohortWindow,
    filters: FunnelFilters,
    group_by: GroupBy = None,
) -> FunnelResult:
    """The governed funnel + outcomes, overall and (optionally) by dimension.

    The "All" group is always first, computed with no GROUP BY; a requested
    ``group_by`` adds one row per value present in the cohort (a source or
    requisition with zero applications in the window never appears — nothing
    to divide by is not a zero).

    SECURITY REVIEW T2-1b (the single-response subtraction attack): for a
    check-in OUTCOME metric, exposing every OTHER group's exact numerator/
    denominator while one group is suppressed lets a reader recover the
    suppressed cell by subtracting them from "All" — "All" minus every VISIBLE
    group equals the one HIDDEN group exactly, whatever the suppression
    floor. So suppression for those metrics is applied UNIFORMLY: if ANY
    non-"All" group is individually suppressed, EVERY non-"All" group is
    suppressed for that metric, even one that would individually clear the
    floor. "All" itself is never touched by this — it is not part of the
    subtraction, only its target.
    """
    previous_timeout = await _set_timeout(db)
    succeeded = False
    try:
        metrics = _metrics_for_cohort(cohort.basis)
        plans = [_plan_metric(m) for m in metrics]
        with_sql = _with_clause(cohort.basis)
        params = _bound_params(company_id, cohort, filters)

        select_exprs = [
            "count(*) FILTER (WHERE status NOT IN ('hired', 'rejected')) AS in_progress"
        ]
        for plan in plans:
            select_exprs.extend(plan.select_sql)
        select_list = ",\n       ".join(select_exprs)

        groups: list[FunnelGroup] = []

        # The overall "All" row — always first, never grouped.
        overall_sql = (
            f"{with_sql}SELECT {select_list} FROM app_facts"  # nosec B608 — see module docstring
        )
        overall_row = (await db.execute(text(overall_sql), params)).mappings().one()
        groups.append(
            FunnelGroup(
                key=None, label="All", in_progress=int(overall_row["in_progress"] or 0),
                metrics={plan.metric.name: _read_metric_value(overall_row, plan) for plan in plans},
            )
        )

        if group_by == "source":
            grouped_sql = (
                f"{with_sql}SELECT source AS grp_key, {select_list} "  # nosec B608 — see docstring
                f"FROM app_facts GROUP BY source"
            )
            rows = (await db.execute(text(grouped_sql), params)).mappings().all()
            for row in rows:
                key = row["grp_key"]
                groups.append(
                    FunnelGroup(
                        key=key, label=SOURCE_LABELS.get(key, key or "Unknown / untracked"),
                        in_progress=int(row["in_progress"] or 0),
                        metrics={plan.metric.name: _read_metric_value(row, plan) for plan in plans},
                    )
                )
        elif group_by == "requisition":
            grouped_sql = (
                f"{with_sql}SELECT requisition_id AS grp_key, requisition_title AS grp_title, "  # nosec B608
                f"{select_list} FROM app_facts GROUP BY requisition_id, requisition_title"
            )
            rows = (await db.execute(text(grouped_sql), params)).mappings().all()
            for row in rows:
                groups.append(
                    FunnelGroup(
                        key=str(row["grp_key"]) if row["grp_key"] else None,
                        label=row["grp_title"] or "(no opening)",
                        in_progress=int(row["in_progress"] or 0),
                        metrics={plan.metric.name: _read_metric_value(row, plan) for plan in plans},
                    )
                )
        elif group_by == "interviewer_score_band":
            # PH5-E4: the band's SQL is the governed dimension's OWN group_sql
            # (DIMENSIONS_REGISTRY["interviewer_score_band@1"], validated at
            # import to derive from nothing but m_hire_interviewer_score) —
            # never re-typed here. It is a module constant reached through the
            # validated registry, never a request value.
            band_sql = current_dimensions()["interviewer_score_band"].group_sql
            grouped_sql = (
                f"{with_sql}SELECT {band_sql} AS grp_key, "  # nosec B608 — see module docstring
                f"{select_list} FROM app_facts GROUP BY {band_sql}"
            )
            rows = (await db.execute(text(grouped_sql), params)).mappings().all()
            for row in rows:
                key = row["grp_key"]
                groups.append(
                    FunnelGroup(
                        key=key, label=INTERVIEWER_SCORE_BAND_LABELS.get(key, key),
                        in_progress=int(row["in_progress"] or 0),
                        metrics={plan.metric.name: _read_metric_value(row, plan) for plan in plans},
                    )
                )

        if group_by is not None:
            _suppress_checkin_outcomes_uniformly(groups, plans)

        succeeded = True
        return FunnelResult(
            registry_hash=REGISTRY_HASH, cohort=cohort, filters=filters, group_by=group_by,
            groups=groups,
        )
    finally:
        if succeeded:  # never on an aborted transaction; see _set_timeout
            await _restore_timeout(db, previous_timeout)


def _suppress_checkin_outcomes_uniformly(
    groups: list[FunnelGroup], plans: list[_MetricPlan]
) -> None:
    """T2-1b: if any non-"All" group is suppressed for a check-in OUTCOME
    metric, suppress that metric in every non-"All" group — see
    :func:`compute_funnel`'s docstring for why a partially-suppressed
    breakdown leaks the hidden cell by subtraction from "All"."""
    # By position, not by key: "All" is always groups[0] (compute_funnel
    # appends it first), so this holds even if a grouped key could be NULL.
    non_all = groups[1:]
    if len(non_all) < 2:
        return
    for plan in plans:
        if not uses_checkin_outcome(plan.metric):
            continue
        name = plan.metric.name
        if not any(g.metrics[name]["suppressed"] for g in non_all):
            continue
        for group in non_all:
            value = group.metrics[name]
            if value["suppressed"]:
                continue
            value["suppressed"] = True
            value["value"] = None
            if "numerator" in value:
                value["numerator"] = None


@dataclass(frozen=True)
class MemberRow:
    enrolment_id: str
    applicant_id: str
    candidate_name: str
    requisition_id: str | None
    requisition_title: str | None
    source: str
    applied_at: str
    #: PH5-E4: the row's mean CURRENT human interviewer scorecard score —
    #: populated whenever one exists, regardless of whether ``score_band`` was
    #: requested; ``routers/hr_metrics.py`` decides whether to surface it (the
    #: design's "when score_band is given" is a RESPONSE-SHAPE choice, not a
    #: reason to compute a different value here).
    interviewer_score: float | None = None


@dataclass(frozen=True)
class MembersResult:
    metric: str
    version: int
    part: Literal["numerator", "denominator"]
    total: int
    truncated: bool
    rows: list[MemberRow]


async def compute_members(
    db: AsyncSession,
    *,
    company_id: CompanyId,
    metric_name: str,
    part: Literal["numerator", "denominator"],
    cohort: CohortWindow,
    filters: FunnelFilters,
    limit: int = MEMBERS_LIMIT,
    score_band: str | None = None,
) -> MembersResult:
    """The applications behind one metric's numerator or denominator.

    ``part`` is ignored for a ``count`` metric (its only population is its
    numerator) and for anything without the requested part (e.g. asking for
    the ``denominator`` of a ``count`` or ``distribution`` metric falls back
    to the numerator population — there is no other population to show).

    ``score_band`` (PH5-E4) additionally narrows the population to enrolments
    whose ``interviewer_score_band`` equals the given key — the SAME governed
    ``group_sql`` :func:`compute_funnel` groups by, never a re-typed copy. The
    caller (``routers/hr_metrics.py``) is responsible for refusing this
    combined with a check-in-derived metric/part the aggregate itself would
    suppress (:func:`~app.metrics.definitions.checkin_outcome_drilldown_blocked`)
    — this function does not re-derive that DPDP rule, only applies the extra
    WHERE clause it is asked for.
    """
    metric = current_metrics().get(metric_name)
    if metric is None:
        raise MetricComputeError(f"unknown metric {metric_name!r}")
    if cohort.basis not in metric.cohort_bases:
        raise MetricComputeError(
            f"{metric.key} is not defined over the {cohort.basis!r} cohort"
        )
    population = metric.denominator if (part == "denominator" and metric.denominator) else metric.numerator

    previous_timeout = await _set_timeout(db)
    succeeded = False
    try:
        with_sql = _with_clause(cohort.basis)
        params = _bound_params(company_id, cohort, filters)
        pop_sql = _population_sql(population)
        # nosec B608 — band_sql is a module constant reached through the
        # validated dimension registry (never a request value); the request
        # VALUE is the bound parameter :score_band.
        band_sql = current_dimensions()["interviewer_score_band"].group_sql
        band_filter_sql = f" AND ({band_sql} = CAST(:score_band AS text))" if score_band else ""
        band_params = {"score_band": score_band}

        count_sql = (
            f"{with_sql}SELECT count(*) FROM app_facts "  # nosec B608
            f"WHERE {pop_sql}{band_filter_sql}"
        )
        total = int(
            (await db.execute(text(count_sql), {**params, **band_params})).scalar_one()
        )

        rows_sql = (
            f"{with_sql}"  # nosec B608 — see module docstring
            "SELECT ef.enrolment_id, ef.applicant_id, a.full_name, ef.requisition_id,"
            "       ef.requisition_title, ef.source, ef.created_at, ef.m_hire_interviewer_score"
            "  FROM app_facts ef"
            "  JOIN applicants a ON a.id = ef.applicant_id AND a.company_id = ef.company_id"
            f" WHERE {pop_sql}{band_filter_sql}"
            " ORDER BY ef.created_at DESC"
            " LIMIT :lim"
        )
        rows = (
            await db.execute(text(rows_sql), {**params, **band_params, "lim": limit})
        ).mappings().all()
        succeeded = True
    finally:
        if succeeded:  # never on an aborted transaction; see _set_timeout
            await _restore_timeout(db, previous_timeout)

    member_rows = [
        MemberRow(
            enrolment_id=str(r["enrolment_id"]),
            applicant_id=str(r["applicant_id"]),
            candidate_name=r["full_name"],
            requisition_id=str(r["requisition_id"]) if r["requisition_id"] else None,
            requisition_title=r["requisition_title"],
            source=r["source"],
            applied_at=r["created_at"].isoformat(),
            interviewer_score=(
                round(float(r["m_hire_interviewer_score"]), 1)
                if r["m_hire_interviewer_score"] is not None
                else None
            ),
        )
        for r in rows
    ]
    resolved_part: Literal["numerator", "denominator"] = (
        "denominator" if population is metric.denominator and population else "numerator"
    )
    return MembersResult(
        metric=metric.name, version=metric.version, part=resolved_part, total=total,
        truncated=total > len(member_rows), rows=member_rows,
    )


def default_cohort_to_date() -> date:
    """"Today", for an open-ended ``to`` — kept as a function (not a constant)
    so a test can freeze it without monkeypatching a module-level value."""
    return datetime.now().date() + timedelta(days=1)
