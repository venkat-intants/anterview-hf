"""The governed metric layer's HR-facing API — PH5-C2.

Three routes, all reads, all company-scoped from the SESSION (never a
request parameter):

* ``GET /hr/metrics/definitions`` — every flag, measure, dimension and metric
  (all versions), with formulas, cohort bases and change notes. Holds no
  candidate data, so both ``hr_manager`` and ``super_admin`` may read it.
* ``GET /hr/analytics/funnel`` — the governed funnel and outcomes, overall
  and optionally grouped by source or requisition. ``hr_manager`` only (the
  existing ``/hr/analytics`` gate).
* ``GET /hr/analytics/members`` — the applications behind one metric's
  numerator or denominator, capped at 200, audited
  (``analytics.members_viewed`` — the metric, part and filters, NEVER
  candidate names). ``hr_manager`` only.

A ``requisition_id`` belonging to another company is not distinguished from
one that does not exist: every query is scoped by ``e.company_id`` (via
``app.metrics.compute``), and ``enrolments.requisition_id`` is FK-paired with
``enrolments.company_id`` against ``job_requisitions(id, company_id)``, so a
foreign id simply matches no row. The funnel and members routes therefore
return an EMPTY result (zero counts / zero rows) rather than a 404 — chosen
over a 404 so a cross-tenant id cannot be distinguished from an id that
matches nothing at all in this company, which is the same non-disclosure
shape every other company-scoped GET in this service already has.

This module never writes, except the ONE deliberate exception the members
route commits: its own audit row. That is C1's "read-only... no writes except
the drill-down audit row", restated here at the route layer, not just in
``app.metrics.compute`` (which the AST test in
``tests/unit/test_ph5_w1_metrics_definitions.py`` covers as an importable
module — this router has an audited write of its own, by request).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from shared.auth.base import User
from sqlalchemy import text as sa_text

from app.application_source import SOURCE_LABELS, SOURCES
from app.database import DbSessionDep
from app.dependencies import HrCtxDep, require_role_password_ok
from app.metrics.compute import (
    CohortWindow,
    FunnelFilters,
    MetricComputeError,
    compute_funnel,
    compute_members,
)
from app.metrics.definitions import (
    DIMENSIONS_REGISTRY,
    FLAGS,
    INTERVIEWER_SCORE_BAND_LABELS,
    MEASURES,
    METRICS,
    REGISTRY_HASH,
    checkin_outcome_drilldown_blocked,
    current_metrics,
    drillable,
    min_cell_size,
)
from app.models import AuditLog
from app.utils.request_ip import extract_client_ip, extract_user_agent

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-metrics"])

_VALID_COHORTS = ("application", "decision", "hire")
_VALID_GROUP_BY = ("source", "requisition")
_VALID_PARTS = ("numerator", "denominator")
_VALID_SCORE_BANDS = tuple(INTERVIEWER_SCORE_BAND_LABELS)

#: PH5-E4: the FIXED metric list each outcome-signals cohort reports —
#: never every metric ``compute_funnel`` happens to return for that cohort
#: basis (the "decision" basis carries the WHOLE pipeline, of which this
#: view shows only the one outcome-relevant rate). "hire" lists all five
#: hire-cohort metrics, which is deliberately the same set
#: ``_metrics_for_cohort("hire")`` already returns — kept as an explicit
#: tuple here anyway, so a future hire-cohort metric does not silently widen
#: this view without a decision to add it.
_OUTCOME_METRICS: dict[str, tuple[str, ...]] = {
    "hire": ("hires", "checkin_coverage", "retention_90d", "performance_90d", "hire_interviewer_score"),
    "decision": ("application_to_hire",),
}


# ---------------------------------------------------------------------------
# GET /hr/metrics/definitions — hr_manager AND super_admin
# ---------------------------------------------------------------------------
async def get_definitions_ctx(
    user: Annotated[User, Depends(require_role_password_ok("hr_manager", "super_admin"))],
    db: DbSessionDep,
) -> tuple[uuid.UUID, uuid.UUID]:
    """(actor_user_id, company_id) for either an hr_manager or a super_admin.

    A dedicated context rather than reusing ``HrCtxDep``/``SuperAdminCtxDep``:
    those each gate on ONE role, and definitions hold no candidate data, so
    the wider audience is deliberate here and only here (see the module
    docstring). Company still comes from the session, joined to companies,
    exactly as the two role-specific contexts do.
    """
    try:
        uid = uuid.UUID(user.user_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid user identity.") from exc
    company_id = await db.scalar(
        sa_text(
            "SELECT u.company_id FROM users u"
            "  JOIN companies c ON c.id = u.company_id AND c.deleted_at IS NULL"
            " WHERE u.id = :uid AND u.deleted_at IS NULL"
        ),
        {"uid": uid},
    )
    if company_id is None:
        raise HTTPException(
            status_code=403, detail="Your account is not assigned to an active company."
        )
    return uid, company_id


DefinitionsCtxDep = Annotated[tuple[uuid.UUID, uuid.UUID], Depends(get_definitions_ctx)]


class FlagOut(BaseModel):
    name: str
    version: int
    description: str


class MeasureOut(BaseModel):
    name: str
    version: int
    description: str


class DimensionValueOut(BaseModel):
    key: str
    label: str


class DimensionOut(BaseModel):
    name: str
    version: int
    label: str
    description: str
    values: list[DimensionValueOut] | None = None


class MetricOut(BaseModel):
    name: str
    version: int
    label: str
    kind: str
    effective_from: str
    current: bool
    description: str
    formula: str
    cohort_bases: list[str]
    dimensions: list[str]
    numerator: list[str]
    denominator: list[str]
    measure: str | None
    buckets: list[str] | None
    change_note: str
    # PH5-C2 coordination: derived from the SAME rule `/hr/analytics/members`
    # enforces (`checkin_outcome_drilldown_blocked`), so the web screen never
    # has to guess which cell it may click into.
    drillable: dict[str, bool]


class DefinitionsOut(BaseModel):
    registry_hash: str
    flags: list[FlagOut]
    measures: list[MeasureOut]
    dimensions: list[DimensionOut]
    metrics: list[MetricOut]


def _dimension_out(name: str) -> DimensionOut:
    dim = DIMENSIONS_REGISTRY[f"{name}@1"]
    values = None
    if name == "source":
        values = [
            DimensionValueOut(key=key, label=SOURCE_LABELS.get(key, key))
            for key in sorted(SOURCES)
        ]
    return DimensionOut(
        name=dim.name, version=dim.version, label=dim.label, description=dim.description,
        values=values,
    )


@router.get("/metrics/definitions", response_model=DefinitionsOut)
async def get_metric_definitions(_ctx: DefinitionsCtxDep) -> DefinitionsOut:
    """Every governed flag, measure, dimension and metric — all versions."""
    current = current_metrics()
    return DefinitionsOut(
        registry_hash=REGISTRY_HASH,
        flags=[
            FlagOut(name=f.name, version=f.version, description=f.description)
            for f in sorted(FLAGS.values(), key=lambda x: (x.name, x.version))
        ],
        measures=[
            MeasureOut(name=m.name, version=m.version, description=m.description)
            for m in sorted(MEASURES.values(), key=lambda x: (x.name, x.version))
        ],
        dimensions=[_dimension_out(name) for name in ("source", "requisition")],
        metrics=[
            MetricOut(
                name=m.name, version=m.version, label=m.label, kind=m.kind,
                effective_from=m.effective_from.isoformat(),
                current=current.get(m.name) is not None and current[m.name].version == m.version,
                description=m.description, formula=m.formula,
                cohort_bases=list(m.cohort_bases), dimensions=list(m.dimensions),
                numerator=list(m.numerator), denominator=list(m.denominator),
                measure=m.measure, buckets=list(m.buckets) if m.buckets else None,
                change_note=m.change_note, drillable=drillable(m),
            )
            for m in sorted(METRICS.values(), key=lambda x: (x.name, x.version))
        ],
    )


# ---------------------------------------------------------------------------
# Shared query parsing — funnel and members
# ---------------------------------------------------------------------------
def _parse_cohort(cohort: str) -> Literal["application", "decision", "hire"]:
    if cohort not in _VALID_COHORTS:
        raise HTTPException(
            status_code=400, detail=f"cohort must be one of {list(_VALID_COHORTS)}"
        )
    return cohort  # type: ignore[return-value]


def _parse_source(source: str | None) -> str | None:
    if source is not None and source not in SOURCES:
        raise HTTPException(status_code=400, detail=f"source must be one of {sorted(SOURCES)}")
    return source


def _cohort_window(
    cohort: str, from_: dt.date | None, to: dt.date | None
) -> CohortWindow:
    return CohortWindow(basis=_parse_cohort(cohort), from_=from_, to_=to)


class MetricValueOut(BaseModel):
    """One metric's figure. Shape varies by ``kind``: a count has only
    ``value``; a rate adds ``numerator``/``denominator``; a median/mean adds
    ``n``; a distribution's ``value`` is itself a bucket->count mapping.
    Unused fields are omitted (None)."""

    metric: str
    version: int
    kind: str
    value: Any = None
    numerator: int | None = None
    denominator: int | None = None
    n: int | None = None
    suppressed: bool | None = None


class FunnelCohortOut(BaseModel):
    basis: str
    from_: str | None = Field(default=None, serialization_alias="from")
    to: str | None = None


class FunnelFiltersOut(BaseModel):
    requisition_id: str | None = None
    source: str | None = None


class FunnelGroupOut(BaseModel):
    key: str | None
    label: str
    in_progress: int
    metrics: dict[str, MetricValueOut]


class FunnelOut(BaseModel):
    registry_hash: str
    cohort: FunnelCohortOut
    filters: FunnelFiltersOut
    group_by: str | None
    groups: list[FunnelGroupOut]


@router.get("/analytics/funnel", response_model=FunnelOut)
async def get_analytics_funnel(
    ctx: HrCtxDep,
    db: DbSessionDep,
    cohort: Annotated[str, Query()] = "application",
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: Annotated[dt.date | None, Query()] = None,
    requisition_id: Annotated[uuid.UUID | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    group_by: Annotated[str | None, Query()] = None,
) -> FunnelOut:
    """The governed funnel + outcomes for the caller's company.

    Returns only the metrics whose ``cohort_bases`` include the requested
    cohort. The first group is always the overall "All"; a ``group_by``
    value adds one row per source/requisition present in the window.
    """
    _hr_uid, company_id = ctx
    if group_by is not None and group_by not in _VALID_GROUP_BY:
        raise HTTPException(
            status_code=400, detail=f"group_by must be one of {list(_VALID_GROUP_BY)}"
        )
    source = _parse_source(source)
    window = _cohort_window(cohort, from_, to)

    result = await compute_funnel(
        db, company_id=company_id, cohort=window,
        filters=FunnelFilters(requisition_id=requisition_id, source=source),
        group_by=group_by,  # type: ignore[arg-type]
    )
    return FunnelOut(
        registry_hash=result.registry_hash,
        cohort=FunnelCohortOut(
            basis=result.cohort.basis,
            from_=result.cohort.from_.isoformat() if result.cohort.from_ else None,
            to=result.cohort.to_.isoformat() if result.cohort.to_ else None,
        ),
        filters=FunnelFiltersOut(
            requisition_id=str(requisition_id) if requisition_id else None, source=source,
        ),
        group_by=group_by,
        groups=[
            FunnelGroupOut(
                key=g.key, label=g.label, in_progress=g.in_progress,
                metrics={
                    name: MetricValueOut(**value) for name, value in g.metrics.items()
                },
            )
            for g in result.groups
        ],
    )


# ---------------------------------------------------------------------------
# GET /hr/analytics/members — the drill-down, audited
# ---------------------------------------------------------------------------
class MemberRowOut(BaseModel):
    enrolment_id: str
    applicant_id: str
    candidate_name: str
    requisition_id: str | None
    requisition_title: str | None
    source: str
    applied_at: str
    # PH5-E4: populated only when the request carries a `score_band` filter —
    # otherwise omitted (None), so an ordinary members drill-down is
    # byte-for-byte what it always was.
    interviewer_score: float | None = None
    evidence_href: str | None = None


class MembersOut(BaseModel):
    metric: str
    version: int
    part: str
    total: int
    truncated: bool
    rows: list[MemberRowOut]


@router.get("/analytics/members", response_model=MembersOut)
async def get_analytics_members(
    request: Request,
    ctx: HrCtxDep,
    db: DbSessionDep,
    metric: Annotated[str, Query()],
    part: Annotated[str, Query()] = "numerator",
    cohort: Annotated[str, Query()] = "application",
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: Annotated[dt.date | None, Query()] = None,
    requisition_id: Annotated[uuid.UUID | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    score_band: Annotated[str | None, Query()] = None,
) -> MembersOut:
    """The applications behind one metric's numerator or denominator.

    Audited as ``analytics.members_viewed`` with the metric, part and
    filters — never a candidate name. Refused (422) for a check-in OUTCOME
    metric/part combination (PH5-C1: those never surface below aggregate) —
    ``checkin_coverage`` (operational — has a check-in been recorded, not
    what it said) is unaffected.

    ``score_band`` (PH5-E4) additionally narrows the population to hires in
    that band, and each row then also carries ``interviewer_score`` and
    ``evidence_href``. The SAME checkin-outcome refusal above still applies
    first — a score-band filter never widens what a checkin-outcome
    metric/part is allowed to show below aggregate.
    """
    hr_uid, company_id = ctx
    if part not in _VALID_PARTS:
        raise HTTPException(status_code=400, detail=f"part must be one of {list(_VALID_PARTS)}")
    if score_band is not None and score_band not in _VALID_SCORE_BANDS:
        raise HTTPException(
            status_code=400, detail=f"score_band must be one of {list(_VALID_SCORE_BANDS)}"
        )
    source = _parse_source(source)
    window = _cohort_window(cohort, from_, to)

    current = current_metrics()
    target = current.get(metric)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Unknown metric {metric!r}.")
    if checkin_outcome_drilldown_blocked(target, part):
        raise HTTPException(
            status_code=422, detail="Check-in outcomes are shown only in aggregate."
        )

    try:
        result = await compute_members(
            db, company_id=company_id, metric_name=metric, part=part,  # type: ignore[arg-type]
            cohort=window, filters=FunnelFilters(requisition_id=requisition_id, source=source),
            score_band=score_band,
        )
    except MetricComputeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.add(
        AuditLog(
            actor_id=hr_uid,
            actor_type="user",
            action="analytics.members_viewed",
            resource_type="metric",
            resource_id=None,
            details={
                "company_id": str(company_id),
                "metric": result.metric,
                "version": result.version,
                "part": result.part,
                "cohort": window.basis,
                "from": from_.isoformat() if from_ else None,
                "to": to.isoformat() if to else None,
                "requisition_id": str(requisition_id) if requisition_id else None,
                "source": source,
                "score_band": score_band,
                "total": result.total,
            },
            ip_address=extract_client_ip(request),
            user_agent=extract_user_agent(request),
            event_ts=dt.datetime.now(tz=dt.UTC),
        )
    )
    await db.commit()
    log.info(
        "analytics.members_viewed", company_id=str(company_id), metric=result.metric,
        part=result.part, total=result.total,
    )
    return MembersOut(
        metric=result.metric, version=result.version, part=result.part, total=result.total,
        truncated=result.truncated,
        rows=[
            MemberRowOut(
                enrolment_id=r.enrolment_id, applicant_id=r.applicant_id,
                candidate_name=r.candidate_name, requisition_id=r.requisition_id,
                requisition_title=r.requisition_title, source=r.source, applied_at=r.applied_at,
                interviewer_score=r.interviewer_score if score_band is not None else None,
                evidence_href=(
                    f"/hr/enrolments/{r.enrolment_id}/evidence" if score_band is not None else None
                ),
            )
            for r in result.rows
        ],
    )


# ---------------------------------------------------------------------------
# GET /hr/analytics/outcome-signals — PH5-E4. Human interviewer scores only;
# never an AI interview score (see app.metrics.definitions.MEASURES
# ["hire_interviewer_score@1"]). A thin wrapper over Wave 1's compute_funnel,
# grouped by the governed interviewer_score_band dimension, filtered to a
# FIXED metric list per cohort (_OUTCOME_METRICS) — never every metric that
# cohort basis happens to carry.
# ---------------------------------------------------------------------------
class OutcomeBandOut(BaseModel):
    key: str
    label: str


class OutcomeSignalOut(BaseModel):
    dimension: str
    version: int
    measure: str
    bands: list[OutcomeBandOut]


class OutcomeGroupOut(BaseModel):
    key: str | None
    label: str
    metrics: dict[str, MetricValueOut]


class OutcomeGuardrailsOut(BaseModel):
    evaluation_signal: str
    min_group: int
    changes_candidate_status: bool


class OutcomeSignalsOut(BaseModel):
    registry_hash: str
    cohort: FunnelCohortOut
    filters: FunnelFiltersOut
    signal: OutcomeSignalOut
    groups: list[OutcomeGroupOut]
    guardrails: OutcomeGuardrailsOut


def _default_outcome_window(to: dt.date | None, from_: dt.date | None) -> tuple[dt.date, dt.date]:
    """The last 12 months, ending today, when the caller supplies neither
    bound — a fixed function (not inlined) so a test can pin the arithmetic
    without freezing the clock."""
    end = to or dt.date.today()
    start = from_ or (end - dt.timedelta(days=365))
    return start, end


@router.get("/analytics/outcome-signals", response_model=OutcomeSignalsOut)
async def get_analytics_outcome_signals(
    ctx: HrCtxDep,
    db: DbSessionDep,
    cohort: Annotated[str, Query()] = "hire",
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: Annotated[dt.date | None, Query()] = None,
    requisition_id: Annotated[uuid.UUID | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
) -> OutcomeSignalsOut:
    """Interview scores and later outcomes, banded by the mean CURRENT human
    interviewer scorecard score. NEVER an AI interview score — those are
    purged 90 days after the session and are structurally excluded from this
    view (the underlying measure reads only ``interviewer_scorecard_scores``).

    A SIGNAL, never a decision: this changes no candidate's status or score.
    Not per-read audited (Wave 1 v2 precedent for aggregate reads) — logged
    at structlog level only, as ``analytics.outcome_signals.read``.
    """
    hr_uid, company_id = ctx
    if cohort not in ("hire", "decision"):
        raise HTTPException(status_code=400, detail="cohort must be one of ['hire', 'decision']")
    source = _parse_source(source)
    start, end = _default_outcome_window(to, from_)
    window = CohortWindow(basis=cohort, from_=start, to_=end)  # type: ignore[arg-type]

    result = await compute_funnel(
        db, company_id=company_id, cohort=window,
        filters=FunnelFilters(requisition_id=requisition_id, source=source),
        group_by="interviewer_score_band",
    )
    wanted = _OUTCOME_METRICS[cohort]
    groups = [
        OutcomeGroupOut(
            key=g.key, label=g.label,
            metrics={
                name: MetricValueOut(**value)
                for name, value in g.metrics.items() if name in wanted
            },
        )
        for g in result.groups
    ]

    log.info(
        "analytics.outcome_signals.read", company_id=str(company_id), cohort=cohort,
        actor_id=hr_uid,
    )
    return OutcomeSignalsOut(
        registry_hash=result.registry_hash,
        cohort=FunnelCohortOut(basis=cohort, from_=start.isoformat(), to=end.isoformat()),
        filters=FunnelFiltersOut(
            requisition_id=str(requisition_id) if requisition_id else None, source=source,
        ),
        signal=OutcomeSignalOut(
            dimension="interviewer_score_band", version=1, measure="hire_interviewer_score@1",
            bands=[
                OutcomeBandOut(key=key, label=label)
                for key, label in INTERVIEWER_SCORE_BAND_LABELS.items()
            ],
        ),
        groups=groups,
        guardrails=OutcomeGuardrailsOut(
            evaluation_signal="human interviewer scorecards only",
            min_group=min_cell_size(),
            changes_candidate_status=False,
        ),
    )
