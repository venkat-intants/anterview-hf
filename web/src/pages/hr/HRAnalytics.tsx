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
import { useAccentColor } from '@/lib/useAccentColor';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Cell,
  PieChart,
  Pie,
  LineChart,
  Line,
  Tooltip,
  CartesianGrid,
} from 'recharts';
import { getHrAnalytics, type HrAnalytics } from '@/api/pipeline';
import { Reveal } from '@/design/components/Reveal';
import { GlassCard, SegTabs } from '@/design/components/primitives';
import { TOOLTIP_STYLE } from '@/lib/chartTheme';
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

// ── Tooltip style (design spec) ───────────────────────────────────────────────

// ── Helpers ───────────────────────────────────────────────────────────────────

function scoreColor(v: number): string {
  if (v >= 85) return '#27c93f';
  if (v >= 70) return '#0088ff';
  if (v >= 55) return '#ffb764';
  return '#e6714f';
}

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
        <span className="text-[var(--ui-soft)]">—</span>
        <span className="text-[var(--ui-faint)]">(not enough data yet)</span>
      </span>
    );
  }
  return <span className="text-[var(--ui-soft)]">{value.toFixed(1)}%</span>;
}

// ── Static chart data (language-mix + score-dist + trend).
//    These have no backing API yet; they render as empty states until
//    the analytics endpoint expands. When the API ships, replace these
//    with derived data from getHrAnalytics. ────────────────────────────────────
const LANG_MIX_STATIC: { label: string; value: number; color: string }[] = [];

const SCORE_DIST_STATIC: { label: string; value: number }[] = [];

const HR_TREND_STATIC: { day: string; interviews: number; avg: number }[] = [];

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
            <div className="w-[84px] text-[13px] text-[var(--ui-soft)]">{row.label}</div>
            <div className="h-7 flex-1 overflow-hidden rounded-[8px] bg-[var(--ui-inset)]">
              {pct > 0 && (
                <div
                  className="flex h-full items-center rounded-[8px] bg-[linear-gradient(90deg,var(--accent),#a887dc)] pl-3 text-[12px] font-semibold"
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
  const { data, isLoading } = useQuery({
    queryKey: ['hr', 'analytics'],
    queryFn: () => getHrAnalytics(),
    staleTime: 60_000,
  });
  const f = data?.funnel;
  const avg = data?.averages;
  const conv = data?.conversion;

  const distData = SCORE_DIST_STATIC.map((d) => ({
    ...d,
    color: scoreColor(parseInt(d.label, 10) + 5),
  }));
  const accent = useAccentColor();

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* Hiring funnel — real data */}
        <Reveal dir="left" className="lg:col-span-2">
          <GlassCard className="p-5">
            <h3 className="mb-5 text-[16px] font-semibold">Hiring funnel</h3>
            {isLoading ? (
              <div className="space-y-2.5">
                {[0, 1, 2, 3, 4].map((i) => (
                  <div key={i} className="h-7 animate-pulse rounded-[8px] bg-[var(--ui-inset)]" />
                ))}
              </div>
            ) : f ? (
              <FunnelBars f={f} />
            ) : null}
            {/* Conversion rates subtitle — the governed `conversion.pct_*`
                fields from this same response (PH5-C2), each a share of
                applications. Never derived from `f` here: a client-side
                hires ÷ interviews (etc.) is a second, conflicting definition
                of the same number and can read over 100%. */}
            {f && avg && conv && (
              <div className="mt-4 flex flex-wrap gap-4 text-[12px] text-[var(--ui-faint)]">
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
          </GlassCard>
        </Reveal>

        {/* Language mix donut */}
        <Reveal dir="right">
          <GlassCard className="h-full p-5">
            <h3 className="mb-3 text-[16px] font-semibold">Language mix</h3>
            {LANG_MIX_STATIC.length > 0 ? (
              <>
                <div className="h-[180px] w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={LANG_MIX_STATIC}
                        dataKey="value"
                        nameKey="label"
                        innerRadius={48}
                        outerRadius={70}
                        paddingAngle={2}
                        stroke="none"
                      >
                        {LANG_MIX_STATIC.map((s) => (
                          <Cell key={s.label} fill={s.color} />
                        ))}
                      </Pie>
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="mt-2 flex flex-col gap-2">
                  {LANG_MIX_STATIC.map((s) => (
                    <div key={s.label} className="flex items-center gap-2 text-[13px]">
                      <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: s.color }} />
                      {s.label}
                      <span className="ml-auto text-[var(--ui-faint)]">{s.value}%</span>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="flex h-[180px] items-center justify-center">
                <p className="text-[13px] text-muted-foreground">No language data yet.</p>
              </div>
            )}
          </GlassCard>
        </Reveal>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {/* Score distribution */}
        <Reveal dir="left">
          <GlassCard className="p-5">
            <h3 className="mb-4 text-[16px] font-semibold">Score distribution</h3>
            {distData.length > 0 ? (
              <div className="h-[220px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={distData}>
                    <CartesianGrid vertical={false} stroke="rgba(255,255,255,0.05)" />
                    <XAxis
                      dataKey="label"
                      tick={{ fill: '#70757c', fontSize: 11 }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      tick={{ fill: '#70757c', fontSize: 11 }}
                      axisLine={false}
                      tickLine={false}
                      width={28}
                    />
                    <Tooltip
                      cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                      contentStyle={TOOLTIP_STYLE}
                    />
                    <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                      {distData.map((d) => (
                        <Cell key={d.label} fill={d.color} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="flex h-[220px] items-center justify-center">
                <p className="text-[13px] text-muted-foreground">No score data yet.</p>
              </div>
            )}
          </GlassCard>
        </Reveal>

        {/* Interviews & avg score trend */}
        <Reveal dir="right">
          <GlassCard className="p-5">
            <h3 className="mb-4 text-[16px] font-semibold">Interviews &amp; avg score</h3>
            {HR_TREND_STATIC.length > 0 ? (
              <div className="h-[220px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={HR_TREND_STATIC}>
                    <CartesianGrid vertical={false} stroke="rgba(255,255,255,0.05)" />
                    <XAxis
                      dataKey="day"
                      tick={{ fill: '#70757c', fontSize: 11 }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      tick={{ fill: '#70757c', fontSize: 11 }}
                      axisLine={false}
                      tickLine={false}
                      width={28}
                    />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    <Line
                      type="monotone"
                      dataKey="interviews"
                      stroke={accent}
                      strokeWidth={2.5}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="avg"
                      stroke="#a887dc"
                      strokeWidth={2.5}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="flex h-[220px] items-center justify-center">
                <p className="text-[13px] text-muted-foreground">No trend data yet.</p>
              </div>
            )}
          </GlassCard>
        </Reveal>
      </div>

      {/* Averages summary row (only when data loaded) */}
      {avg && (
        <Reveal>
          <div className="flex flex-wrap gap-6 rounded-[16px] border border-border bg-[var(--ui-inset-soft)] px-5 py-4 text-[13px] text-muted-foreground">
            {avg.avg_ats !== null && (
              <span>
                Avg ATS score:{' '}
                <span className="font-semibold text-foreground">{Math.round(avg.avg_ats)}</span>
              </span>
            )}
            {avg.avg_exam_percent !== null && (
              <span>
                Avg exam score:{' '}
                <span className="font-semibold text-foreground">
                  {Math.round(avg.avg_exam_percent)}%
                </span>
              </span>
            )}
            {avg.avg_interview_composite !== null && (
              <span>
                Avg interview score:{' '}
                <span className="font-semibold text-foreground">
                  {avg.avg_interview_composite.toFixed(1)}/10
                </span>
              </span>
            )}
          </div>
        </Reveal>
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

const FIELD_CLS =
  'w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none';

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
    'Decision cohort: applications decided (hired or rejected) in this period — the fair way to compare outcomes.',
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
  return (
    <GlassCard className="p-5">
      <h2 className="text-[15px] font-semibold text-foreground">Cohort</h2>
      <div className="mt-3">
        <SegTabs
          tabs={COHORT_TABS}
          active={filters.cohort}
          onChange={(k) => onChange({ cohort: k as CohortBasis })}
        />
        <p className="mt-2 text-[12px] text-muted-foreground">{COHORT_HELP[filters.cohort]}</p>
        {filters.cohort === 'application' ? (
          <p className="mt-1 text-[12px] text-[var(--ui-info)]">
            Comparing outcomes (hire rates)? Applications still in progress make this cohort read
            low —{' '}
            <button
              type="button"
              onClick={() => onChange({ cohort: 'decision' })}
              className="underline underline-offset-2"
            >
              switch to the decision cohort
            </button>{' '}
            for a fair comparison.
          </p>
        ) : null}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <div>
          <label
            htmlFor="an-from"
            className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]"
          >
            From
          </label>
          <input
            id="an-from"
            type="date"
            value={filters.from}
            onChange={(e) => onChange({ from: e.target.value })}
            className={FIELD_CLS}
          />
        </div>
        <div>
          <label
            htmlFor="an-to"
            className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]"
          >
            To
          </label>
          <input
            id="an-to"
            type="date"
            value={filters.to}
            onChange={(e) => onChange({ to: e.target.value })}
            className={FIELD_CLS}
          />
        </div>
        <div>
          <label
            htmlFor="an-opening"
            className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]"
          >
            Opening
          </label>
          <select
            id="an-opening"
            value={filters.requisitionId}
            onChange={(e) => onChange({ requisitionId: e.target.value })}
            className={FIELD_CLS}
          >
            <option value="">All openings</option>
            {openings.map((o) => (
              <option key={o.id} value={o.id}>
                {o.title}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label
            htmlFor="an-source"
            className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]"
          >
            Source
          </label>
          <select
            id="an-source"
            value={filters.source}
            onChange={(e) => onChange({ source: e.target.value })}
            className={FIELD_CLS}
          >
            <option value="">All sources</option>
            {sourceOptions.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <span className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]">
            Group by
          </span>
          <SegTabs
            tabs={GROUP_TABS}
            active={filters.groupBy}
            onChange={(k) => onChange({ groupBy: k as FilterState['groupBy'] })}
          />
        </div>
      </div>
    </GlassCard>
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
    <div className="mx-auto max-w-[1280px] px-6 py-8 lg:px-8">
      <h1 className="text-[28px] font-semibold tracking-[-1px]">Analytics</h1>
      <p className="mt-1 text-[14px] text-muted-foreground">
        Governed hiring metrics — versioned, auditable, recomputed from the underlying records every
        time you look.
      </p>

      {forbidden ? (
        <div className="mt-6 flex flex-col gap-5">
          <div className="rounded-[14px] border border-border bg-[var(--ui-inset-soft)] p-4 text-[13px] text-[var(--ui-soft)]">
            Your role sees how these metrics are defined, not this company&apos;s numbers. An HR
            manager can open the full funnel and quality-of-hire view.
          </div>
          <MetricGlossary definitionsQuery={definitionsQuery} onInfo={setInfoMetric} />
        </div>
      ) : (
        <div className="mt-6 flex flex-col gap-5">
          <CohortControls
            filters={filters}
            onChange={onFiltersChange}
            openings={openingsQuery.data ?? []}
            sourceOptions={sourceOptions}
          />

          <GlassCard className="p-5">
            <h2 className="text-[15px] font-semibold text-foreground">The funnel</h2>
            <div className="mt-3">
              {funnelQuery.isLoading ? (
                <div className="space-y-2.5">
                  {[0, 1, 2, 3, 4].map((i) => (
                    <div key={i} className="h-7 animate-pulse rounded-[8px] bg-[var(--ui-inset)]" />
                  ))}
                </div>
              ) : funnelQuery.isError ? (
                <p className="text-[13px] text-[var(--ui-danger)]">
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
          </GlassCard>

          {showGroupTable ? (
            <GlassCard className="p-5">
              <h2 className="text-[15px] font-semibold text-foreground">
                Compare {filters.groupBy === 'source' ? 'sources' : 'openings'}
              </h2>
              {funnelQuery.data ? (
                <div className="mt-3">
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
            </GlassCard>
          ) : null}

          <GlassCard
            className="border-[rgba(var(--accent-rgb),0.22)] p-5"
            data-testid="quality-of-hire"
          >
            <h2 className="text-[15px] font-semibold text-foreground">Quality of hire</h2>
            <div className="mt-3 flex items-start gap-2.5 rounded-[12px] border border-[rgba(var(--accent-rgb),0.3)] bg-[rgba(var(--accent-rgb),0.06)] p-3.5">
              <Info
                size={15}
                className="mt-0.5 shrink-0 text-[var(--ui-info)]"
                aria-hidden="true"
              />
              <p className="text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
                Quality signals are recorded by your team after hiring. They describe outcomes; they
                never change an application or a decision.
              </p>
            </div>
            <div className="mt-4">
              {qualityQuery.isLoading ? (
                <p className="text-[13px] text-muted-foreground">Loading…</p>
              ) : qualityQuery.isError ? (
                <p className="text-[13px] text-[var(--ui-danger)]">
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
          </GlassCard>

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
    </div>
  );
}
