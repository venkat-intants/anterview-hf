// metrics.ts — the governed metric layer (PH5-C1/C2): definitions, the
// company funnel (application/decision/hire cohorts) and the audited
// drill-down behind any count or rate. Field names match the data_gateway
// contract in services/data_gateway/app/metrics EXACTLY.
//
// WHY THIS IS A SEPARATE MODULE FROM api/hr.ts AND api/pipeline.ts
// Those two already each define an `HrAnalytics`/`getHrAnalytics` pair for the
// UNCHANGED `GET /hr/analytics` response (funnel + averages, still backed by
// `application_progress`). This module is the NEW governed layer: every
// number here carries its own `metric@version`, is recomputed live, and can
// be suppressed for a small sample. The two layers intentionally disagree in
// places (see the metrics' own `change_note`) — merging the modules would
// make that look like a bug instead of the documented v1 change it is.
//
// The client NEVER derives a rate from neighbouring counts (stages are not
// nested) and never invents a percentage above what the server returned —
// every MetricResult below is rendered as-is.

import { apiGet } from './client';

// ── Cohort + grouping ────────────────────────────────────────────────────────

export type CohortBasis = 'application' | 'decision' | 'hire';
export type GroupByOption = 'source' | 'requisition';
export type MetricKind = 'count' | 'rate' | 'median' | 'mean' | 'distribution';
export type MetricPart = 'numerator' | 'denominator';

// ── /hr/metrics/definitions ──────────────────────────────────────────────────

export interface FlagDefinition {
  name: string;
  version: number;
  description: string;
}

export interface MeasureDefinition {
  name: string;
  version: number;
  description: string;
}

export interface MetricDefinition {
  name: string;
  version: number;
  label: string;
  kind: MetricKind;
  effective_from: string;
  current: boolean;
  description: string;
  formula: string;
  cohort_bases: CohortBasis[];
  dimensions: GroupByOption[];
  numerator: string[] | null;
  denominator: string[] | null;
  measure: string | null;
  buckets: string[] | null;
  change_note: string | null;
  /**
   * Server-authoritative clickability for `/hr/analytics/members` — replaces
   * the earlier hand-kept NON_DRILLABLE_PARTS. A check-in outcome such as
   * `retention_90d` carries `numerator: false` (who "retained" is never a
   * named list; the server 422s it), while `denominator` and count metrics'
   * lone part stay clickable. Always render from this flag, never re-derive
   * "is this the kind of metric that COULD have a named list" client-side.
   */
  drillable: { numerator: boolean; denominator: boolean };
}

/** A dimension a metric may be grouped by (`source`, `requisition`, …), with
 *  its own human label/description for the "How is this calculated?" panel.
 *  `values` is present for a closed-vocabulary dimension (`source`) — the
 *  server-authoritative option list, replacing a hand-kept one. */
export interface DimensionDefinition {
  name: string;
  version: number;
  label: string;
  description: string;
  values?: { key: string; label: string }[];
}

export interface MetricDefinitionsResponse {
  registry_hash: string;
  flags: FlagDefinition[];
  measures: MeasureDefinition[];
  metrics: MetricDefinition[];
  dimensions: DimensionDefinition[];
}

export function getMetricDefinitions(): Promise<MetricDefinitionsResponse> {
  return apiGet<MetricDefinitionsResponse>('/hr/metrics/definitions');
}

// ── /hr/analytics/funnel ──────────────────────────────────────────────────────

export interface CountMetricResult {
  metric: string;
  version: number;
  kind: 'count';
  value: number;
}

export interface RateMetricResult {
  metric: string;
  version: number;
  kind: 'rate';
  /**
   * A percentage, one decimal place. Null when the denominator is 0, OR when
   * `suppressed` is a CHECK-IN-linked metric (retention_90d) — the outcome
   * itself is aggregate-only. A suppressed PIPELINE (non-check-in) rate —
   * e.g. application_to_hire — now comes back WITH its real value (policy
   * change, code review): `suppressed` alone decides whether the UI shows
   * "too few to compare"; `value === null` is never a proxy for it, and a
   * null value is never shown anywhere (tooltip included) regardless of kind.
   */
  value: number | null;
  /** Null exactly when `value` is (see above) — retention_90d's numerator
   *  (who "retained") is never a named list; a suppressed pipeline rate's
   *  numerator stays a real number. */
  numerator: number | null;
  /** Always present, even when suppressed. */
  denominator: number;
  suppressed: boolean;
}

export interface MedianMeanMetricResult {
  metric: string;
  version: number;
  kind: 'median' | 'mean';
  /** Null when there is nothing to average, or the result is suppressed. */
  value: number | null;
  /** Always present, even when suppressed. */
  n: number;
  suppressed: boolean;
}

export interface DistributionMetricResult {
  metric: string;
  version: number;
  kind: 'distribution';
  /** Null when suppressed — the bucket counts are withheld, not just hidden. */
  value: Record<string, number> | null;
  /** Always present, even when suppressed. */
  n: number;
  suppressed: boolean;
}

export type MetricResult =
  | CountMetricResult
  | RateMetricResult
  | MedianMeanMetricResult
  | DistributionMetricResult;

export interface FunnelGroup {
  /** null for the single "All" group; otherwise the source code or the
   *  requisition id, depending on `group_by`. */
  key: string | null;
  label: string;
  in_progress: number;
  metrics: Record<string, MetricResult>;
}

export interface FunnelResponse {
  registry_hash: string;
  cohort: { basis: CohortBasis; from: string | null; to: string | null };
  filters: { requisition_id: string | null; source: string | null };
  group_by: GroupByOption | null;
  groups: FunnelGroup[];
}

export interface FunnelQuery {
  cohort?: CohortBasis;
  from?: string;
  to?: string;
  requisition_id?: string;
  source?: string;
  group_by?: GroupByOption;
}

function buildFunnelParams(opts: FunnelQuery): URLSearchParams {
  const p = new URLSearchParams();
  if (opts.cohort) p.set('cohort', opts.cohort);
  if (opts.from) p.set('from', opts.from);
  if (opts.to) p.set('to', opts.to);
  if (opts.requisition_id) p.set('requisition_id', opts.requisition_id);
  if (opts.source) p.set('source', opts.source);
  if (opts.group_by) p.set('group_by', opts.group_by);
  return p;
}

export function getAnalyticsFunnel(opts: FunnelQuery = {}): Promise<FunnelResponse> {
  const q = buildFunnelParams(opts).toString();
  return apiGet<FunnelResponse>(`/hr/analytics/funnel${q ? `?${q}` : ''}`);
}

// ── /hr/analytics/members (the audited drill-down) ───────────────────────────

export interface MemberRow {
  enrolment_id: string;
  applicant_id: string;
  candidate_name: string;
  requisition_id: string | null;
  requisition_title: string | null;
  source: string;
  applied_at: string;
}

export interface MembersResponse {
  metric: string;
  version: number;
  part: MetricPart;
  total: number;
  /** Rows are capped at 200, newest first; `total` is the full count. */
  truncated: boolean;
  rows: MemberRow[];
}

export interface MembersQuery extends FunnelQuery {
  metric: string;
  /** Ignored server-side for a count metric. */
  part?: MetricPart;
}

/**
 * Whether a part of a metric may be drilled into, straight from the
 * definition's server-authoritative `drillable` flag — the sole source of
 * truth (replaces the earlier hand-kept NON_DRILLABLE_PARTS). `false` (never
 * a button) until the definition has loaded — a click that might 422 is
 * worse than a number that takes a moment to become clickable. A count
 * metric has no numerator/denominator distinction; callers always pass
 * 'numerator', which is what `drillable.numerator` describes for it too.
 * median/mean/distribution are never drillable — MetricCell never calls this
 * for them, but `false` is the correct answer if it did.
 */
export function isDrillable(definition: MetricDefinition | undefined, part: MetricPart): boolean {
  if (!definition) return false;
  if (definition.kind !== 'count' && definition.kind !== 'rate') return false;
  return definition.drillable[part];
}

export function getAnalyticsMembers(opts: MembersQuery): Promise<MembersResponse> {
  const p = buildFunnelParams(opts);
  p.set('metric', opts.metric);
  if (opts.part) p.set('part', opts.part);
  return apiGet<MembersResponse>(`/hr/analytics/members?${p.toString()}`);
}

// ── Canonical metric ordering (v1) ───────────────────────────────────────────
// Mirrors "Metrics (version 1)" in the PH5 wave-1 design doc. Used only to
// order the UI; any metric name the server returns that is not listed here
// still renders — appended after the known ones — so a v2 metric is never
// silently dropped.

export const PIPELINE_COUNT_METRICS = [
  'applications',
  'screened',
  'assessed',
  'interviewed',
  'selected',
  'hires',
  'rejections',
] as const;

export const PIPELINE_RATE_METRICS = [
  'application_to_screen',
  'application_to_assess',
  'application_to_interview',
  'application_to_hire',
  'screen_to_interview',
  'interview_to_hire',
  'offer_acceptance',
] as const;

export const HIRE_COHORT_METRICS = [
  'hires',
  'time_to_hire_days',
  'checkin_coverage',
  'retention_90d',
  'performance_90d',
  'hire_interviewer_score',
] as const;

/** The known metric names for a cohort, in display order, then whatever else
 *  the server sent (sorted), so a future metric is appended rather than lost. */
export function orderedMetricNames(available: string[], cohort: CohortBasis): string[] {
  const canonical: readonly string[] =
    cohort === 'hire' ? HIRE_COHORT_METRICS : [...PIPELINE_COUNT_METRICS, ...PIPELINE_RATE_METRICS];
  const known = canonical.filter((name) => available.includes(name));
  const rest = available.filter((name) => !canonical.includes(name)).sort();
  return [...known, ...rest];
}

// ── Source vocabulary — server-authoritative ─────────────────────────────────
// PH5 wave-1 follow-up (B): `/hr/metrics/definitions`'s `source` dimension now
// carries `values: [{key, label}]` (services/data_gateway/app/application_source.py
// SOURCES, with human labels). Read it from there everywhere; this file no
// longer hand-mirrors the vocabulary.

export interface SourceOption {
  value: string;
  label: string;
}

/**
 * FALLBACK ONLY — used solely when `/hr/metrics/definitions` cannot be
 * reached at all (offline, the endpoint down). Deliberately tiny: it exists
 * so the source picker and filter degrade to SOMETHING usable rather than an
 * empty dropdown, not to stand in for the real vocabulary.
 */
const FALLBACK_SOURCE_OPTIONS: SourceOption[] = [
  { value: 'internal', label: 'Internal (HR-added)' },
  { value: 'unknown', label: 'Unknown / untracked' },
  { value: 'other', label: 'Other' },
];

/** The `source` dimension's option list from a definitions response, or the
 *  tiny fallback above when it hasn't loaded (or doesn't carry one). */
export function sourceOptionsFromDefinitions(
  defs: MetricDefinitionsResponse | undefined,
): SourceOption[] {
  const values = defs?.dimensions.find((d) => d.name === 'source')?.values;
  if (!values || values.length === 0) return FALLBACK_SOURCE_OPTIONS;
  return values.map((v) => ({ value: v.key, label: v.label }));
}

/** A source code's human label, from the definitions response when available,
 *  else the tiny fallback, else the raw code (never a blank cell). */
export function sourceLabel(code: string, defs?: MetricDefinitionsResponse): string {
  const fromDefs = defs?.dimensions
    .find((d) => d.name === 'source')
    ?.values?.find((v) => v.key === code)?.label;
  if (fromDefs) return fromDefs;
  return FALLBACK_SOURCE_OPTIONS.find((o) => o.value === code)?.label ?? code;
}

// ── Display-label overrides ───────────────────────────────────────────────────
// The registry's own `label` is what "How is this calculated?" shows; this is
// for the one place product copy is stricter than the metric label itself.

const LABEL_OVERRIDES: Record<string, string> = {
  // The console must never let this read as anything but human-recorded.
  hire_interviewer_score: 'Human interviewer scorecards',
};

export function metricLabel(name: string, definition?: MetricDefinition): string {
  return LABEL_OVERRIDES[name] ?? definition?.label ?? name;
}
