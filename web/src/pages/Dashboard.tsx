// Dashboard — candidate home page.
// Layout: two-column hero + 4-up stats + two-column recent/next-steps.
// Data: wired 100% to the 4 live react-query feeds — no mock data rendered.
// Shell: bare content; AppShell is provided by the router (no double-wrap).

import { useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { getMe, logout } from '@/api/auth';
import { listSessions } from '@/api/sessions';
import { listScorecards } from '@/api/scorecard';
import { getCurrentResume, uploadResume } from '@/api/resume';
import { useAuth } from '@/context/AuthContext';
import { toast } from '@/lib/toast';
import { formatDate, formatDuration, statusProps } from '@/lib/formatters';
import { cn } from '@/lib/utils';

import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import {
  GlassCard,
  StatCard,
  ScoreRing,
  Pill,
  StatusTag,
  Avatar,
} from '@/design/components/primitives';
import FileUploadZone from '@/components/FileUploadZone';
import { PromoBanner, TrustStrip } from '@/design/components/banners';
import {
  ArrowRight,
  Mic,
  Briefcase,
  FileText,
  ListChecks,
  CheckCircle2,
  AlertTriangle,
  Clock,
  ExternalLink,
  Sparkles,
  Target,
  Flame,
  ChevronRight,
  Languages,
  ShieldCheck,
} from '@/design/components/icons';

import { gradientFor, initialsOf, scoreColor } from '@/design/data/shared';
import type { TagTone } from '@/design/components/primitives';

// ── Inline skeleton — avoids @/components/ui/* shadcn dep ────────────────────

function Sk({ className }: { className?: string }) {
  return (
    <div
      className={cn('animate-pulse rounded-md bg-white/[0.06]', className)}
      aria-hidden="true"
    />
  );
}

// ── Constants ──────────────────────────────────────────────────────────────────

const RESUME_MAX_BYTES = 5 * 1024 * 1024; // 5 MB
const RECENT_COUNT = 3;

// ── Nudge tone → icon mapping ─────────────────────────────────────────────────

const NUDGE_ICON = {
  electric: Sparkles,
  amber: Target,
  forest: Flame,
} as const;

type NudgeTone = keyof typeof NUDGE_ICON;

const NUDGE_COLOR: Record<NudgeTone, string> = {
  electric: 'text-[#60a5fa]',
  amber: 'text-[#ffb764]',
  forest: 'text-[#27c93f]',
};

// ── Status → StatusTag tone ────────────────────────────────────────────────────

function sessionTagTone(status: string): TagTone {
  switch (status) {
    case 'completed':
      return 'forest';
    case 'in_progress':
      return 'electric';
    case 'abandoned':
      return 'neutral';
    case 'failed':
      return 'ember';
    default:
      return 'neutral';
  }
}

// ── Days-of-week labels ───────────────────────────────────────────────────────

const DOW_LABELS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'] as const;

// ── Dashboard ─────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { accessToken, user, clearAuth } = useAuth();
  const queryClient = useQueryClient();

  // ── Query: profile ──────────────────────────────────────────────────────────
  const {
    data: me,
    isLoading: meLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => getMe(accessToken ?? undefined),
    enabled: accessToken !== null,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  // ── Query: sessions ─────────────────────────────────────────────────────────
  const { data: sessionsData, isLoading: sessionsLoading } = useQuery({
    queryKey: ['sessions', { page: 1, perPage: RECENT_COUNT }],
    queryFn: () => listSessions({ page: 1, perPage: RECENT_COUNT }),
    staleTime: 2 * 60 * 1000,
    retry: false,
  });

  // ── Query: scorecards ───────────────────────────────────────────────────────
  const { data: scorecardsData, isLoading: scorecardsLoading } = useQuery({
    queryKey: ['scorecards', { page: 1, perPage: 20 }],
    queryFn: () => listScorecards({ page: 1, perPage: 20 }),
    staleTime: 2 * 60 * 1000,
    retry: false,
  });

  // ── Query: current resume ───────────────────────────────────────────────────
  const { data: currentResume, isLoading: resumeLoading } = useQuery({
    queryKey: ['resume', 'current'],
    queryFn: getCurrentResume,
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  // ── Logout mutation ─────────────────────────────────────────────────────────
  const logoutMutation = useMutation({
    mutationFn: () => logout(),
    onSettled: () => {
      queryClient.clear();
      clearAuth();
      void navigate('/login', { replace: true });
    },
    onError: () => {
      toast.error(t('error.generic'));
    },
  });

  // ── Resume upload handler ────────────────────────────────────────────────────
  const handleResumeUpload = useCallback(
    (file: File, onProgress: (pct: number) => void) => {
      if (!accessToken) return Promise.reject(new Error('No access token'));
      return uploadResume(file, accessToken, onProgress).then((result) => {
        void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
        void queryClient.invalidateQueries({ queryKey: ['resume', 'current'] });
        return { text_length: result.text_length };
      });
    },
    [accessToken, queryClient],
  );

  // ── Derived values ──────────────────────────────────────────────────────────

  const isLoading = meLoading;
  const statsLoading = sessionsLoading || scorecardsLoading || resumeLoading;

  const interviewsTaken = sessionsData?.total ?? 0;
  const recentSessions = (sessionsData?.items ?? []).slice(0, RECENT_COUNT);

  // avg composite from scorecards (backend: 0–10) — multiply ×10 for ScoreRing (0–100)
  const avgScore0to100 = (() => {
    const items = scorecardsData?.items ?? [];
    const valid = items.filter((s) => s.composite_score !== null);
    if (valid.length === 0) return null;
    const sum = valid.reduce((acc, s) => acc + (s.composite_score ?? 0), 0);
    return Math.round((sum / valid.length) * 10);
  })();

  // Best single composite (0–100 scale)
  const bestScore0to100 = (() => {
    const items = scorecardsData?.items ?? [];
    const valid = items.filter((s) => s.composite_score !== null);
    if (valid.length === 0) return null;
    return Math.round(Math.max(...valid.map((s) => s.composite_score ?? 0)) * 10);
  })();

  // Total practice time from recent sessions (sum of duration_seconds)
  const totalPracticeSeconds = recentSessions.reduce(
    (acc, s) => acc + (s.duration_seconds ?? 0),
    0,
  );
  const practiceTimeLabel = totalPracticeSeconds > 0
    ? formatDuration(totalPracticeSeconds)
    : '—';

  // Distinct languages across recent sessions
  const distinctLanguages = new Set(recentSessions.map((s) => s.language)).size;

  const hasResume = Boolean(currentResume) || Boolean(me?.has_resume);
  const firstName = (me?.full_name ?? user?.full_name ?? '').split(' ')[0] ?? '';
  const isAdmin = (me?.roles ?? user?.roles ?? []).includes('admin');

  // Readiness ring: avg composite ×10 (0–100) when available
  const readinessScore = avgScore0to100 ?? 0;

  // Weekly streak — derive from recentSessions created_at
  // DOW_LABELS maps index 0→Mon … 6→Sun (JS: getDay 0=Sun,1=Mon…6=Sat)
  const todayMidnight = (() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
  })();
  const weekDaysHit = new Set<number>();
  recentSessions.forEach((s) => {
    const d = new Date(s.created_at);
    const jsDay = d.getDay(); // 0=Sun
    const monBased = jsDay === 0 ? 6 : jsDay - 1; // 0=Mon…6=Sun
    const diff = Math.floor((todayMidnight.getTime() - d.setHours(0, 0, 0, 0)) / 86400000);
    if (diff >= 0 && diff < 7) weekDaysHit.add(monBased);
  });

  // ── Full-page error state ───────────────────────────────────────────────────
  if (isError) {
    return (
      <div role="alert" className="flex flex-col items-center justify-center py-24 gap-4">
        <AlertTriangle className="h-10 w-10 text-[#e6714f]" aria-hidden="true" />
        <p className="text-[14px] text-[#888b91]">
          {error instanceof Error ? error.message : t('dashboard.failedToLoadProfile')}
        </p>
        <Pill
          variant="danger"
          type="button"
          onClick={() => logoutMutation.mutate()}
        >
          {t('dashboard.returnToLogin')}
        </Pill>
      </div>
    );
  }

  // ── Page ────────────────────────────────────────────────────────────────────
  return (
    <div className="mx-auto max-w-[1200px] px-6 py-8 lg:px-8 space-y-5">

      {/* ── Row 0: Brand promo banner + trust chips ── */}
      <Reveal>
        <PromoBanner
          tone="aurora"
          badge="Voice-first"
          eyebrow="AI Interview Studio"
          title="Practice like it's real. Walk in ready."
          subtitle="Talk to a lifelike AI interviewer in your language, then get a competency scorecard in minutes — not days. The more you practise, the higher your readiness climbs."
          cta={{ label: 'Start a mock interview', to: '/start' }}
          icon={Mic}
          dismissId="candidate-hero-v1"
        />
      </Reveal>
      <TrustStrip
        className="px-0.5"
        items={[
          { icon: Mic, label: 'Voice-first' },
          { icon: Languages, label: '22 Indian languages' },
          { icon: Sparkles, label: 'Instant AI scorecard' },
          { icon: ShieldCheck, label: 'DPDP-compliant' },
        ]}
      />

      {/* ── Row 1: Hero + Readiness (2-col) ── */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1.4fr_1fr]">

        {/* LEFT: Hero GlassCard — electric gradient */}
        <Reveal dir="left">
          <GlassCard
            feature
            className="flex h-full flex-col justify-between gap-6 min-h-[200px]"
          >
            <div>
              <p className="text-[12px] font-semibold uppercase tracking-[0.1em] text-[#60a5fa] mb-2">
                Welcome back
              </p>
              {isLoading ? (
                <>
                  <Sk className="h-9 w-72 rounded-lg mb-2" />
                  <Sk className="h-4 w-80 rounded" />
                </>
              ) : (
                <>
                  <h1
                    className="font-semibold tracking-[-1px] text-white"
                    style={{ fontSize: 'clamp(28px, 4vw, 40px)' }}
                  >
                    {`Let's get you hired${firstName ? `, ${firstName}` : ''}.`}
                  </h1>
                  <p className="mt-2 text-[14px] text-[#9fb6d6] max-w-[480px]">
                    {interviewsTaken > 0
                      ? 'You’re building momentum — one more mock interview keeps your readiness climbing.'
                      : 'Start your first mock interview to see your readiness score.'}
                  </p>
                </>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <Pill type="button" onClick={() => void navigate('/start')}>
                {t('dashboard.startInterview')}
              </Pill>
              <Pill
                variant="ghost"
                type="button"
                onClick={() => void navigate('/jobs')}
              >
                {t('dashboard.browseJobs')}
              </Pill>
              {isAdmin && (
                <Pill
                  variant="outline"
                  type="button"
                  onClick={() => void navigate('/admin/jd')}
                >
                  {t('dashboard.adminJdUpload')}
                </Pill>
              )}
            </div>
          </GlassCard>
        </Reveal>

        {/* RIGHT: Interview readiness card */}
        <Reveal dir="right">
          <GlassCard className="flex h-full flex-col gap-4">
            <h3 className="text-[15px] font-semibold">
              {t('dashboard.readinessTitle')}
            </h3>

            <div className="flex items-center gap-5 flex-1">
              {/* Ring */}
              <div className="shrink-0">
                {statsLoading ? (
                  <Sk className="h-[120px] w-[120px] rounded-full" />
                ) : (
                  <ScoreRing
                    score={readinessScore}
                    size={120}
                    label={t('dashboard.ringLabel')}
                  />
                )}
              </div>

              {/* Right of ring */}
              <div className="flex flex-col gap-2 min-w-0">
                <p className="text-[12.5px] text-[#9fb6d6]">
                  {statsLoading
                    ? t('app.loading')
                    : interviewsTaken > 0
                      ? t('dashboard.readinessDesc', { count: interviewsTaken })
                      : t('dashboard.readinessDescNoData')}
                </p>
                <Link
                  to="/resume"
                  className="inline-flex items-center gap-1 text-[12.5px] text-[#60a5fa] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded w-fit"
                >
                  Improve resume →
                </Link>
                <Link
                  to="/history"
                  className="inline-flex items-center gap-1 text-[12.5px] text-[#60a5fa] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded w-fit"
                >
                  {t('dashboard.seeBreakdown')}
                  <ArrowRight size={13} aria-hidden="true" />
                </Link>
              </div>
            </div>
          </GlassCard>
        </Reveal>
      </div>

      {/* ── Row 2: 4-up StatCards ── */}
      <Stagger className="grid grid-cols-2 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {/* 1 — Interviews taken */}
        <StaggerItem>
          <StatCard
            label={t('dashboard.statInterviews')}
            value={statsLoading ? '—' : String(interviewsTaken)}
            trend="flat"
            className="h-full"
          />
        </StaggerItem>

        {/* 2 — Best score */}
        <StaggerItem>
          <StatCard
            label={t('dashboard.statBestScore')}
            value={
              statsLoading
                ? '—'
                : bestScore0to100 !== null
                  ? `${bestScore0to100}`
                  : '—'
            }
            delta={bestScore0to100 !== null ? '/ 100' : undefined}
            trend={
              bestScore0to100 !== null && bestScore0to100 >= 70 ? 'up' : 'flat'
            }
            className="h-full"
          />
        </StaggerItem>

        {/* 3 — Practice time */}
        <StaggerItem>
          <StatCard
            label="practice time"
            value={statsLoading ? '—' : practiceTimeLabel}
            trend="flat"
            className="h-full"
          />
        </StaggerItem>

        {/* 4 — Languages used */}
        <StaggerItem>
          <StatCard
            label="languages used"
            value={statsLoading ? '—' : String(distinctLanguages)}
            trend="flat"
            className="h-full"
          />
        </StaggerItem>
      </Stagger>

      {/* ── Row 3: Recent interviews + Next steps/Resume (2-col) ── */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1.6fr_1fr]">

        {/* LEFT: Recent interviews */}
        <Reveal dir="left">
          <GlassCard className="p-5">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-[15px] font-semibold">
                {t('dashboard.recentInterviewsTitle')}
              </h3>
              <Link
                to="/history"
                className="text-[12.5px] text-[#60a5fa] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded"
              >
                {t('dashboard.viewAllHistory')} →
              </Link>
            </div>

            {sessionsLoading ? (
              <div className="flex flex-col gap-2.5">
                <Sk className="h-16 w-full rounded-[12px]" />
                <Sk className="h-16 w-full rounded-[12px]" />
                <Sk className="h-16 w-full rounded-[12px]" />
              </div>
            ) : recentSessions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 text-center gap-3">
                <ListChecks className="h-8 w-8 text-[#888b91]/40" aria-hidden="true" />
                <p className="text-[13.5px] text-[#888b91]">
                  {t('dashboard.recentInterviewsEmpty')}
                </p>
                <button
                  type="button"
                  onClick={() => void navigate('/start')}
                  className="inline-flex items-center gap-1.5 text-[12.5px] text-[#60a5fa] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded"
                >
                  <Mic size={13} aria-hidden="true" />
                  {t('dashboard.startInterview')}
                </button>
              </div>
            ) : (
              <ul
                className="flex flex-col gap-2.5"
                aria-label={t('dashboard.recentInterviewsTitle')}
              >
                {recentSessions.map((session) => {
                  const { label } = statusProps(session.status);
                  const tone = sessionTagTone(session.status);
                  const initials = initialsOf(session.job_title);
                  const seedNum =
                    session.session_id.charCodeAt(0) +
                    session.session_id.charCodeAt(1);
                  const gradient = gradientFor(seedNum);
                  const scoreForColor =
                    session.scorecard_id !== null ? 72 : 0;

                  return (
                    <li
                      key={session.session_id}
                      data-testid={`recent-session-${session.session_id}`}
                    >
                      {session.scorecard_id ? (
                        <Link
                          to={`/scorecard/${session.scorecard_id}`}
                          className="flex items-center gap-3 rounded-[12px] border border-white/[0.07] bg-white/[0.02] p-3.5 transition-colors hover:border-[rgba(var(--accent-rgb),0.4)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                        >
                          <Avatar
                            initials={initials}
                            gradient={gradient}
                            size={36}
                          />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-[13.5px] font-medium">
                              {session.job_title}
                            </div>
                            <div className="mt-0.5 flex items-center gap-2 flex-wrap">
                              <StatusTag tone={tone} dot className="mt-0.5">
                                {label}
                              </StatusTag>
                              <span className="flex items-center gap-1 text-[12px] text-[#888b91]">
                                <Clock size={11} aria-hidden="true" />
                                {formatDuration(session.duration_seconds)}
                              </span>
                            </div>
                          </div>
                          <div className="text-right shrink-0">
                            <div
                              className="text-[13px] font-semibold"
                              style={{ color: scoreColor(scoreForColor) }}
                            >
                              {formatDate(session.created_at)}
                            </div>
                            <ExternalLink
                              size={13}
                              className="ml-auto mt-0.5 text-[#70757c]"
                              aria-hidden="true"
                            />
                          </div>
                        </Link>
                      ) : (
                        <div className="flex items-center gap-3 rounded-[12px] border border-white/[0.07] bg-white/[0.02] p-3.5">
                          <Avatar
                            initials={initials}
                            gradient={gradient}
                            size={36}
                          />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-[13.5px] font-medium">
                              {session.job_title}
                            </div>
                            <div className="mt-0.5 flex items-center gap-2 flex-wrap">
                              <StatusTag tone={tone} dot className="mt-0.5">
                                {label}
                              </StatusTag>
                              <span className="flex items-center gap-1 text-[12px] text-[#888b91]">
                                <Clock size={11} aria-hidden="true" />
                                {formatDuration(session.duration_seconds)}
                              </span>
                            </div>
                          </div>
                          <div className="shrink-0 text-[12px] text-[#888b91]">
                            {formatDate(session.created_at)}
                          </div>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </GlassCard>
        </Reveal>

        {/* RIGHT: Next steps + Resume stacked */}
        <div className="flex flex-col gap-5">

          {/* Next steps (nudges) */}
          <Reveal dir="right">
            <GlassCard className="p-5">
              <h3 className="mb-3 text-[15px] font-semibold">Next steps</h3>

              {/* Weekly streak strip */}
              <div className="mb-4">
                <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#70757c] mb-2">
                  This week
                </p>
                <div className="flex items-center gap-1.5" aria-hidden="true">
                  {DOW_LABELS.map((lbl, i) => (
                    <div key={i} className="flex flex-col items-center gap-1">
                      <span
                        className={cn(
                          'h-7 w-7 rounded-[8px] flex items-center justify-center text-[10px] font-semibold transition-colors',
                          weekDaysHit.has(i)
                            ? 'bg-[rgba(var(--accent-rgb),0.22)] text-[#60a5fa] border border-[rgba(var(--accent-rgb),0.4)]'
                            : 'bg-white/[0.04] text-[#70757c] border border-white/[0.06]',
                        )}
                      >
                        {lbl}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="flex flex-col gap-2.5">
                {/* Resume nudge — only when no resume on file */}
                {!statsLoading && !hasResume && (() => {
                  const Icon = NUDGE_ICON.amber;
                  return (
                    <div className="flex items-start gap-3 rounded-[12px] border border-white/[0.07] bg-white/[0.02] p-3.5">
                      <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-white/[0.05]">
                        <Icon size={16} className={NUDGE_COLOR.amber} aria-hidden="true" />
                      </span>
                      <div className="min-w-0">
                        <div className="text-[13.5px] font-medium">
                          {t('dashboard.nudgeResumeTitle')}
                        </div>
                        <div className="text-[12.5px] text-[#888b91]">
                          {t('dashboard.nudgeResumeBody')}
                        </div>
                      </div>
                    </div>
                  );
                })()}

                {/* Practice nudge — shown when interviews > 0 */}
                {!statsLoading && interviewsTaken > 0 && (() => {
                  const tone: NudgeTone =
                    avgScore0to100 !== null && avgScore0to100 >= 70 ? 'forest' : 'electric';
                  const Icon = NUDGE_ICON[tone];
                  return (
                    <div className="flex items-start gap-3 rounded-[12px] border border-white/[0.07] bg-white/[0.02] p-3.5">
                      <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-white/[0.05]">
                        <Icon size={16} className={NUDGE_COLOR[tone]} aria-hidden="true" />
                      </span>
                      <div className="min-w-0">
                        <div className="text-[13.5px] font-medium">
                          {t('dashboard.nudgePracticeTitle')}
                        </div>
                        <div className="text-[12.5px] text-[#888b91]">
                          {t('dashboard.nudgePracticeBody')}
                        </div>
                      </div>
                    </div>
                  );
                })()}

                {/* First interview nudge — when no interviews yet */}
                {!statsLoading && interviewsTaken === 0 && (() => {
                  const Icon = NUDGE_ICON.electric;
                  return (
                    <div className="flex items-start gap-3 rounded-[12px] border border-white/[0.07] bg-white/[0.02] p-3.5">
                      <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-white/[0.05]">
                        <Icon size={16} className={NUDGE_COLOR.electric} aria-hidden="true" />
                      </span>
                      <div className="min-w-0">
                        <div className="text-[13.5px] font-medium">
                          {t('dashboard.nudgeFirstTitle')}
                        </div>
                        <div className="text-[12.5px] text-[#888b91]">
                          {t('dashboard.nudgeFirstBody')}
                        </div>
                      </div>
                    </div>
                  );
                })()}

                {/* Jobs CTA — always visible */}
                <div className="flex items-start gap-3 rounded-[12px] border border-white/[0.07] bg-white/[0.02] p-3.5">
                  <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-white/[0.05]">
                    <Briefcase size={16} className="text-[#888b91]" aria-hidden="true" />
                  </span>
                  <div className="min-w-0">
                    <div className="text-[13.5px] font-medium">
                      {t('dashboard.nudgeJobsTitle')}
                    </div>
                    <div className="text-[12.5px] text-[#888b91]">
                      <Link
                        to="/jobs"
                        className="text-[#60a5fa] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded"
                      >
                        {t('dashboard.browseJobs')}
                      </Link>
                    </div>
                  </div>
                </div>
              </div>
            </GlassCard>
          </Reveal>

          {/* Resume card */}
          <Reveal dir="right" delay={0.08}>
            <GlassCard className="p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h3 className="text-[15px] font-semibold">
                    {t('dashboard.resumeCardTitle')}
                  </h3>
                  <p className="mt-0.5 text-[12.5px] text-[#888b91]">
                    {t('dashboard.resumeCardDesc')}
                  </p>
                </div>
                <Link
                  to="/resume"
                  className="inline-flex items-center gap-1 text-[12.5px] text-[#60a5fa] hover:underline shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded"
                >
                  {t('nav.resume')}
                  <ChevronRight size={13} aria-hidden="true" />
                </Link>
              </div>

              {/* Resume status row */}
              {!resumeLoading && !meLoading && (
                <div
                  className={cn(
                    'mb-4 flex items-center gap-2.5 rounded-[12px] border p-3',
                    hasResume
                      ? 'border-[rgba(39,201,63,0.25)] bg-[rgba(39,201,63,0.06)]'
                      : 'border-white/[0.07] bg-white/[0.02]',
                  )}
                >
                  {hasResume ? (
                    <CheckCircle2
                      size={18}
                      className="shrink-0 text-[#27c93f]"
                      aria-hidden="true"
                    />
                  ) : (
                    <FileText
                      size={18}
                      className="shrink-0 text-[#888b91]"
                      aria-hidden="true"
                    />
                  )}
                  <div className="min-w-0">
                    <div className="text-[13px] font-medium">
                      {hasResume
                        ? (currentResume?.filename ?? t('dashboard.resumeOnFile'))
                        : t('dashboard.noResumeYet')}
                    </div>
                    {hasResume && currentResume?.uploaded_at && (
                      <div className="text-[11.5px] text-[#888b91]">
                        {t('dashboard.uploadedOn', {
                          date: formatDate(currentResume.uploaded_at),
                        })}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {(resumeLoading || meLoading) && (
                <Sk className="mb-4 h-14 w-full rounded-[12px]" />
              )}

              {!resumeLoading && !meLoading && (
                <FileUploadZone
                  label={t('dashboard.resumeCardTitle')}
                  accept="application/pdf"
                  maxBytes={RESUME_MAX_BYTES}
                  onUpload={handleResumeUpload}
                  existingFileLabel={
                    hasResume
                      ? (currentResume?.filename ?? t('dashboard.resumeOnFile'))
                      : undefined
                  }
                />
              )}
            </GlassCard>
          </Reveal>
        </div>
      </div>
    </div>
  );
}
