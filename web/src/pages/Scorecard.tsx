// Scorecard — full-screen results page (outside AppShell).
// Route: /scorecard/:scorecardId
//
// Design: paper canvas, white section cards. A score ring + radar up top, the
// per-competency breakdown below, then strengths / improvements / summary.
// Data: getScorecard query (live, 0–10 scale); ScoreRing receives score*10 (0–100).
// Must-preserve: query, skeleton, ErrorState+toast, rationale accordions,
// recharts radar, strengths/improvements/summary, PDF gate, back-nav, all t() keys.
// The AI never decides a hiring outcome: nothing here reads as one.

import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useAccentColor } from '@/lib/useAccentColor';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  Download,
  CheckCircle2,
  AlertTriangle,
  TrendingUp,
  ChevronDown,
  Info,
  ShieldCheck,
} from '@/design/components/icons';
import {
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import { useAuth } from '@/context/AuthContext';
import { isGuestOnly } from '@/lib/guest';
import { downloadUrl } from '@/lib/safeUrl';
import { getScorecard } from '@/api/scorecard';
import type { ScoreBreakdown, ImprovementItem, AxisFeedbackEntry } from '@/api/scorecard';
import { getSessionIntegrity } from '@/api/integrity';
import type { IntegrityReport } from '@/api/integrity';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { GRID_STROKE, TOOLTIP_STYLE } from '@/lib/chartTheme';
import { Button, buttonVariants } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Breadcrumbs, PageHeader, SectionCard } from '@/design/components/page';
import { ScoreRing, StatusTag, type TagTone } from '@/design/components/primitives';
import { Collapsible } from '@/design/components/collapsible';
import { useScopedPersistKey } from '@/hooks/useScopedPersistKey';

// ── Constants ──────────────────────────────────────────────────────────────────

const DIMENSION_LABEL_KEYS: Record<keyof ScoreBreakdown, string> = {
  communication: 'scorecard.dimensionCommunication',
  technical: 'scorecard.dimensionTechnical',
  problem_solving: 'scorecard.dimensionProblemSolving',
  confidence: 'scorecard.dimensionConfidence',
};

const DIMENSION_ORDER: Array<keyof ScoreBreakdown> = [
  'communication',
  'technical',
  'problem_solving',
  'confidence',
];

// ── Score helpers (0–10 scale) ────────────────────────────────────────────────

/** Map a 0–10 score to a StatusTag tone. */
function scoreTone(score: number): TagTone {
  if (score >= 8) return 'forest';
  if (score >= 6) return 'electric';
  if (score >= 4) return 'amber';
  return 'ember';
}

/** Map a 0–10 score to a readable i18n key. */
function scoreLabelKey(score: number): string {
  if (score >= 8) return 'scorecard.labelExcellent';
  if (score >= 6) return 'scorecard.labelGood';
  if (score >= 4) return 'scorecard.labelFair';
  return 'scorecard.labelNeedsWork';
}

/** Per-band status tokens for a 0–10 score: the pill ink, its wash, and the bar
 *  fill. The number is always printed beside the bar, so the band never rests
 *  on colour alone. */
function scoreBand(score: number): { ink: string; wash: string; fill: string } {
  if (score >= 8) return { ink: 'var(--ui-ok)', wash: 'var(--ui-ok-wash)', fill: 'var(--ui-ok)' };
  if (score >= 6) return { ink: 'var(--ui-info)', wash: 'var(--ui-info-wash)', fill: 'var(--accent)' };
  if (score >= 4) return { ink: 'var(--ui-warn)', wash: 'var(--ui-warn-wash)', fill: 'var(--ui-warn)' };
  return { ink: 'var(--ui-danger)', wash: 'var(--ui-danger-wash)', fill: 'var(--ui-danger)' };
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function ErrorState({
  isGuest,
  onRetry,
  retrying,
}: {
  isGuest: boolean;
  onRetry: () => void;
  retrying: boolean;
}) {
  const { t } = useTranslation();
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <div
        role="alert"
        className="w-full max-w-md space-y-5 rounded-card border border-border bg-card p-8 text-center shadow-card"
      >
        <div className="mx-auto inline-flex h-12 w-12 items-center justify-center rounded-full bg-[var(--ui-danger-wash)]">
          <AlertTriangle className="h-6 w-6 text-[var(--ui-danger)]" aria-hidden="true" />
        </div>
        <div>
          <p className="text-[17px] font-bold text-foreground">{t('scorecard.notAvailableTitle')}</p>
          <p className="mt-1.5 text-[14px] text-muted-foreground">{t('scorecard.notAvailableDesc')}</p>
        </div>
        <div className="flex flex-wrap justify-center gap-2.5">
          <Button type="button" onClick={onRetry} disabled={retrying}>
            {t('scorecard.tryAgain')}
          </Button>
          {/* History refuses a guest token, so a guest is not sent there (audit CA-M14). */}
          {!isGuest && (
            <Link to="/history" className={buttonVariants({ variant: 'outline' })}>
              {t('scorecard.backToHistory')}
            </Link>
          )}
        </div>
      </div>
    </main>
  );
}

// ScoreBarRow — collapsible detail accordion (aria-expanded / aria-controls preserved).
function ScoreBarRow({
  label,
  score,
  rationale,
  feedback,
}: {
  label: string;
  score: number;
  rationale?: string;
  feedback?: AxisFeedbackEntry;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const pct = Math.round((score / 10) * 100);
  const panelId = `rationale-${label.replace(/\s+/g, '-').toLowerCase()}`;
  const hasRationale = Boolean(rationale && rationale.trim());
  const band = scoreBand(score);

  return (
    <div className="space-y-2">
      {/* Clickable header — "why this score" toggle */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
        className={cn(
          'flex min-h-[44px] w-full items-center justify-between gap-3 rounded-[12px] px-3 py-2 text-left',
          'transition-colors hover:bg-[var(--ui-inset)]',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
        )}
      >
        <span className="flex items-center gap-1.5 text-[14px] font-semibold text-foreground">
          {label}
          <ChevronDown
            className={cn(
              'h-4 w-4 text-muted-foreground transition-transform duration-200',
              open && 'rotate-180',
            )}
            aria-hidden="true"
          />
        </span>
        <span
          className="flex-none rounded-full px-2.5 py-0.5 text-[13px] font-bold tabular-nums"
          style={{ color: band.ink, background: band.wash }}
        >
          {score} / 10
        </span>
      </button>

      <div
        className="h-2 overflow-hidden rounded-full bg-[var(--ui-inset-strong)]"
        aria-label={t('scorecard.scoreOutOf', { label, score })}
      >
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, background: band.fill }}
        />
      </div>

      {/* Detail panel: why this score + what went wrong + how to improve */}
      {open && (
        <div
          id={panelId}
          className="mt-1 space-y-3 rounded-[14px] bg-[var(--ui-inset-soft)] px-4 py-3"
        >
          <div>
            <p className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wide text-[var(--ui-info)]">
              <Info className="h-3.5 w-3.5" aria-hidden="true" />
              {t('scorecard.whyThisScore')}
            </p>
            <p className="text-[14px] leading-relaxed text-foreground">
              {hasRationale ? rationale : t('scorecard.rationaleUnavailable')}
            </p>
          </div>

          {feedback && feedback.went_wrong.length > 0 && (
            <div>
              <p className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wide text-[var(--ui-danger)]">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                {t('scorecard.whatWentWrong')}
              </p>
              <ul className="space-y-1.5" aria-label={t('scorecard.wentWrongAria', { label })}>
                {feedback.went_wrong.map((point, idx) => (
                  <li key={idx} className="flex items-start gap-2 text-[14px] leading-relaxed text-foreground">
                    <span
                      className="mt-[8px] h-1.5 w-1.5 flex-none rounded-full bg-[var(--ui-danger)]"
                      aria-hidden="true"
                    />
                    {point}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {feedback && feedback.how_to_improve.length > 0 && (
            <div>
              <p className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wide text-[var(--ui-ok)]">
                <TrendingUp className="h-3.5 w-3.5" aria-hidden="true" />
                {t('scorecard.howToImprove')}
              </p>
              <ul className="space-y-1.5" aria-label={t('scorecard.improveAria', { label })}>
                {feedback.how_to_improve.map((point, idx) => (
                  <li key={idx} className="flex items-start gap-2 text-[14px] leading-relaxed text-foreground">
                    <CheckCircle2
                      className="mt-0.5 h-4 w-4 flex-none text-[var(--ui-ok)]"
                      aria-hidden="true"
                    />
                    {point}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface RadarDataPoint {
  dimension: string;
  score: number;
  fullMark: number;
}

// Recharts radar — live 4-dimension data (0–10 scale preserved). Grid, labels
// and tooltip come from lib/chartTheme so they follow the mode.
function DimensionRadar({ scores }: { scores: ScoreBreakdown }) {
  const { t } = useTranslation();
  const data: RadarDataPoint[] = DIMENSION_ORDER.map((key) => ({
    dimension: t(DIMENSION_LABEL_KEYS[key])
      .replace(' Knowledge', '')
      .replace(' Solving', '\nSolving'),
    score: scores[key],
    fullMark: 10,
  }));
  const accent = useAccentColor();

  return (
    <div className="h-[260px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={data} outerRadius="72%">
          <PolarGrid stroke={GRID_STROKE} />
          <PolarAngleAxis
            dataKey="dimension"
            tick={{ fill: 'var(--chart-text)', fontSize: 12 }}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(value) => [`${Number(value ?? 0)} / 10`, 'Score']}
          />
          <Radar
            name="Score"
            dataKey="score"
            stroke={accent}
            fill={accent}
            fillOpacity={0.25}
            strokeWidth={2}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}

function StrengthItem({ text }: { text: string }) {
  return (
    <li className="flex items-start gap-2.5 rounded-[12px] bg-[var(--ui-ok-wash)] px-3.5 py-3 text-[14px] text-foreground">
      <CheckCircle2 className="mt-0.5 h-4 w-4 flex-none text-[var(--ui-ok)]" aria-hidden="true" />
      <span className="leading-relaxed">{text}</span>
    </li>
  );
}

function ImprovementCard({ item }: { item: ImprovementItem }) {
  return (
    <li className="flex items-start gap-2.5 rounded-[12px] bg-[var(--ui-warn-wash)] px-3.5 py-3 text-[14px]">
      <TrendingUp className="mt-0.5 h-4 w-4 flex-none text-[var(--ui-warn)]" aria-hidden="true" />
      <p className="leading-relaxed">
        <span className="font-semibold text-foreground">{item.area}:</span>{' '}
        <span className="text-foreground">{item.suggestion}</span>
      </p>
    </li>
  );
}

// ── Integrity (proctoring) panel ───────────────────────────────────────────────

/** Proctoring event types → translation keys for their candidate-facing labels. */
const INTEGRITY_LABEL_KEYS: Record<string, string> = {
  gaze_away: 'scorecard.evGazeAway',
  face_absent: 'scorecard.evFaceAbsent',
  multiple_faces: 'scorecard.evMultipleFaces',
  tab_blur: 'scorecard.evTabBlur',
  fullscreen_exit: 'scorecard.evFullscreenExit',
  copy: 'scorecard.evCopy',
  paste: 'scorecard.evPaste',
  second_voice: 'scorecard.evSecondVoice',
  devtools_open: 'scorecard.evDevtools',
  proctor_error: 'scorecard.evProctorError',
};

/** Integrity score ink (0–100 scale) — same thresholds as the admin console. */
function integrityColor(score: number): string {
  if (score >= 80) return 'var(--ui-ok)';
  if (score >= 60) return 'var(--ui-warn)';
  return 'var(--ui-danger)';
}

/** mm:ss offset of an event into the interview; falls back to wall-clock time. */
function fmtOffset(eventIso: string, sessionStartIso: string | null): string {
  const ev = new Date(eventIso).getTime();
  const start = sessionStartIso ? new Date(sessionStartIso).getTime() : NaN;
  if (!Number.isNaN(ev) && !Number.isNaN(start) && ev >= start) {
    const total = Math.round((ev - start) / 1000);
    const mm = String(Math.floor(total / 60)).padStart(2, '0');
    const ss = String(total % 60).padStart(2, '0');
    return `${mm}:${ss}`;
  }
  const d = new Date(eventIso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function IntegrityPanel({ report }: { report: IntegrityReport }) {
  const { t } = useTranslation();
  const timelineKey = useScopedPersistKey('scorecard-integrity-timeline');
  const score = report.integrity_score;
  const byType = report.summary?.by_type ?? {};
  const flaggedSeconds = report.summary?.flagged_seconds ?? {};
  const types = Object.keys(byType);
  const timeline = report.events;

  const labelFor = (type: string): string =>
    INTEGRITY_LABEL_KEYS[type] ? t(INTEGRITY_LABEL_KEYS[type]) : type;

  // integrity_score null = proctoring never ran; the parent hides the panel.
  if (score === null) return null;

  return (
    <SectionCard
      title={t('scorecard.integrityTitle')}
      icon={ShieldCheck}
      description={t('scorecard.integrityNote')}
    >
      <div className="mb-4 flex items-baseline gap-2">
        <span
          className="text-[30px] font-bold tabular-nums tracking-[-1px]"
          style={{ color: integrityColor(score) }}
          data-testid="integrity-score"
        >
          {score}
        </span>
        <span className="text-[13.5px] text-muted-foreground">{t('scorecard.integrityOutOf')}</span>
      </div>

      {types.length === 0 ? (
        <p className="text-[14px] font-medium text-[var(--ui-ok)]">{t('scorecard.integrityNoFlags')}</p>
      ) : (
        <div className="flex flex-col gap-2.5" aria-label={t('scorecard.integrityFlagsAria')}>
          {types.map((type) => (
            <div
              key={type}
              className="flex items-center gap-3 rounded-[12px] bg-[var(--ui-inset-soft)] p-3"
            >
              <AlertTriangle className="h-4 w-4 flex-none text-[var(--ui-warn)]" aria-hidden="true" />
              <span className="flex-1 text-[14px] text-foreground">{labelFor(type)}</span>
              <span className="font-mono text-[12px] tabular-nums text-muted-foreground">
                {byType[type]}×{flaggedSeconds[type] ? ` · ${flaggedSeconds[type]}s` : ''}
              </span>
            </div>
          ))}
        </div>
      )}

      {timeline.length > 0 && (
        <Collapsible
          className="mt-4"
          title={t('scorecard.integrityTimeline')}
          count={timeline.length}
          summary={t('scorecard.integrityTimelineSummary', { count: timeline.length })}
          persistKey={timelineKey}
          data-testid="integrity-timeline"
        >
          <ul
            className="max-h-64 space-y-1 overflow-y-auto"
            aria-label={t('scorecard.integrityTimelineAria')}
          >
            {timeline.map((ev, idx) => (
              <li
                key={idx}
                className="flex items-center justify-between gap-2 rounded-[10px] bg-[var(--ui-inset-soft)] px-3 py-2 text-[13px]"
              >
                <span className="flex items-center gap-2 text-foreground">
                  <span className="font-mono tabular-nums text-muted-foreground">
                    {fmtOffset(ev.started_at, report.session_started_at)}
                  </span>
                  {labelFor(ev.event_type)}
                </span>
                {ev.duration_seconds != null && (
                  <span className="font-mono tabular-nums text-muted-foreground">
                    {ev.duration_seconds}s
                  </span>
                )}
              </li>
            ))}
          </ul>
        </Collapsible>
      )}
    </SectionCard>
  );
}

// ── Loading skeleton ───────────────────────────────────────────────────────────

function ScorecardSkeleton() {
  const { t } = useTranslation();
  return (
    <main className="min-h-screen bg-background px-4 py-10">
      {/* Screen-reader status — preserves existing test contract */}
      <span role="status" aria-label={t('scorecard.loadingAria')} aria-live="polite" className="sr-only" />
      <div className="mx-auto max-w-[1180px] space-y-5">
        <Skeleton className="h-10 w-64 rounded-full" />
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
          <Skeleton className="h-64 rounded-card" />
          <Skeleton className="h-64 rounded-card lg:col-span-2" />
        </div>
        <Skeleton className="h-48 rounded-card" />
        <Skeleton className="h-36 rounded-card" />
      </div>
    </main>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function Scorecard() {
  const { t } = useTranslation();
  const { scorecardId } = useParams<{ scorecardId: string }>();
  const { accessToken, user } = useAuth();
  // An invited guest can read this scorecard and nothing else in the account
  // area, so the trail and the "browse roles" link are left out for them.
  const isGuest = isGuestOnly(user?.roles);

  // Live query — preserved exactly: key ['scorecard',id], enabled guard, staleTime, retry:false
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['scorecard', scorecardId],
    queryFn: () => {
      if (!scorecardId || !accessToken) {
        throw new Error('Missing scorecard ID or access token');
      }
      return getScorecard(scorecardId, accessToken);
    },
    enabled: Boolean(scorecardId) && Boolean(accessToken),
    staleTime: 10 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  // Integrity report — best-effort second query keyed off the scorecard's
  // session_id. getSessionIntegrity resolves to null on ANY error, so this can
  // never break the page; the panel simply doesn't render.
  const { data: integrity } = useQuery({
    queryKey: ['integrity', data?.session_id],
    queryFn: () => getSessionIntegrity(data!.session_id),
    enabled: Boolean(data?.session_id) && Boolean(accessToken),
    staleTime: 10 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  // Fire error toast once per distinct error transition — never on every render.
  useEffect(() => {
    if (isError) {
      toast.error(t('scorecard.loadError'));
    }
  }, [isError, t]);

  if (isLoading) return <ScorecardSkeleton />;
  if (isError || !data) {
    return (
      <ErrorState isGuest={isGuest} onRetry={() => void refetch()} retrying={isFetching} />
    );
  }

  // Scale conversion: live data is 0–10; ScoreRing expects 0–100.
  const compositeScore100 = Math.round(data.composite_score * 10);
  const verdict = t(scoreLabelKey(data.composite_score));
  const tone = scoreTone(data.composite_score);

  // https only (see downloadUrl) — a server string is never trusted as an href.
  const pdfHref = data.report_pdf_url ? downloadUrl(data.report_pdf_url) : null;
  // One primary per page (audit CA-L13): the header's link is the primary one,
  // the footer repeats it as an outline for someone who read to the end.
  const pdfLink = (variant: 'default' | 'outline') =>
    pdfHref ? (
      <a
        href={pdfHref}
        target="_blank"
        rel="noopener noreferrer"
        className={buttonVariants({ variant })}
      >
        <Download aria-hidden="true" />
        {t('scorecard.downloadPdf')}
      </a>
    ) : null;

  return (
    <main className="min-h-screen bg-background px-4 py-8">
      <div className="mx-auto max-w-[1180px] space-y-5 sm:px-2 lg:px-4">
        <PageHeader
          className="mb-0"
          breadcrumbs={
            isGuest ? undefined : (
              <Breadcrumbs
                items={[
                  { label: t('nav.dashboard'), to: '/dashboard' },
                  { label: t('nav.history'), to: '/history' },
                  { label: t('scorecard.title') },
                ]}
              />
            )
          }
          eyebrow={t('scorecard.badge')}
          title={t('scorecard.title')}
          actions={pdfLink('default')}
        />

        {/* Row 1: ScoreRing (left) + Radar (right) */}
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
          <section className="flex flex-col items-center justify-center gap-5 rounded-card border border-border bg-card p-8 text-center shadow-card">
            {/* sr-only overall label for a11y */}
            <span className="sr-only">
              {`${t('scorecard.overallScore')}: ${data.composite_score.toFixed(1)} ${t('scorecard.outOf10')}`}
            </span>

            {/* ScoreRing takes 0–100; live data is 0–10, so multiply by 10 */}
            <ScoreRing score={compositeScore100} size={160} label={t('scorecard.outOf100')} />

            <div className="flex flex-col items-center gap-2">
              <StatusTag tone={tone} dot>
                {verdict}
              </StatusTag>
              <p className="text-[13px] text-[var(--ui-soft)]">{t('scorecard.overallScore')}</p>
              {/* A score is feedback on how the interview went. It is never a
                  hiring outcome, and the page must say so where the number is.
                  A guest is reading an invited interview, not a practice one. */}
              <p data-testid="not-a-decision" className="max-w-[260px] text-[12.5px] leading-snug text-muted-foreground">
                {isGuest ? t('practice.notDecisionGuest') : t('practice.notDecision')}
              </p>
            </div>
          </section>

          {/* Radar chart — recharts over live data.scores (4 real dimensions) */}
          <SectionCard
            className="lg:col-span-2"
            title={t('scorecard.scoreBreakdown')}
            description={t('scorecard.tapForDetail')}
          >
            <DimensionRadar scores={data.scores} />
          </SectionCard>
        </div>

        {/* Competency bars with collapsible rationale accordions */}
        <SectionCard title={t('scorecard.competencyBreakdown')}>
          <div className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
            {DIMENSION_ORDER.map((key) => (
              <ScoreBarRow
                key={key}
                label={t(DIMENSION_LABEL_KEYS[key])}
                score={data.scores[key]}
                rationale={data.rationale?.[key]}
                feedback={data.axis_feedback?.[key]}
              />
            ))}
          </div>
        </SectionCard>

        {/* Strengths / Improvements */}
        {(data.strengths.length > 0 || data.improvements.length > 0) && (
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
            {data.strengths.length > 0 && (
              <SectionCard title={t('scorecard.keyStrengths')} icon={CheckCircle2}>
                <ul className="flex flex-col gap-2.5" aria-label={t('scorecard.strengthsListAria')}>
                  {data.strengths.map((strength, idx) => (
                    <StrengthItem key={idx} text={strength} />
                  ))}
                </ul>
              </SectionCard>
            )}

            {data.improvements.length > 0 && (
              <SectionCard title={t('scorecard.areasForImprovement')} icon={TrendingUp}>
                <ul className="flex flex-col gap-2.5" aria-label={t('scorecard.improvementsListAria')}>
                  {data.improvements.map((item, idx) => (
                    <ImprovementCard key={idx} item={item} />
                  ))}
                </ul>
              </SectionCard>
            )}
          </div>
        )}

        {/* Summary */}
        {data.summary && (
          <SectionCard title={t('scorecard.summary')}>
            <p className="text-[15px] leading-relaxed text-foreground">{data.summary}</p>
          </SectionCard>
        )}

        {/* Interview integrity (proctoring) */}
        {integrity && integrity.integrity_score !== null && <IntegrityPanel report={integrity} />}

        {/* CTA footer */}
        {/* A guest has no practice interviews or roles to browse, so the
            "improve your score" pitch is for account holders only (audit
            CA-M14); a guest gets just the copy to keep, when there is one. */}
        {(!isGuest || pdfHref) && (
        <div className="flex flex-wrap items-center justify-between gap-4 rounded-card border border-border bg-card p-6 shadow-card">
          <div>
            <div className="text-[17px] font-bold text-foreground">
              {isGuest ? t('scorecard.keepCopyTitle') : t('scorecard.improveTitle')}
            </div>
            <p className="mt-1 text-[14px] text-muted-foreground">
              {isGuest ? t('scorecard.keepCopyDesc') : t('scorecard.improveDesc')}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2.5">
            {!isGuest && (
              <>
                <Link to="/start" className={buttonVariants({ variant: 'outline' })}>
                  {t('scorecard.practiseAgain')}
                </Link>
                <Link to="/jobs" className={buttonVariants({ variant: 'outline' })}>
                  {t('scorecard.browseRoles')}
                </Link>
              </>
            )}
            {pdfLink('outline')}
          </div>
        </div>
        )}
      </div>
    </main>
  );
}
