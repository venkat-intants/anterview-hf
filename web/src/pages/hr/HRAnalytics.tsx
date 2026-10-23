// HRAnalytics — company-scoped hiring analytics.
// Layout: faithfully reproduces anterview-pages/src/screens/hr/HRAnalytics.tsx
//   (hiring-funnel horizontal bars, language-mix donut, score-distribution bar
//    chart, interviews & avg-score line chart).
// Behavior: live getHrAnalytics query (funnel + averages); charts use real data
//   where available and gracefully empty-state where there is no backing API.
//   BOTH exports preserved:
//     default HRAnalytics — embeddable panel used by HRPipeline. Funnel
//       counts + averages are unchanged — still the old `GET /hr/analytics`
//       (application_progress), which the metric-layer migration explicitly
//       keeps working as-is. PH5 wave-1 AUDIT FIX: its rate subtitle used to
//       divide two funnel counts client-side (e.g. hires ÷ interviews for a
//       "Hire rate") — a second, conflicting definition of the same figure
//       the governed layer publishes, and one that could read over 100%. It
//       now reads `conversion.pct_*` straight off the same `GET
//       /hr/analytics` response (PH5-C2 added that block without changing
//       the endpoint), same as HRConsole's stat strip already did. Nothing
//       in this file divides one number by another any more.
//     HRAnalyticsPage     — standalone /hr/analytics route. PH5 wave 1 (C1)
//       rewrites this to the governed metric layer: cohort-scoped funnel,
//       per-group comparison, quality-of-hire and an auditable drill-down.
//       English-only by design (CLAUDE.md — the HR console is not translated).
//       Code review follow-up: this file is the composition root only —
//       every section below Cohort lives in web/src/components/hr/analytics/
//       (MetricCell, PipelineFunnelSection, GroupComparisonTable,
//       QualityOfHireSection, MetricGlossary, MetricInfoDialog,
//       MembersDrillDown, CheckinDueCard), a pure move with no behaviour
//       change. CohortControls stays here — it is page-specific control
//       state, not a data-driven section.

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { QueryErrorPanel } from '@/components/QueryState';
import { getHrAnalytics, type HrAnalytics } from '@/api/pipeline';
import { SegTabs } from '@/design/components/primitives';
import { PageHeader, SectionCard, Page } from '@/design/components/page';
import { Skeleton } from '@/components/ui/skeleton';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { useAuth } from '@/context/AuthContext';
import { ApiError, isTransientApiError } from '@/api/client';
import { listRequisitions, type Requisition } from '@/api/requisitions';
import CandidateDrawer from '@/components/CandidateDrawer';
import { Info } from '@/design/components/icons';
import {
  getMetricDefinitions,
  getAnalyticsFunnel,
  sourceOptionsFromDefinitions,
  type CohortBasis,
  type GroupByOption,
  type MetricPart,
} from '@/api/metrics';
import { listCheckinsDue } from '@/api/checkins';
import type { FilterState } from '@/components/hr/analytics/types';
import PipelineFunnelSection from '@/components/hr/analytics/PipelineFunnelSection';
import GroupComparisonTable from '@/components/hr/analytics/GroupComparisonTable';
import QualityOfHireSection from '@/components/hr/analytics/QualityOfHireSection';
import MetricGlossary from '@/components/hr/analytics/MetricGlossary';
import MetricInfoDialog from '@/components/hr/analytics/MetricInfoDialog';
import MembersDrillDown from '@/components/hr/analytics/MembersDrillDown';
import CheckinDueCard from '@/components/hr/analytics/CheckinDueCard';
import OutcomeSignalsSection from '@/components/hr/analytics/OutcomeSignalsSection';
import { formatScore } from '@/lib/formatters';

// ── Tooltip style (design spec) ───────────────────────────────────────────────

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * A governed conversion rate, rendered exactly as `GET /hr/analytics` sent
 * it — this file never divides two of its own numbers to build one. Null
 * means "nobody has applied yet" (the only reason this block ever nulls; see
 * `HrConversion`'s docstring), not "too few to compare" — that suppression
 * concept belongs to the separate governed-metric layer (HRAnalyticsPage),
 * not this response.
 */
function ConversionRate({ value }: { value: number | null }): JSX.Element {
  if (value === null) {
    return (
      <span className="inline-flex items-center gap-1">
        <span className="text-[var(--ui-soft)]">-</span>
        <span className="text-muted-foreground">(not enough data yet)</span>
      </span>
    );
  }
  return <span className="text-[var(--ui-soft)]">{value.toFixed(1)}%</span>;
}

// ── FunnelBars — horizontal bar chart using real funnel data ─────────────────
/** Applications, which every other funnel count is a count of (B5). People
 *  would undercount the top of a funnel whose lower bars count applications —
 *  one person shortlisted for two openings is two shortlists. Older servers
 *  send only people. */
function applied(f: HrAnalytics['funnel']): number {
  return f.total_applications ?? f.total_applicants;
}

function FunnelBars({ f }: { f: HrAnalytics['funnel'] }) {
  const max = applied(f) || 1;
  const rows = [
    { label: 'Applied', value: applied(f) },
    { label: 'Shortlisted', value: f.shortlisted },
    { label: 'Exam passed', value: f.exam_passed },
    { label: 'Interviewed', value: f.interview_completed },
    { label: 'Hired', value: f.hired },
  ];

  if (!rows.some((r) => r.value > 0)) {
    return (
      <div className="flex h-[140px] items-center justify-center">
        <p className="text-[13px] text-muted-foreground">No pipeline data yet.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2.5">
      {rows.map((row) => {
        const pct = Math.round((row.value / max) * 100);
        return (
          <div key={row.label} className="flex items-center gap-3.5">
            <div className="w-[84px] shrink-0 text-[13px] text-[var(--ui-soft)]">{row.label}</div>
            <div className="h-7 flex-1 overflow-hidden rounded-[8px] bg-[var(--ui-inset-strong)]">
              {pct > 0 && (
                <div
                  className="flex h-full items-center rounded-[8px] bg-[var(--accent)] pl-3 text-[12px] font-semibold text-primary-foreground"
                  style={{ width: `${pct}%` }}
                >
                  {row.value.toLocaleString('en-IN')}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Default export: embeddable panel (used by HRPipeline) ─────────────────────
export default function HRAnalytics() {
  const analyticsQuery = useQuery({
    queryKey: ['hr', 'analytics'],
    queryFn: () => getHrAnalytics(),
    staleTime: 60_000,
  });
  const { data, isLoading } = analyticsQuery;
  const f = data?.funnel;
  const avg = data?.averages;
  const conv = data?.conversion;

  return (
    <div className="space-y-5">
      <div>
        {/* Hiring funnel — real data */}
        <SectionCard
          title="Hiring funnel"
          description="Applications at each stage, from applied to hired."
        >
          {isLoading ? (
            <div className="space-y-2.5">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-7 rounded-[8px]" />
              ))}
            </div>
          ) : f ? (
            <FunnelBars f={f} />
          ) : analyticsQuery.isError ? (
            // HR-M5: a failed load used to render nothing at all.
            <QueryErrorPanel
              title="Could not load the hiring funnel."
              error={analyticsQuery.error}
              onRetry={() => void analyticsQuery.refetch()}
              retrying={analyticsQuery.isFetching}
            />
          ) : null}
          {/* HR-L15: this panel is the quick summary; the governed funnel,
              with cohorts and definitions for every number, is its own page. */}
          <p className="mt-4 text-[13px]">
            <Link
              to="/hr/analytics"
              className="font-semibold text-[var(--accent)] underline-offset-2 hover:underline"
            >
              Open full analytics
            </Link>
          </p>
          {/* Conversion rates subtitle — the governed `conversion.pct_*`
              fields from this same response (PH5-C2), each a share of
              applications. Never derived from `f` here: a client-side
              hires ÷ interviews (etc.) is a second, conflicting definition
              of the same number and can read over 100%. */}
          {f && avg && conv && (
            <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-[13px] text-muted-foreground">
              <span>
                Shortlist rate: <ConversionRate value={conv.pct_shortlisted} />
              </span>
              <span>
                Sat exam rate: <ConversionRate value={conv.pct_sat_exam} />
              </span>
              <span>
                Interviewed rate: <ConversionRate value={conv.pct_interviewed} />
              </span>
              <span>
                Hire rate: <ConversionRate value={conv.pct_hired} />
              </span>
            </div>
          )}
        </SectionCard>
      </div>

      {/* Averages summary row (only when data loaded) */}
      {avg && (
        <div>
          <div className="flex flex-wrap gap-6 rounded-[16px] border border-border bg-[var(--ui-inset-soft)] px-5 py-4 text-[13px] text-muted-foreground">
            {avg.avg_ats !== null && (
              <span>
                Avg ATS score:{' '}
                <span className="font-semibold text-foreground">
                  {formatScore('resume', avg.avg_ats)}
                </span>
              </span>
            )}
            {avg.avg_exam_percent !== null && (
              <span>
                Avg exam score:{' '}
                <span className="font-semibold text-foreground">
                  {formatScore('exam', avg.avg_exam_percent)}
                </span>
              </span>
            )}
            {avg.avg_interview_composite !== null && (
              <span>
                Avg interview score:{' '}
                <span className="font-semibold text-foreground">
                  {formatScore('interview', avg.avg_interview_composite)}
                </span>
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// PH5 Wave 1 (C1/C2) — governed metric layer screen (HRAnalyticsPage)
// ═══════════════════════════════════════════════════════════════════════════
//
// Everything below is new for PH5. It is a SEPARATE data path from the panel
// above: every number here comes from `/hr/analytics/funnel` and
// `/hr/metrics/definitions` — versioned, recomputed live, and never derived
// client-side (a rate is never built from neighbouring counts; stages are not
// nested). See scratchpad ph5_wave1_design_v2.md for the full contract. Every
// section past Cohort is imported from components/hr/analytics/ — see the
// header comment.

const COHORT_TABS = [
  { key: 'application', label: 'Application' },
  { key: 'decision', label: 'Decision' },
  { key: 'hire', label: 'Hire' },
];

const GROUP_TABS = [
  { key: 'none', label: 'None' },
  { key: 'source', label: 'Source' },
  { key: 'requisition', label: 'Opening' },
];

const COHORT_HELP: Record<CohortBasis, string> = {
  application: 'Application cohort: people who applied in this period.',
  decision:
    'Decision cohort: applications decided (hired or rejected) in this period - the fair way to compare outcomes.',
  hire: 'Hire cohort: people hired in this period.',
};

const DEFAULT_FILTERS: FilterState = {
  cohort: 'application',
  from: '',
  to: '',
  requisitionId: '',
  source: '',
  groupBy: 'none',
};

interface DrillTarget {
  metric: string;
  part: MetricPart;
  cohort: CohortBasis;
  requisition_id?: string;
  source?: string;
}

// ── CohortControls — cohort basis, date range, opening + source filters ─────
// Page-specific control state, not a data-driven section — stays in the
// composition root rather than moving to components/hr/analytics/.
function CohortControls({
  filters,
  onChange,
  openings,
  sourceOptions,
}: {
  filters: FilterState;
  onChange: (patch: Partial<FilterState>) => void;
  openings: Requisition[];
  sourceOptions: { value: string; label: string }[];
}): JSX.Element {
  // HR-L14: the dates are edited here and reach the queries only while From is
  // on or before To. An inverted range used to be sent as-is and came back as
  // an empty funnel, which reads as "nobody applied" rather than as a typo.
  const [dates, setDates] = useState({ from: filters.from, to: filters.to });
  const rangeInverted = Boolean(dates.from && dates.to && dates.from > dates.to);

  function changeDate(patch: Partial<typeof dates>): void {
    const next = { ...dates, ...patch };
    setDates(next);
    if (!(next.from && next.to && next.from > next.to)) onChange(next);
  }

  return (
    <SectionCard title="Cohort">
      <div>
        <SegTabs
          tabs={COHORT_TABS}
          active={filters.cohort}
          onChange={(k) => onChange({ cohort: k as CohortBasis })}
        />
        <p className="mt-2 text-[13px] text-muted-foreground">{COHORT_HELP[filters.cohort]}</p>
        {filters.cohort === 'application' ? (
          <p className="mt-1 text-[13px] text-[var(--ui-info)]">
            Comparing outcomes (hire rates)? Applications still in progress make this cohort read
            low -{' '}
            <button
              type="button"
              onClick={() => onChange({ cohort: 'decision' })}
              className="rounded font-semibold underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              switch to the decision cohort
            </button>{' '}
            for a fair comparison.
          </p>
        ) : null}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <FormField
          id="an-from"
          label="From"
          describedBy={rangeInverted ? 'an-range-error' : undefined}
        >
          {(p) => (
            <Input
              {...p}
              type="date"
              value={dates.from}
              onChange={(e) => changeDate({ from: e.target.value })}
              aria-invalid={rangeInverted || undefined}
            />
          )}
        </FormField>
        <FormField id="an-to" label="To" describedBy={rangeInverted ? 'an-range-error' : undefined}>
          {(p) => (
            <Input
              {...p}
              type="date"
              value={dates.to}
              onChange={(e) => changeDate({ to: e.target.value })}
              aria-invalid={rangeInverted || undefined}
            />
          )}
        </FormField>
        <FormField id="an-opening" label="Opening">
          {(p) => (
            <SelectControl
              {...p}
              value={filters.requisitionId}
              onChange={(e) => onChange({ requisitionId: e.target.value })}
            >
              <option value="">All openings</option>
              {openings.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.title}
                </option>
              ))}
            </SelectControl>
          )}
        </FormField>
        <FormField id="an-source" label="Source">
          {(p) => (
            <SelectControl
              {...p}
              value={filters.source}
              onChange={(e) => onChange({ source: e.target.value })}
            >
              <option value="">All sources</option>
              {sourceOptions.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </SelectControl>
          )}
        </FormField>
        <div>
          <span className="mb-1.5 block text-[13px] font-semibold text-foreground">Group by</span>
          <SegTabs
            tabs={GROUP_TABS}
            active={filters.groupBy}
            onChange={(k) => onChange({ groupBy: k as FilterState['groupBy'] })}
          />
        </div>
      </div>
      {rangeInverted ? (
        <p id="an-range-error" role="alert" className="mt-2 text-[13px] text-[var(--ui-danger)]">
          From must be on or before To. The figures below still use the last valid range.
        </p>
      ) : null}
    </SectionCard>
  );
}

// ── Named export: standalone /hr/analytics page ───────────────────────────────
export function HRAnalyticsPage(): JSX.Element {
  const { user } = useAuth();
  const roles = user?.roles ?? [];
  const isHrManager = roles.includes('hr_manager');

  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [infoMetric, setInfoMetric] = useState<string | null>(null);
  const [drillDown, setDrillDown] = useState<DrillTarget | null>(null);
  const [openCandidate, setOpenCandidate] = useState<{
    applicant_id: string;
    enrolment_id: string;
    /** Set when opened from "Check-ins due" — lands the drawer on the
     *  90-day check-in section instead of its top. */
    focusSection?: 'checkin';
  } | null>(null);

  function onFiltersChange(patch: Partial<FilterState>) {
    setFilters((f) => ({ ...f, ...patch }));
  }

  function openDrillDown(
    metric: string,
    part: MetricPart,
    cohort: CohortBasis,
    requisitionId?: string,
    source?: string,
  ) {
    setDrillDown({ metric, part, cohort, requisition_id: requisitionId, source });
  }

  // Definitions hold no candidate data — available to hr_manager AND
  // super_admin (and fetched regardless, so the glossary always works).
  const definitionsQuery = useQuery({
    queryKey: ['hr', 'metrics', 'definitions'],
    queryFn: getMetricDefinitions,
    staleTime: 5 * 60_000,
  });

  const openingsQuery = useQuery({
    queryKey: ['hr', 'requisitions', 'analytics-filter'],
    queryFn: () => listRequisitions({ limit: 200 }),
    enabled: isHrManager,
    staleTime: 60_000,
  });

  // PH5 wave-1 follow-up (A2) — the "Check-ins due" card inside Quality of hire.
  const checkinsDueQuery = useQuery({
    queryKey: ['hr', 'checkins', 'due'],
    queryFn: listCheckinsDue,
    enabled: isHrManager,
    staleTime: 60_000,
  });

  const sourceOptions = sourceOptionsFromDefinitions(definitionsQuery.data);

  const sharedFilters = {
    from: filters.from || undefined,
    to: filters.to || undefined,
    requisition_id: filters.requisitionId || undefined,
    source: filters.source || undefined,
  };

  // The funnel + per-group comparison — cohort-basis-driven.
  const funnelQuery = useQuery({
    queryKey: [
      'hr',
      'analytics',
      'funnel',
      filters.cohort,
      filters.from,
      filters.to,
      filters.requisitionId,
      filters.source,
      filters.groupBy,
    ],
    queryFn: () =>
      getAnalyticsFunnel({
        cohort: filters.cohort,
        group_by: filters.groupBy === 'none' ? undefined : filters.groupBy,
        ...sharedFilters,
      }),
    enabled: isHrManager,
    retry: (count, error) => isTransientApiError(error) && count < 2,
  });

  // Quality of hire always reads the HIRE cohort, independent of the funnel's
  // own cohort selector — it is a standing section, not a view of the same
  // pick (C1: "Quality of hire (hire cohort)").
  const qualityQuery = useQuery({
    queryKey: [
      'hr',
      'analytics',
      'funnel',
      'hire-cohort-standing',
      filters.from,
      filters.to,
      filters.requisitionId,
      filters.source,
    ],
    queryFn: () => getAnalyticsFunnel({ cohort: 'hire', ...sharedFilters }),
    enabled: isHrManager,
    retry: (count, error) => isTransientApiError(error) && count < 2,
  });

  // A super_admin sees definitions only — never a candidate-scoped number
  // (`/hr/analytics/funnel` is hr_manager-only). Gated by role client-side
  // AND defensively by an actual 403, in case a role ever changes shape.
  const forbidden =
    !isHrManager || (funnelQuery.error instanceof ApiError && funnelQuery.error.status === 403);

  const allGroup =
    funnelQuery.data?.groups.find((g) => g.key === null) ?? funnelQuery.data?.groups[0];
  const qualityGroup =
    qualityQuery.data?.groups.find((g) => g.key === null) ?? qualityQuery.data?.groups[0];
  const showGroupTable = filters.groupBy !== 'none' && (funnelQuery.data?.groups.length ?? 0) > 0;

  return (
    <Page width="workspace">
      <PageHeader
        title="Analytics"
        description="Governed hiring metrics - versioned, auditable, recomputed from the underlying records every time you look."
      />

      {forbidden ? (
        <div className="flex flex-col gap-5">
          <div className="rounded-[16px] border border-border bg-[var(--ui-inset-soft)] p-4 text-[14px] text-foreground">
            Your role sees how these metrics are defined, not this company&apos;s numbers. An HR
            manager can open the full funnel and quality-of-hire view.
          </div>
          <MetricGlossary definitionsQuery={definitionsQuery} onInfo={setInfoMetric} />
        </div>
      ) : (
        <div className="flex flex-col gap-5">
          <CohortControls
            filters={filters}
            onChange={onFiltersChange}
            openings={openingsQuery.data ?? []}
            sourceOptions={sourceOptions}
          />

          <SectionCard title="The funnel">
            <div>
              {funnelQuery.isLoading ? (
                <div className="space-y-2.5">
                  {[0, 1, 2, 3, 4].map((i) => (
                    <Skeleton key={i} className="h-7 rounded-[8px]" />
                  ))}
                </div>
              ) : funnelQuery.isError ? (
                <p role="alert" className="text-[13px] text-[var(--ui-danger)]">
                  Could not load the funnel just now.
                </p>
              ) : (
                <PipelineFunnelSection
                  group={allGroup}
                  cohort={filters.cohort}
                  definitions={definitionsQuery.data}
                  onInfo={setInfoMetric}
                  onDrillDown={(metric, part) =>
                    openDrillDown(
                      metric,
                      part,
                      filters.cohort,
                      filters.requisitionId || undefined,
                      filters.source || undefined,
                    )
                  }
                />
              )}
            </div>
          </SectionCard>

          {showGroupTable ? (
            <SectionCard title={`Compare ${filters.groupBy === 'source' ? 'sources' : 'openings'}`}>
              {funnelQuery.data ? (
                <div>
                  <GroupComparisonTable
                    groups={funnelQuery.data.groups}
                    definitions={definitionsQuery.data}
                    cohort={filters.cohort}
                    groupBy={filters.groupBy as GroupByOption}
                    filters={filters}
                    onInfo={setInfoMetric}
                    openDrillDown={openDrillDown}
                  />
                </div>
              ) : null}
            </SectionCard>
          ) : null}

          <SectionCard title="Quality of hire" data-testid="quality-of-hire">
            <div className="flex items-start gap-2.5 rounded-[12px] border border-[var(--ui-info)]/30 bg-[var(--ui-info-wash)] p-3.5">
              <Info
                size={15}
                className="mt-0.5 shrink-0 text-[var(--ui-info)]"
                aria-hidden="true"
              />
              <p className="text-[13px] leading-relaxed text-foreground">
                Quality signals are recorded by your team after hiring. They describe outcomes; they
                never change an application or a decision.
              </p>
            </div>
            <div className="mt-4">
              {qualityQuery.isLoading ? (
                <Skeleton className="h-16 rounded-[12px]" />
              ) : qualityQuery.isError ? (
                <p role="alert" className="text-[13px] text-[var(--ui-danger)]">
                  Could not load quality-of-hire data just now.
                </p>
              ) : (
                <QualityOfHireSection
                  group={qualityGroup}
                  definitions={definitionsQuery.data}
                  onInfo={setInfoMetric}
                  onDrillDown={(metric, part) =>
                    openDrillDown(
                      metric,
                      part,
                      'hire',
                      filters.requisitionId || undefined,
                      filters.source || undefined,
                    )
                  }
                />
              )}
            </div>

            <CheckinDueCard
              rows={checkinsDueQuery.data ?? []}
              isLoading={checkinsDueQuery.isLoading}
              isError={checkinsDueQuery.isError}
              onOpen={(row) =>
                setOpenCandidate({
                  applicant_id: row.applicant_id,
                  enrolment_id: row.enrolment_id,
                  focusSection: 'checkin',
                })
              }
            />
          </SectionCard>

          <OutcomeSignalsSection
            filters={sharedFilters}
            definitions={definitionsQuery.data}
            onOpenCandidate={setOpenCandidate}
          />

          <MetricGlossary definitionsQuery={definitionsQuery} onInfo={setInfoMetric} />
        </div>
      )}

      {infoMetric ? (
        <MetricInfoDialog
          metricName={infoMetric}
          definitions={definitionsQuery.data}
          onClose={() => setInfoMetric(null)}
        />
      ) : null}

      {drillDown ? (
        <MembersDrillDown
          metricName={drillDown.metric}
          part={drillDown.part}
          definitions={definitionsQuery.data}
          query={{
            cohort: drillDown.cohort,
            from: filters.from || undefined,
            to: filters.to || undefined,
            requisition_id: drillDown.requisition_id,
            source: drillDown.source,
          }}
          onClose={() => setDrillDown(null)}
          onOpenCandidate={setOpenCandidate}
        />
      ) : null}

      <CandidateDrawer
        applicantId={openCandidate?.applicant_id ?? null}
        enrolmentId={openCandidate?.enrolment_id ?? null}
        onClose={() => setOpenCandidate(null)}
        focusSection={openCandidate?.focusSection}
      />
    </Page>
  );
}
