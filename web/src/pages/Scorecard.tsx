// Scorecard — full-screen results page (outside AppShell).
// Route: /scorecard/:scorecardId
//
// Design: new dark GlassCard layout from @/design/components/primitives.
// Data: getScorecard query (live, 0–10 scale); ScoreRing receives score*10 (0–100).
// Must-preserve: query, skeleton, ErrorState+toast, rationale accordions,
// recharts radar, strengths/improvements/summary, PDF gate, back-nav, all t() keys.

import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useAccentColor } from '@/lib/useAccentColor';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  Download,
  CheckCircle2,
  AlertTriangle,
  TrendingUp,
  LayoutDashboard,
  ChevronDown,
  Info,
  History,
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
import { getScorecard } from '@/api/scorecard';
import type { ScoreBreakdown, ImprovementItem } from '@/api/scorecard';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import {
  GlassCard,
  ScoreRing,
  StatusTag,
  type TagTone,
} from '@/design/components/primitives';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';

// ── Inline skeleton (avoids @/components/ui/skeleton — shadcn forbidden on this page) ──

function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse bg-white/[0.06]', className)} />;
}

// ── Animation ─────────────────────────────────────────────────────────────────

const fadeDown = {
  hidden: { opacity: 0, y: -14 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.45, ease: [0.22, 1, 0.36, 1] as const } },
};

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

/** Convert a 0–10 score to a hex colour for the competency bars. */
function scoreHexColor(score: number): string {
  if (score >= 8) return '#27c93f';
  if (score >= 6) return 'var(--accent)';
  if (score >= 4) return '#ffb764';
  return '#e6714f';
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function ErrorState() {
  const { t } = useTranslation();
  return (
    <main className="min-h-screen bg-[#09090b] flex items-center justify-center px-4">
      <div
        role="alert"
        className="rounded-[24px] border border-white/[0.08] bg-[#0f0f10] p-8 max-w-md w-full text-center space-y-5"
      >
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-[rgba(230,113,79,0.16)] mx-auto">
          <AlertTriangle className="h-6 w-6 text-[#e6714f]" aria-hidden="true" />
        </div>
        <div>
          <p className="text-[16px] font-semibold text-white">
            {t('scorecard.notAvailableTitle')}
          </p>
          <p className="mt-1.5 text-[13.5px] text-[#888b91]">
            {t('scorecard.notAvailableDesc')}
          </p>
        </div>
        <Link
          to="/history"
          className="inline-flex items-center gap-2 rounded-pill border border-white/10 bg-white/[0.06] px-5 py-2.5 text-[14px] font-semibold text-white hover:bg-white/[0.1] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          {t('scorecard.backToHistory')}
        </Link>
      </div>
    </main>
  );
}

// ScoreBarRow — collapsible rationale accordion (aria-expanded / aria-controls preserved).
function ScoreBarRow({
  label,
  score,
  rationale,
}: {
  label: string;
  score: number;
  rationale?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const pct = Math.round((score / 10) * 100);
  const panelId = `rationale-${label.replace(/\s+/g, '-').toLowerCase()}`;
  const hasRationale = Boolean(rationale && rationale.trim());
  const hexColor = scoreHexColor(score);

  return (
    <div className="space-y-2">
      {/* Clickable header — "why this score" toggle */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
        className={cn(
          'w-full flex items-center justify-between gap-3 rounded-[12px] px-3 py-2 text-left',
          'transition-colors hover:bg-white/[0.05]',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
        )}
      >
        <span className="flex items-center gap-1.5 text-[13.5px] font-medium text-[#b8babf]">
          {label}
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 text-[#5a5f66] transition-transform duration-200',
              open && 'rotate-180',
            )}
            aria-hidden="true"
          />
        </span>
        <span
          className="flex-none rounded-pill px-2.5 py-0.5 text-[12px] font-semibold tabular-nums"
          style={{ color: hexColor, background: `${hexColor}1a` }}
        >
          {score} / 10
        </span>
      </button>

      {/* Gradient progress bar */}
      <div className="h-2 overflow-hidden rounded-full bg-white/[0.07]" aria-label={`${label}: ${score} out of 10`}>
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{
            width: `${pct}%`,
            background: `linear-gradient(90deg,var(--accent),${hexColor})`,
          }}
        />
      </div>

      {/* Rationale panel */}
      {open && (
        <div
          id={panelId}
          className="mt-1 rounded-[14px] border border-white/[0.08] bg-[rgba(var(--accent-rgb),0.06)] px-4 py-3"
        >
          <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-[#60a5fa]">
            <Info className="h-3.5 w-3.5" aria-hidden="true" />
            {t('scorecard.whyThisScore')}
          </p>
          <p className="text-[13px] leading-relaxed text-[#888b91]">
            {hasRationale ? rationale : t('scorecard.rationaleUnavailable')}
          </p>
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

// Recharts radar — dark theme, live 4-dimension data (0–10 scale preserved).
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
          <PolarGrid stroke="rgba(255,255,255,0.08)" />
          <PolarAngleAxis
            dataKey="dimension"
            tick={{ fill: '#b8babf', fontSize: 11 }}
          />
          <Tooltip
            contentStyle={{
              background: '#1c1c1e',
              border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: '10px',
              fontSize: 12,
              color: '#f5f5f7',
            }}
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
    <li className="flex items-start gap-2.5 text-[13.5px] text-[#b8babf]">
      <CheckCircle2 className="h-4 w-4 text-[#27c93f] flex-none mt-0.5" aria-hidden="true" />
      <span className="leading-relaxed">{text}</span>
    </li>
  );
}

function ImprovementCard({ item }: { item: ImprovementItem }) {
  return (
    <li className="flex items-start gap-2.5 text-[13.5px]">
      <TrendingUp className="h-4 w-4 text-[#ffb764] flex-none mt-0.5" aria-hidden="true" />
      <p className="leading-relaxed">
        <span className="font-semibold text-white">{item.area}:</span>{' '}
        <span className="text-[#b8babf]">{item.suggestion}</span>
      </p>
    </li>
  );
}

// ── Loading skeleton ───────────────────────────────────────────────────────────

function ScorecardSkeleton() {
  return (
    <main className="min-h-screen bg-[#09090b] py-10 px-4">
      {/* Screen-reader status — preserves existing test contract */}
      <span
        role="status"
        aria-label="Loading scorecard"
        aria-live="polite"
        className="sr-only"
      />
      <div className="max-w-[1180px] mx-auto space-y-5">
        <Skeleton className="h-10 w-64 rounded-full bg-white/[0.06]" />
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
          <Skeleton className="h-64 rounded-[24px] bg-white/[0.06]" />
          <Skeleton className="h-64 rounded-[24px] bg-white/[0.06] lg:col-span-2" />
        </div>
        <Skeleton className="h-48 rounded-[24px] bg-white/[0.06]" />
        <Skeleton className="h-36 rounded-[24px] bg-white/[0.06]" />
      </div>
    </main>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function Scorecard() {
  const { t } = useTranslation();
  const { scorecardId } = useParams<{ scorecardId: string }>();
  const { accessToken } = useAuth();

  // Live query — preserved exactly: key ['scorecard',id], enabled guard, staleTime, retry:false
  const { data, isLoading, isError } = useQuery({
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

  // Fire error toast once per distinct error transition — never on every render.
  useEffect(() => {
    if (isError) {
      toast.error('Could not load scorecard. Please try again.');
    }
  }, [isError]);

  if (isLoading) return <ScorecardSkeleton />;
  if (isError || !data) return <ErrorState />;

  // Scale conversion: live data is 0–10; ScoreRing expects 0–100.
  const compositeScore100 = Math.round(data.composite_score * 10);
  const verdict = t(scoreLabelKey(data.composite_score));
  const tone = scoreTone(data.composite_score);

  return (
    <main className="min-h-screen bg-[#09090b] py-8 px-4">
      <div className="mx-auto max-w-[1180px] px-2 lg:px-4">

        {/* ── Page header ─────────────────────────────────────────────── */}
        <motion.header
          initial="hidden"
          animate="visible"
          variants={fadeDown}
          className="mb-7"
        >
          {/* Back navigation — history + dashboard links */}
          <nav className="flex items-center gap-3 mb-5" aria-label="Breadcrumb">
            <Link
              to="/history"
              className="inline-flex items-center gap-1.5 rounded-pill border border-white/[0.08] bg-white/[0.04] px-3.5 py-1.5 text-[13px] text-[#888b91] hover:bg-white/[0.08] hover:text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
              {t('nav.history')}
            </Link>
            <span className="h-4 w-px bg-white/10" aria-hidden="true" />
            <Link
              to="/dashboard"
              className="inline-flex items-center gap-1.5 rounded-pill border border-white/[0.08] bg-white/[0.04] px-3.5 py-1.5 text-[13px] text-[#888b91] hover:bg-white/[0.08] hover:text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              <LayoutDashboard className="h-3.5 w-3.5" aria-hidden="true" />
              {t('nav.dashboard')}
            </Link>
          </nav>

          {/* Role + badge breadcrumb line */}
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-[13px] text-[#888b91]">
                <History className="h-3.5 w-3.5" aria-hidden="true" />
                {t('scorecard.badge')}
              </div>
              <h1 className="mt-1 text-[26px] font-semibold tracking-[-0.5px] text-white">
                {t('scorecard.title')}
              </h1>
            </div>

            {/* PDF download — only when url is present (conditional preserved) */}
            {data.report_pdf_url && (
              <a
                href={data.report_pdf_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-pill bg-white px-5 py-2.5 text-[14px] font-semibold text-black hover:bg-[#eaeaea] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                <Download className="h-4 w-4" aria-hidden="true" />
                {t('scorecard.downloadPdf')}
              </a>
            )}
          </div>
        </motion.header>

        {/* ── Row 1: ScoreRing (left) + Radar (right) ──────────────────── */}
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
          {/* Score + verdict card */}
          <Reveal dir="left">
            <GlassCard feature className="flex h-full flex-col items-center justify-center gap-5 text-center p-8">
              {/* sr-only overall label for a11y */}
              <span className="sr-only">
                {`${t('scorecard.overallScore')}: ${data.composite_score.toFixed(1)} ${t('scorecard.outOf10')}`}
              </span>

              {/* ScoreRing takes 0–100; live data is 0–10, so multiply by 10 */}
              <ScoreRing
                score={compositeScore100}
                size={160}
                label={t('scorecard.outOf10')}
              />

              <div className="flex flex-col items-center gap-2">
                <StatusTag tone={tone} dot>
                  {verdict}
                </StatusTag>
                <p className="text-[12px] text-[#9fb6d6]">
                  {t('scorecard.overallScore')}
                </p>
              </div>
            </GlassCard>
          </Reveal>

          {/* Radar chart — recharts over live data.scores (4 real dimensions) */}
          <Reveal className="lg:col-span-2">
            <GlassCard className="h-full p-5">
              <h3 className="mb-2 text-[15px] font-semibold text-white">
                {t('scorecard.scoreBreakdown')}
              </h3>
              <p className="mb-3 text-[12.5px] text-[#888b91]">{t('scorecard.tapForDetail')}</p>
              <DimensionRadar scores={data.scores} />
            </GlassCard>
          </Reveal>
        </div>

        {/* ── Competency bars with collapsible rationale accordions ──────── */}
        <Reveal className="mt-5">
          <GlassCard className="p-5">
            <h3 className="mb-4 text-[15px] font-semibold text-white">
              {/* "Competency breakdown" — design-only label, no existing t() key */}
              Competency breakdown
            </h3>
            <Stagger className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {DIMENSION_ORDER.map((key) => (
                <StaggerItem key={key}>
                  <ScoreBarRow
                    label={t(DIMENSION_LABEL_KEYS[key])}
                    score={data.scores[key]}
                    rationale={data.rationale?.[key]}
                  />
                </StaggerItem>
              ))}
            </Stagger>
          </GlassCard>
        </Reveal>

        {/* ── Strengths / Improvements ──────────────────────────────────── */}
        {(data.strengths.length > 0 || data.improvements.length > 0) && (
          <div className="mt-5 grid grid-cols-1 gap-5 md:grid-cols-2">
            {/* Strengths — conditional on data */}
            {data.strengths.length > 0 && (
              <Reveal dir="left">
                <GlassCard className="h-full p-5">
                  <h3 className="mb-3 flex items-center gap-2 text-[15px] font-semibold text-white">
                    <CheckCircle2 className="h-4 w-4 text-[#27c93f]" aria-hidden="true" />
                    {t('scorecard.keyStrengths')}
                  </h3>
                  <ul className="flex flex-col gap-2.5" aria-label="Key strengths list">
                    {data.strengths.map((strength, idx) => (
                      <StrengthItem key={idx} text={strength} />
                    ))}
                  </ul>
                </GlassCard>
              </Reveal>
            )}

            {/* Improvements — conditional on data */}
            {data.improvements.length > 0 && (
              <Reveal dir="right">
                <GlassCard className="h-full p-5">
                  <h3 className="mb-3 flex items-center gap-2 text-[15px] font-semibold text-white">
                    <TrendingUp className="h-4 w-4 text-[#ffb764]" aria-hidden="true" />
                    {t('scorecard.areasForImprovement')}
                  </h3>
                  <ul className="flex flex-col gap-3" aria-label="Areas for improvement list">
                    {data.improvements.map((item, idx) => (
                      <ImprovementCard key={idx} item={item} />
                    ))}
                  </ul>
                </GlassCard>
              </Reveal>
            )}
          </div>
        )}

        {/* ── Summary ───────────────────────────────────────────────────── */}
        {data.summary && (
          <Reveal className="mt-5">
            <GlassCard className="p-5">
              <h3 className="mb-3 text-[15px] font-semibold text-white">
                {t('scorecard.summary')}
              </h3>
              <p className="text-[13.5px] leading-relaxed text-[#b8babf]">
                {data.summary}
              </p>
            </GlassCard>
          </Reveal>
        )}

        {/* ── CTA footer ────────────────────────────────────────────────── */}
        <Reveal className="mt-6">
          <div className="flex flex-wrap items-center justify-between gap-4 rounded-[24px] border border-white/[0.08] bg-[#0f0f10] p-5">
            <div>
              {/* "Want to improve your score?" — design-only label, no existing t() key */}
              <div className="text-[15px] font-semibold text-white">Want to improve your score?</div>
              {/* "Retake or try a different role." — design-only label */}
              <p className="text-[13px] text-[#888b91]">Retake the interview or try a different role.</p>
            </div>
            <div className="flex items-center gap-2.5">
              {/* "Browse roles" anchor styled as a ghost Pill — Link wrapping a button is invalid HTML */}
              <Link
                to="/jobs"
                className="inline-flex items-center justify-center gap-2 rounded-[9999px] px-5 py-2.5 text-[13px] font-semibold bg-white/[0.06] text-white border border-white/10 hover:bg-white/[0.1] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                Browse roles
              </Link>
              {data.report_pdf_url && (
                <a
                  href={data.report_pdf_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center justify-center gap-2 rounded-[9999px] px-5 py-2.5 text-[13px] font-semibold bg-white text-black hover:bg-[#eaeaea] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                >
                  <Download className="h-3.5 w-3.5" aria-hidden="true" />
                  {t('scorecard.downloadPdf')}
                </a>
              )}
            </div>
          </div>
        </Reveal>

      </div>
    </main>
  );
}
