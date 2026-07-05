// AdminAnalytics — by-role bar chart, by-language breakdown, score distribution.
// Route: /admin/analytics (inside AdminRoute + AppShell)
//
// Sources merged:
//   A) Layout — design/screens/admin/AdminAnalytics.tsx (page header, GlassCard
//      surfaces, Reveal wrappers, recharts styling, language pie legend shape)
//   B) Behavior — current live AdminAnalytics.tsx (getByRole + getByLanguage +
//      getScoreDistribution; by-role count bar horizontal; avg-axis-scores-by-role
//      grouped bar; by-language pie + per-lang avg legend; score-distribution bar
//      + 4 axis averages; loading/empty/error states)
//
// NOTE: Design's mock StatCards (Interviews MTD / Completion rate / Avg duration /
//       Spend) and daily AreaChart are omitted — no live backing endpoint.

import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAccentColor } from '@/lib/useAccentColor';
import {
  BarChart3,
  Activity,
} from '@/design/components/icons';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  PieChart,
  Pie,
  Legend,
} from 'recharts';
import { getByRole, getByLanguage, getScoreDistribution } from '@/api/admin';
import { languageLabel } from '@/lib/formatters';
import { toast } from '@/lib/toast';
import { GlassCard } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';

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

// Deterministic categorical palette — Signal-Blue leads, cool secondaries follow.
const PALETTE = [
  '#0088ff', // Signal-Blue (primary series — fixed categorical anchor)
  '#8b5cf6', // violet
  '#10b981', // emerald
  '#f59e0b', // amber
  '#ef4444', // rose
  '#06b6d4', // cyan
  '#ec4899', // pink
];

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtScore(v: number | null): string {
  if (v === null) return '—';
  return v.toFixed(2);
}

// ── Chart skeleton ─────────────────────────────────────────────────────────────

function ChartSkeleton({ height = 220 }: { height?: number }) {
  return (
    <div
      className="w-full rounded-xl bg-white/[0.04] animate-pulse"
      style={{ height }}
      aria-hidden="true"
    />
  );
}

// ── Empty chart state ──────────────────────────────────────────────────────────

function ChartEmpty({ height = 220 }: { height?: number }) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-3 text-center"
      style={{ height }}
    >
      <BarChart3 className="h-8 w-8 text-[#888b91]/40" aria-hidden="true" />
      <p className="text-[13px] text-[#888b91]">No data available yet.</p>
    </div>
  );
}

// ── By-role count bar chart (horizontal) ──────────────────────────────────────

function ByRoleChart({
  data,
}: {
  data: { job_title: string; interview_count: number; avg_composite: number | null }[];
}) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 4, right: 24, bottom: 0, left: 8 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} horizontal={false} />
        <XAxis
          type="number"
          tick={CHART_TICKS}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
        />
        <YAxis
          type="category"
          dataKey="job_title"
          width={130}
          tick={CHART_TICKS}
          tickLine={false}
          axisLine={false}
        />
        <Tooltip
          cursor={{ fill: 'rgba(255,255,255,0.04)' }}
          contentStyle={TOOLTIP_STYLE}
          formatter={(value, name) => {
            if (name === 'interview_count') return [value ?? 0, 'Interviews'];
            return [`${Number(value ?? 0)} / 10`, 'Avg Score'];
          }}
        />
        <Bar dataKey="interview_count" radius={[0, 4, 4, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill={PALETTE[i % PALETTE.length]} fillOpacity={0.85} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── Avg axis scores by role — grouped bar ─────────────────────────────────────

function ByRoleScoreChart({
  data,
}: {
  data: {
    job_title: string;
    avg_composite: number | null;
    avg_communication: number | null;
    avg_technical: number | null;
    avg_problem_solving: number | null;
    avg_confidence: number | null;
  }[];
}) {
  const chartData = data.map((d) => ({
    name: d.job_title.length > 18 ? `${d.job_title.slice(0, 16)}…` : d.job_title,
    Communication: d.avg_communication ?? 0,
    Technical: d.avg_technical ?? 0,
    'Problem Solving': d.avg_problem_solving ?? 0,
    Confidence: d.avg_confidence ?? 0,
  }));

  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={chartData} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} vertical={false} />
        <XAxis
          dataKey="name"
          tick={CHART_TICKS}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          domain={[0, 10]}
          tick={CHART_TICKS}
          tickLine={false}
          axisLine={false}
          width={28}
        />
        <Tooltip
          cursor={{ fill: 'rgba(255,255,255,0.04)' }}
          contentStyle={TOOLTIP_STYLE}
          formatter={(value, name) => [`${Number(value ?? 0).toFixed(2)} / 10`, name as string]}
        />
        <Legend
          wrapperStyle={{ fontSize: 11, color: '#9a9aa0' }}
        />
        <Bar dataKey="Communication" fill={PALETTE[0]} fillOpacity={0.8} radius={[2, 2, 0, 0]} />
        <Bar dataKey="Technical" fill={PALETTE[1]} fillOpacity={0.8} radius={[2, 2, 0, 0]} />
        <Bar dataKey="Problem Solving" fill={PALETTE[2]} fillOpacity={0.8} radius={[2, 2, 0, 0]} />
        <Bar dataKey="Confidence" fill={PALETTE[3]} fillOpacity={0.8} radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── By-language pie chart + per-lang avg legend ────────────────────────────────

function ByLanguagePie({
  data,
}: {
  data: { language: string; interview_count: number; avg_composite: number | null }[];
}) {
  const pieData = data.map((d) => ({
    name: languageLabel(d.language),
    value: d.interview_count,
    avg: d.avg_composite,
    _raw: d.language,
  }));

  return (
    <div className="flex flex-col gap-4">
      <div className="h-[180px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={pieData}
              cx="50%"
              cy="50%"
              innerRadius={48}
              outerRadius={70}
              paddingAngle={2}
              dataKey="value"
              stroke="none"
              label={({ name, percent }: { name?: string; percent?: number }) =>
                `${name ?? ''} ${Math.round((percent ?? 0) * 100)}%`
              }
              labelLine={false}
            >
              {pieData.map((_, i) => (
                <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              formatter={(value, _name, props) => [
                `${Number(value ?? 0)} interviews · avg ${fmtScore(
                  (props.payload as { avg?: number | null } | undefined)?.avg ?? null,
                )} / 10`,
              ]}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>

      {/* Per-language avg legend */}
      <div className="mt-2 flex flex-col gap-2">
        {data.map((d, i) => (
          <div key={d.language} className="flex items-center gap-2 text-[13px]">
            <span
              className="h-2.5 w-2.5 flex-none rounded-[3px]"
              style={{ background: PALETTE[i % PALETTE.length] }}
              aria-hidden="true"
            />
            <span className="font-medium text-white">{languageLabel(d.language)}</span>
            <span className="ml-auto text-[#70757c] tabular-nums">
              {d.interview_count} interviews
            </span>
            <span className="text-[#888b91] tabular-nums">
              avg {fmtScore(d.avg_composite)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Score distribution + 4-axis averages ──────────────────────────────────────

function DistributionSection({
  buckets,
  avgCommunication,
  avgTechnical,
  avgProblemSolving,
  avgConfidence,
}: {
  buckets: { label: string; count: number }[];
  avgCommunication: number | null;
  avgTechnical: number | null;
  avgProblemSolving: number | null;
  avgConfidence: number | null;
}) {
  const accent = useAccentColor();
  return (
    <div className="space-y-4">
      <div className="h-[220px] w-full">
        <ResponsiveContainer width="100%" height="100%">
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
            <Bar dataKey="count" fill={accent} fillOpacity={0.85} radius={[6, 6, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Per-axis averages */}
      <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 border-t border-white/[0.06] pt-3 text-[12px] text-[#888b91]">
        <span>
          Communication:{' '}
          <strong className="text-white font-semibold">{fmtScore(avgCommunication)}</strong>
        </span>
        <span>
          Technical:{' '}
          <strong className="text-white font-semibold">{fmtScore(avgTechnical)}</strong>
        </span>
        <span>
          Problem Solving:{' '}
          <strong className="text-white font-semibold">{fmtScore(avgProblemSolving)}</strong>
        </span>
        <span>
          Confidence:{' '}
          <strong className="text-white font-semibold">{fmtScore(avgConfidence)}</strong>
        </span>
      </div>
    </div>
  );
}

// ── AdminAnalytics page ────────────────────────────────────────────────────────

export default function AdminAnalytics() {
  const {
    data: roleData,
    isLoading: roleLoading,
    isError: roleError,
    error: roleErr,
  } = useQuery({
    queryKey: ['admin', 'by-role'],
    queryFn: getByRole,
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  const { data: langData, isLoading: langLoading } = useQuery({
    queryKey: ['admin', 'by-language'],
    queryFn: getByLanguage,
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

  useEffect(() => {
    if (roleError) {
      toast.error(
        roleErr instanceof Error ? roleErr.message : 'Failed to load analytics data.',
      );
    }
  }, [roleError, roleErr]);

  // ── Page ──────────────────────────────────────────────────────────────────────

  return (
    <div className="mx-auto max-w-[1280px] px-0 py-0 space-y-8">

      {/* Page heading — design layout */}
      <div>
        <div className="flex items-center gap-2 text-[13px] text-[#888b91]">
          <Activity size={15} className="text-[#60a5fa]" aria-hidden="true" />
          Usage, throughput and spend across the platform
        </div>
        <h1 className="mt-1 text-[28px] font-semibold tracking-[-1px] text-white">Analytics</h1>
        <p className="mt-1 text-[14px] text-[#888b91]">
          Aggregated performance data broken down by role, language, and score distribution.
        </p>
      </div>

      {/* Row 1: by-role count + by-role axis-score breakdown */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {/* Interview count by role */}
        <Reveal dir="left">
          <GlassCard className="p-5">
            <h3 className="mb-4 text-[16px] font-semibold text-white">Interviews by Role</h3>
            {roleLoading ? (
              <ChartSkeleton height={240} />
            ) : !roleData || roleData.length === 0 ? (
              <ChartEmpty height={240} />
            ) : (
              <ByRoleChart data={roleData} />
            )}
          </GlassCard>
        </Reveal>

        {/* Avg axis scores by role — unique live feature, preserved */}
        <Reveal dir="right">
          <GlassCard className="p-5">
            <h3 className="mb-4 text-[16px] font-semibold text-white">
              Avg Axis Scores by Role
            </h3>
            {roleLoading ? (
              <ChartSkeleton height={240} />
            ) : !roleData || roleData.length === 0 ? (
              <ChartEmpty height={240} />
            ) : (
              <ByRoleScoreChart data={roleData} />
            )}
          </GlassCard>
        </Reveal>
      </div>

      {/* Row 2: by-language pie + score distribution — design's bottom section */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* By language — design gives this the right-panel slot */}
        <Reveal dir="right">
          <GlassCard className="h-full p-5">
            <h3 className="mb-3 text-[16px] font-semibold text-white">Language Mix</h3>
            {langLoading ? (
              <ChartSkeleton height={180} />
            ) : !langData || langData.length === 0 ? (
              <ChartEmpty height={180} />
            ) : (
              <ByLanguagePie data={langData} />
            )}
          </GlassCard>
        </Reveal>

        {/* Score distribution + axis averages — design's 2-col span section */}
        <Reveal dir="left" className="lg:col-span-2">
          <GlassCard className="p-5">
            <h3 className="mb-4 text-[16px] font-semibold text-white">Score Distribution</h3>
            {distLoading ? (
              <ChartSkeleton height={220} />
            ) : !distData ? (
              <ChartEmpty height={220} />
            ) : (
              <DistributionSection
                buckets={distData.buckets}
                avgCommunication={distData.avg_communication}
                avgTechnical={distData.avg_technical}
                avgProblemSolving={distData.avg_problem_solving}
                avgConfidence={distData.avg_confidence}
              />
            )}
          </GlassCard>
        </Reveal>
      </div>
    </div>
  );
}
