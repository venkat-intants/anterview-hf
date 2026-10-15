// AdminInterviewDetail — drill-in detail for one interview session.
// Route: /admin/interviews/:sessionId (inside AdminRoute + AppShell)
//
// Layout: breadcrumbs (Interviews → this interview) + PageHeader (candidate, status) → cards:
//   • Session details
//   • 3-col grid: score ring + competency breakdown
//   • 3-col row: transcript (lg:col-span-2) + proctoring (right col)
//   • Radar, Strengths, Improvements, Summary cards below
//
// Behavior: 100% live — getInterviewDetail + getInterviewTranscript,
//   expandable per-axis rationale, "no scorecard" branch, integrity panel
//   (integrity_score / by_type / flagged_seconds / timeline / "not enabled"),
//   session metadata grid, back nav, skeleton/error.

import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAccentColor } from '@/lib/useAccentColor';
import { AXIS_STYLE, GRID_STROKE, TOOLTIP_STYLE } from '@/lib/chartTheme';
import {
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
import { Collapsible, ProgressiveList } from '@/design/components/collapsible';
import { useScopedPersistKey } from '@/hooks/useScopedPersistKey';
import { Button } from '@/components/ui/button';
import { GlassCard, ScoreRing, StatusTag, Avatar } from '@/design/components/primitives';
import {
  Page,
  Breadcrumbs,
  DataList,
  EmptyState,
  PageHeader,
  SectionCard,
} from '@/design/components/page';

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtScore(v: number | null): string {
  if (v === null) return '—';
  return v.toFixed(2);
}

// Design scoreColor works on 0–100; live scores are 0–10 → multiply by 10
function scoreColor(score: number): string {
  const pct = score * 10;
  if (pct >= 85) return 'var(--ui-ok)';
  if (pct >= 70) return 'var(--ui-info)';
  if (pct >= 55) return 'var(--ui-warn)';
  return 'var(--ui-danger)';
}

// Map live status codes → StatusTag tones
function statusTone(status: string): 'forest' | 'electric' | 'amber' | 'ember' | 'neutral' {
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
  'linear-gradient(135deg,var(--accent),var(--ui-lavender))',
  'linear-gradient(135deg,var(--ui-ok),var(--accent))',
  'linear-gradient(135deg,var(--ui-role-platform),var(--ui-lavender))',
  'linear-gradient(135deg,var(--ui-warn),var(--ui-role-platform))',
  'linear-gradient(135deg,var(--ui-info),var(--ui-ok))',
  'linear-gradient(135deg,var(--ui-lavender),var(--accent))',
];

function gradientFor(id: string): string {
  const seed = id.charCodeAt(0) + id.charCodeAt(id.length - 1);
  return GRADIENTS[Math.abs(seed) % GRADIENTS.length];
}

// Integrity score colour (0–100 scale)
function integrityColor(score: number): string {
  if (score >= 80) return 'var(--ui-ok)';
  if (score >= 60) return 'var(--ui-warn)';
  return 'var(--ui-danger)';
}

function fmtClock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

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
        <PolarGrid stroke={GRID_STROKE} />
        <PolarAngleAxis dataKey="dimension" tick={{ fill: AXIS_STYLE.stroke, fontSize: 12 }} />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
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
          'transition-colors hover:bg-[var(--ui-inset)]',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
        )}
      >
        <span className="flex items-center gap-1.5 text-[13.5px] text-[var(--ui-soft)]">
          {label}
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 text-muted-foreground transition-transform duration-200',
              open && 'rotate-180',
            )}
            aria-hidden="true"
          />
        </span>
        <span
          className="font-mono text-[13px] font-semibold tabular-nums"
          style={{ color: scoreColor(score) }}
        >
          {fmtScore(score)}
        </span>
      </button>

      {/* Progress bar */}
      <div className="h-2 rounded-full bg-[var(--ui-inset-strong)]">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{
            width: `${pct}%`,
            background: scoreColor(score),
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
          className="mt-1 rounded-[14px] border border-border bg-[var(--ui-inset-soft)] px-3.5 py-3"
        >
          <p className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wide text-[var(--ui-info)]">
            <Info className="h-3.5 w-3.5" aria-hidden="true" />
            Why this score
          </p>
          <p className="text-[13px] leading-relaxed text-muted-foreground">
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
    <SectionCard title="Transcript" icon={MessageSquare} className="h-full">
      {isLoading ? (
        <div className="flex flex-col gap-4" aria-label="Loading transcript" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="flex gap-3">
              <Skeleton className="h-6 w-12 rounded-pill" />
              <Skeleton className="h-10 flex-1 rounded-[14px]" />
            </div>
          ))}
        </div>
      ) : turns.length === 0 ? (
        <EmptyState icon={MessageSquare} title="No transcript recorded for this session." />
      ) : (
        <ol className="flex flex-col gap-4" aria-label="Interview transcript">
          {turns.map((turn) => {
            const isInterviewer = turn.speaker === 'interviewer';
            return (
              <li key={turn.turn_number} className="flex gap-3">
                <span
                  className={cn(
                    'h-fit flex-none rounded-pill px-2.5 py-1 text-[12px] font-semibold',
                    isInterviewer
                      ? 'bg-[var(--ui-lavender-wash)] text-[var(--ui-lavender)]'
                      : 'bg-[var(--ui-info-wash)] text-[var(--ui-info)]',
                  )}
                >
                  {isInterviewer ? 'AI' : 'C'}
                </span>
                <p className="min-w-0 flex-1 break-words text-[14px] leading-[1.55] text-[var(--ui-soft)]">
                  {turn.text?.trim() || (
                    <span className="italic text-muted-foreground">[no text]</span>
                  )}
                </p>
                {turn.created_at && (
                  <span className="flex-none font-mono text-[12px] text-muted-foreground">
                    {fmtClock(turn.created_at)}
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </SectionCard>
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
  const timelineKey = useScopedPersistKey('interview-integrity-timeline');
  // The newest event by its own time, whatever order the list arrives in.
  const newestEvent = timeline.reduce<IntegrityEventItem | null>(
    (n, ev) => (n === null || ev.started_at > n.started_at ? ev : n),
    null,
  );

  return (
    <SectionCard
      title="Proctoring"
      icon={ShieldCheck}
      description="AI-assisted flagging for human review — not an automated decision."
      className="h-full"
    >
      {score === null || score === undefined ? (
        <EmptyState icon={ShieldCheck} title="Proctoring was not enabled for this session." />
      ) : (
        <>
          <div className="mb-4 flex items-baseline gap-2">
            <span
              className="text-[30px] font-semibold tabular-nums tracking-[-1px]"
              style={{ color: integrityColor(score) }}
            >
              {score}
            </span>
            <span className="text-[13px] text-muted-foreground">/ 100 integrity</span>
          </div>

          {types.length === 0 ? (
            <p className="flex items-center gap-1.5 text-[13.5px] text-foreground">
              <CheckCircle2 size={15} className="text-[var(--ui-ok)]" aria-hidden="true" />
              No integrity flags were raised.
            </p>
          ) : (
            <div className="flex flex-col gap-2.5" aria-label="Integrity flags">
              {types.map((t) => (
                <div
                  key={t}
                  className="flex items-center gap-3 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-3"
                >
                  <AlertTriangle
                    size={15}
                    className="flex-none text-[var(--ui-warn)]"
                    aria-hidden="true"
                  />
                  <span className="flex-1 text-[13px] text-[var(--ui-soft)]">
                    {INTEGRITY_LABELS[t] ?? t}
                  </span>
                  <span className="font-mono text-[12px] text-muted-foreground tabular-nums">
                    {byType[t]}×{flaggedSeconds[t] ? ` · ${flaggedSeconds[t]}s` : ''}
                  </span>
                </div>
              ))}
            </div>
          )}

          {timeline.length > 0 && (
            <Collapsible
              title="Event timeline"
              headingLevel="h3"
              count={timeline.length}
              noun="event"
              latest={
                newestEvent
                  ? `${INTEGRITY_LABELS[newestEvent.event_type] ?? newestEvent.event_type} at ${fmtClock(newestEvent.started_at)}`
                  : undefined
              }
              persistKey={timelineKey}
              className="mt-3"
              data-testid="integrity-timeline"
            >
              <ProgressiveList
                items={timeline}
                initial={5}
                noun="events"
                getKey={(ev, idx) => `${ev.started_at}-${idx}`}
                className="space-y-1"
                aria-label="Integrity event timeline"
                renderItem={(ev) => (
                  <li className="flex items-center justify-between gap-2 rounded-[10px] bg-[var(--ui-inset-soft)] px-2.5 py-1.5 text-[12px]">
                    <span className="flex items-center gap-2 text-[var(--ui-soft)]">
                      <span className="font-mono text-muted-foreground tabular-nums">
                        {fmtClock(ev.started_at)}
                      </span>
                      {INTEGRITY_LABELS[ev.event_type] ?? ev.event_type}
                    </span>
                    {ev.duration_seconds != null && (
                      <span className="font-mono text-muted-foreground tabular-nums">
                        {ev.duration_seconds}s
                      </span>
                    )}
                  </li>
                )}
              />
            </Collapsible>
          )}
        </>
      )}
    </SectionCard>
  );
}

// ── Loading skeleton ───────────────────────────────────────────────────────────

function DetailSkeleton() {
  return (
    <div className="space-y-5" aria-busy="true" aria-label="Loading interview">
      <div className="flex items-center gap-4">
        <Skeleton className="h-[52px] w-[52px] rounded-full" />
        <div className="space-y-2">
          <Skeleton className="h-7 w-56" />
          <Skeleton className="h-4 w-72" />
        </div>
      </div>
      <Skeleton className="h-40 w-full rounded-card" />
      <Skeleton className="h-64 w-full rounded-card" />
      <Skeleton className="h-32 w-full rounded-card" />
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
  const { data: transcriptData, isLoading: transcriptLoading } = useQuery({
    queryKey: ['admin', 'transcript', sessionId],
    queryFn: () => getInterviewTranscript(sessionId!),
    enabled: Boolean(sessionId),
    retry: false,
    staleTime: 5 * 60 * 1000,
    throwOnError: false,
  });

  useEffect(() => {
    if (isError) {
      toast.error(error instanceof Error ? error.message : 'Failed to load interview detail.');
    }
  }, [isError, error]);

  const crumbs = (
    <Breadcrumbs
      items={[{ label: 'Interviews', to: '/admin/interviews' }, { label: 'Interview' }]}
    />
  );

  // ── Loading ──

  if (isLoading) {
    return (
      <Page width="default">
        {crumbs}
        <DetailSkeleton />
      </Page>
    );
  }

  // ── Error / not found ──

  if (isError || !data) {
    return (
      <Page width="default">
        {crumbs}
        <div role="alert">
          <SectionCard title="Interview not found">
            <EmptyState
              size="page"
              icon={AlertCircle}
              title="This interview could not be loaded"
              description={
                error instanceof Error ? error.message : 'The session could not be loaded.'
              }
              action={
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void navigate('/admin/interviews')}
                >
                  Back to Interviews
                </Button>
              }
            />
          </SectionCard>
        </div>
      </Page>
    );
  }

  const { label: statusLabel } = statusProps(data.status);
  const tone = statusTone(data.status);
  const sc = data.scorecard;

  // For ScoreRing: design expects 0–100; live composite is 0–10 → multiply by 10
  const ringScore = sc?.composite_score != null ? Math.round(sc.composite_score * 10) : 0;

  return (
    <Page width="default">
      {/* Candidate header */}
      <div className="flex items-start gap-4">
        <Avatar
          initials={initialsOf(data.candidate_name, data.candidate_email)}
          gradient={gradientFor(data.session_id)}
          size={52}
          className="mt-1 hidden sm:inline-flex"
        />
        <PageHeader
          className="mb-0 min-w-0 flex-1"
          breadcrumbs={crumbs}
          title={data.candidate_name ?? data.candidate_email}
          description={
            <span className="flex flex-wrap items-center gap-1.5 text-[14px]">
              {data.job_title && <span>{data.job_title}</span>}
              {data.job_title && <span aria-hidden="true">·</span>}
              <span>{languageLabel(data.language)}</span>
              <span aria-hidden="true">·</span>
              <span className="break-all font-mono text-[12px]">{data.session_id}</span>
            </span>
          }
          actions={<StatusTag tone={tone}>{statusLabel}</StatusTag>}
        />
      </div>

      {/* Session metadata grid */}
      <SectionCard title="Session Details">
        <DataList
          columns={3}
          items={[
            { label: 'Role', value: data.job_title ?? '—' },
            { label: 'Language', value: languageLabel(data.language) },
            { label: 'Duration', value: formatDuration(data.duration_seconds) },
            { label: 'Started', value: data.started_at ? formatDate(data.started_at) : '—' },
            { label: 'Completed', value: data.completed_at ? formatDate(data.completed_at) : '—' },
            {
              label: 'Preferred Language',
              value: data.candidate_preferred_language
                ? languageLabel(data.candidate_preferred_language)
                : '—',
            },
          ]}
        />
      </SectionCard>

      {/* Scorecard section */}
      {sc ? (
        <>
          {/* Score ring + competency breakdown — 3-col grid */}
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
            {/* ScoreRing takes 0–100; live scores are 0–10, so pass score*10 */}
            <GlassCard className="flex flex-col items-center justify-center gap-3 py-7 text-center">
              <ScoreRing score={ringScore} size={140} label="overall" />
              <p className="text-[13px] text-muted-foreground">
                {sc.composite_score != null ? `${fmtScore(sc.composite_score)} / 10` : 'Not scored'}
              </p>
            </GlassCard>

            <SectionCard
              title="Competency breakdown"
              description="Select any aspect to see why it received this score."
              className="lg:col-span-2"
            >
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
            </SectionCard>
          </div>

          {/* Transcript (left, lg:col-span-2) + Proctoring (right) */}
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
            {/* Transcript — live, backed by getInterviewTranscript */}
            <div className="lg:col-span-2">
              <TranscriptPanel turns={transcriptData?.turns ?? []} isLoading={transcriptLoading} />
            </div>

            <IntegrityPanel
              score={data.integrity_score}
              summary={data.proctoring_summary}
              events={data.integrity_events}
            />
          </div>

          {/* Radar chart */}
          <SectionCard
            title="Score Radar"
            description="Performance across all four competency axes (0–10)."
          >
            <ScorecardRadar scorecard={sc} />
          </SectionCard>

          {/* Strengths */}
          {sc.strengths && sc.strengths.length > 0 && (
            <SectionCard title="Key Strengths">
              <ul className="space-y-2.5" aria-label="Key strengths">
                {sc.strengths.map((s, idx) => (
                  <li key={idx} className="flex gap-2.5">
                    <CheckCircle2
                      className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-ok)]"
                      aria-hidden="true"
                    />
                    <p className="text-[14px] leading-[1.55] text-[var(--ui-soft)]">{s}</p>
                  </li>
                ))}
              </ul>
            </SectionCard>
          )}

          {/* Improvements */}
          {sc.improvements && sc.improvements.length > 0 && (
            <SectionCard title="Areas for Improvement">
              <ul className="space-y-2.5" aria-label="Areas for improvement">
                {sc.improvements.map((item, idx) => (
                  <li key={idx} className="flex gap-2.5">
                    <TrendingUp
                      className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-warn)]"
                      aria-hidden="true"
                    />
                    <p className="text-[14px] leading-[1.55] text-[var(--ui-soft)]">
                      <span className="font-semibold text-foreground">{item.area}:</span>{' '}
                      {item.suggestion}
                    </p>
                  </li>
                ))}
              </ul>
            </SectionCard>
          )}

          {/* Summary */}
          {sc.summary && (
            <SectionCard title="Summary">
              <p className="text-[14px] leading-[1.6] text-[var(--ui-soft)]">{sc.summary}</p>
            </SectionCard>
          )}
        </>
      ) : (
        /* No scorecard yet — transcript + proctoring still shown */
        <>
          <SectionCard title="Scorecard">
            <EmptyState
              icon={TrendingUp}
              title="No scorecard yet"
              description="This session has not been scored. Scoring happens when the session completes."
            />
          </SectionCard>

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <TranscriptPanel turns={transcriptData?.turns ?? []} isLoading={transcriptLoading} />
            </div>
            <IntegrityPanel
              score={data.integrity_score}
              summary={data.proctoring_summary}
              events={data.integrity_events}
            />
          </div>
        </>
      )}
    </Page>
  );
}
