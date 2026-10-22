// Dashboard — candidate home page.
//
// ORDER: greeting (no button) → the ONE next step → where the applications
// stand (only when there are any) → the practice plan and recent practice, with
// the resume beside them in the aside.
//
// The next step comes from the candidate's action queue (lib/candidateActions):
// an assessment under way, then anything waiting on them soonest first, then
// "with the hiring team", and only when there is no open application at all, the
// practice step. It never mints a link on render — the button asks for one when
// pressed. There is exactly one practice call-to-action in the body: the next
// step's own when practice is the next step, otherwise the plan card's.
//
// Data: wired 100% to live react-query feeds — no mock data rendered.
// Shell: bare content; AppShell is provided by the router (no double-wrap).

import { useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useLocation, Link, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';

import { getMe, logout } from '@/api/auth';
import { getOnboardingStatus, getPracticePlan, type OnboardingGoal } from '@/api/onboarding';
import { listSessions } from '@/api/sessions';
import { listScorecards } from '@/api/scorecard';
import { getCurrentResume, uploadResume } from '@/api/resume';
import type { MyApplication } from '@/api/applications';
import { useAuth } from '@/context/AuthContext';
import { useCandidateActions } from '@/hooks/useCandidateActions';
import { actionsForApplication, isWaitingOnYou, turnOf } from '@/lib/candidateActions';
import { stageText } from '@/lib/applicationStage';
import { toast } from '@/lib/toast';
import { formatDate, formatDuration, statusProps } from '@/lib/formatters';
import { cn } from '@/lib/utils';

import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Page, PageBody, PageHeader, EmptyState } from '@/design/components/page';
import { Card } from '@/design/components/card';
import { Row, RowList } from '@/design/components/rows';
import { Stat, StatGroup } from '@/design/components/stat-group';
import { NextStep, TurnBadge } from '@/design/components/next-step';
import { StatusTag } from '@/design/components/primitives';
import FileUploadZone from '@/components/FileUploadZone';
import PracticePlanCard from '@/components/practice/PracticePlanCard';
import CandidateNextStep from '@/components/candidate/CandidateNextStep';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  FileText,
  ListChecks,
  ChevronRight,
} from '@/design/components/icons';

import { initialsOf } from '@/design/data/shared';
import type { TagTone } from '@/design/components/primitives';

// ── Constants ──────────────────────────────────────────────────────────────────

const RESUME_MAX_BYTES = 5 * 1024 * 1024; // 5 MB
const RECENT_COUNT = 3;
/** Application rows shown on the dashboard; the rest are one "View all" away. */
const APPLICATION_ROWS = 5;

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

// ── Small shared pieces ───────────────────────────────────────────────────────

const TEXT_LINK =
  'rounded text-[13px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';

// ── Loading gate ──────────────────────────────────────────────────────────────
// Shown until we know whether this account still owes onboarding. Rendering the
// real dashboard first and redirecting after would flash a generic, empty page
// at exactly the user it is least suited to — someone who has never seen the
// product. Shape-matched to the page below so the swap is not a jolt.

function DashboardSkeleton() {
  return (
    <Page data-testid="dashboard-skeleton">
      <Skeleton aria-busy="true" className="h-[88px] w-full max-w-[560px] rounded-[16px]" />
      <Skeleton className="h-[84px] w-full rounded-card" />
      <Skeleton className="h-[200px] w-full rounded-card" />
    </Page>
  );
}

/** "Round 2 of 4" when the server has told us, otherwise the stage in the reader's language. */
function applicationMeta(app: MyApplication, t: TFunction): string {
  const where =
    app.round_number && app.total_rounds
      ? t('track.roundOf', { n: app.round_number, total: app.total_rounds })
      : stageText(t, app);
  return `${app.company_name} · ${where}`;
}

const TURN_RANK = { you: 0, them: 1, closed: 2 } as const;

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
    refetch: refetchMe,
    isFetching: meFetching,
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
  // decide what the practice step says.
  const { data: plan, isLoading: planLoading } = useQuery({
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

  // ── The action queue: applications, interviews, offers ──────────────────────
  // Loops and offers are fetched only when there is an application; no polling.
  const interviewsTaken = sessionsData?.total ?? 0;
  const planReady = plan?.ready === true;
  const queue = useCandidateActions({
    enabled: accessToken !== null,
    practice: {
      planReady,
      focusCompetency: plan?.focus_competency_name ?? null,
      interviewsTaken,
    },
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

  const allSessions = sessionsData?.items ?? [];
  const recentSessions = allSessions.slice(0, RECENT_COUNT);

  // avg composite from scorecards (backend: 0–10) — multiply ×10 for the 0–100 scale
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
  const totalPracticeSeconds = allSessions.reduce((acc, s) => acc + (s.duration_seconds ?? 0), 0);

  // Distinct languages practised in
  const distinctLanguages = new Set(allSessions.map((s) => s.language)).size;

  const hasResume = Boolean(currentResume) || Boolean(me?.has_resume);
  const firstName = (me?.full_name ?? user?.full_name ?? '').split(' ')[0] ?? '';
  const isAdmin = (me?.roles ?? user?.roles ?? []).includes('admin');

  // ── Personalization ─────────────────────────────────────────────────────────
  // `planReady` means they told us a target role, so the page can speak about
  // that job instead of pitching the product at them.
  const goalLine = onboarding?.goal ? t(GOAL_LINE_KEY[onboarding.goal]) : null;

  const heroSubtitle = (() => {
    if (!planReady || !plan) {
      return interviewsTaken > 0 ? t('dashboard.heroMomentum') : t('dashboard.heroFirstRun');
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
  // Index 0→Mon … 6→Sun (JS: getDay 0=Sun,1=Mon…6=Sat)
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
  // An account that already has applications is NOT sent there: someone who
  // activated to follow an application should land on it, not be asked what
  // job they are practising for. The redirect therefore waits for the
  // applications answer — painting the dashboard first and yanking it away, or
  // redirecting before knowing, are the two ways to get this wrong.
  if (onboardingLoading) {
    return <DashboardSkeleton />;
  }
  if (onboarding && onboarding.applicable && !onboarding.seen) {
    if (queue.isLoading) return <DashboardSkeleton />;
    // A FAILED applications call is "unknown", not "none": a real applicant must
    // not be sent into the practice wizard because a request failed.
    if (queue.applications.length === 0 && !queue.isError) {
      return <Navigate to="/onboarding" replace />;
    }
  }

  // ── Full-page error state ───────────────────────────────────────────────────
  if (isError) {
    return (
      <Page width="form">
        <div
          role="alert"
          className="flex max-w-[480px] flex-col items-center justify-center gap-4 py-16 text-center"
        >
          <AlertTriangle className="h-10 w-10 text-[var(--ui-danger)]" aria-hidden="true" />
          <p className="text-[15px] text-muted-foreground">
            {t('dashboard.failedToLoadProfile')}
          </p>
          {/* A failed /me is usually temporary: retry first, and keep signing
              out as the secondary way out (audit CA-L1). */}
          <div className="flex flex-wrap justify-center gap-2.5">
            <Button
              type="button"
              onClick={() => void refetchMe()}
              disabled={meFetching}
              aria-busy={meFetching}
            >
              {meFetching ? t('error.tryingAgain') : t('error.tryAgain')}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => logoutMutation.mutate()}
              disabled={logoutMutation.isPending}
            >
              {t('nav.logout')}
            </Button>
          </div>
        </div>
      </Page>
    );
  }

  // ── The next step ───────────────────────────────────────────────────────────
  const top = queue.actions[0];
  const waitingCount = queue.actions.filter(isWaitingOnYou).length;
  // A practice step depends on the plan and the session count; wait for both so
  // it does not read "start your first interview" and then change its mind.
  // A failed request is not an empty queue. When anything failed and nothing
  // known is waiting on the candidate, the strip says so and offers a retry
  // rather than falling through to "start practising" / "nothing needed".
  const checkFailed = queue.anyFailed && !(top && isWaitingOnYou(top));
  const nextStepReady =
    !queue.isLoading &&
    (checkFailed || !top?.kind.startsWith('practice') || (!planLoading && !sessionsLoading));
  const practiceOwnsCta = !checkFailed && (top?.kind.startsWith('practice') ?? false);

  // ── Application rows (only when there are any) ──────────────────────────────
  const appRows = queue.applications
    .map((app) => ({
      app,
      turn: turnOf(app, actionsForApplication(queue.actions, app.id)),
    }))
    .sort((a, b) => TURN_RANK[a.turn] - TURN_RANK[b.turn])
    .slice(0, APPLICATION_ROWS);

  // ── Page ────────────────────────────────────────────────────────────────────
  return (
    <Page width="default">
      {/* Greeting. No button: the page's one primary is the next step below. */}
      {isLoading ? (
        <div className="space-y-3" aria-busy="true">
          <Skeleton className="h-9 w-72 max-w-full rounded-lg" />
          <Skeleton className="h-4 w-80 max-w-full rounded" />
        </div>
      ) : (
        <PageHeader
          eyebrow={planReady && plan?.domain_label ? plan.domain_label : t('dashboard.heroEyebrow')}
          title={
            firstName
              ? t('dashboard.heroGreetingNamed', { name: firstName })
              : t('dashboard.heroGreeting')
          }
          description={
            <>
              {heroSubtitle}
              {goalLine ? (
                <span className="mt-1.5 block text-[13.5px] text-[var(--ui-soft)]">{goalLine}</span>
              ) : null}
            </>
          }
          actions={
            isAdmin ? (
              <Button type="button" variant="outline" onClick={() => void navigate('/admin/jd')}>
                {t('dashboard.adminJdUpload')}
              </Button>
            ) : undefined
          }
        />
      )}

      {/* Arrival from onboarding */}
      {justOnboarded && (
        <div className="flex items-center gap-2.5 rounded-[12px] bg-[var(--ui-ok-wash)] px-4 py-3">
          <CheckCircle2 size={16} className="shrink-0 text-[var(--ui-ok)]" aria-hidden="true" />
          <p className="text-[13.5px] text-foreground">
            <span className="font-semibold">
              {firstName
                ? t('dashboard.onboardedTitleNamed', { name: firstName })
                : t('dashboard.onboardedTitle')}
            </span>{' '}
            <span className="text-[var(--ui-soft)]">{t('dashboard.onboardedBody')}</span>
          </p>
        </div>
      )}

      {/* The one next step. */}
      {nextStepReady && checkFailed ? (
        <NextStep
          data-testid="next-step"
          tone="warn"
          eyebrow={t('nextStep.eyebrow')}
          title={t('nextStep.checkFailedTitle')}
          context={t('nextStep.checkFailedContext')}
          action={
            <Button type="button" variant="outline" onClick={() => queue.refetchFailed()}>
              {t('nextStep.checkFailedRetry')}
            </Button>
          }
        />
      ) : nextStepReady && top ? (
        <CandidateNextStep action={top} waitingCount={waitingCount} />
      ) : (
        <Skeleton className="h-[84px] w-full rounded-card" aria-busy="true" />
      )}

      <PageBody
        asideLabel={t('dashboard.resumeCardTitle')}
        aside={
          <Card
            as="section"
            titleAs="h2"
            title={t('dashboard.resumeCardTitle')}
            description={t('dashboard.resumeCardDesc')}
            actions={
              <Link to="/resume" className={cn(TEXT_LINK, 'inline-flex items-center gap-1')}>
                {t('nav.resume')}
                <ChevronRight size={14} aria-hidden="true" />
              </Link>
            }
          >
            {/* Resume status row */}
            {!resumeLoading && !meLoading && (
              <div
                className={cn(
                  'mb-4 flex items-center gap-2.5 rounded-[12px] p-3',
                  hasResume ? 'bg-[var(--ui-ok-wash)]' : 'bg-[var(--ui-inset-soft)]',
                )}
              >
                {hasResume ? (
                  <CheckCircle2
                    size={18}
                    className="shrink-0 text-[var(--ui-ok)]"
                    aria-hidden="true"
                  />
                ) : (
                  <FileText
                    size={18}
                    className="shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                )}
                <div className="min-w-0">
                  <div className="truncate text-[13.5px] font-semibold text-foreground">
                    {hasResume
                      ? (currentResume?.filename ?? t('dashboard.resumeOnFile'))
                      : t('dashboard.noResumeYet')}
                  </div>
                  {hasResume && currentResume?.uploaded_at && (
                    <div className="text-[12px] text-muted-foreground">
                      {t('dashboard.uploadedOn', {
                        date: formatDate(currentResume.uploaded_at),
                      })}
                    </div>
                  )}
                </div>
              </div>
            )}

            {(resumeLoading || meLoading) && (
              <Skeleton className="mb-4 h-14 w-full rounded-[12px]" />
            )}

            {!resumeLoading && !meLoading && (
              <FileUploadZone
                label={t('dashboard.resumeCardTitle')}
                accept="application/pdf"
                maxBytes={RESUME_MAX_BYTES}
                onUpload={handleResumeUpload}
                /* No existingFileLabel: the status well above already names the
                   resume on file (and its upload date); passing it here printed
                   the same filename a second time directly underneath. */
              />
            )}
          </Card>
        }
      >
        {/* Where each application stands: one row each, whose move it is. */}
        {appRows.length > 0 && (
          <Card
            as="section"
            titleAs="h2"
            data-testid="dashboard-applications"
            title={t('track.yourApplications')}
            actions={
              <Link to="/applications" className={TEXT_LINK}>
                {t('track.viewAll')} →
              </Link>
            }
          >
            <RowList className="-my-3">
              {appRows.map(({ app, turn }) => (
                <Row
                  key={app.id}
                  to="/applications"
                  title={app.job_title}
                  meta={applicationMeta(app, t)}
                  trailing={<TurnBadge turn={turn} label={t(`turn.${turn}`)} />}
                />
              ))}
            </RowList>
          </Card>
        )}

        {/* The practice plan, built from THEIR role: the work to do next. */}
        <PracticePlanCard
          hideStart={practiceOwnsCta}
          hidePersonalize={top?.kind === 'practice_target'}
          thisWeek={weekDaysHit}
        />

        {/* The numbers, once, next to the interviews they summarise. Not drawn
            at all until there is something to count. */}
        {!statsLoading && (
          <StatGroup
            aria-label={t('practice.summaryAria')}
            missingLabel={t('practice.notAvailable')}
            data-testid="practice-stats"
          >
            <Stat
              label={t('dashboard.statInterviews')}
              value={interviewsTaken > 0 ? interviewsTaken : null}
            />
            <Stat label={t('dashboard.statAvgScore')} value={avgScore0to100} unit="/ 100" />
            <Stat label={t('dashboard.statBestScore')} value={bestScore0to100} unit="/ 100" />
            <Stat
              label={t('dashboard.statPracticeTime')}
              value={totalPracticeSeconds > 0 ? formatDuration(totalPracticeSeconds) : null}
            />
            <Stat
              label={t('dashboard.statLanguagesUsed')}
              value={distinctLanguages > 0 ? distinctLanguages : null}
            />
          </StatGroup>
        )}

        <Card
          as="section"
          titleAs="h2"
          emphasis="raised"
          title={t('dashboard.recentInterviewsTitle')}
          actions={
            <Link to="/history" className={TEXT_LINK}>
              {t('dashboard.viewAllHistory')} →
            </Link>
          }
        >
          {sessionsLoading ? (
            <div className="flex flex-col gap-2.5" aria-busy="true">
              <Skeleton className="h-14 w-full rounded-[12px]" />
              <Skeleton className="h-14 w-full rounded-[12px]" />
              <Skeleton className="h-14 w-full rounded-[12px]" />
            </div>
          ) : recentSessions.length === 0 ? (
            // No button here: the page already has its one practice call to action.
            <EmptyState bordered={false} icon={ListChecks} title={t('practice.recentEmpty')} />
          ) : (
            <RowList className="-my-3" aria-label={t('dashboard.recentInterviewsTitle')}>
              {recentSessions.map((session) => {
                const { label } = statusProps(session.status);
                return (
                  <Row
                    key={session.session_id}
                    data-testid={`recent-session-${session.session_id}`}
                    to={session.scorecard_id ? `/scorecard/${session.scorecard_id}` : undefined}
                    leading={
                      <span
                        className="flex h-10 w-10 flex-none items-center justify-center rounded-full border border-border bg-[var(--ui-inset)] text-[13px] font-bold text-[var(--ui-soft)]"
                        aria-hidden="true"
                      >
                        {initialsOf(session.job_title)}
                      </span>
                    }
                    title={session.job_title}
                    meta={
                      <span className="flex flex-wrap items-center gap-2">
                        <StatusTag tone={sessionTagTone(session.status)} dot>
                          {label}
                        </StatusTag>
                        <span className="flex items-center gap-1">
                          <Clock size={12} aria-hidden="true" />
                          {formatDuration(session.duration_seconds)}
                        </span>
                      </span>
                    }
                    trailing={
                      <span className="text-[12.5px] text-muted-foreground">
                        {formatDate(session.created_at)}
                      </span>
                    }
                  />
                );
              })}
            </RowList>
          )}
        </Card>
      </PageBody>
    </Page>
  );
}
