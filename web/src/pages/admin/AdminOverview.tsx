// AdminOverview — platform KPI page.
// Route: /admin/overview (inside AdminRoute + AppShell)
//
// Cross-tenant and aggregate by design: counts, rates, averages and service
// health only — no per-candidate detail is shown here.
//
// Layout: PageHeader → six KPI cards → two chart cards → system status cards.
// Data: all live react-query hooks (getOverview, getTrends, getScoreDistribution,
// getSystemHealth with a 60 s refetch), data-testid="overview-tiles".

import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, AlertCircle } from '@/design/components/icons';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { getOverview, getTrends, getScoreDistribution, getSystemHealth } from '@/api/admin';
import type { TrendItem, ScoreBucket } from '@/api/admin';
import { QueryErrorPanel } from '@/components/QueryState';
import { formatDuration, formatScore, formatTime } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import { Skeleton } from '@/components/ui/skeleton';
import { StatCard, StatusTag } from '@/design/components/primitives';
import { EmptyState, PageHeader, SectionCard, Page } from '@/design/components/page';
import { useAccentColor } from '@/lib/useAccentColor';
import { AXIS_STYLE, GRID_STROKE, TOOLTIP_STYLE } from '@/lib/chartTheme';

// ── System-health board labels / dot colors ────────────────────────────────────

const STATUS_DOT: Record<string, string> = {
  operational: 'bg-[var(--ui-ok)]',
  degraded: 'bg-[var(--ui-warn)]',
  down: 'bg-[var(--ui-danger)]',
};

const SERVICE_LABEL: Record<string, string> = {
  admin_ops: 'Admin & Ops',
  interview_core: 'Interview Core',
  data_gateway: 'Data Gateway',
  feedback_billing: 'Feedback & Billing',
  postgres: 'PostgreSQL',
  redis: 'Redis',
};

const STATUS_TONE: Record<string, 'forest' | 'amber' | 'ember'> = {
  operational: 'forest',
  degraded: 'amber',
  down: 'ember',
};

// ── Chart theme tokens ─────────────────────────────────────────────────────────

const CHART_GRID = GRID_STROKE;
const CHART_TICKS = { fill: AXIS_STYLE.stroke, fontSize: 12 } as const;

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtPct(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

function fmtDurationSecs(seconds: number | null): string {
  if (seconds === null) return '-';
  return formatDuration(Math.round(seconds));
}

// ── Loading / empty placeholders ───────────────────────────────────────────────

function TileSkeleton() {
  return (
    <div className="h-[116px] rounded-card border border-border bg-card p-5 shadow-card">
      <Skeleton className="h-3.5 w-28" />
      <Skeleton className="mt-4 h-8 w-24 rounded-[10px]" />
      <Skeleton className="mt-3 h-3 w-32" />
    </div>
  );
}

function ChartSkeleton({ height = 200 }: { height?: number }) {
  return <Skeleton className="w-full rounded-[14px]" style={{ height }} aria-hidden="true" />;
}

// ── Trend line chart ───────────────────────────────────────────────────────────

function TrendsChart({ items }: { items: TrendItem[] }) {
  const accent = useAccentColor();
  const data = items.map((item) => ({
    ...item,
    label: new Date(item.date).toLocaleDateString('en-IN', {
      day: 'numeric',
      month: 'short',
    }),
  }));

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={data} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
        <XAxis
          dataKey="label"
          tick={CHART_TICKS}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={CHART_TICKS}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
          width={28}
        />
        <Tooltip
          cursor={{ stroke: CHART_GRID }}
          contentStyle={TOOLTIP_STYLE}
          formatter={(value, name) => {
            if (name === 'interview_count') return [value ?? 0, 'Interviews'];
            return [`${Number(value ?? 0)} / 10`, 'Average score'];
          }}
        />
        <Line
          type="monotone"
          dataKey="interview_count"
          stroke={accent}
          strokeWidth={2.5}
          dot={false}
          activeDot={{ r: 4, fill: accent, stroke: 'var(--card)' }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

// ── Score distribution bar chart ───────────────────────────────────────────────

function DistributionChart({ buckets }: { buckets: ScoreBucket[] }) {
  const accent = useAccentColor();
  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={buckets} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} vertical={false} />
        <XAxis dataKey="label" tick={CHART_TICKS} tickLine={false} axisLine={false} />
        <YAxis
          tick={CHART_TICKS}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
          width={28}
        />
        <Tooltip
          cursor={{ fill: 'var(--ui-inset)' }}
          contentStyle={TOOLTIP_STYLE}
          formatter={(value) => [value ?? 0, 'Interviews']}
        />
        <Bar dataKey="count" fill={accent} fillOpacity={0.85} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── AdminOverview page ─────────────────────────────────────────────────────────

export default function AdminOverview() {
  const overviewQuery = useQuery({
    queryKey: ['admin', 'overview'],
    queryFn: getOverview,
    staleTime: 2 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });
  const {
    data: overview,
    isLoading: overviewLoading,
    isError: overviewError,
    error: overviewErr,
  } = overviewQuery;

  const trendsQuery = useQuery({
    queryKey: ['admin', 'trends'],
    queryFn: () => getTrends(),
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });
  const { data: trendsData, isLoading: trendsLoading, isError: trendsError } = trendsQuery;

  const distQuery = useQuery({
    queryKey: ['admin', 'score-distribution'],
    queryFn: getScoreDistribution,
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });
  const { data: distData, isLoading: distLoading, isError: distError } = distQuery;

  // System status — live peer-service + datastore health (refreshes each minute).
  const healthQuery = useQuery({
    queryKey: ['admin', 'system-health'],
    queryFn: getSystemHealth,
    refetchInterval: 60 * 1000,
    staleTime: 30 * 1000,
    retry: false,
    throwOnError: false,
  });
  const { data: health, isLoading: healthLoading } = healthQuery;

  const header = (
    <PageHeader
      eyebrow="Platform"
      title="Overview"
      description="Platform health, throughput and performance at a glance."
    />
  );

  // ── Full-page error state ─────────────────────────────────────────────────────

  if (overviewError && !overviewLoading) {
    return (
      <Page width="default">
        {header}
        <SectionCard title="Platform numbers">
          <QueryErrorPanel
            title="Could not load the platform numbers."
            error={overviewErr}
            onRetry={() => void overviewQuery.refetch()}
            retrying={overviewQuery.isFetching}
          />
        </SectionCard>
      </Page>
    );
  }

  // ── Page ──────────────────────────────────────────────────────────────────────

  return (
    <Page width="default">
      {header}

      {/* KPI cards — 6 live metrics mapped 1-to-1 from getOverview.
          data-testid wraps the outer grid so tests can query the container
          regardless of whether it's in skeleton or data state. */}
      <div data-testid="overview-tiles">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {overviewLoading ? (
            Array.from({ length: 6 }).map((_, i) => <TileSkeleton key={i} />)
          ) : (
            <>
              <StatCard
                label="Total candidates"
                value={(overview?.total_candidates ?? 0).toLocaleString('en-IN')}
              />
              <StatCard
                label="Total interviews"
                value={(overview?.total_interviews ?? 0).toLocaleString('en-IN')}
              />
              <StatCard
                label="Completed"
                value={(overview?.completed_interviews ?? 0).toLocaleString('en-IN')}
                delta={`${fmtPct(overview?.completion_rate ?? 0)} completion rate`}
              />
              <StatCard
                label="Average composite score"
                value={formatScore('interview', overview?.avg_composite_score ?? null)}
                delta="interview average"
              />
              <StatCard
                label="Average duration"
                value={fmtDurationSecs(overview?.avg_duration_seconds ?? null)}
              />
              <StatCard
                label="Interviews today"
                value={String(overview?.interviews_today ?? 0)}
                delta={`${overview?.interviews_last_7d ?? 0} last 7d · ${overview?.interviews_last_30d ?? 0} last 30d`}
              />
            </>
          )}
        </div>
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <SectionCard
          title="Daily interview volume (30 days)"
          description="Interviews per day over the last 30 days, across all companies."
        >
          {trendsLoading ? (
            <ChartSkeleton height={200} />
          ) : trendsError ? (
            <QueryErrorPanel
              title="Could not load the trend."
              error={trendsQuery.error}
              onRetry={() => void trendsQuery.refetch()}
              retrying={trendsQuery.isFetching}
              className="h-[200px]"
            />
          ) : trendsData && trendsData.items.length > 0 ? (
            <TrendsChart items={trendsData.items} />
          ) : (
            <EmptyState title="No trend data available." className="h-[200px]" />
          )}
        </SectionCard>

        <SectionCard
          title="Score distribution"
          description="How composite scores spread across score bands, with the average for each axis."
        >
          {distLoading ? (
            <ChartSkeleton height={180} />
          ) : distError ? (
            <QueryErrorPanel
              title="Could not load the distribution."
              error={distQuery.error}
              onRetry={() => void distQuery.refetch()}
              retrying={distQuery.isFetching}
              className="h-[180px]"
            />
          ) : distData ? (
            <>
              <DistributionChart buckets={distData.buckets} />

              {/* Per-axis averages — real values from getScoreDistribution */}
              <div className="mt-4 grid grid-cols-1 gap-x-6 gap-y-1.5 border-t border-border pt-3 text-[13px] text-muted-foreground sm:grid-cols-2">
                <span>
                  Communication:{' '}
                  <strong className="font-semibold text-foreground">
                    {formatScore('interview', distData.avg_communication)}
                  </strong>
                </span>
                <span>
                  Technical:{' '}
                  <strong className="font-semibold text-foreground">
                    {formatScore('interview', distData.avg_technical)}
                  </strong>
                </span>
                <span>
                  Problem solving:{' '}
                  <strong className="font-semibold text-foreground">
                    {formatScore('interview', distData.avg_problem_solving)}
                  </strong>
                </span>
                <span>
                  Confidence:{' '}
                  <strong className="font-semibold text-foreground">
                    {formatScore('interview', distData.avg_confidence)}
                  </strong>
                </span>
              </div>
            </>
          ) : (
            <EmptyState title="No distribution data available." className="h-[180px]" />
          )}
        </SectionCard>
      </div>

      {/* System status — backed by getSystemHealth (queryKey ['admin','system-health'],
          refetchInterval 60s). */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <SectionCard title="Microservices" description="Live health of each backend service.">
          {healthLoading ? (
            <ChartSkeleton height={160} />
          ) : health ? (
            <ul className="flex flex-col">
              {health.services
                .filter((s) => s.kind === 'service')
                .map((s) => (
                  <li
                    key={s.name}
                    className="flex items-center gap-3 border-b border-border py-3 last:border-0"
                  >
                    <span
                      className={cn('h-2.5 w-2.5 flex-none rounded-full', STATUS_DOT[s.status])}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1 truncate text-[14px] font-medium text-foreground">
                      {SERVICE_LABEL[s.name] ?? s.name}
                    </span>
                    {s.latency_ms != null && (
                      <span className="font-mono text-[12px] text-muted-foreground">
                        {s.latency_ms}ms
                      </span>
                    )}
                    <StatusTag
                      tone={STATUS_TONE[s.status] ?? 'neutral'}
                      dot={s.status === 'operational'}
                    >
                      {s.status}
                    </StatusTag>
                  </li>
                ))}
            </ul>
          ) : (
            <QueryErrorPanel
              title="Could not load the system status."
              error={healthQuery.error}
              onRetry={() => void healthQuery.refetch()}
              retrying={healthQuery.isFetching}
            />
          )}
        </SectionCard>

        <SectionCard
          title="System status"
          description="Datastores and the overall platform state. Refreshes every minute."
          action={
            health ? (
              <StatusTag tone={health.overall === 'operational' ? 'forest' : 'amber'}>
                {health.overall === 'operational' ? 'All systems operational' : 'Degraded'}
              </StatusTag>
            ) : undefined
          }
        >
          {healthLoading ? (
            <ChartSkeleton height={120} />
          ) : health ? (
            <>
              {/* Datastores */}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {health.services
                  .filter((s) => s.kind === 'datastore')
                  .map((s) => (
                    <div
                      key={s.name}
                      className="rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-4"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-[14px] font-semibold text-foreground">
                          {SERVICE_LABEL[s.name] ?? s.name}
                        </span>
                        {s.status === 'operational' ? (
                          <CheckCircle2
                            size={16}
                            className="text-[var(--ui-ok)]"
                            aria-hidden="true"
                          />
                        ) : (
                          <AlertCircle
                            size={16}
                            className="text-[var(--ui-warn)]"
                            aria-hidden="true"
                          />
                        )}
                      </div>
                      <StatusTag tone={STATUS_TONE[s.status] ?? 'neutral'} dot className="mt-3">
                        {s.status}
                      </StatusTag>
                    </div>
                  ))}
              </div>
              <p className="mt-4 text-[12.5px] text-muted-foreground">
                Checked {formatTime(health.checked_at)}
              </p>
            </>
          ) : (
            <QueryErrorPanel
              title="Could not load the system status."
              error={healthQuery.error}
              onRetry={() => void healthQuery.refetch()}
              retrying={healthQuery.isFetching}
            />
          )}
        </SectionCard>
      </div>
    </Page>
  );
}
