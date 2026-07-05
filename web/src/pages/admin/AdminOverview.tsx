// AdminOverview — admin KPI tile page.
// Route: /admin/overview (inside AdminRoute + AppShell)
//
// Layout: reproduced from design screen AdminOverview.tsx.
// Behavior: all live react-query hooks, charts, system health board preserved.
//
// Sources merged:
//   A) Layout — design/screens/admin/AdminOverview.tsx (page header style,
//      Stagger/Reveal, GlassCard surfaces, stat tile shape, System Status section)
//   B) Behavior — current live AdminOverview.tsx (getOverview 6 KPI tiles,
//      getTrends LineChart, getScoreDistribution BarChart + axis averages,
//      getSystemHealth refetchInterval 60s, data-testid="overview-tiles",
//      error/skeleton states, sparkline)

import { useEffect, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import {
  Users,
  ClipboardList,
  CheckCircle2,
  TrendingUp,
  Clock,
  Calendar,
  AlertCircle,
  Activity,
  Server,
} from '@/design/components/icons';
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
import { toast } from '@/lib/toast';
import { formatDuration } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Stagger, StaggerItem, Reveal } from '@/design/components/Reveal';

// ── System-health board labels / dot colors ────────────────────────────────────

const STATUS_DOT: Record<string, string> = {
  operational: 'bg-[#27c93f]',
  degraded: 'bg-[#ffb764]',
  down: 'bg-[#e6714f]',
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

// ── Dark chart theme tokens ────────────────────────────────────────────────────

const CHART_GRID = 'rgba(255,255,255,0.08)';
const CHART_TICKS = { fill: '#9a9aa0', fontSize: 11 } as const;
const TOOLTIP_STYLE = {
  background: '#1c1c1e',
  border: '1px solid rgba(255,255,255,0.1)',
  borderRadius: 12,
  fontSize: 12,
  color: '#f5f5f7',
} as const;
const SERIES_BLUE = '#0088ff';

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtScore(v: number | null): string {
  if (v === null) return '—';
  return v.toFixed(2);
}

function fmtPct(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

function fmtDurationSecs(seconds: number | null): string {
  if (seconds === null) return '—';
  return formatDuration(Math.round(seconds));
}

// ── Inline sparkline (SVG) ─────────────────────────────────────────────────────

function Sparkline({ points, color = SERIES_BLUE }: { points: number[]; color?: string }) {
  if (points.length < 2) return null;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const W = 100;
  const H = 32;
  const d = points
    .map((p, i) => `${(i / (points.length - 1)) * W},${H - ((p - min) / span) * H}`)
    .join(' ');
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={H}
      preserveAspectRatio="none"
      aria-hidden="true"
      className="mt-3"
    >
      <polyline
        points={d}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.7}
      />
    </svg>
  );
}

// ── KPI stat tile ──────────────────────────────────────────────────────────────

interface StatTileProps {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  sub?: string;
  loading?: boolean;
  spark?: number[];
  feature?: boolean;
}

function StatTile({ icon, label, value, sub, loading, spark, feature }: StatTileProps) {
  return (
    <GlassCard feature={feature} hover className="p-5 h-full">
      <div className="flex items-center justify-between">
        <span className="text-[12.5px] text-[#888b91]">{label}</span>
        <span className="text-[#9a9aa0]" aria-hidden="true">
          {icon}
        </span>
      </div>

      {loading ? (
        <div
          className="mt-3 h-8 w-28 rounded-lg bg-white/[0.06] animate-pulse"
          aria-hidden="true"
        />
      ) : (
        <div className="mt-2 text-[28px] font-semibold tracking-[-1px] text-white leading-none tabular-nums">
          {value}
        </div>
      )}

      {sub && !loading && (
        <p className="mt-1.5 text-[12px] text-[#888b91]">{sub}</p>
      )}

      {spark && !loading && (
        <Sparkline points={spark} color={feature ? '#60a5fa' : SERIES_BLUE} />
      )}
    </GlassCard>
  );
}

// ── Skeleton tile for loading state ───────────────────────────────────────────

function TileSkeleton() {
  return (
    <GlassCard className="p-5 h-full">
      <div className="flex items-center gap-2.5">
        <div className="h-4 w-4 rounded bg-white/[0.06] animate-pulse" />
        <div className="h-3 w-28 rounded bg-white/[0.06] animate-pulse" />
      </div>
      <div className="mt-3 h-8 w-24 rounded-lg bg-white/[0.06] animate-pulse" />
      <div className="mt-2 h-2.5 w-32 rounded bg-white/[0.06] animate-pulse" />
    </GlassCard>
  );
}

// ── Chart skeleton ─────────────────────────────────────────────────────────────

function ChartSkeleton({ height = 200 }: { height?: number }) {
  return (
    <div
      className="w-full rounded-xl bg-white/[0.04] animate-pulse"
      style={{ height }}
      aria-hidden="true"
    />
  );
}

// ── Trend line chart ───────────────────────────────────────────────────────────

function TrendsChart({ items }: { items: TrendItem[] }) {
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
            return [`${Number(value ?? 0)} / 10`, 'Avg Score'];
          }}
        />
        <Line
          type="monotone"
          dataKey="interview_count"
          stroke={SERIES_BLUE}
          strokeWidth={2.5}
          dot={false}
          activeDot={{ r: 4, fill: SERIES_BLUE, stroke: '#1c1c1e' }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

// ── Score distribution bar chart ───────────────────────────────────────────────

function DistributionChart({ buckets }: { buckets: ScoreBucket[] }) {
  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={buckets} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} vertical={false} />
        <XAxis
          dataKey="label"
          tick={CHART_TICKS}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          tick={CHART_TICKS}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
          width={28}
        />
        <Tooltip
          cursor={{ fill: 'rgba(0,136,255,0.06)' }}
          contentStyle={TOOLTIP_STYLE}
          formatter={(value) => [value ?? 0, 'Interviews']}
        />
        <Bar
          dataKey="count"
          fill={SERIES_BLUE}
          fillOpacity={0.85}
          radius={[4, 4, 0, 0]}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── AdminOverview page ─────────────────────────────────────────────────────────

export default function AdminOverview() {
  const {
    data: overview,
    isLoading: overviewLoading,
    isError: overviewError,
    error: overviewErr,
  } = useQuery({
    queryKey: ['admin', 'overview'],
    queryFn: getOverview,
    staleTime: 2 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  const { data: trendsData, isLoading: trendsLoading } = useQuery({
    queryKey: ['admin', 'trends'],
    queryFn: () => getTrends(),
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  const { data: distData, isLoading: distLoading } = useQuery({
    queryKey: ['admin', 'score-distribution'],
    queryFn: getScoreDistribution,
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  // System status — live peer-service + datastore health (refreshes each minute).
  const { data: health, isLoading: healthLoading } = useQuery({
    queryKey: ['admin', 'system-health'],
    queryFn: getSystemHealth,
    refetchInterval: 60 * 1000,
    staleTime: 30 * 1000,
    retry: false,
    throwOnError: false,
  });

  useEffect(() => {
    if (overviewError) {
      toast.error(
        overviewErr instanceof Error ? overviewErr.message : 'Failed to load overview data.',
      );
    }
  }, [overviewError, overviewErr]);

  // ── Full-page error state ─────────────────────────────────────────────────────

  if (overviewError && !overviewLoading) {
    return (
      <div
        role="alert"
        className="flex flex-col items-center justify-center py-24 gap-4 text-center"
      >
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-[rgba(230,113,79,0.14)]">
          <AlertCircle className="h-6 w-6 text-[#e6714f]" aria-hidden="true" />
        </div>
        <p className="text-[15px] font-semibold text-white">Failed to load overview</p>
        <p className="text-[13px] text-[#888b91]">
          {overviewErr instanceof Error ? overviewErr.message : 'Unknown error'}
        </p>
      </div>
    );
  }

  // ── Sparkline seeds — derive from trends data when available ──────────────────

  const trendItems = trendsData?.items ?? [];
  const last7 = trendItems.slice(-7);
  const interviewSpark = last7.length >= 2 ? last7.map((t) => t.interview_count) : undefined;

  // ── Page ──────────────────────────────────────────────────────────────────────

  return (
    <div className="mx-auto max-w-[1280px] px-0 py-0 space-y-8">

      {/* Page heading — design layout */}
      <motion.div
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      >
        <div className="flex items-center gap-2 text-[13px] text-[#888b91]">
          <Activity size={15} className="text-[#60a5fa]" aria-hidden="true" />
          Live · Intants AI Platform
        </div>
        <h1 className="mt-1 text-[28px] font-semibold tracking-[-1px] text-white">
          Admin Overview
        </h1>
        <p className="mt-1 text-[14px] text-[#888b91]">
          Platform health, throughput and performance at a glance.
        </p>
      </motion.div>

      {/* KPI tiles — 6 live metrics mapped 1-to-1 from getOverview.
          data-testid wraps the outer grid so tests can query the container
          regardless of whether it's in skeleton or data state. */}
      <div data-testid="overview-tiles">
        {overviewLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 auto-rows-fr">
            {Array.from({ length: 6 }).map((_, i) => (
              <TileSkeleton key={i} />
            ))}
          </div>
        ) : (
          <Stagger className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 auto-rows-fr">
            {/* Tile 1 — Total Candidates */}
            <StaggerItem className="h-full">
              <StatTile
                icon={<Users size={16} />}
                label="Total Candidates"
                value={(overview?.total_candidates ?? 0).toLocaleString('en-IN')}
                feature
              />
            </StaggerItem>

            {/* Tile 2 — Total Interviews */}
            <StaggerItem className="h-full">
              <StatTile
                icon={<ClipboardList size={16} />}
                label="Total Interviews"
                value={(overview?.total_interviews ?? 0).toLocaleString('en-IN')}
                spark={interviewSpark}
              />
            </StaggerItem>

            {/* Tile 3 — Completed + completion rate */}
            <StaggerItem className="h-full">
              <StatTile
                icon={<CheckCircle2 size={16} />}
                label="Completed"
                value={(overview?.completed_interviews ?? 0).toLocaleString('en-IN')}
                sub={`${fmtPct(overview?.completion_rate ?? 0)} completion rate`}
              />
            </StaggerItem>

            {/* Tile 4 — Avg Composite Score */}
            <StaggerItem className="h-full">
              <StatTile
                icon={<TrendingUp size={16} />}
                label="Avg Composite Score"
                value={fmtScore(overview?.avg_composite_score ?? null)}
                sub="out of 10"
              />
            </StaggerItem>

            {/* Tile 5 — Avg Duration */}
            <StaggerItem className="h-full">
              <StatTile
                icon={<Clock size={16} />}
                label="Avg Duration"
                value={fmtDurationSecs(overview?.avg_duration_seconds ?? null)}
              />
            </StaggerItem>

            {/* Tile 6 — Activity (today / 7d / 30d) */}
            <StaggerItem className="h-full">
              <StatTile
                icon={<Calendar size={16} />}
                label="Activity"
                value={
                  <span>
                    {overview?.interviews_today ?? 0}
                    <span className="text-[16px] text-[#888b91] font-normal"> today</span>
                  </span>
                }
                sub={`${overview?.interviews_last_7d ?? 0} last 7d · ${overview?.interviews_last_30d ?? 0} last 30d`}
              />
            </StaggerItem>
          </Stagger>
        )}
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {/* Daily interview trends */}
        <Reveal dir="left" className="h-full">
          <GlassCard className="p-5 h-full">
            <h3 className="mb-4 text-[16px] font-semibold text-white">
              Daily Interview Volume (30 days)
            </h3>
            {trendsLoading ? (
              <ChartSkeleton height={200} />
            ) : trendsData && trendsData.items.length > 0 ? (
              <TrendsChart items={trendsData.items} />
            ) : (
              <div className="flex items-center justify-center h-[200px] text-[13px] text-[#888b91]">
                No trend data available.
              </div>
            )}
          </GlassCard>
        </Reveal>

        {/* Score distribution */}
        <Reveal dir="right" className="h-full">
          <GlassCard className="p-5 h-full">
            <h3 className="mb-4 text-[16px] font-semibold text-white">
              Score Distribution
            </h3>
            {distLoading ? (
              <ChartSkeleton height={180} />
            ) : distData ? (
              <>
                <DistributionChart buckets={distData.buckets} />

                {/* Per-axis averages — real values from getScoreDistribution */}
                <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-1.5 border-t border-white/[0.06] pt-3 text-[12px] text-[#888b91]">
                  <span>
                    Communication:{' '}
                    <strong className="text-white font-semibold">
                      {fmtScore(distData.avg_communication)}
                    </strong>
                  </span>
                  <span>
                    Technical:{' '}
                    <strong className="text-white font-semibold">
                      {fmtScore(distData.avg_technical)}
                    </strong>
                  </span>
                  <span>
                    Problem Solving:{' '}
                    <strong className="text-white font-semibold">
                      {fmtScore(distData.avg_problem_solving)}
                    </strong>
                  </span>
                  <span>
                    Confidence:{' '}
                    <strong className="text-white font-semibold">
                      {fmtScore(distData.avg_confidence)}
                    </strong>
                  </span>
                </div>
              </>
            ) : (
              <div className="flex items-center justify-center h-[180px] text-[13px] text-[#888b91]">
                No distribution data available.
              </div>
            )}
          </GlassCard>
        </Reveal>
      </div>

      {/* System Status board — design layout with two-column panels (Microservices + AI Providers).
          Backed by getSystemHealth (queryKey ['admin','system-health'], refetchInterval 60s). */}
      <Reveal>
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          {/* Microservices panel */}
          <GlassCard className="p-5 h-full">
            <h3 className="mb-4 flex items-center gap-2 text-[16px] font-semibold text-white">
              <Server size={17} className="text-[#60a5fa]" aria-hidden="true" />
              Microservices
            </h3>

            {healthLoading ? (
              <ChartSkeleton height={160} />
            ) : health ? (
              <div className="flex flex-col">
                {health.services
                  .filter((s) => s.kind === 'service')
                  .map((s) => (
                    <div
                      key={s.name}
                      className="flex items-center gap-3 border-b border-white/[0.05] py-3 last:border-0"
                    >
                      <span
                        className={cn(
                          'h-2.5 w-2.5 flex-none rounded-full',
                          STATUS_DOT[s.status],
                        )}
                        aria-hidden="true"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="truncate text-[13.5px] font-medium text-white">
                          {SERVICE_LABEL[s.name] ?? s.name}
                        </div>
                      </div>
                      {s.latency_ms != null && (
                        <span className="font-mono text-[12px] text-[#888b91]">
                          {s.latency_ms}ms
                        </span>
                      )}
                      <StatusTag tone={STATUS_TONE[s.status] ?? 'neutral'} dot={s.status === 'operational'}>
                        {s.status}
                      </StatusTag>
                    </div>
                  ))}
              </div>
            ) : (
              <div className="text-[13px] text-[#888b91]">Status unavailable.</div>
            )}
          </GlassCard>

          {/* Datastores + overall status panel */}
          <GlassCard className="p-5 h-full">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h3 className="flex items-center gap-2 text-[16px] font-semibold text-white">
                <Activity size={16} className="text-[#60a5fa]" aria-hidden="true" />
                System Status
              </h3>
              {health && (
                <span
                  className={cn(
                    'rounded-full px-2.5 py-1 text-[11.5px] font-semibold',
                    health.overall === 'operational'
                      ? 'bg-[rgba(39,201,63,0.16)] text-[#27c93f]'
                      : 'bg-[rgba(255,183,100,0.16)] text-[#ffb764]',
                  )}
                >
                  {health.overall === 'operational' ? 'All systems operational' : 'Degraded'}
                </span>
              )}
            </div>

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
                        className="rounded-[12px] border border-white/[0.07] bg-white/[0.02] p-4"
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-[14px] font-semibold text-white">
                            {SERVICE_LABEL[s.name] ?? s.name}
                          </span>
                          {s.status === 'operational' ? (
                            <CheckCircle2 size={16} className="text-[#27c93f]" aria-hidden="true" />
                          ) : (
                            <AlertCircle size={16} className="text-[#ffb764]" aria-hidden="true" />
                          )}
                        </div>
                        <StatusTag
                          tone={STATUS_TONE[s.status] ?? 'neutral'}
                          dot
                          className="mt-3"
                        >
                          {s.status}
                        </StatusTag>
                      </div>
                    ))}
                </div>
                <p className="mt-4 text-[11px] text-[#5a5f66]">
                  Checked {new Date(health.checked_at).toLocaleTimeString()}
                </p>
              </>
            ) : (
              <div className="text-[13px] text-[#888b91]">Status unavailable.</div>
            )}
          </GlassCard>
        </div>
      </Reveal>
    </div>
  );
}
