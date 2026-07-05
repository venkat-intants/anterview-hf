// AdminInterviewDetail — drill-in detail for one interview session.
// Route: /admin/interviews/:sessionId (inside AdminRoute + AppShell)
//
// Layout: reproduced from design screen (AdminInterviewDetail.tsx).
//   • Back link → candidate header → 3-col grid (score ring + breakdown)
//   • 2nd 3-col row: transcript (lg:col-span-2) + integrity (right col)
//   • Radar, Strengths, Improvements, Summary cards below
//
// Behavior: 100% live — getInterviewDetail + getInterviewTranscript,
//   expandable per-axis rationale, "no scorecard" branch, integrity panel
//   (integrity_score / by_type / flagged_seconds / timeline / "not enabled"),
//   session metadata grid, back nav, skeleton/error.

import { useEffect, useState, type ReactNode } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { motion, type Variants } from 'framer-motion';
import { useAccentColor } from '@/lib/useAccentColor';
import {
  ArrowLeft,
  User,
  Briefcase,
  Globe,
  Clock,
  CalendarDays,
  Download,
  CheckCircle2,
  TrendingUp,
  AlertCircle,
  ChevronDown,
  Info,
  ShieldCheck,
  AlertTriangle,
  MessageSquare,
} from '@/design/components/icons';
import { cn } from '@/lib/utils';
import {
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import { getInterviewDetail, getInterviewTranscript } from '@/api/admin';
import type {
  ScorecardDetail,
  ProctoringSummary,
  IntegrityEventItem,
  TranscriptTurn,
} from '@/api/admin';
import { toast } from '@/lib/toast';
import { formatDate, formatDuration, statusProps, languageLabel } from '@/lib/formatters';
import { Skeleton } from '@/components/ui/skeleton';
import {
  GlassCard,
  ScoreRing,
  StatusTag,
  Avatar,
  Pill,
} from '@/design/components/primitives';

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtScore(v: number | null): string {
  if (v === null) return '—';
  return v.toFixed(2);
}

// Design scoreColor works on 0–100; live scores are 0–10 → multiply by 10
function scoreColor(score: number): string {
  const pct = score * 10;
  if (pct >= 85) return '#27c93f';
  if (pct >= 70) return 'var(--accent)';
  if (pct >= 55) return '#ffb764';
  return '#e6714f';
}

// Map live status codes → StatusTag tones
function statusTone(
  status: string,
): 'forest' | 'electric' | 'amber' | 'ember' | 'neutral' {
  switch (status) {
    case 'completed':
      return 'forest';
    case 'in_progress':
      return 'electric';
    case 'abandoned':
      return 'amber';
    case 'failed':
      return 'ember';
    default:
      return 'neutral';
  }
}

// Initials from name or email
function initialsOf(name: string | null, email: string): string {
  if (name) {
    return name
      .split(' ')
      .map((w) => w[0] ?? '')
      .join('')
      .slice(0, 2)
      .toUpperCase();
  }
  return email.slice(0, 2).toUpperCase();
}

const GRADIENTS = [
  'linear-gradient(135deg,var(--accent),#a887dc)',
  'linear-gradient(135deg,#16c253,var(--accent))',
  'linear-gradient(135deg,#dd55e7,#a887dc)',
  'linear-gradient(135deg,#ffb764,#dd55e7)',
  'linear-gradient(135deg,#0fb7fa,#16c253)',
  'linear-gradient(135deg,#a887dc,var(--accent))',
];

function gradientFor(id: string): string {
  const seed = id.charCodeAt(0) + id.charCodeAt(id.length - 1);
  return GRADIENTS[Math.abs(seed) % GRADIENTS.length];
}

// Integrity score colour (0–100 scale)
function integrityColor(score: number): string {
  if (score >= 80) return '#27c93f';
  if (score >= 60) return '#ffb764';
  return '#e6714f';
}

function fmtClock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// ── Animation ──────────────────────────────────────────────────────────────────

const stagger: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.07 } },
};

const fadeUp: Variants = {
  hidden: { opacity: 0, y: 14 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.22, 1, 0.36, 1] } },
};

// ── Axis definitions ───────────────────────────────────────────────────────────

type AxisKey = 'communication' | 'technical' | 'problem_solving' | 'confidence';

const AXIS_LABELS: Record<AxisKey, string> = {
  communication: 'Communication',
  technical: 'Technical',
  problem_solving: 'Problem Solving',
  confidence: 'Confidence',
};

const AXIS_ORDER: AxisKey[] = ['communication', 'technical', 'problem_solving', 'confidence'];

const INTEGRITY_LABELS: Record<string, string> = {
  gaze_away: 'Looked away from screen',
  face_absent: 'Face not visible',
  multiple_faces: 'Multiple faces detected',
  tab_blur: 'Switched tab / window',
  fullscreen_exit: 'Left fullscreen',
  copy: 'Copied text',
  paste: 'Pasted text',
  second_voice: 'Second voice detected',
  devtools_open: 'Developer tools opened',
};

// ── Radar chart ────────────────────────────────────────────────────────────────

interface RadarDataPoint {
  dimension: string;
  score: number;
  fullMark: number;
}

function ScorecardRadar({ scorecard }: { scorecard: ScorecardDetail }) {
  const data: RadarDataPoint[] = AXIS_ORDER.map((key) => ({
    dimension: AXIS_LABELS[key],
    score: scorecard[key] ?? 0,
    fullMark: 10,
  }));
  const accent = useAccentColor();

  return (
    <ResponsiveContainer width="100%" height={220}>
      <RadarChart data={data} margin={{ top: 8, right: 24, bottom: 8, left: 24 }}>
        <PolarGrid stroke="rgba(255,255,255,0.08)" />
        <PolarAngleAxis
          dataKey="dimension"
          tick={{ fill: '#888b91', fontSize: 11 }}
        />
        <Tooltip
          contentStyle={{
            background: '#0f0f10',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: '10px',
            fontSize: 12,
            color: '#ffffff',
          }}
          formatter={(value) => [`${Number(value ?? 0)} / 10`, 'Score']}
        />
        <Radar
          name="Score"
          dataKey="score"
          stroke={accent}
          fill={accent}
          fillOpacity={0.18}
          strokeWidth={2}
        />
      </RadarChart>
    </ResponsiveContainer>
  );
}

// ── Score bar row with expandable rationale ────────────────────────────────────

function ScoreBarRow({
  label,
  score,
  rationale,
}: {
  label: string;
  score: number | null;
  rationale?: string;
}) {
  const [open, setOpen] = useState(false);
  if (score === null) return null;

  const pct = Math.round((score / 10) * 100);
  const panelId = `admin-rationale-${label.replace(/\s+/g, '-').toLowerCase()}`;
  const hasRationale = Boolean(rationale?.trim());

  return (
    <div key={label} className="space-y-1.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
        className={cn(
          'w-full flex items-center justify-between gap-2 rounded-[10px] -mx-1 px-2 py-1.5 text-left',
          'transition-colors hover:bg-white/[0.04]',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
        )}
      >
        <span className="flex items-center gap-1.5 text-[12.5px] text-[#b8babf]">
          {label}
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 text-[#70757c] transition-transform duration-200',
              open && 'rotate-180',
            )}
            aria-hidden="true"
          />
        </span>
        <span
          className="font-mono font-semibold text-[12.5px] tabular-nums"
          style={{ color: scoreColor(score) }}
        >
          {fmtScore(score)}
        </span>
      </button>

      {/* Progress bar */}
      <div className="h-2 rounded-full bg-white/[0.07]">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{
            width: `${pct}%`,
            background: `linear-gradient(90deg,var(--accent),${scoreColor(score)})`,
          }}
          role="progressbar"
          aria-valuenow={score}
          aria-valuemin={0}
          aria-valuemax={10}
          aria-label={`${label}: ${fmtScore(score)} out of 10`}
        />
      </div>

      {open && (
        <div
          id={panelId}
          className="mt-1 rounded-[14px] border border-white/[0.08] bg-white/[0.03] px-3.5 py-3"
        >
          <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--accent)]">
            <Info className="h-3.5 w-3.5" aria-hidden="true" />
            Why this score
          </p>
          <p className="text-[13px] leading-relaxed text-[#888b91]">
            {hasRationale
              ? rationale
              : 'A detailed rationale is not available for this scorecard (it was scored before per-aspect explanations were added).'}
          </p>
        </div>
      )}
    </div>
  );
}

// ── Transcript panel (live — backed by getInterviewTranscript) ─────────────────

function TranscriptPanel({ turns, isLoading }: { turns: TranscriptTurn[]; isLoading: boolean }) {
  return (
    <GlassCard className="p-5">
      <h3 className="mb-4 text-[15px] font-semibold text-white flex items-center gap-2">
        <MessageSquare size={16} className="text-[#a887dc]" aria-hidden="true" />
        Transcript
      </h3>

      {isLoading ? (
        <div className="flex flex-col gap-4" aria-label="Loading transcript" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="flex gap-3">
              <Skeleton className="h-6 w-12 rounded-pill bg-white/[0.07]" />
              <Skeleton className="h-10 flex-1 rounded-[14px] bg-white/[0.05]" />
            </div>
          ))}
        </div>
      ) : turns.length === 0 ? (
        <p className="text-[13px] text-[#888b91]">No transcript recorded for this session.</p>
      ) : (
        <ol className="flex flex-col gap-4" aria-label="Interview transcript">
          {turns.map((turn) => {
            const isInterviewer = turn.speaker === 'interviewer';
            return (
              <li key={turn.turn_number} className="flex gap-3">
                <span
                  className={cn(
                    'flex-none rounded-pill px-2.5 py-1 text-[11px] font-semibold',
                    isInterviewer
                      ? 'bg-[rgba(168,135,220,0.18)] text-[#c89ce8]'
                      : 'bg-[rgba(var(--accent-rgb),0.16)] text-[#60a5fa]',
                  )}
                >
                  {isInterviewer ? 'AI' : 'C'}
                </span>
                <p className="flex-1 text-[13.5px] leading-[1.55] text-[#cccccc]">
                  {turn.text?.trim() || (
                    <span className="italic text-[#70757c]">[no text]</span>
                  )}
                </p>
                {turn.created_at && (
                  <span className="flex-none font-mono text-[11px] text-[#5a5f66]">
                    {fmtClock(turn.created_at)}
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </GlassCard>
  );
}

// ── Integrity (proctoring) panel ──────────────────────────────────────────────

function IntegrityPanel({
  score,
  summary,
  events,
}: {
  score: number | null | undefined;
  summary: ProctoringSummary | null | undefined;
  events: IntegrityEventItem[] | undefined;
}) {
  const byType = summary?.by_type ?? {};
  const flaggedSeconds = summary?.flagged_seconds ?? {};
  const types = Object.keys(byType);
  const timeline = events ?? [];

  return (
    <GlassCard className="p-5">
      <h3 className="mb-4 flex items-center gap-2 text-[15px] font-semibold text-white">
        <ShieldCheck size={16} className="text-[#27c93f]" aria-hidden="true" />
        Proctoring
      </h3>

      <p className="mb-4 text-[12px] text-[#888b91]">
        AI-assisted flagging for human review — not an automated decision.
      </p>

      {score === null || score === undefined ? (
        <p className="text-[13px] text-[#888b91]">
          Proctoring was not enabled for this session.
        </p>
      ) : (
        <>
          <div className="mb-4 flex items-baseline gap-2">
            <span
              className="text-[30px] font-semibold tabular-nums tracking-[-1px]"
              style={{ color: integrityColor(score) }}
            >
              {score}
            </span>
            <span className="text-[13px] text-[#888b91]">/ 100 integrity</span>
          </div>

          {types.length === 0 ? (
            <p className="text-[13px] text-[#27c93f]">No integrity flags were raised. ✓</p>
          ) : (
            <div className="flex flex-col gap-2.5" aria-label="Integrity flags">
              {types.map((t) => (
                <div
                  key={t}
                  className="flex items-center gap-3 rounded-[12px] border border-white/[0.07] bg-white/[0.02] p-3"
                >
                  <AlertTriangle
                    size={15}
                    className="flex-none text-[#ffb764]"
                    aria-hidden="true"
                  />
                  <span className="flex-1 text-[12.5px] text-[#b8babf]">
                    {INTEGRITY_LABELS[t] ?? t}
                  </span>
                  <span className="font-mono text-[11px] text-[#70757c] tabular-nums">
                    {byType[t]}×{flaggedSeconds[t] ? ` · ${flaggedSeconds[t]}s` : ''}
                  </span>
                </div>
              ))}
            </div>
          )}

          {timeline.length > 0 && (
            <div className="pt-3">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.5px] text-[#888b91]">
                Event timeline
              </p>
              <ul
                className="space-y-1 max-h-64 overflow-y-auto"
                aria-label="Integrity event timeline"
              >
                {timeline.map((ev, idx) => (
                  <li
                    key={idx}
                    className="flex items-center justify-between gap-2 rounded-[10px] bg-white/[0.02] px-2.5 py-1.5 text-[11.5px]"
                  >
                    <span className="flex items-center gap-2 text-[#b8babf]">
                      <span className="font-mono text-[#70757c] tabular-nums">
                        {fmtClock(ev.started_at)}
                      </span>
                      {INTEGRITY_LABELS[ev.event_type] ?? ev.event_type}
                    </span>
                    {ev.duration_seconds != null && (
                      <span className="font-mono text-[#70757c] tabular-nums">
                        {ev.duration_seconds}s
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </GlassCard>
  );
}

// ── Meta grid item ─────────────────────────────────────────────────────────────

function MetaItem({
  icon,
  label,
  value,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[9px] bg-white/[0.07] text-[#70757c] mt-0.5">
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-[11px] font-medium uppercase tracking-[0.5px] text-[#70757c]">{label}</p>
        <p className="mt-0.5 text-[13px] text-[#b8babf]">{value}</p>
      </div>
    </div>
  );
}

// ── Loading skeleton ───────────────────────────────────────────────────────────

function DetailSkeleton() {
  return (
    <div className="space-y-5">
      <Skeleton className="h-8 w-48 rounded bg-white/[0.06]" />
      <Skeleton className="h-40 w-full rounded-[24px] bg-white/[0.04]" />
      <Skeleton className="h-64 w-full rounded-[24px] bg-white/[0.04]" />
      <Skeleton className="h-32 w-full rounded-[24px] bg-white/[0.04]" />
    </div>
  );
}

// ── AdminInterviewDetail page ──────────────────────────────────────────────────

export default function AdminInterviewDetail() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['admin', 'interview', sessionId],
    queryFn: () => {
      if (!sessionId) throw new Error('Missing session ID');
      return getInterviewDetail(sessionId);
    },
    enabled: Boolean(sessionId),
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  // Live transcript query (queryKey ['admin','transcript',sessionId])
  const {
    data: transcriptData,
    isLoading: transcriptLoading,
  } = useQuery({
    queryKey: ['admin', 'transcript', sessionId],
    queryFn: () => getInterviewTranscript(sessionId!),
    enabled: Boolean(sessionId),
    retry: false,
    staleTime: 5 * 60 * 1000,
    throwOnError: false,
  });

  useEffect(() => {
    if (isError) {
      toast.error(
        error instanceof Error ? error.message : 'Failed to load interview detail.',
      );
    }
  }, [isError, error]);

  // ── Loading ──

  if (isLoading) {
    return (
      <motion.div initial="hidden" animate="visible" variants={stagger} className="space-y-6">
        <motion.div variants={fadeUp}>
          <Skeleton className="h-9 w-48 rounded bg-white/[0.06]" />
        </motion.div>
        <motion.div variants={fadeUp}>
          <DetailSkeleton />
        </motion.div>
      </motion.div>
    );
  }

  // ── Error / not found ──

  if (isError || !data) {
    return (
      <div
        role="alert"
        className="flex flex-col items-center justify-center py-24 gap-4 text-center"
      >
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-[rgba(230,113,79,0.14)]">
          <AlertCircle className="h-6 w-6 text-[#e6714f]" aria-hidden="true" />
        </div>
        <p className="text-[15px] font-semibold text-white">Interview not found</p>
        <p className="text-[13px] text-[#888b91]">
          {error instanceof Error ? error.message : 'The session could not be loaded.'}
        </p>
        <Pill variant="ghost" onClick={() => void navigate('/admin/interviews')}>
          Back to Interviews
        </Pill>
      </div>
    );
  }

  const { label: statusLabel } = statusProps(data.status);
  const tone = statusTone(data.status);
  const sc = data.scorecard;

  // For ScoreRing: design expects 0–100; live composite is 0–10 → multiply by 10
  const ringScore = sc?.composite_score != null ? Math.round(sc.composite_score * 10) : 0;

  return (
    <motion.div
      initial="hidden"
      animate="visible"
      variants={stagger}
      className="mx-auto max-w-[1180px] space-y-5"
    >
      {/* Back nav */}
      <motion.div variants={fadeUp}>
        <button
          type="button"
          onClick={() => void navigate('/admin/interviews')}
          className="inline-flex items-center gap-1.5 text-[13px] text-[#888b91] hover:text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded"
          aria-label="Back to interview list"
        >
          <ArrowLeft size={15} aria-hidden="true" />
          All interviews
        </button>
      </motion.div>

      {/* Candidate header */}
      <motion.div
        variants={fadeUp}
        className="flex flex-wrap items-center justify-between gap-4"
      >
        <div className="flex items-center gap-4">
          <Avatar
            initials={initialsOf(data.candidate_name, data.candidate_email)}
            gradient={gradientFor(data.session_id)}
            size={52}
          />
          <div>
            <h1 className="text-[24px] font-semibold tracking-[-0.8px] text-white">
              {data.candidate_name ?? data.candidate_email}
            </h1>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[13px] text-[#888b91]">
              {data.job_title && <span>{data.job_title}</span>}
              {data.job_title && <span>·</span>}
              <span>{languageLabel(data.language)}</span>
              <span>·</span>
              <span className="font-mono text-[11px]">{data.session_id}</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <StatusTag tone={tone}>{statusLabel}</StatusTag>
          {/* PDF export — disabled; feedback_billing not yet wired to admin_ops */}
          <Pill
            variant="ghost"
            className="px-4 py-2.5 opacity-50 cursor-not-allowed"
            disabled
            aria-label="Export PDF — coming soon"
          >
            <Download size={15} aria-hidden="true" />
            Export
          </Pill>
        </div>
      </motion.div>

      {/* Session metadata grid */}
      <motion.div variants={fadeUp}>
        <GlassCard className="p-6">
          <h3 className="mb-4 text-[15px] font-semibold text-white">Session Details</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            <MetaItem
              icon={<Briefcase className="h-4 w-4" />}
              label="Role"
              value={data.job_title ?? '—'}
            />
            <MetaItem
              icon={<Globe className="h-4 w-4" />}
              label="Language"
              value={languageLabel(data.language)}
            />
            <MetaItem
              icon={<Clock className="h-4 w-4" />}
              label="Duration"
              value={formatDuration(data.duration_seconds)}
            />
            <MetaItem
              icon={<CalendarDays className="h-4 w-4" />}
              label="Started"
              value={data.started_at ? formatDate(data.started_at) : '—'}
            />
            <MetaItem
              icon={<CalendarDays className="h-4 w-4" />}
              label="Completed"
              value={data.completed_at ? formatDate(data.completed_at) : '—'}
            />
            <MetaItem
              icon={<User className="h-4 w-4" />}
              label="Preferred Language"
              value={
                data.candidate_preferred_language
                  ? languageLabel(data.candidate_preferred_language)
                  : '—'
              }
            />
          </div>
        </GlassCard>
      </motion.div>

      {/* Scorecard section */}
      {sc ? (
        <>
          {/* Score ring + competency breakdown — 3-col grid */}
          <motion.div
            variants={fadeUp}
            className="grid grid-cols-1 gap-5 lg:grid-cols-3"
          >
            {/* ScoreRing: design is 0–100; we pass score*10 */}
            <GlassCard
              feature
              className="flex flex-col items-center justify-center gap-3 py-7 text-center"
            >
              <ScoreRing
                score={ringScore}
                size={140}
                label="overall"
              />
              <p className="text-[12px] text-[#888b91]">
                {sc.composite_score != null
                  ? `${fmtScore(sc.composite_score)} / 10`
                  : 'Not scored'}
              </p>
              <StatusTag tone={tone}>{statusLabel}</StatusTag>
            </GlassCard>

            {/* Competency breakdown bars — lg:col-span-2 */}
            <GlassCard className="p-5 lg:col-span-2">
              <h3 className="mb-4 text-[15px] font-semibold text-white">Competency breakdown</h3>
              <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
                {AXIS_ORDER.map((key) => (
                  <ScoreBarRow
                    key={key}
                    label={AXIS_LABELS[key]}
                    score={sc[key]}
                    rationale={sc.rationale?.[key]}
                  />
                ))}
              </div>
              <p className="mt-3 text-[12px] text-[#888b91]">
                Click any aspect to see why it received this score.
              </p>
            </GlassCard>
          </motion.div>

          {/* Transcript (left, lg:col-span-2) + Proctoring (right) — design layout */}
          <motion.div
            variants={fadeUp}
            className="grid grid-cols-1 gap-5 lg:grid-cols-3"
          >
            {/* Transcript — live, backed by getInterviewTranscript */}
            <div className="lg:col-span-2">
              <TranscriptPanel
                turns={transcriptData?.turns ?? []}
                isLoading={transcriptLoading}
              />
            </div>

            {/* Integrity / proctoring */}
            <IntegrityPanel
              score={data.integrity_score}
              summary={data.proctoring_summary}
              events={data.integrity_events}
            />
          </motion.div>

          {/* Radar chart */}
          <motion.div variants={fadeUp}>
            <GlassCard className="p-5">
              <h3 className="mb-1 text-[15px] font-semibold text-white">Score Radar</h3>
              <p className="mb-3 text-[12px] text-[#888b91]">
                Performance across all four competency axes (0–10).
              </p>
              <ScorecardRadar scorecard={sc} />
            </GlassCard>
          </motion.div>

          {/* Strengths */}
          {sc.strengths && sc.strengths.length > 0 && (
            <motion.div variants={fadeUp}>
              <GlassCard className="p-5">
                <h3 className="mb-4 flex items-center gap-2 text-[15px] font-semibold text-white">
                  <CheckCircle2 size={16} className="text-[#27c93f]" aria-hidden="true" />
                  Key Strengths
                </h3>
                <ul className="space-y-2.5" aria-label="Key strengths">
                  {sc.strengths.map((s, idx) => (
                    <li key={idx} className="flex gap-2.5">
                      <CheckCircle2
                        className="h-4 w-4 text-[#27c93f] shrink-0 mt-0.5"
                        aria-hidden="true"
                      />
                      <p className="text-[13.5px] leading-[1.55] text-[#cccccc]">{s}</p>
                    </li>
                  ))}
                </ul>
              </GlassCard>
            </motion.div>
          )}

          {/* Improvements */}
          {sc.improvements && sc.improvements.length > 0 && (
            <motion.div variants={fadeUp}>
              <GlassCard className="p-5">
                <h3 className="mb-4 flex items-center gap-2 text-[15px] font-semibold text-white">
                  <TrendingUp size={16} className="text-[#ffb764]" aria-hidden="true" />
                  Areas for Improvement
                </h3>
                <ul className="space-y-2.5" aria-label="Areas for improvement">
                  {sc.improvements.map((item, idx) => (
                    <li key={idx} className="flex gap-2.5">
                      <TrendingUp
                        className="h-4 w-4 text-[#ffb764] shrink-0 mt-0.5"
                        aria-hidden="true"
                      />
                      <p className="text-[13.5px] leading-[1.55] text-[#cccccc]">
                        <span className="font-semibold text-white">{item.area}:</span>{' '}
                        {item.suggestion}
                      </p>
                    </li>
                  ))}
                </ul>
              </GlassCard>
            </motion.div>
          )}

          {/* Summary */}
          {sc.summary && (
            <motion.div variants={fadeUp}>
              <GlassCard className="p-5">
                <h3 className="mb-3 text-[15px] font-semibold text-white">Summary</h3>
                <p className="text-[13.5px] leading-[1.6] text-[#cccccc]">{sc.summary}</p>
              </GlassCard>
            </motion.div>
          )}
        </>
      ) : (
        /* No scorecard yet — transcript + proctoring still shown */
        <>
          <motion.div variants={fadeUp}>
            <GlassCard className="flex flex-col items-center justify-center py-14 text-center gap-3">
              <div className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-white/[0.06]">
                <TrendingUp className="h-6 w-6 text-[#70757c]" aria-hidden="true" />
              </div>
              <p className="text-[15px] font-semibold text-white">No scorecard yet</p>
              <p className="text-[13px] text-[#888b91]">
                This session has not been scored. Scoring happens when the session completes.
              </p>
            </GlassCard>
          </motion.div>

          {/* Transcript + proctoring in design 3-col layout even without scorecard */}
          <motion.div
            variants={fadeUp}
            className="grid grid-cols-1 gap-5 lg:grid-cols-3"
          >
            <div className="lg:col-span-2">
              <TranscriptPanel
                turns={transcriptData?.turns ?? []}
                isLoading={transcriptLoading}
              />
            </div>
            <IntegrityPanel
              score={data.integrity_score}
              summary={data.proctoring_summary}
              events={data.integrity_events}
            />
          </motion.div>
        </>
      )}
    </motion.div>
  );
}
