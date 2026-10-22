// InterviewComplete — shown after the interview session ends.
//
// Polls for the scorecard linked to this session:
//   1. If the sessionId is known, calls listSessions to find the scorecard_id.
//   2. Once scorecard_id is found, redirects to /scorecard/:id.
//   3. After SCORECARD_POLL_TIMEOUT_MS, stops polling and shows a timeout state
//      with a manual "Check again" retry and a back-to-dashboard CTA.
//
// Visual: a calm centred card with the 4-step "preparing" list
// (presentation only — real redirect source of truth is the poll, not the steps).
// The early-exit branch renders a warm warning card.
// Bare content inside AppShell — no shell is imported here.

import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  CheckCircle2,
  Clock,
  LayoutDashboard,
  ArrowRight,
  RefreshCw,
  XCircle,
  Sparkles,
} from '@/design/components/icons';
import { listSessions } from '@/api/sessions';
import { useAuth } from '@/context/AuthContext';
import { isGuestOnly } from '@/lib/guest';
import { SUPPORT_EMAIL } from '@/lib/contact';
import { cn } from '@/lib/utils';
import { Button, buttonVariants } from '@/components/ui/button';
import { StatusTag } from '@/design/components/primitives';
import { Breadcrumbs } from '@/design/components/page';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 3_000;
const SCORECARD_POLL_TIMEOUT_MS = 90_000;

/** Animated steps shown while the scorecard is preparing (presentation-only). */
const PREPARING_STEP_KEYS = [
  'interviewComplete.stepTranscribing',
  'interviewComplete.stepScoring',
  'interviewComplete.stepBenchmarking',
  'interviewComplete.stepGenerating',
] as const;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface LocationState {
  message?: string;
  /** True when the candidate clicked "End session" before the normal completion event. */
  endedEarly?: boolean;
}

// ---------------------------------------------------------------------------
// Small presentational sub-components
// ---------------------------------------------------------------------------

/** Single step row rendered while polling for the scorecard. The state reads
 *  from the icon's shape (check / spinner / empty ring), not its colour. */
function StepRow({
  label,
  state,
}: {
  label: string;
  state: 'pending' | 'active' | 'done';
}) {
  return (
    <li
      className={cn(
        'flex items-center gap-3 rounded-[12px] px-4 py-3 transition-colors',
        state === 'done'
          ? 'bg-[var(--ui-ok-wash)]'
          : state === 'active'
            ? 'bg-[var(--ui-info-wash)]'
            : 'bg-[var(--ui-inset-soft)]',
      )}
    >
      <span
        className={cn(
          'flex-none',
          state === 'done'
            ? 'text-[var(--ui-ok)]'
            : state === 'active'
              ? 'text-[var(--ui-info)]'
              : 'text-muted-foreground',
        )}
        aria-hidden="true"
      >
        {state === 'done' ? (
          <CheckCircle2 size={18} />
        ) : state === 'active' ? (
          <span className="block h-4 w-4 animate-spin rounded-full border-2 border-[var(--ui-line-strong)] border-t-[var(--accent)]" />
        ) : (
          <span className="block h-4 w-4 rounded-full border border-[var(--ui-line-strong)]" />
        )}
      </span>
      <span
        className={cn(
          'text-[14px]',
          state === 'pending' ? 'text-muted-foreground' : 'font-medium text-foreground',
        )}
      >
        {label}
      </span>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function InterviewComplete() {
  const { t } = useTranslation();
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  // A candidate invited by link has no dashboard, history or self-serve
  // interviews — the server refuses a guest token on all three — so this page
  // offers them only what they can open: the scorecard, when it is ready.
  const { user } = useAuth();
  const isGuest = isGuestOnly(user?.roles);

  const state = location.state as LocationState | null;
  const message = state?.message ?? t('interviewComplete.defaultMessage');
  const endedEarly = state?.endedEarly === true;

  // ── Polling state ──────────────────────────────────────────────────────────
  const [pollingActive, setPollingActive] = useState(!endedEarly);
  const [hasTimedOut, setHasTimedOut] = useState(false);
  const startTimeRef = useRef(Date.now());

  // ── Animated step state (presentation) ────────────────────────────────────
  // Advances through PREPARING_STEP_KEYS while polling is live.
  // Deliberately does NOT gate the redirect — the real poll does.
  const [visualStep, setVisualStep] = useState(0);

  // Poll listSessions to find this session's scorecard_id (normal path only)
  const { data: sessionsData } = useQuery({
    queryKey: ['sessions', 'complete-poll', sessionId],
    queryFn: () => listSessions({ page: 1, perPage: 20 }),
    enabled: !endedEarly && pollingActive && !!sessionId,
    refetchInterval: pollingActive ? POLL_INTERVAL_MS : false,
    staleTime: 0,
    retry: false,
  });

  const thisSession = sessionsData?.items.find((s) => s.session_id === sessionId);
  const scorecardId = thisSession?.scorecard_id ?? null;

  // Redirect when scorecard_id arrives (source of truth)
  useEffect(() => {
    if (!scorecardId) return;
    setPollingActive(false);
    void navigate(`/scorecard/${scorecardId}`, { replace: true });
  }, [scorecardId, navigate]);

  // 90 s timeout guard
  useEffect(() => {
    if (!pollingActive) return;
    const remaining = SCORECARD_POLL_TIMEOUT_MS - (Date.now() - startTimeRef.current);
    const timerId = setTimeout(() => {
      setPollingActive(false);
      setHasTimedOut(true);
    }, Math.max(0, remaining));
    return () => clearTimeout(timerId);
  }, [pollingActive]);

  // Advance the visual step every ~900 ms, cycling slowly
  useEffect(() => {
    if (!pollingActive) return;
    if (visualStep >= PREPARING_STEP_KEYS.length) return;
    const id = setTimeout(() => setVisualStep((s) => s + 1), 900);
    return () => clearTimeout(id);
  }, [pollingActive, visualStep]);

  // Reset visual steps when polling is re-enabled after a timeout retry
  function handleCheckAgain() {
    startTimeRef.current = Date.now();
    setVisualStep(0);
    setHasTimedOut(false);
    setPollingActive(true);
  }

  const isPreparing = !endedEarly && pollingActive && !scorecardId;

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex min-h-[calc(100vh-4rem)] items-center justify-center px-4 py-12">
      <div className="w-full max-w-[480px]">
        {/* History is reached through this trail, so it is not repeated below. */}
        {!isGuest && (
          <Breadcrumbs
            className="mb-6"
            items={[
              { label: t('nav.history'), to: '/history' },
              { label: t('interviewComplete.crumb') },
            ]}
          />
        )}

        {/* ── Hero icon ──────────────────────────────────────────────────── */}
        <div className="flex justify-center">
          <span
            className={cn(
              'flex h-20 w-20 items-center justify-center rounded-full',
              endedEarly
                ? 'bg-[var(--ui-warn-wash)] text-[var(--ui-warn)]'
                : 'bg-highlight text-highlight-foreground',
            )}
          >
            {endedEarly ? (
              <XCircle size={38} aria-hidden="true" />
            ) : (
              <CheckCircle2 size={38} aria-hidden="true" />
            )}
          </span>
        </div>

        {/* ── Heading + subtitle ─────────────────────────────────────────── */}
        <div className="mt-6 text-center">
          <h1 className="text-[28px] font-bold tracking-[-0.6px] text-foreground">
            {endedEarly ? t('interviewComplete.titleEarly') : t('interviewComplete.title')}
          </h1>
          <p className="mt-2 text-[15px] leading-relaxed text-muted-foreground">{message}</p>
          {/* The scorecard that follows is feedback, not an outcome. Said here
              too, before the number appears. Not shown after an early exit,
              where no feedback is coming. */}
          {!endedEarly && (
            <p data-testid="not-a-decision" className="mt-1.5 text-[13px] text-muted-foreground">
              {isGuest ? t('practice.notDecisionGuest') : t('practice.notDecision')}
            </p>
          )}

          {sessionId && (
            <p className="mt-1.5 break-all font-mono text-[12px] text-muted-foreground">
              {t('interviewComplete.sessionLabel', { id: sessionId })}
            </p>
          )}
        </div>

        <div className="mt-8 flex flex-col gap-3">
          {/* ── Early-exit card ─────────────────────────────────────────── */}
          {endedEarly && (
            <div
              data-testid="early-exit-card"
              className="rounded-card border border-border bg-card p-5 text-center shadow-card"
            >
              <div className="flex flex-col items-center gap-2" aria-live="polite">
                <StatusTag tone="amber" dot>
                  {t('interviewComplete.earlyExitTitle')}
                </StatusTag>
                <p className="mt-1 text-[14px] text-muted-foreground">
                  {/* A guest has no "new interview" to start (audit CA-H5). */}
                  {isGuest
                    ? t('interviewComplete.earlyExitDescGuest', { supportEmail: SUPPORT_EMAIL })
                    : t('interviewComplete.earlyExitDesc')}
                </p>
              </div>
            </div>
          )}

          {/* ── Scorecard section (normal completion only) ──────────────── */}
          {!endedEarly && (
            <>
              {isPreparing && (
                <section className="rounded-card border border-border bg-card p-5 shadow-card">
                  <div className="mb-4 flex items-center justify-between" aria-live="polite">
                    <h2 className="text-[15px] font-bold text-foreground">
                      {t('interviewComplete.preparingScorecard')}
                    </h2>
                    <span
                      className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--ui-line-strong)] border-t-[var(--accent)]"
                      aria-hidden="true"
                    />
                  </div>

                  {/* 4-step preparing list (presentation) */}
                  <ol className="flex flex-col gap-2">
                    {PREPARING_STEP_KEYS.map((key, i) => {
                      const stepState =
                        i < visualStep ? 'done' : i === visualStep ? 'active' : 'pending';
                      return <StepRow key={key} label={t(key)} state={stepState} />;
                    })}
                  </ol>

                  <p className="mt-4 text-center text-[13px] text-muted-foreground">
                    {t('interviewComplete.scorecardDesc')}
                  </p>
                </section>
              )}

              {hasTimedOut && (
                <div className="rounded-card border border-border bg-card p-5 text-center shadow-card">
                  <div className="flex flex-col items-center gap-3" aria-live="polite">
                    <Clock size={30} className="text-muted-foreground" aria-hidden="true" />
                    <div>
                      <p className="text-[15px] font-semibold text-foreground">
                        {t('interviewComplete.scorecardTimeout')}
                      </p>
                      <p className="mt-1 text-[14px] text-muted-foreground">
                        {/* A guest cannot open interview history. */}
                        {isGuest
                          ? t('interviewComplete.scorecardTimeoutDescGuest')
                          : t('interviewComplete.scorecardTimeoutDesc')}
                      </p>
                    </div>
                    <Button type="button" variant="outline" onClick={handleCheckAgain}>
                      <RefreshCw aria-hidden="true" />
                      {t('interviewComplete.checkAgain')}
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}

          {/* ── Navigation CTAs ─────────────────────────────────────────── */}
          {!isGuest && (
            <>
              <Link to="/dashboard" className={cn(buttonVariants({ size: 'lg' }), 'w-full')}>
                <LayoutDashboard aria-hidden="true" />
                {t('interviewComplete.backToDashboard')}
              </Link>

              <Link to="/start" className={cn(buttonVariants({ variant: 'outline' }), 'w-full')}>
                <ArrowRight aria-hidden="true" />
                {t('interviewComplete.newInterview')}
              </Link>
            </>
          )}

          {/* Shown when the visual steps are done but the real redirect hasn't
              fired yet (edge: fast poll hit between step 4 and redirect).
              Presentation only — the real navigate() fires the moment
              scorecardId arrives. */}
          {!endedEarly && visualStep >= PREPARING_STEP_KEYS.length && !hasTimedOut && (
            <div className="flex justify-center pt-1">
              <span
                className="inline-flex items-center gap-2 rounded-full bg-[var(--ui-info-wash)] px-6 py-3 text-[14px] font-semibold text-[var(--ui-info)] motion-safe:animate-pulse"
                aria-live="polite"
              >
                <Sparkles size={15} aria-hidden="true" />
                {t('interviewComplete.redirecting')}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
