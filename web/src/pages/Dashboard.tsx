// Dashboard — candidate home page.
// Layout: two-column hero + 4-up stats + two-column recent/next-steps.
// Data: wired 100% to the 4 live react-query feeds — no mock data rendered.
// Shell: bare content; AppShell is provided by the router (no double-wrap).

import { useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useLocation, Link, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { getMe, logout } from '@/api/auth';
import {
  getOnboardingStatus,
  getPracticePlan,
  type OnboardingGoal,
} from '@/api/onboarding';
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
import PracticePlanCard from '@/components/practice/PracticePlanCard';
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

import { gradientFor, initialsOf } from '@/design/data/shared';
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

/** How many sessions the aggregate widgets are computed over.
 *
 * The feed under "Recent interviews" shows three rows, but practice time,
 * languages used and the weekly strip are lifetime/7-day facts and three rows
 * cannot answer either — they used to, and under-reported by an order of
 * magnitude. 100 is the server's `per_page` ceiling (interview_core
 * sessions.py), one request, and comfortably past any self-serve history; past
 * that the time tile becomes a floor rather than a total, which is still the
 * right direction to be wrong in.
 */
const STATS_COUNT = 100;

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

// ── Goal → hero copy ──────────────────────────────────────────────────────────
// The wizard asks why they're practising; this is where that answer earns its
// keep. "Campus placement in three weeks" and "just getting comfortable" want
// very different encouragement on the page they see every day.

const GOAL_LINE_KEY: Record<OnboardingGoal, string> = {
  campus_placement: 'dashboard.goalCampusPlacement',
  first_job: 'dashboard.goalFirstJob',
  switching_field: 'dashboard.goalSwitchingField',
  interview_soon: 'dashboard.goalInterviewSoon',
  general_practice: 'dashboard.goalGeneralPractice',
};

// ── Loading gate ──────────────────────────────────────────────────────────────
// Shown until we know whether this account still owes onboarding. Rendering the
// real dashboard first and redirecting after would flash a generic, empty page
// at exactly the user it is least suited to — someone who has never seen the
// product. Shape-matched to the page below so the swap is not a jolt.

function DashboardSkeleton() {
  return (
    <div className="mx-auto max-w-[1200px] px-6 py-8 lg:px-8 space-y-5" aria-busy="true">
      <Sk className="h-[132px] w-full rounded-[16px]" />
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1.4fr_1fr]">
        <Sk className="h-[200px] w-full rounded-[16px]" />
        <Sk className="h-[200px] w-full rounded-[16px]" />
      </div>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Sk className="h-[92px] rounded-[16px]" />
        <Sk className="h-[92px] rounded-[16px]" />
        <Sk className="h-[92px] rounded-[16px]" />
        <Sk className="h-[92px] rounded-[16px]" />
      </div>
    </div>
  );
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { accessToken, user, clearAuth } = useAuth();
  const queryClient = useQueryClient();

  // Set by the onboarding handoff so the first dashboard reads as the end of
  // that flow rather than an unrelated page.
  const justOnboarded =
    (location.state as { justOnboarded?: boolean } | null)?.justOnboarded === true;

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

  // ── Query: onboarding status (drives the first-login redirect) ──────────────
  // retry:false — a 403 (privileged account with no self-serve plan) is a
  // permanent answer, and retrying it just delays the dashboard.
  const { data: onboarding, isLoading: onboardingLoading } = useQuery({
    queryKey: ['onboarding-status'],
    queryFn: getOnboardingStatus,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  // ── Query: practice plan ────────────────────────────────────────────────────
  // Same key PracticePlanCard uses, so this is one request served twice from
  // cache — not a second round-trip. Read here to personalize the hero and to
  // decide whether the acquisition banner still has a job to do.
  const { data: plan } = useQuery({
    queryKey: ['practice-plan'],
    queryFn: getPracticePlan,
    staleTime: 60_000,
    retry: false,
  });

  // ── Query: sessions ─────────────────────────────────────────────────────────
  // One page serves both the 3-row feed and every aggregate on the page. Two
  // queries (3 rows + 100 rows) would be two round-trips for a strict subset.
  const { data: sessionsData, isLoading: sessionsLoading } = useQuery({
    queryKey: ['sessions', { page: 1, perPage: STATS_COUNT }],
    queryFn: () => listSessions({ page: 1, perPage: STATS_COUNT }),
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
  const allSessions = sessionsData?.items ?? [];
  const recentSessions = allSessions.slice(0, RECENT_COUNT);

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

  // Total practice time (sum of duration_seconds) over the whole fetched history
  const totalPracticeSeconds = allSessions.reduce(
    (acc, s) => acc + (s.duration_seconds ?? 0),
    0,
  );
  const practiceTimeLabel = totalPracticeSeconds > 0
    ? formatDuration(totalPracticeSeconds)
    : '—';

  // Distinct languages practised in
  const distinctLanguages = new Set(allSessions.map((s) => s.language)).size;

  const hasResume = Boolean(currentResume) || Boolean(me?.has_resume);
  const firstName = (me?.full_name ?? user?.full_name ?? '').split(' ')[0] ?? '';
  const isAdmin = (me?.roles ?? user?.roles ?? []).includes('admin');

  // ── Personalization ─────────────────────────────────────────────────────────
  // `planReady` means they told us a target role, so the page can speak about
  // that job instead of pitching the product at them.
  const planReady = plan?.ready === true;
  const goalLine = onboarding?.goal ? t(GOAL_LINE_KEY[onboarding.goal]) : null;

  const heroSubtitle = (() => {
    if (!planReady || !plan) {
      return interviewsTaken > 0
        ? t('dashboard.heroMomentum')
        : t('dashboard.heroFirstRun');
    }
    // plan.interviews_completed, not interviewsTaken: the sessions feed counts
    // every session, the plan counts the ones that produced a scorecard. A
    // sentence about the plan must use the plan's own number or it can claim
    // "your weakest area is X" off zero scored interviews.
    if (plan.interviews_completed === 0) {
      return t('dashboard.heroPlanReady', {
        role: plan.target_role,
        count: plan.competencies.length,
      });
    }
    if (plan.focus_competency_name) {
      return t('dashboard.heroFocus', {
        role: plan.target_role,
        competency: plan.focus_competency_name,
      });
    }
    return t('dashboard.heroKeepGoing', { role: plan.target_role });
  })();

  // Weekly streak — derived from the full session history, not the 3-row feed:
  // three rows can light at most three of the seven days, which silently
  // under-rewards exactly the behaviour the strip exists to reinforce.
  // DOW_LABELS maps index 0→Mon … 6→Sun (JS: getDay 0=Sun,1=Mon…6=Sat)
  const todayMidnight = (() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
  })();
  const weekDaysHit = new Set<number>();
  allSessions.forEach((s) => {
    const d = new Date(s.created_at);
    const jsDay = d.getDay(); // 0=Sun
    const monBased = jsDay === 0 ? 6 : jsDay - 1; // 0=Mon…6=Sun
    const diff = Math.floor((todayMidnight.getTime() - d.setHours(0, 0, 0, 0)) / 86400000);
    if (diff >= 0 && diff < 7) weekDaysHit.add(monBased);
  });

  // ── First-login personalization redirect ────────────────────────────────────
  // Placed here rather than in Login/GoogleCallback because both land
  // self-serve users on /dashboard, and patching every navigate() site would
  // drift the moment a fourth auth path is added. (Register skips the round
  // trip and goes straight to /onboarding, since a self-registered account is
  // always a candidate.) `applicable` is false for HR/admin, and `seen` flips
  // on both complete AND skip, so this fires exactly once per account and
  // never for a privileged user.
  //
  // The gate below is what makes the redirect invisible: without it the whole
  // generic dashboard paints first and is then yanked away.
  if (onboardingLoading) {
    return <DashboardSkeleton />;
  }
  if (onboarding && onboarding.applicable && !onboarding.seen) {
    return <Navigate to="/onboarding" replace />;
  }

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

      {/* ── Row 0: arrival from onboarding ── */}
      {justOnboarded && (
        <Reveal>
          <div className="flex items-center gap-2.5 rounded-[12px] border border-[rgba(39,201,63,0.25)] bg-[rgba(39,201,63,0.06)] px-4 py-3">
            <CheckCircle2 size={16} className="shrink-0 text-[#27c93f]" aria-hidden="true" />
            <p className="text-[13px]">
              <span className="font-medium">
                {firstName
                  ? t('dashboard.onboardedTitleNamed', { name: firstName })
                  : t('dashboard.onboardedTitle')}
              </span>{' '}
              <span className="text-[#9fb6d6]">{t('dashboard.onboardedBody')}</span>
            </p>
          </div>
        </Reveal>
      )}

      {/* ── Row 0.5: personal practice plan (self-serve users) ──
          Above the fold once it exists: a plan built from THEIR role outranks
          a banner pitching a product they have already bought into. */}
      <Reveal>
        <PracticePlanCard />
      </Reveal>

      {/* ── Row 1: Brand promo banner — only while there is nothing personal to
          show. Once a plan exists this is the least useful thing on the page. ── */}
      {!planReady && (
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
      )}
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
                {planReady && plan?.domain_label
                  ? plan.domain_label
                  : t('dashboard.heroEyebrow')}
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
                    {firstName
                      ? t('dashboard.heroGreetingNamed', { name: firstName })
                      : t('dashboard.heroGreeting')}
                  </h1>
                  <p className="mt-2 text-[14px] text-[#9fb6d6] max-w-[480px]">
                    {heroSubtitle}
                  </p>
                  {goalLine && (
                    <p className="mt-1.5 text-[12.5px] text-[#70757c] max-w-[480px]">
                      {goalLine}
                    </p>
                  )}
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

        {/* RIGHT: Average-score card.
            Named for what it is — the unweighted mean of scored composites.
            The practice plan card above shows the server's competency-weighted
            `readiness`; calling both "readiness" put two different numbers
            under one word 200px apart. */}
        <Reveal dir="right">
          <GlassCard className="flex h-full flex-col gap-4">
            <h3 className="text-[15px] font-semibold">
              {t('dashboard.avgScoreTitle')}
            </h3>

            <div className="flex items-center gap-5 flex-1">
              {/* Ring */}
              <div className="shrink-0">
                {statsLoading ? (
                  <Sk className="h-[120px] w-[120px] rounded-full" />
                ) : avgScore0to100 === null ? (
                  // No scored interview yet. A ring at 0 is not "no data", it
                  // is a failing grade — say nothing instead of saying zero.
                  <div
                    className="flex h-[120px] w-[120px] flex-col items-center justify-center rounded-full border border-white/[0.08]"
                    aria-label={t('dashboard.readinessDescNoData')}
                  >
                    <span className="text-[28px] font-semibold tracking-[-1px] text-[#70757c]">
                      —
                    </span>
                    <span className="text-[10px] uppercase tracking-[1px] text-[#70757c]">
                      {t('dashboard.avgScoreRingLabel')}
                    </span>
                  </div>
                ) : (
                  <ScoreRing
                    score={avgScore0to100}
                    size={120}
                    label={t('dashboard.avgScoreRingLabel')}
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
                  {t('dashboard.improveResume')} →
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
            label={t('dashboard.statPracticeTime')}
            value={statsLoading ? '—' : practiceTimeLabel}
            trend="flat"
            className="h-full"
          />
        </StaggerItem>

        {/* 4 — Languages used */}
        <StaggerItem>
          <StatCard
            label={t('dashboard.statLanguagesUsed')}
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
                            {/* Neutral, like the unscored row below: this is a
                                date, and the semantic score palette here used
                                to paint every scored session the same "good"
                                accent off a hardcoded 72. */}
                            <div className="text-[13px] font-semibold text-[#888b91]">
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
              <h3 className="mb-3 text-[15px] font-semibold">
                {t('dashboard.nextStepsTitle')}
              </h3>

              {/* Weekly streak strip */}
              <div className="mb-4">
                <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#70757c] mb-2">
                  {t('dashboard.thisWeekTitle')}
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
