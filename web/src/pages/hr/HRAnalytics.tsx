// HRAnalytics — company-scoped hiring analytics.
// Layout: faithfully reproduces anterview-pages/src/screens/hr/HRAnalytics.tsx
//   (hiring-funnel horizontal bars, language-mix donut, score-distribution bar
//    chart, interviews & avg-score line chart).
// Behavior: live getHrAnalytics query (funnel + averages); charts use real data
//   where available and gracefully empty-state where there is no backing API.
//   BOTH exports preserved:
//     default HRAnalytics — embeddable panel used by HRPipeline.
//     HRAnalyticsPage     — standalone /hr/analytics route.

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
import { GlassCard } from '@/design/components/primitives';

// ── Tooltip style (design spec) ───────────────────────────────────────────────
const TOOLTIP_STYLE = {
  background: '#0f0f10',
  border: '1px solid rgba(255,255,255,0.1)',
  borderRadius: 12,
  color: '#fff',
  fontSize: 12,
} as const;

// ── Helpers ───────────────────────────────────────────────────────────────────

function scoreColor(v: number): string {
  if (v >= 85) return '#27c93f';
  if (v >= 70) return '#0088ff';
  if (v >= 55) return '#ffb764';
  return '#e6714f';
}

function rate(part: number, whole: number): string {
  if (!whole) return '—';
  return `${Math.round((part / whole) * 100)}%`;
}

// ── Static chart data (language-mix + score-dist + trend).
//    These have no backing API yet; they render as empty states until
//    the analytics endpoint expands. When the API ships, replace these
//    with derived data from getHrAnalytics. ────────────────────────────────────
const LANG_MIX_STATIC: { label: string; value: number; color: string }[] = [];

const SCORE_DIST_STATIC: { label: string; value: number }[] = [];

const HR_TREND_STATIC: { day: string; interviews: number; avg: number }[] = [];

// ── FunnelBars — horizontal bar chart using real funnel data ─────────────────
function FunnelBars({ f }: { f: HrAnalytics['funnel'] }) {
  const max = f.total_applicants || 1;
  const rows = [
    { label: 'Applied',     value: f.total_applicants },
    { label: 'Shortlisted', value: f.shortlisted },
    { label: 'Exam passed', value: f.exam_passed },
    { label: 'Interviewed', value: f.interview_completed },
    { label: 'Hired',       value: f.hired },
  ];

  if (!rows.some((r) => r.value > 0)) {
    return (
      <div className="flex h-[140px] items-center justify-center">
        <p className="text-[13px] text-[#888b91]">No pipeline data yet.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2.5">
      {rows.map((row) => {
        const pct = Math.round((row.value / max) * 100);
        return (
          <div key={row.label} className="flex items-center gap-3.5">
            <div className="w-[84px] text-[13px] text-[#b8babf]">{row.label}</div>
            <div className="h-7 flex-1 overflow-hidden rounded-[8px] bg-white/[0.05]">
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
                  <div key={i} className="h-7 animate-pulse rounded-[8px] bg-white/[0.05]" />
                ))}
              </div>
            ) : f ? (
              <FunnelBars f={f} />
            ) : null}
            {/* Conversion rates subtitle when data is available */}
            {f && avg && (
              <div className="mt-4 flex flex-wrap gap-4 text-[12px] text-[#70757c]">
                <span>
                  Shortlist rate:{' '}
                  <span className="text-[#b8babf]">
                    {rate(f.shortlisted, f.total_applicants)}
                  </span>
                </span>
                <span>
                  Pass rate:{' '}
                  <span className="text-[#b8babf]">{rate(f.exam_passed, f.exam_taken)}</span>
                </span>
                <span>
                  Hire rate:{' '}
                  <span className="text-[#b8babf]">
                    {rate(f.hired, f.interview_completed)}
                  </span>
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
                      <span
                        className="h-2.5 w-2.5 rounded-[3px]"
                        style={{ background: s.color }}
                      />
                      {s.label}
                      <span className="ml-auto text-[#70757c]">{s.value}%</span>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="flex h-[180px] items-center justify-center">
                <p className="text-[13px] text-[#888b91]">No language data yet.</p>
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
                <p className="text-[13px] text-[#888b91]">No score data yet.</p>
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
                <p className="text-[13px] text-[#888b91]">No trend data yet.</p>
              </div>
            )}
          </GlassCard>
        </Reveal>
      </div>

      {/* Averages summary row (only when data loaded) */}
      {avg && (
        <Reveal>
          <div className="flex flex-wrap gap-6 rounded-[16px] border border-white/[0.06] bg-white/[0.02] px-5 py-4 text-[13px] text-[#888b91]">
            {avg.avg_ats !== null && (
              <span>
                Avg ATS score:{' '}
                <span className="font-semibold text-white">{Math.round(avg.avg_ats)}</span>
              </span>
            )}
            {avg.avg_exam_percent !== null && (
              <span>
                Avg exam score:{' '}
                <span className="font-semibold text-white">
                  {Math.round(avg.avg_exam_percent)}%
                </span>
              </span>
            )}
            {avg.avg_interview_composite !== null && (
              <span>
                Avg interview score:{' '}
                <span className="font-semibold text-white">
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

// ── Named export: standalone /hr/analytics page ───────────────────────────────
export function HRAnalyticsPage() {
  return (
    <div className="mx-auto max-w-[1280px] px-6 py-8 lg:px-8">
      <h1 className="text-[28px] font-semibold tracking-[-1px]">Analytics</h1>
      <p className="mt-1 text-[14px] text-[#888b91]">
        Funnel, scores and language insights.
      </p>
      <div className="mt-6">
        <HRAnalytics />
      </div>
    </div>
  );
}
