// InterviewComplete — shown after the interview session ends.
//
// Polls for the scorecard linked to this session:
//   1. If the sessionId is known, calls listSessions to find the scorecard_id.
//   2. Once scorecard_id is found, redirects to /scorecard/:id.
//   3. After SCORECARD_POLL_TIMEOUT_MS, stops polling and shows a timeout state
//      with a manual "Check again" retry and a back-to-dashboard CTA.
//
// Visual: design-kit dark-glass treatment with the 4-step "preparing" animation
// (presentation only — real redirect source of truth is the poll, not the steps).
// The early-exit branch renders an amber warning card.
// Bare content inside AppShell — no shell/AuroraField imported here.

import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { motion } from 'framer-motion';
import {
  CheckCircle2,
  Clock,
  LayoutDashboard,
  History,
  ArrowRight,
  RefreshCw,
  XCircle,
  Sparkles,
} from '@/design/components/icons';
import { listSessions } from '@/api/sessions';
import { cn } from '@/lib/utils';
import {
  GlassCard,
  StatusTag,
} from '@/design/components/primitives';
import { staggerParent, staggerChild, springSoft } from '@/design/lib/motion';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 3_000;
const SCORECARD_POLL_TIMEOUT_MS = 90_000;

/** Animated steps shown while the scorecard is preparing (presentation-only). */
const PREPARING_STEPS = [
  'Transcribing your answers',
  'Scoring competencies',
  'Benchmarking against role',
  'Generating scorecard',
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

/** Single step row rendered while polling for the scorecard. */
function StepRow({
  label,
  state,
}: {
  label: string;
  state: 'pending' | 'active' | 'done';
}) {
  return (
    <motion.div
      variants={staggerChild}
      className={cn(
        'flex items-center gap-3 rounded-[12px] border px-4 py-3 transition-colors',
        state === 'done'
          ? 'border-[rgba(39,201,63,0.22)] bg-[rgba(39,201,63,0.04)]'
          : state === 'active'
            ? 'border-[rgba(var(--accent-rgb),0.22)] bg-[rgba(var(--accent-rgb),0.04)]'
            : 'border-white/[0.06] bg-white/[0.01]',
      )}
    >
      <span
        className={cn(
          'flex-none',
          state === 'done'
            ? 'text-[#27c93f]'
            : state === 'active'
              ? 'text-[#60a5fa]'
              : 'text-[#5a5f66]',
        )}
        aria-hidden="true"
      >
        {state === 'done' ? (
          <CheckCircle2 size={17} />
        ) : state === 'active' ? (
          <span className="block h-[15px] w-[15px] animate-spin rounded-full border-2 border-white/15 border-t-[#60a5fa]" />
        ) : (
          <span className="block h-[15px] w-[15px] rounded-full border border-white/15" />
        )}
      </span>
      <span
        className={cn(
          'text-[13.5px]',
          state === 'pending' ? 'text-[#70757c]' : 'text-white',
        )}
      >
        {label}
      </span>
    </motion.div>
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

  const state = location.state as LocationState | null;
  const message = state?.message ?? t('interviewComplete.defaultMessage');
  const endedEarly = state?.endedEarly === true;

  // ── Polling state ──────────────────────────────────────────────────────────
  const [pollingActive, setPollingActive] = useState(!endedEarly);
  const [hasTimedOut, setHasTimedOut] = useState(false);
  const startTimeRef = useRef(Date.now());

  // ── Animated step state (presentation) ────────────────────────────────────
  // Advances through PREPARING_STEPS while polling is live.
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
    if (visualStep >= PREPARING_STEPS.length) return;
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
      <div className="w-full max-w-[460px]">
        {/* ── Hero icon ──────────────────────────────────────────────────── */}
        <div className="flex justify-center">
          <motion.span
            className={cn(
              'flex h-20 w-20 items-center justify-center rounded-full',
              endedEarly
                ? 'border border-[rgba(255,183,100,0.3)] bg-[rgba(255,183,100,0.1)]'
                : 'border border-[rgba(var(--accent-rgb),0.25)] bg-[linear-gradient(135deg,rgba(var(--accent-rgb),0.18),rgba(168,135,220,0.18))]',
            )}
            initial={{ scale: 0.7, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={springSoft}
          >
            {endedEarly ? (
              <XCircle size={38} className="text-[#ffb764]" aria-hidden="true" />
            ) : (
              <CheckCircle2 size={38} className="text-[#60a5fa]" aria-hidden="true" />
            )}
          </motion.span>
        </div>

        {/* ── Heading + subtitle ─────────────────────────────────────────── */}
        <motion.div
          className="mt-6 text-center"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, ease: [0.2, 0.7, 0.2, 1], delay: 0.08 }}
        >
          <h1 className="text-[26px] font-semibold tracking-[-0.8px] text-white">
            {endedEarly ? t('interviewComplete.titleEarly') : t('interviewComplete.title')}
          </h1>
          <p className="mt-2 text-[14px] leading-relaxed text-[#888b91]">{message}</p>

          {sessionId && (
            <p className="mt-1.5 font-mono text-[11.5px] text-[#5a5f66] break-all">
              {t('interviewComplete.sessionLabel', { id: sessionId })}
            </p>
          )}
        </motion.div>

        <motion.div
          className="mt-8 flex flex-col gap-3"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, ease: [0.2, 0.7, 0.2, 1], delay: 0.18 }}
        >
          {/* ── Early-exit card ─────────────────────────────────────────── */}
          {endedEarly && (
            <div data-testid="early-exit-card">
            <GlassCard
              className="rounded-[20px] border-[rgba(255,183,100,0.18)] bg-[rgba(255,183,100,0.04)] p-5 text-center"
            >
              <div
                className="flex flex-col items-center gap-2"
                aria-live="polite"
              >
                <StatusTag tone="amber" dot>
                  {t('interviewComplete.earlyExitTitle')}
                </StatusTag>
                <p className="mt-1 text-[13px] text-[#888b91]">
                  {t('interviewComplete.earlyExitDesc')}
                </p>
              </div>
            </GlassCard>
            </div>
          )}

          {/* ── Scorecard section (normal completion only) ──────────────── */}
          {!endedEarly && (
            <>
              {isPreparing && (
                <GlassCard className="rounded-[20px] p-5">
                  <div
                    className="mb-4 flex items-center justify-between"
                    aria-live="polite"
                  >
                    <span className="text-[13px] font-medium text-[#60a5fa]">
                      {t('interviewComplete.preparingScorecard')}
                    </span>
                    <span
                      className="h-4 w-4 animate-spin rounded-full border-2 border-white/15 border-t-[#60a5fa]"
                      aria-hidden="true"
                    />
                  </div>

                  {/* 4-step preparing animation (presentation) */}
                  <motion.div
                    className="flex flex-col gap-2"
                    variants={staggerParent}
                    initial="hidden"
                    animate="show"
                  >
                    {PREPARING_STEPS.map((step, i) => {
                      const stepState =
                        i < visualStep
                          ? 'done'
                          : i === visualStep
                            ? 'active'
                            : 'pending';
                      return (
                        <StepRow key={step} label={step} state={stepState} />
                      );
                    })}
                  </motion.div>

                  <p className="mt-4 text-center text-[12px] text-[#5a5f66]">
                    {t('interviewComplete.scorecardDesc')}
                  </p>
                </GlassCard>
              )}

              {hasTimedOut && (
                <GlassCard className="rounded-[20px] p-5 text-center">
                  <div
                    className="flex flex-col items-center gap-3"
                    aria-live="polite"
                  >
                    <Clock
                      size={30}
                      className="text-[#5a5f66]"
                      aria-hidden="true"
                    />
                    <div>
                      <p className="text-[14px] font-medium text-white">
                        {t('interviewComplete.scorecardTimeout')}
                      </p>
                      <p className="mt-1 text-[13px] text-[#888b91]">
                        {t('interviewComplete.scorecardTimeoutDesc')}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={handleCheckAgain}
                      className={cn(
                        'inline-flex items-center gap-1.5 rounded-[9999px] border border-white/15 px-4 py-2 text-[13px] font-medium text-white',
                        'bg-white/[0.06] transition-colors hover:bg-white/[0.1]',
                        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-black',
                      )}
                    >
                      <RefreshCw size={14} aria-hidden="true" />
                      {t('interviewComplete.checkAgain')}
                    </button>
                  </div>
                </GlassCard>
              )}
            </>
          )}

          {/* ── Navigation CTAs ─────────────────────────────────────────── */}
          <Link
            to="/dashboard"
            className={cn(
              'flex w-full items-center justify-center gap-2 rounded-[12px] border border-white/[0.08]',
              'bg-white px-5 py-3 text-[14px] font-semibold text-black',
              'transition-colors hover:bg-[#eaeaea]',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-black',
            )}
          >
            <LayoutDashboard size={16} aria-hidden="true" />
            {t('interviewComplete.backToDashboard')}
          </Link>

          <div className="grid grid-cols-2 gap-3">
            <Link
              to="/history"
              className={cn(
                'flex items-center justify-center gap-1.5 rounded-[12px] border border-white/[0.1]',
                'bg-white/[0.04] px-4 py-2.5 text-[13.5px] font-medium text-[#b8babf]',
                'transition-colors hover:bg-white/[0.08] hover:text-white',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-black',
              )}
            >
              <History size={15} aria-hidden="true" />
              {t('interviewComplete.history')}
            </Link>

            <Link
              to="/start"
              className={cn(
                'flex items-center justify-center gap-1.5 rounded-[12px] border border-white/[0.1]',
                'bg-white/[0.04] px-4 py-2.5 text-[13.5px] font-medium text-[#b8babf]',
                'transition-colors hover:bg-white/[0.08] hover:text-white',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-black',
              )}
            >
              <ArrowRight size={15} aria-hidden="true" />
              {t('interviewComplete.newInterview')}
            </Link>
          </div>

          {/* ── "View scorecard" Pill — shown when visual steps are done
                but real redirect hasn't fired yet (edge: fast poll hit
                between step 4 and redirect). Also shown as a convenience
                link if the user is waiting. Presentation only — the real
                navigate() fires the moment scorecardId arrives. ─────── */}
          {!endedEarly && visualStep >= PREPARING_STEPS.length && !hasTimedOut && (
            <div className="flex justify-center pt-1">
              <span
                className={cn(
                  'inline-flex items-center gap-2 rounded-[9999px] px-6 py-3 text-[14px] font-semibold',
                  'bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa] border border-[rgba(var(--accent-rgb),0.35)]',
                  'animate-pulse',
                )}
                aria-live="polite"
              >
                <Sparkles size={15} aria-hidden="true" />
                Redirecting to your scorecard…
              </span>
            </div>
          )}
        </motion.div>
      </div>
    </div>
  );
}
