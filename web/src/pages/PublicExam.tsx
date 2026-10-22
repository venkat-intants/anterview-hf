// PublicExam — applicant exam-taking page (HR workflow Phase 2+).
//
// PUBLIC, no login. The magic-link token comes from the URL #fragment (never sent
// to the server in the URL) and is forwarded as the X-Exam-Token header by the
// publicExam API client. No app shell — a clean, focused full-screen experience.
//
// SECURITY: token MUST come from window.location.hash — NOT useParams.
//
// Architecture:
//   - Fetches the TakeExam shape which now includes ordered `sections`.
//   - Walking sections: MCQ sections render an inline question list; coding
//     sections embed <CodingTaking mode="embedded"> which reports submissions up.
//   - A single `answers` map (qid → index) and `submissions` map (qid → CodingAnswer)
//     accumulate across ALL sections and are sent together via submitRound.
//   - Per-section time_limit_seconds drives a client-side countdown that advances
//     automatically when it elapses.
//   - Overall round deadline drives the global countdown (server is authoritative).
//   - useExamProctor: requests fullscreen on round start; fullscreen_exit +
//     tab_blur violations shown to the user; auto-submits at maxViolations.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Loader2,
  CheckCircle2,
  Send,
  Clock,
  ListChecks,
  ShieldCheck,
  AlertTriangle,
  Maximize2,
} from '@/design/components/icons';
import {
  getPublicExam,
  startExam,
  grantCameraConsent,
  submitRound,
  type CodingAnswer,
  type ExamResult,
  type PublicSection,
} from '@/api/publicExam';
import { cn } from '@/lib/utils';
import { isTransientApiError } from '@/api/client';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { ProgressMeter } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { PublicFrame, PublicNotice } from '@/components/PublicFrame';
import { PublicNext } from '@/components/PublicNext';
import CodingTaking from '@/pages/exam/CodingTaking';
import { useExamProctor } from '@/pages/exam/useExamProctor';
import CameraConsentModal from '@/pages/exam/CameraConsentModal';
import ProctorIndicator from '@/pages/exam/ProctorIndicator';

function fmt(totalSec: number): string {
  const s = Math.max(0, totalSec);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

type Phase = 'intro' | 'taking' | 'result';

// ── Shared public frame (loading / error / result states) ──
function PageWrap({ children, step }: { children: React.ReactNode; step?: React.ReactNode }) {
  return (
    <PublicFrame width="narrow" hideLanguage privateLink step={step}>
      {children}
    </PublicFrame>
  );
}

// ── Fullscreen-exit blocking overlay ─────────────────────────────────────────
interface FullscreenOverlayProps {
  violationCount: number;
  /** null when this attempt's auto-submit is relaxed (PH4-D2) — there is no
   *  threshold to count down to, so we do not invent one. */
  maxViolations: number | null;
  onReturn: () => void;
}

function FullscreenOverlay({ violationCount, maxViolations, onReturn }: FullscreenOverlayProps) {
  const { t } = useTranslation();
  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="fs-overlay-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/95 px-4"
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ type: 'spring', stiffness: 260, damping: 22 }}
        className="w-full max-w-sm"
      >
        <GlassCard className="flex flex-col items-center gap-4 p-6 text-center shadow-elevated sm:p-8">
          <span className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-[var(--ui-danger-wash)] text-[var(--ui-danger)]">
            <AlertTriangle className="h-7 w-7" aria-hidden="true" />
          </span>
          <h2 id="fs-overlay-title" className="text-[20px] font-bold leading-tight text-foreground">
            {t('publicExam.fullscreenExitTitle')}
          </h2>
          <p className="text-body-sm text-muted-foreground">{t('publicExam.fullscreenExitSub')}</p>
          {maxViolations !== null && maxViolations > 0 && (
            <p className="text-[13px] font-medium text-[var(--ui-warn)]">
              {t('publicExam.violationsRemaining', {
                remaining: Math.max(0, maxViolations - violationCount),
                max: maxViolations,
              })}
            </p>
          )}
          <Button onClick={onReturn} className="mt-2 h-12 w-full gap-2">
            <Maximize2 size={16} aria-hidden="true" />
            {t('publicExam.returnFullscreen')}
          </Button>
        </GlassCard>
      </motion.div>
    </div>
  );
}

// ── Section stepper header ────────────────────────────────────────────────────
interface SectionStepperProps {
  sections: PublicSection[];
  currentIndex: number;
}

function SectionStepper({ sections, currentIndex }: SectionStepperProps) {
  if (sections.length <= 1) return null;
  return (
    <div className="flex items-center gap-2 overflow-x-auto py-1">
      {sections.map((s, i) => (
        <div key={s.id} className="flex items-center gap-2">
          <div
            className={cn(
              'flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[12px] font-semibold',
              i < currentIndex
                ? 'bg-[var(--ui-ok-wash)] text-[var(--ui-ok)]'
                : i === currentIndex
                  ? 'bg-[var(--ui-highlight)] text-[var(--ui-highlight-ink)] ring-2 ring-[var(--accent)]'
                  : 'bg-[var(--ui-inset)] text-muted-foreground',
            )}
            aria-current={i === currentIndex ? 'step' : undefined}
          >
            {i < currentIndex ? <CheckCircle2 size={12} aria-hidden="true" /> : i + 1}
          </div>
          <span
            className={cn(
              'shrink-0 text-[13.5px]',
              i === currentIndex ? 'font-semibold text-foreground' : 'font-medium text-muted-foreground',
            )}
          >
            {s.title}
          </span>
          {i < sections.length - 1 && (
            <div
              className={cn(
                'h-px w-6 shrink-0',
                i < currentIndex ? 'bg-[var(--ui-ok)]/40' : 'bg-[var(--ui-inset-strong)]',
              )}
            />
          )}
        </div>
      ))}
    </div>
  );
}

// Per tab, and only for as long as the tab lives. Holding the exam token here
// is what lets the address bar be cleared without a reload costing a candidate
// their attempt.
const EXAM_TOKEN_KEY = 'anthire.exam.token';

// ── Main component ────────────────────────────────────────────────────────────
export default function PublicExam() {
  const { t } = useTranslation();

  // SECURITY: token from #fragment — never from a path param.
  //
  // Then out of the address bar, and into this tab's sessionStorage. The
  // fragment keeps it off the wire, out of access logs and out of Referer —
  // that part was always right — but it stayed on screen and in the history
  // entry for the whole attempt: a live credential in the address bar of a
  // session that is fullscreened, proctored and routinely screen-shared, left
  // behind in history on a shared machine.
  //
  // sessionStorage rather than state alone, because this page is not one you
  // pass through: a reload with the hash already stripped would have locked a
  // candidate out of an attempt whose clock was still running. Per tab, and
  // gone when the tab closes.
  const [token] = useState(() => {
    const fromHash = window.location.hash.replace(/^#/, '').trim();
    if (fromHash) {
      try {
        sessionStorage.setItem(EXAM_TOKEN_KEY, fromHash);
      } catch {
        // Private mode, or storage blocked. The token is in state either way;
        // only surviving a reload is lost, and the emailed link still works.
      }
      return fromHash;
    }
    try {
      return sessionStorage.getItem(EXAM_TOKEN_KEY) ?? '';
    } catch {
      return '';
    }
  });
  const [consent, setConsent] = useState(false);

  // ── Camera-proctoring consent (camera-proctoring contract §3) — its OWN
  // gate, entirely separate from `consent` above (the recording/scoring
  // consent). 'pending' only ever matters when the round requires it; a round
  // that doesn't is simply never checked against this state.
  const [cameraConsent, setCameraConsent] = useState<'pending' | 'granted' | 'declined'>('pending');

  useEffect(() => {
    if (window.location.hash) window.history.replaceState(null, '', '/exam');
  }, []);

  const examQ = useQuery({
    queryKey: ['public-exam', token],
    queryFn: () => getPublicExam(token),
    enabled: token.length > 0,
    // A definitive rejection (404 bad/revoked link) must not be retried, but a
    // transient failure (network blip, Space replica swap, 5xx) must not brand
    // a perfectly good link "invalid" — retry those a couple of times.
    retry: (failureCount, error) => isTransientApiError(error) && failureCount < 2,
    staleTime: Infinity,
  });

  const [phase, setPhase] = useState<Phase>('intro');
  const [attemptId, setAttemptId] = useState<string | null>(null);
  const [deadline, setDeadline] = useState<string | null>(null);
  const [result, setResult] = useState<ExamResult | null>(null);

  // Unified answer + submission maps across all sections.
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [submissions, setSubmissions] = useState<Record<string, CodingAnswer>>({});

  // Section stepper state.
  const [sectionIndex, setSectionIndex] = useState(0);

  // Global round countdown (UX only — server is the real time authority).
  const [remaining, setRemaining] = useState<number | null>(null);
  // Per-section countdown.
  const [sectionRemaining, setSectionRemaining] = useState<number | null>(null);

  const submittedRef = useRef(false);

  // ── Derive sections (sorted) ────────────────────────────────────────────
  const sections = useMemo<PublicSection[]>(() => {
    if (!examQ.data?.sections?.length) return [];
    return [...examQ.data.sections].sort((a, b) => a.position - b.position);
  }, [examQ.data]);

  const currentSection = sections[sectionIndex] ?? null;

  // The company's own setting on this round (camera-proctoring contract §3).
  // `Boolean(...)`: defaults an absent field to `false` (no camera gate,
  // browser-events-only) rather than crashing on an older API response.
  const cameraRequired = Boolean(examQ.data?.camera_required);

  // ── Start mutation ───────────────────────────────────────────────────────
  const startMut = useMutation({
    // `cameraConsent` only travels when the round actually requires it — a
    // round with no camera gate has nothing to consent to, so this sends
    // `undefined` (omitted from the request body) rather than `false`, which
    // would misrecord a decision the candidate was never asked to make.
    // Consent is RECORDED first, through its own endpoint, then the attempt
    // starts — the server gates on the dpdp_consent_ledger, never on a field
    // this client sends. A consent asserted in a start payload is not
    // evidence, and DPDP asks for evidence.
    mutationFn: async () => {
      if (cameraRequired && cameraConsent === 'granted') {
        await grantCameraConsent(token);
      }
      return startExam(token);
    },
    onSuccess: (s) => {
      setAttemptId(s.attempt_id);
      setDeadline(s.deadline);
      setAttemptMaxViolations(s.max_violations);
      setPhase('taking');
    },
  });

  // ── Submit mutation (unified round submit) ────────────────────────────────
  const submitMut = useMutation({
    mutationFn: () => submitRound(token, attemptId ?? '', answers, submissions),
    onSuccess: (r) => {
      setResult(r);
      setPhase('result');
      // The credential has done its job. Leaving it in sessionStorage would
      // keep a live exam token readable by script on any page of this origin
      // for the rest of the tab's life, long after the attempt is graded.
      try {
        sessionStorage.removeItem(EXAM_TOKEN_KEY);
      } catch {
        // Blocked or unavailable — it was never written either. The token is
        // still in state, which is what the result screen renders from.
      }
    },
    onError: () => {
      submittedRef.current = false;
    },
  });

  const doSubmit = useCallback(() => {
    if (submittedRef.current || !attemptId) return;
    submittedRef.current = true;
    submitMut.mutate();
  }, [attemptId, submitMut]);

  // ── Proctoring ───────────────────────────────────────────────────────────
  // The attempt's own frozen value wins once /exam/start has answered:
  // `null` means relaxed (PH4-D2) and must never be compared numerically --
  // `count >= null` coerces to `count >= 0`, which is true from the first
  // event. Before the attempt exists there is nothing to relax yet.
  const [attemptMaxViolations, setAttemptMaxViolations] = useState<number | null | undefined>(
    undefined,
  );
  const maxViolations =
    attemptMaxViolations !== undefined
      ? attemptMaxViolations
      : (examQ.data?.max_integrity_violations ?? 3);

  const {
    isFullscreen,
    fullscreenSupported,
    violationCount,
    enterFullscreen,
    cameraVideoRef,
    cameraAvailable,
    cameraSettled,
    cameraDenied,
    cameraModelReady,
    cameraWarning,
    cameraCalibrating,
  } = useExamProctor({
    enabled: phase === 'taking',
    attemptId: attemptId ?? '',
    token,
    maxViolations,
    onAutoSubmit: doSubmit,
    // Camera proctoring only ever runs once ALL THREE are true: the round
    // requires it, the candidate gave the dedicated consent above, and the
    // attempt is actually in progress. Declining or a round that doesn't use
    // it at all both leave this false — browser-events-only, same as today.
    cameraEnabled: phase === 'taking' && cameraRequired && cameraConsent === 'granted',
  });

  // Request fullscreen as soon as the round starts (triggered from the "Start
  // exam" button click, which is the required user gesture).
  const handleStart = useCallback(async () => {
    // FULLSCREEN FIRST, BEFORE THE NETWORK CALL. requestFullscreen() is only
    // honoured while the browser still considers the click "transient user
    // activation", and awaiting the start request spends it — on a slow
    // connection the request that used to run here was simply refused, and
    // the candidate landed on question one outside fullscreen with the
    // proctor already watching. The old comment claimed this was "still
    // within the gesture stack"; after an awaited round trip it is not.
    await enterFullscreen();

    const s = await new Promise<{
      attempt_id: string;
      started_at: string;
      deadline: string | null;
      max_violations: number | null;
    }>((resolve, reject) => {
      startMut.mutate(undefined, { onSuccess: resolve, onError: reject });
    });
    setAttemptId(s.attempt_id);
    setDeadline(s.deadline);
    setAttemptMaxViolations(s.max_violations);
    // Last: flipping to 'taking' is what arms the proctor, so nothing it could
    // record happens before the candidate is actually set up. The remaining
    // start-up signal it waits on — the camera permission prompt — is handled
    // by features/proctoring/useArmed.
    setPhase('taking');
  }, [startMut, enterFullscreen]);

  // ── Global countdown ─────────────────────────────────────────────────────
  useEffect(() => {
    if (phase !== 'taking' || !deadline) return;
    const target = new Date(deadline).getTime();
    const tick = () => setRemaining(Math.round((target - Date.now()) / 1000));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [phase, deadline]);

  // Auto-submit when global clock hits zero.
  useEffect(() => {
    if (phase === 'taking' && remaining !== null && remaining <= 0) doSubmit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [remaining, phase]);

  // ── Per-section countdown ────────────────────────────────────────────────
  useEffect(() => {
    if (phase !== 'taking' || !currentSection?.time_limit_seconds) {
      setSectionRemaining(null);
      return;
    }
    const limitSec = currentSection.time_limit_seconds;
    let elapsed = 0;
    const tick = () => {
      elapsed++;
      setSectionRemaining(Math.max(0, limitSec - elapsed));
    };
    setSectionRemaining(limitSec);
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
    // Reset when the section or phase changes.
  }, [phase, sectionIndex, currentSection]);

  // Advance to next section when section time elapses.
  useEffect(() => {
    if (sectionRemaining !== null && sectionRemaining <= 0) {
      const next = sectionIndex + 1;
      if (next < sections.length) {
        setSectionIndex(next);
      } else {
        // Last section timed out — submit the round.
        doSubmit();
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sectionRemaining]);

  // ── Violation count display (local counter runs ahead for responsiveness) ─

  // ── Rendering helpers ────────────────────────────────────────────────────

  // MCQ section questions (sorted by position).
  const sectionQuestions = useMemo(
    () =>
      currentSection?.kind === 'mcq'
        ? [...(currentSection.questions ?? [])].sort((a, b) => a.position - b.position)
        : [],
    [currentSection],
  );

  const answeredCountForSection = useMemo(
    () => sectionQuestions.filter((q) => answers[q.id] !== undefined).length,
    [sectionQuestions, answers],
  );

  // ── A link that can no longer be used: drop the token it left behind ─────
  // The per-tab token is otherwise removed only by a successful submit, so an
  // expired, revoked or already-used link left a dead credential readable from
  // sessionStorage for the rest of the tab's life. Only TERMINAL states clear
  // it: a transient load error (the link may be fine, and Retry needs the
  // token) and a failed submit (the candidate must be able to try again) do not.
  const linkUnusable = token.length > 0 && examQ.isError && !isTransientApiError(examQ.error);
  const alreadyCompleted =
    phase === 'intro' && Boolean(examQ.data?.already_submitted) && !examQ.data?.allow_retake;
  useEffect(() => {
    if (!linkUnusable && !alreadyCompleted) return;
    try {
      sessionStorage.removeItem(EXAM_TOKEN_KEY);
    } catch {
      // Blocked or unavailable: it was never written either.
    }
  }, [linkUnusable, alreadyCompleted]);

  // ── Error / loading states ───────────────────────────────────────────────
  // Transient failure (network / server hiccup) — the link may be fine.
  if (token && examQ.isError && isTransientApiError(examQ.error)) {
    return (
      <PageWrap>
        <PublicNotice
          title={t('publicExam.networkTitle')}
          description={t('publicExam.networkDesc')}
          action={
            <Button className="h-12 px-8" onClick={() => void examQ.refetch()}>
              {t('publicExam.retry')}
            </Button>
          }
        />
      </PageWrap>
    );
  }

  if (!token || examQ.isError) {
    return (
      <PageWrap>
        <PublicNotice
          title={t('publicExam.invalidTitle')}
          description={t('publicExam.invalidDesc')}
          hint={t('publicFrame.reopenEmail')}
        />
      </PageWrap>
    );
  }

  if (examQ.isLoading || !examQ.data) {
    return (
      <PageWrap>
        <div role="status" className="flex flex-col items-center gap-3 py-20">
          <Loader2 className="h-8 w-8 animate-spin text-[var(--accent)]" aria-hidden="true" />
          <p className="text-[14px] text-muted-foreground">{t('publicExam.loading')}</p>
        </div>
      </PageWrap>
    );
  }

  const exam = examQ.data;

  // ── Already completed ────────────────────────────────────────────────────
  if (phase !== 'result' && exam.already_submitted && !exam.allow_retake) {
    return (
      <PageWrap step={t('public.step.assessment')}>
        <PublicNotice
          tone="ok"
          title={t('publicExam.completedTitle')}
          description={t('publicExam.completedDesc')}
        />
        <PublicNext className="mt-6">{t('public.reviewNext')}</PublicNext>
      </PageWrap>
    );
  }

  // ── Camera proctoring required and declined ──────────────────────────────
  // Camera-proctoring contract §3: if the round requires it and the
  // candidate declines, they cannot start, and the screen says why and what
  // to do. Reconsidering re-opens the consent step rather than being a dead
  // end — declining is the candidate's call to make, not a one-way door.
  if (phase === 'intro' && cameraRequired && cameraConsent === 'declined') {
    return (
      <PageWrap>
        <PublicNotice
          title={t('publicExam.cameraRequiredTitle')}
          description={t('publicExam.cameraRequiredDesc')}
          action={
            <Button className="h-12 px-8" onClick={() => setCameraConsent('pending')}>
              {t('publicExam.cameraReconsider')}
            </Button>
          }
        />
      </PageWrap>
    );
  }

  // ── Result ───────────────────────────────────────────────────────────────
  if (phase === 'result' && result) {
    return (
      <PageWrap step={t('public.step.assessment')}>
        {/* Submitted, and with the hiring team. Deliberately NOT a verdict: no
            tick or cross, no pass or fail, no tone that varies with the score
            (a person decides, never the software). The score is shown as a
            plain number only; `result.passed` is not read here. */}
        <div data-testid="exam-submitted" className="flex w-full flex-col gap-6">
          <PublicNotice
            tone="info"
            icon={<Send size={22} aria-hidden="true" />}
            title={t('public.exam.submittedTitle')}
            description={t('public.exam.submittedDesc')}
            hint={
              <>
                {t('public.exam.score', {
                  percent: result.score_percent,
                  raw: result.score_raw,
                  max: result.score_max,
                })}
                {result.status === 'expired' ? t('publicExam.submittedAtLimit') : ''}
              </>
            }
          />
          <PublicNext>{t('public.reviewNext')}</PublicNext>
        </div>
      </PageWrap>
    );
  }

  // ── Intro ────────────────────────────────────────────────────────────────
  if (phase === 'intro') {
    const facts = [
      {
        icon: ListChecks,
        label: t('publicExam.factQuestions'),
        value: t('publicExam.questionsCount', { count: exam.total_questions }),
      },
      ...(exam.time_limit_seconds
        ? [
            {
              icon: Clock,
              label: t('publicExam.factDuration'),
              value: t('publicExam.minutes', {
                count: Math.round(exam.time_limit_seconds / 60),
              }),
            },
          ]
        : []),
    ];
    // An auto-named round ("Round 1" for round 1) says nothing the exam title
    // does not, and printed through roundLabel it read "Round 1: Round 1".
    const roundTitle = exam.round_title?.trim() ?? '';
    const isDefaultRoundTitle =
      /^Round \d+$/.test(roundTitle) && roundTitle === `Round ${exam.round_number}`;
    const showRoundLabel =
      roundTitle.length > 0 && roundTitle !== exam.title && !isDefaultRoundTitle;

    return (
      <PublicFrame width="narrow" hideLanguage privateLink step={t('public.step.assessment')}>
        <div className="w-full">
          <GlassCard className="p-5 sm:p-8">
            {/* Header row */}
            <div className="flex items-center justify-between">
              <StatusTag tone="forest" dot>
                {t('publicExam.liveExam')}
              </StatusTag>
              {/* The exam id, not the token. This used to print the first ten
                  characters of the raw secret — roughly 60 of its 256 bits,
                  in the DOM, on a screen that gets shared and captured. The
                  remainder is still infeasible to guess, so it was never
                  exploitable on its own; it simply bought nothing. */}
              <span className="font-mono text-[12px] text-[var(--ui-soft)]">#{exam.exam_id.slice(0, 8)}</span>
            </div>

            <h1 className="mt-4 text-[28px] font-bold leading-[1.15] tracking-[-0.6px] text-foreground">
              {exam.title}
            </h1>
            {showRoundLabel && (
              <p className="mt-0.5 text-body-sm text-muted-foreground">
                {t('publicExam.roundLabel', { n: exam.round_number, title: exam.round_title })}
              </p>
            )}

            {exam.description && (
              <p className="mt-1.5 text-body-sm text-muted-foreground">{exam.description}</p>
            )}

            {/* Fact grid */}
            <div
              className={cn('mt-6 grid gap-3', facts.length === 2 ? 'grid-cols-2' : 'grid-cols-1')}
            >
              {facts.map((f) => {
                const Icon = f.icon;
                return (
                  <div
                    key={f.label}
                    className="rounded-[12px] bg-[var(--ui-inset-soft)] p-4"
                  >
                    <Icon size={16} className="text-[var(--ui-info)]" aria-hidden="true" />
                    <div className="mt-2 text-[12px] font-semibold uppercase tracking-[0.06em] text-[var(--ui-soft)]">
                      {f.label}
                    </div>
                    <div className="mt-0.5 text-[15px] font-semibold text-foreground">
                      {f.value}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Sections preview */}
            {sections.length > 1 && (
              <div className="mt-4 space-y-1.5">
                {sections.map((s, i) => (
                  <div
                    key={s.id}
                    className="flex items-center justify-between rounded-[12px] border border-border bg-[var(--ui-inset-soft)] px-3.5 py-2.5 text-[13px]"
                  >
                    <span className="text-muted-foreground">
                      {i + 1}. {s.title}
                    </span>
                    <span className="capitalize text-[var(--ui-soft)]">{s.kind}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Timer warning */}
            {exam.time_limit_seconds && (
              <p className="mt-4 text-[13.5px] font-medium text-[var(--ui-warn)]">{t('publicExam.timerNote')}</p>
            )}

            {/* PH4-D2 — the fact only: never the percentage, never why. The
                round/section limits below are already the scaled ones. */}
            {exam.adjustments &&
            (exam.adjustments.extra_time_percent || exam.adjustments.deadline_extended) ? (
              <div className="mt-4 flex items-start gap-2 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] px-3.5 py-3 text-[13px] leading-relaxed text-muted-foreground">
                <Clock size={14} className="mt-0.5 shrink-0 text-[var(--ui-ok)]" aria-hidden="true" />
                <span>{t('publicExam.adjustmentNotice')}</span>
              </div>
            ) : null}

            {/* Proctoring notice */}
            <div className="mt-4 flex items-start gap-2 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] px-3.5 py-3 text-[13px] leading-relaxed text-muted-foreground">
              <ShieldCheck
                size={14}
                className="mt-0.5 shrink-0 text-[var(--ui-ok)]"
                aria-hidden="true"
              />
              <span>{t('publicExam.proctoringNotice')}</span>
            </div>

            {/* Camera-proctoring status — informational only, never the consent
                itself (camera-proctoring contract §3: consent is its own
                separate step, the CameraConsentModal below). */}
            {cameraRequired && (
              <div className="mt-3 flex items-start gap-2 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] px-3.5 py-3 text-[13px] leading-relaxed text-muted-foreground">
                <ShieldCheck
                  size={14}
                  className={cn(
                    'mt-0.5 shrink-0',
                    cameraConsent === 'granted' ? 'text-[var(--ui-ok)]' : 'text-[var(--ui-warn)]',
                  )}
                  aria-hidden="true"
                />
                <span>
                  {cameraConsent === 'granted'
                    ? t('publicExam.cameraConsentGrantedNotice')
                    : t('publicExam.cameraConsentPendingNotice')}
                </span>
              </div>
            )}

            {/* DPDP consent */}
            <label className="mt-4 flex cursor-pointer items-start gap-3 rounded-[16px] border border-border bg-[var(--ui-inset-soft)] p-4 text-[13.5px] leading-relaxed text-foreground focus-within:ring-2 focus-within:ring-[var(--accent)]">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
                className="mt-0.5 h-5 w-5 flex-none accent-[var(--accent)]"
              />
              <span className="flex items-start gap-1.5">
                <ShieldCheck size={16} className="mt-0.5 flex-none text-[var(--ui-ok)]" aria-hidden="true" />
                {t('publicExam.consentLabel')}
              </span>
            </label>

            <Button
              className="mt-6 h-12 w-full text-[15px]"
              disabled={
                !consent || startMut.isPending || (cameraRequired && cameraConsent !== 'granted')
              }
              onClick={() => void handleStart()}
            >
              {startMut.isPending ? (
                <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
              ) : null}
              {t('publicExam.startExam')}
            </Button>

            {startMut.isError && (
              <p role="alert" className="mt-3 text-center text-[14px] text-[var(--ui-danger)]">
                {startMut.error instanceof Error
                  ? startMut.error.message
                  : t('publicExam.couldNotStart')}
              </p>
            )}
          </GlassCard>
        </div>

        {/* Camera-proctoring consent — its own gate, before anything else can
            proceed. Never bundled with the DPDP recording/scoring consent
            above (camera-proctoring contract §3). */}
        {cameraRequired && cameraConsent === 'pending' && (
          <CameraConsentModal
            onAgree={() => setCameraConsent('granted')}
            onDecline={() => setCameraConsent('declined')}
          />
        )}
      </PublicFrame>
    );
  }

  // ── Taking phase ─────────────────────────────────────────────────────────

  // Violation warning banner (non-blocking — shown inside the page, not as overlay).
  const showViolationWarning = violationCount > 0 && isFullscreen;

  return (
    <div className="min-h-screen bg-background font-sans text-foreground">
      {/* Fullscreen-exit blocking overlay */}
      <AnimatePresence>
        {fullscreenSupported && !isFullscreen && phase === 'taking' && (
          <FullscreenOverlay
            violationCount={violationCount}
            maxViolations={maxViolations}
            onReturn={() => void enterFullscreen()}
          />
        )}
      </AnimatePresence>

      {/* Sticky bar */}
      <div className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-border bg-card px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[15px] font-bold text-foreground">{exam.title}</p>
          {sections.length > 0 && currentSection && (
            <p className="text-[13px] text-muted-foreground">
              {t('publicExam.sectionProgress', {
                current: sectionIndex + 1,
                total: sections.length,
                title: currentSection.title,
              })}
            </p>
          )}
          {exam.adjustments &&
          (exam.adjustments.extra_time_percent || exam.adjustments.deadline_extended) ? (
            <p className="text-[13px] text-[var(--ui-ok)]">{t('publicExam.adjustmentNotice')}</p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Camera-proctoring indicator — candidate-visible, never decoration
              (camera-proctoring contract §4). Only rendered once the camera
              is actually the active signal path; a declined/not-required
              round shows nothing extra here. */}
          {cameraRequired && cameraConsent === 'granted' && (
            <ProctorIndicator
              cameraSettled={cameraSettled}
              cameraAvailable={cameraAvailable}
              cameraDenied={cameraDenied}
              cameraModelReady={cameraModelReady}
              cameraCalibrating={cameraCalibrating}
              cameraWarning={cameraWarning}
            />
          )}

          {/* Violation badge */}
          {violationCount > 0 && (
            <div
              className="flex items-center gap-1 rounded-full bg-[var(--ui-danger-wash)] px-3 py-1.5 text-[13px] font-medium text-[var(--ui-danger)]"
              aria-live="polite"
              aria-label={t('publicExam.violationBadgeLabel', { count: violationCount })}
            >
              <AlertTriangle size={11} aria-hidden="true" />
              {violationCount}/{maxViolations}
            </div>
          )}

          {/* Section timer */}
          {sectionRemaining !== null && (
            <div
              className={cn(
                'flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] font-medium tabular-nums',
                sectionRemaining <= 30
                  ? 'bg-[var(--ui-danger-wash)] text-[var(--ui-danger)]'
                  : 'bg-[var(--ui-inset)] text-muted-foreground',
              )}
              aria-live="polite"
              aria-label={t('publicExam.sectionTimeRemaining', { time: fmt(sectionRemaining) })}
            >
              <Clock className="h-3.5 w-3.5" aria-hidden="true" />
              {fmt(sectionRemaining)}
            </div>
          )}

          {/* Global timer */}
          {remaining !== null && (
            <div
              className={cn(
                // The clock is the most legible thing in the bar: largest, boldest,
                // and it gains a border (not only a colour) when time is nearly up.
                'flex items-center gap-2 rounded-full border px-4 py-2 text-[20px] font-bold leading-none tabular-nums',
                remaining <= 30
                  ? 'border-[var(--ui-danger)] bg-[var(--ui-danger-wash)] text-[var(--ui-danger)]'
                  : 'border-[var(--ui-line-strong)] bg-[var(--ui-inset)] text-foreground',
              )}
              aria-live="polite"
              aria-label={t('publicExam.timeRemaining', { time: fmt(remaining) })}
            >
              <Clock className="h-5 w-5" aria-hidden="true" />
              {fmt(remaining)}
            </div>
          )}
        </div>
      </div>

      {/* Violation warning banner */}
      <AnimatePresence>
        {showViolationWarning && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className="flex items-center justify-center gap-2 border-b border-[var(--ui-danger)]/30 bg-[var(--ui-danger-wash)] px-4 py-2.5 text-[13px] font-medium text-[var(--ui-danger)]"
            role="alert"
          >
            <AlertTriangle size={13} aria-hidden="true" />
            {maxViolations === null
              ? t('publicExam.violationNoted', { count: violationCount })
              : t('publicExam.violationWarning', {
                  count: violationCount,
                  remaining: Math.max(0, maxViolations - violationCount),
                })}
          </motion.div>
        )}
      </AnimatePresence>

      <div className="mx-auto max-w-2xl px-4 py-6 sm:py-8">
        {/* Progress through the current section — always on screen, so a
            candidate under the clock never has to scroll to see how far on
            they are. Reuses the existing answered/total counts. */}
        {currentSection?.kind === 'mcq' && sectionQuestions.length > 0 && (
          <div className="mb-5">
            <div className="mb-1.5 flex items-center justify-between text-[13px] text-muted-foreground">
              <span className="text-[15px] font-bold tabular-nums text-foreground">
                {answeredCountForSection}/{sectionQuestions.length}
              </span>
              <span>
                {answeredCountForSection < sectionQuestions.length
                  ? t('publicExam.unanswered', {
                      count: sectionQuestions.length - answeredCountForSection,
                    })
                  : t('publicExam.allAnswered')}
              </span>
            </div>
            <ProgressMeter
              value={(answeredCountForSection / sectionQuestions.length) * 100}
              label={currentSection.title}
            />
          </div>
        )}

        {/* Section stepper */}
        {sections.length > 1 && (
          <div className="mb-6">
            <SectionStepper sections={sections} currentIndex={sectionIndex} />
          </div>
        )}

        <AnimatePresence mode="wait">
          {currentSection ? (
            <motion.div
              key={currentSection.id}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              transition={{ duration: 0.18 }}
            >
              {/* ── MCQ Section ── */}
              {currentSection.kind === 'mcq' && (
                <div className="space-y-4">
                  {sectionQuestions.map((q, i) => (
                    <motion.div
                      key={q.id}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: i * 0.04 }}
                    >
                      <GlassCard className="p-4 sm:p-6">
                        <p className="text-[16px] font-semibold leading-relaxed text-foreground">
                          {i + 1}. {q.prompt}
                        </p>
                        <div className="mt-4 space-y-2" role="radiogroup" aria-label={q.prompt}>
                          {q.options.map((opt, oi) => {
                            const checked = answers[q.id] === oi;
                            return (
                              <label
                                key={oi}
                                className={cn(
                                  'flex min-h-12 cursor-pointer items-center gap-3 rounded-[14px] border px-4 py-3 text-[15px] transition-colors focus-within:ring-2 focus-within:ring-[var(--accent)]',
                                  checked
                                    ? 'border-foreground bg-[var(--ui-inset)] font-medium text-foreground'
                                    : 'border-border text-foreground hover:border-[var(--ui-line-strong)] hover:bg-[var(--ui-inset-soft)]',
                                )}
                              >
                                <input
                                  type="radio"
                                  name={q.id}
                                  checked={checked}
                                  onChange={() => setAnswers((prev) => ({ ...prev, [q.id]: oi }))}
                                  className="h-5 w-5 flex-none accent-[var(--accent)]"
                                />
                                {opt}
                              </label>
                            );
                          })}
                        </div>
                      </GlassCard>
                    </motion.div>
                  ))}
                </div>
              )}

              {/* ── Coding Section ── */}
              {currentSection.kind === 'coding' && (
                <CodingTaking
                  mode="embedded"
                  token={token}
                  questions={currentSection.coding_questions}
                  onSubmissionsChange={(subs) => setSubmissions((prev) => ({ ...prev, ...subs }))}
                />
              )}
            </motion.div>
          ) : null}
        </AnimatePresence>

        {/* Navigation + submit footer */}
        <div className="sticky bottom-0 -mx-4 mt-8 flex items-center justify-between gap-3 border-t border-border bg-card px-4 py-3">
          <div className="text-[13px] text-muted-foreground">
            {currentSection?.kind === 'mcq' &&
              (answeredCountForSection < sectionQuestions.length
                ? t('publicExam.unanswered', {
                    count: sectionQuestions.length - answeredCountForSection,
                  })
                : t('publicExam.allAnswered'))}
          </div>

          <div className="flex items-center gap-3">
            {/* Next section button (shown when not on the last section) */}
            {sectionIndex < sections.length - 1 && (
              <Button variant="outline" className="h-11" onClick={() => setSectionIndex((i) => i + 1)}>
                {t('publicExam.nextSection')}
              </Button>
            )}

            {/* Submit (shown on last section or when only one section) */}
            {sectionIndex === sections.length - 1 && (
              <Button className="h-11 px-6" onClick={doSubmit} disabled={submitMut.isPending}>
                {submitMut.isPending ? (
                  <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
                ) : null}
                {t('publicExam.submitExam')}
              </Button>
            )}
          </div>
        </div>

        {submitMut.isError && (
          <p role="alert" className="mt-3 text-right text-[14px] text-[var(--ui-danger)]">
            {submitMut.error instanceof Error
              ? submitMut.error.message
              : t('publicExam.submitFailed')}
          </p>
        )}
      </div>

      {/* Camera self-view (PiP) — local-only, never published anywhere; exists
          so the candidate can SEE they are on camera, not just be told so
          (camera-proctoring contract §4). Mirrored so it feels like a mirror,
          matching the interview's own self-view. Kept in the DOM (not
          display:none) whenever the camera is the active signal path, even
          before the stream attaches, so MediaPipe always has a live element
          to read from the moment frames arrive. */}
      {cameraRequired && cameraConsent === 'granted' && (
        <div
          className={cn(
            'fixed bottom-6 right-4 z-40 h-28 w-20 overflow-hidden rounded-xl',
            'border border-border bg-card shadow-elevated',
            'transition-opacity duration-500',
            cameraAvailable ? 'opacity-100' : 'opacity-0',
          )}
          aria-hidden="true"
        >
          <video
            ref={cameraVideoRef}
            autoPlay
            playsInline
            muted
            className="h-full w-full object-cover scale-x-[-1]"
          />
        </div>
      )}
    </div>
  );
}
