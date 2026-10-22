// AdminAnalytics — by-role bar chart, by-language breakdown, score distribution.
// Route: /admin/analytics (inside AdminRoute + AppShell)
//
// Cross-tenant and aggregate by design: counts and averages by role, language
// and score band — no per-candidate detail.
//
// Layout: PageHeader → section cards. Data: getByRole + getByLanguage +
// getScoreDistribution; by-role count bar (horizontal), avg-axis-scores-by-role
// grouped bar, by-language pie + per-language average legend, score-distribution
// bar + 4 axis averages. Each card resolves to a skeleton, data, an empty state
// or an error state.
//
// NOTE: There is no live endpoint for interviews-per-month, spend or a daily
//       series, so none of those are shown.

import { useQuery } from '@tanstack/react-query';
import { useAccentColor } from '@/lib/useAccentColor';
import { BarChart3, AlertTriangle } from '@/design/components/icons';
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
import { languageLabel, formatScore } from '@/lib/formatters';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState, PageHeader, SectionCard, Page } from '@/design/components/page';
import { AXIS_STYLE, GRID_STROKE, PALETTE, TOOLTIP_STYLE } from '@/lib/chartTheme';

// ── Chart theme tokens ────────────────────────────────────────────────────

const CHART_GRID = GRID_STROKE;
const CHART_TICKS = { fill: AXIS_STYLE.stroke, fontSize: 12 } as const;

// ── Helpers ────────────────────────────────────────────────────────────────────

// ── Chart skeleton ─────────────────────────────────────────────────────────────

function ChartSkeleton({ height = 220 }: { height?: number }) {
  return <Skeleton className="w-full rounded-[14px]" style={{ height }} aria-hidden="true" />;
}

// ── Empty / error chart states ─────────────────────────────────────────────────

function ChartEmpty() {
  return (
    <EmptyState
      icon={BarChart3}
      title="No data available yet."
      description="This fills in as interviews are completed."
    />
  );
}

function ChartError() {
  return (
    <EmptyState
      icon={AlertTriangle}
      title="Could not load this chart."
      description="Reload the page to try again."
    />
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
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 0, left: 8 }}>
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
          cursor={{ fill: 'var(--ui-inset)' }}
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
        <XAxis dataKey="name" tick={CHART_TICKS} tickLine={false} axisLine={false} />
        <YAxis domain={[0, 10]} tick={CHART_TICKS} tickLine={false} axisLine={false} width={28} />
        <Tooltip
          cursor={{ fill: 'var(--ui-inset)' }}
          contentStyle={TOOLTIP_STYLE}
          formatter={(value, name) => [`${Number(value ?? 0).toFixed(2)} / 10`, name as string]}
        />
        <Legend wrapperStyle={{ fontSize: 12, color: AXIS_STYLE.stroke }} />
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
                `${Number(value ?? 0)} interviews · avg ${formatScore(
                  'interview',
                  (props.payload as { avg?: number | null } | undefined)?.avg ?? null,
                )}`,
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
            <span className="font-medium text-foreground">{languageLabel(d.language)}</span>
            <span className="ml-auto text-muted-foreground tabular-nums">
              {d.interview_count} interviews
            </span>
            <span className="text-muted-foreground tabular-nums">
              avg {formatScore('interview', d.avg_composite)}
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
            <Bar dataKey="count" fill={accent} fillOpacity={0.85} radius={[6, 6, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Per-axis averages */}
      <div className="grid grid-cols-1 gap-x-6 gap-y-1.5 border-t border-border pt-3 text-[13px] text-muted-foreground sm:grid-cols-2">
        <span>
          Communication:{' '}
          <strong className="font-semibold text-foreground">{formatScore('interview', avgCommunication)}</strong>
        </span>
        <span>
          Technical:{' '}
          <strong className="font-semibold text-foreground">{formatScore('interview', avgTechnical)}</strong>
        </span>
        <span>
          Problem Solving:{' '}
          <strong className="font-semibold text-foreground">{formatScore('interview', avgProblemSolving)}</strong>
        </span>
        <span>
          Confidence:{' '}
          <strong className="font-semibold text-foreground">{formatScore('interview', avgConfidence)}</strong>
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
  } = useQuery({
    queryKey: ['admin', 'by-role'],
    queryFn: getByRole,
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  const {
    data: langData,
    isLoading: langLoading,
    isError: langError,
  } = useQuery({
    queryKey: ['admin', 'by-language'],
    queryFn: getByLanguage,
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  const {
    data: distData,
    isLoading: distLoading,
    isError: distError,
  } = useQuery({
    queryKey: ['admin', 'score-distribution'],
    queryFn: getScoreDistribution,
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  // ── Page ──────────────────────────────────────────────────────────────────────

  return (
    <Page width="default">
      <PageHeader
        eyebrow="Platform"
        title="Analytics"
        description="Aggregated performance data broken down by role, language, and score distribution."
      />

      {/* Row 1: by-role count + by-role axis-score breakdown */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <SectionCard
          title="Interviews by Role"
          description="How many interviews each role has had, across all companies."
        >
          {roleLoading ? (
            <ChartSkeleton height={240} />
          ) : roleError ? (
            <ChartError />
          ) : !roleData || roleData.length === 0 ? (
            <ChartEmpty />
          ) : (
            <ByRoleChart data={roleData} />
          )}
        </SectionCard>

        <SectionCard
          title="Avg Axis Scores by Role"
          description="Average communication, technical, problem-solving and confidence score for each role."
        >
          {roleLoading ? (
            <ChartSkeleton height={240} />
          ) : roleError ? (
            <ChartError />
          ) : !roleData || roleData.length === 0 ? (
            <ChartEmpty />
          ) : (
            <ByRoleScoreChart data={roleData} />
          )}
        </SectionCard>
      </div>

      {/* Row 2: by-language pie + score distribution */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <SectionCard title="Language Mix" description="Share of interviews by interview language.">
          {langLoading ? (
            <ChartSkeleton height={180} />
          ) : langError ? (
            <ChartError />
          ) : !langData || langData.length === 0 ? (
            <ChartEmpty />
          ) : (
            <ByLanguagePie data={langData} />
          )}
        </SectionCard>

        <SectionCard
          title="Score Distribution"
          description="How composite scores spread across score bands, with the average for each axis."
          className="lg:col-span-2"
        >
          {distLoading ? (
            <ChartSkeleton height={220} />
          ) : distError ? (
            <ChartError />
          ) : !distData ? (
            <ChartEmpty />
          ) : (
            <DistributionSection
              buckets={distData.buckets}
              avgCommunication={distData.avg_communication}
              avgTechnical={distData.avg_technical}
              avgProblemSolving={distData.avg_problem_solving}
              avgConfidence={distData.avg_confidence}
            />
          )}
        </SectionCard>
      </div>
    </Page>
  );
}
