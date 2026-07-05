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
  AlertCircle,
  CheckCircle2,
  XCircle,
  Clock,
  ListChecks,
  Languages,
  ShieldCheck,
  AlertTriangle,
  Maximize2,
} from '@/design/components/icons';
import {
  getPublicExam,
  startExam,
  submitRound,
  type CodingAnswer,
  type ExamResult,
  type PublicSection,
} from '@/api/publicExam';
import { cn } from '@/lib/utils';
import { AuroraField } from '@/design/components/AuroraField';
import { GlassCard, Pill, StatusTag } from '@/design/components/primitives';
import CodingTaking from '@/pages/exam/CodingTaking';
import { useExamProctor } from '@/pages/exam/useExamProctor';

function fmt(totalSec: number): string {
  const s = Math.max(0, totalSec);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

type Phase = 'intro' | 'taking' | 'result';

// ── Full-page dark centred layout (shared by loading / error / result states) ──
function PageWrap({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center bg-midnight px-6 py-12 font-sans text-foreground">
      <AuroraField />
      {/* Logo mark */}
      <div className="absolute left-6 top-6 z-10 flex items-center gap-2.5">
        <span className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-[linear-gradient(135deg,#112d72,#a887dc)]">
          <span className="h-2.5 w-2.5 rounded-full bg-white" />
        </span>
        <span className="text-[15px] font-semibold text-foreground">Anterview</span>
      </div>
      <div className="relative z-10 flex w-full max-w-[520px] flex-col items-center gap-4 text-center">
        {children}
      </div>
    </div>
  );
}

// ── Fullscreen-exit blocking overlay ─────────────────────────────────────────
interface FullscreenOverlayProps {
  violationCount: number;
  maxViolations: number;
  onReturn: () => void;
}

function FullscreenOverlay({ violationCount, maxViolations, onReturn }: FullscreenOverlayProps) {
  const { t } = useTranslation();
  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="fs-overlay-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-midnight/95 px-6 backdrop-blur-xl"
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ type: 'spring', stiffness: 260, damping: 22 }}
        className="w-full max-w-sm"
      >
        <GlassCard className="flex flex-col items-center gap-4 p-8 text-center">
          <span className="inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-[rgba(230,113,79,0.15)] text-ember">
            <AlertTriangle className="h-7 w-7" aria-hidden="true" />
          </span>
          <h2 id="fs-overlay-title" className="text-subheading font-semibold text-foreground">
            {t('publicExam.fullscreenExitTitle')}
          </h2>
          <p className="text-body-sm text-muted-foreground">{t('publicExam.fullscreenExitSub')}</p>
          {maxViolations > 0 && (
            <p className="text-caption text-amber-glow">
              {t('publicExam.violationsRemaining', {
                remaining: Math.max(0, maxViolations - violationCount),
                max: maxViolations,
              })}
            </p>
          )}
          <Pill onClick={onReturn} className="mt-2 w-full gap-2">
            <Maximize2 size={16} aria-hidden="true" />
            {t('publicExam.returnFullscreen')}
          </Pill>
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
              'flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold',
              i < currentIndex
                ? 'bg-vivid-mint/20 text-vivid-mint'
                : i === currentIndex
                  ? 'bg-electric text-midnight'
                  : 'bg-white/[0.06] text-fog',
            )}
            aria-current={i === currentIndex ? 'step' : undefined}
          >
            {i < currentIndex ? <CheckCircle2 size={12} aria-hidden="true" /> : i + 1}
          </div>
          <span
            className={cn(
              'shrink-0 text-caption',
              i === currentIndex ? 'text-foreground' : 'text-fog',
            )}
          >
            {s.title}
          </span>
          {i < sections.length - 1 && (
            <div
              className={cn(
                'h-px w-6 shrink-0',
                i < currentIndex ? 'bg-vivid-mint/30' : 'bg-white/[0.08]',
              )}
            />
          )}
        </div>
      ))}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function PublicExam() {
  const { t } = useTranslation();

  // SECURITY: token from #fragment — never from a path param.
  const [token] = useState(() => window.location.hash.replace(/^#/, '').trim());
  const [consent, setConsent] = useState(false);

  const examQ = useQuery({
    queryKey: ['public-exam', token],
    queryFn: () => getPublicExam(token),
    enabled: token.length > 0,
    retry: false,
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

  // ── Start mutation ───────────────────────────────────────────────────────
  const startMut = useMutation({
    mutationFn: () => startExam(token),
    onSuccess: (s) => {
      setAttemptId(s.attempt_id);
      setDeadline(s.deadline);
      setPhase('taking');
    },
  });

  // ── Submit mutation (unified round submit) ────────────────────────────────
  const submitMut = useMutation({
    mutationFn: () => submitRound(token, attemptId ?? '', answers, submissions),
    onSuccess: (r) => {
      setResult(r);
      setPhase('result');
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
  const maxViolations = examQ.data?.max_integrity_violations ?? 3;

  const { isFullscreen, fullscreenSupported, violationCount, enterFullscreen } = useExamProctor({
    enabled: phase === 'taking',
    attemptId: attemptId ?? '',
    token,
    maxViolations,
    onAutoSubmit: doSubmit,
  });

  // Request fullscreen as soon as the round starts (triggered from the "Start
  // exam" button click, which is the required user gesture).
  const handleStart = useCallback(async () => {
    const s = await new Promise<{
      attempt_id: string;
      started_at: string;
      deadline: string | null;
    }>((resolve, reject) => {
      startMut.mutate(undefined, { onSuccess: resolve, onError: reject });
    });
    // Enter fullscreen after the phase changes (still within the gesture stack).
    setAttemptId(s.attempt_id);
    setDeadline(s.deadline);
    setPhase('taking');
    await enterFullscreen();
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

  // ── Error / loading states ───────────────────────────────────────────────
  if (!token || examQ.isError) {
    return (
      <PageWrap>
        <span className="inline-flex h-12 w-12 items-center justify-center rounded-[9px] bg-[rgba(255,183,100,0.15)] text-amber-glow">
          <AlertCircle className="h-6 w-6" aria-hidden="true" />
        </span>
        <h1 className="text-subheading font-semibold text-foreground">
          {t('publicExam.invalidTitle')}
        </h1>
        <p className="max-w-sm text-body-sm text-muted-foreground">{t('publicExam.invalidDesc')}</p>
      </PageWrap>
    );
  }

  if (examQ.isLoading || !examQ.data) {
    return (
      <PageWrap>
        <Loader2 className="h-8 w-8 animate-spin text-electric" aria-hidden="true" />
        <p className="text-body-sm text-muted-foreground">{t('publicExam.loading')}</p>
      </PageWrap>
    );
  }

  const exam = examQ.data;

  // ── Already completed ────────────────────────────────────────────────────
  if (phase !== 'result' && exam.already_submitted && !exam.allow_retake) {
    return (
      <PageWrap>
        <span className="inline-flex h-12 w-12 items-center justify-center rounded-[9px] bg-[rgba(39,201,63,0.15)] text-vivid-mint">
          <CheckCircle2 className="h-6 w-6" aria-hidden="true" />
        </span>
        <h1 className="text-subheading font-semibold text-foreground">
          {t('publicExam.completedTitle')}
        </h1>
        <p className="max-w-sm text-body-sm text-muted-foreground">
          {t('publicExam.completedDesc')}
        </p>
      </PageWrap>
    );
  }

  // ── Result ───────────────────────────────────────────────────────────────
  if (phase === 'result' && result) {
    return (
      <PageWrap>
        <motion.div
          initial={{ opacity: 0, scale: 0.92 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ type: 'spring', stiffness: 220, damping: 20 }}
          className="w-full"
        >
          <GlassCard className="flex flex-col items-center gap-4 p-8 text-center">
            <span
              className={cn(
                'inline-flex h-16 w-16 items-center justify-center rounded-3xl',
                result.passed
                  ? 'bg-[rgba(39,201,63,0.15)] text-vivid-mint'
                  : 'bg-[rgba(230,113,79,0.15)] text-ember',
              )}
            >
              {result.passed ? (
                <CheckCircle2 className="h-8 w-8" aria-hidden="true" />
              ) : (
                <XCircle className="h-8 w-8" aria-hidden="true" />
              )}
            </span>
            <h1 className="text-heading font-semibold tracking-heading text-foreground">
              {result.score_percent}%
            </h1>
            <p className="text-body-sm text-muted-foreground">
              {t('publicExam.points', { raw: result.score_raw, max: result.score_max })}
              {result.status === 'expired' ? t('publicExam.submittedAtLimit') : ''}
            </p>
            <StatusTag tone={result.passed ? 'forest' : 'ember'} dot>
              {result.passed ? t('publicExam.passed') : t('publicExam.notThisTime')}
            </StatusTag>
            <p className="max-w-sm text-body-sm text-muted-foreground">
              {t('publicExam.resultThanks')}
            </p>
          </GlassCard>
        </motion.div>
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
      {
        icon: Languages,
        label: t('publicExam.factLanguage'),
        value: 'EN · हि · తె',
      },
    ];

    return (
      <div className="relative flex min-h-screen flex-col items-center justify-center bg-midnight px-6 py-12 font-sans text-foreground">
        <AuroraField />
        <div className="relative z-10 w-full max-w-[520px]">
          {/* Logo */}
          <div className="mb-6 flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-[linear-gradient(135deg,#112d72,#a887dc)]">
              <span className="h-2.5 w-2.5 rounded-full bg-white" />
            </span>
            <span className="text-[15px] font-semibold text-foreground">Anterview</span>
          </div>

          <GlassCard className="p-8">
            {/* Header row */}
            <div className="flex items-center justify-between">
              <StatusTag tone="forest" dot>
                {t('publicExam.liveExam')}
              </StatusTag>
              <span className="font-mono text-[11px] text-fog">#{token.slice(0, 10)}</span>
            </div>

            <h1 className="mt-4 text-[26px] font-semibold tracking-[-0.8px] text-foreground">
              {exam.title}
            </h1>
            {exam.round_title && exam.round_title !== exam.title && (
              <p className="mt-0.5 text-body-sm text-muted-foreground">
                {t('publicExam.roundLabel', { n: exam.round_number, title: exam.round_title })}
              </p>
            )}

            {exam.description && (
              <p className="mt-1.5 text-body-sm text-muted-foreground">{exam.description}</p>
            )}

            {/* Fact grid */}
            <div
              className={cn('mt-6 grid gap-3', facts.length === 3 ? 'grid-cols-3' : 'grid-cols-2')}
            >
              {facts.map((f) => {
                const Icon = f.icon;
                return (
                  <div
                    key={f.label}
                    className="rounded-[12px] border border-white/[0.08] bg-white/[0.02] p-4"
                  >
                    <Icon size={16} className="text-electric" aria-hidden="true" />
                    <div className="mt-2 text-[11px] uppercase tracking-[0.5px] text-fog">
                      {f.label}
                    </div>
                    <div className="mt-0.5 text-[13.5px] font-medium text-foreground">
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
                    className="flex items-center justify-between rounded-[10px] border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-caption"
                  >
                    <span className="text-muted-foreground">
                      {i + 1}. {s.title}
                    </span>
                    <span className="capitalize text-fog">{s.kind}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Timer warning */}
            {exam.time_limit_seconds && (
              <p className="mt-4 text-caption text-amber-glow">{t('publicExam.timerNote')}</p>
            )}

            {/* Proctoring notice */}
            <div className="mt-4 flex items-start gap-2 rounded-[10px] border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 text-caption text-muted-foreground">
              <ShieldCheck
                size={14}
                className="mt-0.5 shrink-0 text-vivid-mint"
                aria-hidden="true"
              />
              <span>{t('publicExam.proctoringNotice')}</span>
            </div>

            {/* DPDP consent */}
            <label className="mt-4 flex cursor-pointer items-start gap-2.5 rounded-[12px] border border-white/[0.08] bg-white/[0.02] p-4 text-[12.5px] text-mist">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
                className="mt-0.5 h-4 w-4 flex-none accent-electric"
              />
              <span className="flex items-center gap-1.5">
                <ShieldCheck size={14} className="flex-none text-vivid-mint" aria-hidden="true" />
                {t('publicExam.consentLabel')}
              </span>
            </label>

            <Pill
              className="mt-6 w-full py-3.5"
              disabled={!consent || startMut.isPending}
              onClick={() => void handleStart()}
            >
              {startMut.isPending ? (
                <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
              ) : null}
              {t('publicExam.startExam')}
            </Pill>

            {startMut.isError && (
              <p className="mt-3 text-center text-body-sm text-ember">
                {startMut.error instanceof Error
                  ? startMut.error.message
                  : t('publicExam.couldNotStart')}
              </p>
            )}
          </GlassCard>
        </div>
      </div>
    );
  }

  // ── Taking phase ─────────────────────────────────────────────────────────

  // Violation warning banner (non-blocking — shown inside the page, not as overlay).
  const showViolationWarning = violationCount > 0 && isFullscreen;

  return (
    <div className="min-h-screen bg-midnight font-sans">
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
      <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-white/[0.08] bg-obsidian/80 px-4 py-3 backdrop-blur-xl">
        <div className="min-w-0 flex-1">
          <p className="truncate text-body-sm font-semibold text-foreground">{exam.title}</p>
          {sections.length > 0 && currentSection && (
            <p className="text-caption text-muted-foreground">
              {t('publicExam.sectionProgress', {
                current: sectionIndex + 1,
                total: sections.length,
                title: currentSection.title,
              })}
            </p>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Violation badge */}
          {violationCount > 0 && (
            <div
              className="flex items-center gap-1 rounded-full bg-[rgba(230,113,79,0.15)] px-2.5 py-1 text-caption text-ember"
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
                'flex items-center gap-1.5 rounded-full px-3 py-1.5 text-caption font-medium tabular-nums',
                sectionRemaining <= 30
                  ? 'bg-[rgba(230,113,79,0.15)] text-ember'
                  : 'bg-white/[0.06] text-muted-foreground',
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
                'flex items-center gap-1.5 rounded-full px-3 py-1.5 text-body-sm font-medium tabular-nums',
                remaining <= 30
                  ? 'bg-[rgba(230,113,79,0.15)] text-ember'
                  : 'bg-white/[0.06] text-foreground',
              )}
              aria-live="polite"
              aria-label={t('publicExam.timeRemaining', { time: fmt(remaining) })}
            >
              <Clock className="h-4 w-4" aria-hidden="true" />
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
            className="flex items-center justify-center gap-2 border-b border-[rgba(230,113,79,0.25)] bg-[rgba(230,113,79,0.08)] px-4 py-2 text-caption text-ember"
            role="alert"
          >
            <AlertTriangle size={13} aria-hidden="true" />
            {t('publicExam.violationWarning', {
              count: violationCount,
              remaining: Math.max(0, maxViolations - violationCount),
            })}
          </motion.div>
        )}
      </AnimatePresence>

      <div className="mx-auto max-w-2xl px-4 py-8">
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
                      <GlassCard className="rounded-[16px] p-6 transition-colors hover:border-electric/30">
                        <p className="text-body font-medium text-foreground">
                          {i + 1}. {q.prompt}
                        </p>
                        <div className="mt-4 space-y-2" role="radiogroup" aria-label={q.prompt}>
                          {q.options.map((opt, oi) => {
                            const checked = answers[q.id] === oi;
                            return (
                              <label
                                key={oi}
                                className={cn(
                                  'flex cursor-pointer items-center gap-3 rounded-[12px] border px-3 py-2.5 text-body-sm transition-colors',
                                  checked
                                    ? 'border-electric/60 bg-electric/10 text-foreground'
                                    : 'border-white/[0.08] text-muted-foreground hover:border-white/20 hover:bg-white/[0.03]',
                                )}
                              >
                                <input
                                  type="radio"
                                  name={q.id}
                                  checked={checked}
                                  onChange={() => setAnswers((prev) => ({ ...prev, [q.id]: oi }))}
                                  className="h-4 w-4 accent-electric"
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
        <div className="mt-8 flex items-center justify-between gap-3 border-t border-white/[0.06] pt-6">
          <div className="text-caption text-muted-foreground">
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
              <Pill variant="ghost" onClick={() => setSectionIndex((i) => i + 1)}>
                {t('publicExam.nextSection')}
              </Pill>
            )}

            {/* Submit (shown on last section or when only one section) */}
            {sectionIndex === sections.length - 1 && (
              <Pill onClick={doSubmit} disabled={submitMut.isPending}>
                {submitMut.isPending ? (
                  <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
                ) : null}
                {t('publicExam.submitExam')}
              </Pill>
            )}
          </div>
        </div>

        {submitMut.isError && (
          <p className="mt-3 text-right text-body-sm text-ember">
            {submitMut.error instanceof Error
              ? submitMut.error.message
              : t('publicExam.submitFailed')}
          </p>
        )}
      </div>
    </div>
  );
}
