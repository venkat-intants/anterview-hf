// CodingTaking — the candidate coding-round take UI.
//
// Two usage modes:
//
//  1. STANDALONE (exam.kind === 'coding', legacy): receives the full TakeExam
//     object, manages its own deadline countdown and submit.
//
//  2. EMBEDDED (mixed round section): receives only the questions for this
//     section, reports answers up to the parent via `onSubmissionsChange`, and
//     does NOT render a submit button (the parent owns the single round submit).
//
// In both modes the component supports:
//   - "Run samples" against the backend /exam/run-code endpoint.
//   - "Run with custom input": a textarea + button that calls /exam/run-code-custom
//     and shows stdout / stderr / exit_code / timed_out.
//
// SECURITY: only sample tests + their expected output are shown — hidden tests
// are graded server-side and never returned here.

import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { motion } from 'framer-motion';
import { Loader2, Clock, Play, CheckCircle2, XCircle, Terminal } from '@/design/components/icons';
import {
  runCode,
  runCodeCustom,
  submitCoding,
  type CodingAnswer,
  type CustomRunResult,
  type ExamResult,
  type PublicCodingQuestion,
  type PublicTestResult,
  type TakeExam,
} from '@/api/publicExam';
import { cn } from '@/lib/utils';
import { GlassCard, Pill } from '@/design/components/primitives';

const CodeEditor = lazy(() => import('@/components/CodeEditor'));

const LANG_LABEL: Record<string, string> = {
  python: 'Python',
  javascript: 'JavaScript',
  typescript: 'TypeScript',
  java: 'Java',
  cpp: 'C++',
  c: 'C',
  go: 'Go',
  csharp: 'C#',
  ruby: 'Ruby',
  rust: 'Rust',
};

function fmt(totalSec: number): string {
  const s = Math.max(0, totalSec);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

// ── Props: two mutually-exclusive usage modes ─────────────────────────────────

/** Standalone mode: wraps the whole exam (kind === 'coding'). */
interface StandaloneProps {
  mode?: 'standalone';
  token: string;
  exam: TakeExam;
  attemptId: string;
  deadline: string | null;
  onResult: (r: ExamResult) => void;
  // Not used in standalone mode — kept undefined.
  questions?: undefined;
  onSubmissionsChange?: undefined;
}

/** Embedded mode: one section of a mixed/coding round. */
interface EmbeddedProps {
  mode: 'embedded';
  token: string;
  /** The section's coding questions (already sorted by position). */
  questions: PublicCodingQuestion[];
  /** Called whenever the user edits any question — parent accumulates submissions. */
  onSubmissionsChange: (submissions: Record<string, CodingAnswer>) => void;
  // Not used in embedded mode.
  exam?: undefined;
  attemptId?: undefined;
  deadline?: undefined;
  onResult?: undefined;
}

type Props = StandaloneProps | EmbeddedProps;

export default function CodingTaking(props: Props) {
  const { t } = useTranslation();

  // Resolve the question list regardless of mode.
  const questions = useMemo<PublicCodingQuestion[]>(() => {
    if (props.mode === 'embedded') {
      return [...props.questions].sort((a, b) => a.position - b.position);
    }
    return [...(props.exam?.coding_questions ?? [])].sort((a, b) => a.position - b.position);
  }, [props.mode, props.questions, props.exam]);

  const [code, setCode] = useState<Record<string, string>>(() =>
    Object.fromEntries(questions.map((q) => [q.id, q.starter_code ?? ''])),
  );
  const [lang, setLang] = useState<Record<string, string>>(() =>
    Object.fromEntries(questions.map((q) => [q.id, q.allowed_languages[0] ?? 'python'])),
  );

  // Sample-run state
  const [runResults, setRunResults] = useState<Record<string, PublicTestResult[]>>({});
  const [runningQid, setRunningQid] = useState<string | null>(null);

  // Custom-input state (per question)
  const [customInput, setCustomInput] = useState<Record<string, string>>({});
  const [customRunning, setCustomRunning] = useState<string | null>(null);
  const [customResult, setCustomResult] = useState<Record<string, CustomRunResult>>({});
  const [showCustom, setShowCustom] = useState<Record<string, boolean>>({});

  // Standalone-only: countdown + submit
  const [remaining, setRemaining] = useState<number | null>(null);
  const submittedRef = useRef(false);

  // ── Notify parent of submission changes (embedded mode) ──────────────────
  const { onSubmissionsChange } =
    props.mode === 'embedded' ? props : { onSubmissionsChange: undefined };

  const buildSubmissions = useCallback(
    (codeMap: Record<string, string>, langMap: Record<string, string>) =>
      Object.fromEntries(
        questions.map((q) => [
          q.id,
          { language: langMap[q.id] ?? 'python', source: codeMap[q.id] ?? '' },
        ]),
      ),
    [questions],
  );

  useEffect(() => {
    if (props.mode !== 'embedded' || !onSubmissionsChange) return;
    onSubmissionsChange(buildSubmissions(code, lang));
    // Only run when code or lang actually changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, lang]);

  // ── Standalone: countdown ────────────────────────────────────────────────
  useEffect(() => {
    if (props.mode === 'embedded') return;
    const deadline = props.deadline;
    if (!deadline) return;
    const target = new Date(deadline).getTime();
    const tick = () => setRemaining(Math.round((target - Date.now()) / 1000));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [props.mode, props.deadline]);

  const submitMut = useMutation({
    mutationFn: () => {
      if (props.mode === 'embedded') throw new Error('Embedded mode: no standalone submit');
      return submitCoding(props.token, props.attemptId, buildSubmissions(code, lang));
    },
    onSuccess: (r) => {
      if (props.mode !== 'embedded') props.onResult(r);
    },
    onError: () => {
      submittedRef.current = false;
    },
  });

  const doSubmit = useCallback(() => {
    if (props.mode === 'embedded') return;
    if (submittedRef.current) return;
    submittedRef.current = true;
    submitMut.mutate();
  }, [props.mode, submitMut]);

  // Auto-submit when the standalone clock hits zero.
  useEffect(() => {
    if (props.mode === 'embedded') return;
    if (remaining !== null && remaining <= 0) doSubmit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [remaining]);

  // ── Sample run ───────────────────────────────────────────────────────────
  const runMut = useMutation({
    mutationFn: (qid: string) => runCode(props.token, qid, lang[qid] ?? 'python', code[qid] ?? ''),
    onSuccess: (res, qid) => setRunResults((p) => ({ ...p, [qid]: res.results })),
    onSettled: () => setRunningQid(null),
  });

  const runSamples = (qid: string) => {
    setRunningQid(qid);
    runMut.mutate(qid);
  };

  // ── Custom input run ─────────────────────────────────────────────────────
  const customRunMut = useMutation({
    mutationFn: (qid: string) =>
      runCodeCustom(props.token, {
        question_id: qid,
        language: lang[qid] ?? 'python',
        source: code[qid] ?? '',
        stdin: customInput[qid] ?? '',
      }),
    onSuccess: (res, qid) => setCustomResult((p) => ({ ...p, [qid]: res })),
    onSettled: () => setCustomRunning(null),
  });

  const runCustom = (qid: string) => {
    setCustomRunning(qid);
    customRunMut.mutate(qid);
  };

  // ── Render ───────────────────────────────────────────────────────────────

  const questionCards = questions.map((q, i) => {
    const results = runResults[q.id];
    const isRunning = runMut.isPending && runningQid === q.id;
    const isCustomRunning = customRunMut.isPending && customRunning === q.id;
    const cr = customResult[q.id];
    const isCustomOpen = showCustom[q.id] ?? false;

    return (
      <motion.div
        key={q.id}
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: i * 0.04 }}
      >
        <GlassCard className="space-y-4 rounded-[16px] p-6">
          {/* Prompt + points */}
          <div className="flex items-start justify-between gap-3">
            <p className="whitespace-pre-wrap text-body font-medium text-foreground">
              {i + 1}. {q.prompt}
            </p>
            <span className="shrink-0 rounded-full bg-white/[0.06] px-2.5 py-1 text-caption text-muted-foreground">
              {q.points} pts
            </span>
          </div>

          {/* Sample tests */}
          {q.sample_tests.length > 0 && (
            <div className="grid gap-2 sm:grid-cols-2">
              {q.sample_tests.map((s, si) => (
                <div
                  key={si}
                  className="rounded-[10px] border border-white/[0.08] bg-white/[0.02] p-3 text-caption"
                >
                  <div className="text-fog">{t('publicExam.codingInputLabel')}</div>
                  <pre className="mt-0.5 whitespace-pre-wrap font-mono text-[12px] text-mist">
                    {s.stdin || '(none)'}
                  </pre>
                  <div className="mt-2 text-fog">{t('publicExam.codingExpectedLabel')}</div>
                  <pre className="mt-0.5 whitespace-pre-wrap font-mono text-[12px] text-mist">
                    {s.expected_output}
                  </pre>
                </div>
              ))}
            </div>
          )}

          {/* Language picker */}
          <div className="flex items-center gap-2">
            <label className="text-caption text-muted-foreground">
              {t('publicExam.codingLanguageLabel')}
            </label>
            <select
              value={lang[q.id]}
              onChange={(e) => setLang((p) => ({ ...p, [q.id]: e.target.value }))}
              className="rounded-[8px] border border-white/[0.1] bg-[rgba(28,29,31,0.7)] px-2.5 py-1.5 text-body-sm text-foreground focus:border-electric focus:outline-none"
            >
              {q.allowed_languages.map((l) => (
                <option key={l} value={l}>
                  {LANG_LABEL[l] ?? l}
                </option>
              ))}
            </select>
          </div>

          {/* Editor */}
          <Suspense
            fallback={
              <div className="flex h-40 items-center justify-center rounded-[12px] border border-white/[0.08] bg-[#0b0c0e]">
                <Loader2 className="h-5 w-5 animate-spin text-electric" aria-hidden="true" />
              </div>
            }
          >
            <CodeEditor
              language={lang[q.id] ?? 'python'}
              value={code[q.id] ?? ''}
              onChange={(next) => setCode((p) => ({ ...p, [q.id]: next }))}
              placeholder={t('publicExam.codingEditorPlaceholder')}
              textareaId={`ce-take-${q.id}`}
            />
          </Suspense>

          {/* Run samples + sample results */}
          <div className="flex flex-wrap items-center gap-3">
            <Pill
              variant="ghost"
              onClick={() => runSamples(q.id)}
              disabled={isRunning}
              className="gap-1.5"
            >
              {isRunning ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Play size={14} aria-hidden="true" />
              )}
              {t('publicExam.runSamples')}
            </Pill>
            {results && (
              <span className="text-caption text-muted-foreground">
                {results.filter((r) => r.passed).length}/{results.length}{' '}
                {t('publicExam.samplesPassing', { count: results.length })}
              </span>
            )}
            {/* Custom input toggle */}
            <button
              type="button"
              onClick={() => setShowCustom((p) => ({ ...p, [q.id]: !isCustomOpen }))}
              className="ml-auto flex items-center gap-1.5 rounded-full border border-white/[0.1] px-3 py-1.5 text-caption text-muted-foreground transition-colors hover:border-white/20 hover:text-foreground"
              aria-expanded={isCustomOpen}
            >
              <Terminal size={12} aria-hidden="true" />
              {t('publicExam.customInput')}
            </button>
          </div>

          {results && results.length > 0 && (
            <div className="space-y-2">
              {results.map((r) => (
                <div
                  key={r.index}
                  className={cn(
                    'rounded-[10px] border p-3 text-caption',
                    r.passed
                      ? 'border-[rgba(39,201,63,0.25)] bg-[rgba(39,201,63,0.05)]'
                      : 'border-[rgba(230,113,79,0.25)] bg-[rgba(230,113,79,0.05)]',
                  )}
                >
                  <div className="flex items-center gap-1.5 font-medium">
                    {r.passed ? (
                      <CheckCircle2 size={13} className="text-vivid-mint" aria-hidden="true" />
                    ) : (
                      <XCircle size={13} className="text-ember" aria-hidden="true" />
                    )}
                    {t('publicExam.sampleN', { n: r.index + 1 })}
                    {r.timed_out ? ` · ${t('publicExam.timedOut')}` : ''}
                    {r.error ? ` · ${r.error}` : ''}
                  </div>
                  {!r.passed && (
                    <div className="mt-1.5 grid gap-1 font-mono text-[12px] text-mist sm:grid-cols-2">
                      <div>
                        <span className="text-fog">{t('publicExam.codingExpected')}</span>
                        <pre className="whitespace-pre-wrap">{r.expected_output}</pre>
                      </div>
                      <div>
                        <span className="text-fog">{t('publicExam.codingGot')}</span>
                        <pre className="whitespace-pre-wrap">{r.actual_output || '(empty)'}</pre>
                      </div>
                      {r.stderr && (
                        <div className="sm:col-span-2">
                          <span className="text-fog">stderr</span>
                          <pre className="whitespace-pre-wrap text-ember">{r.stderr}</pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Custom input panel */}
          {isCustomOpen && (
            <div className="space-y-2 rounded-[12px] border border-white/[0.08] bg-white/[0.02] p-4">
              <label className="text-caption text-muted-foreground">
                {t('publicExam.customInputLabel')}
              </label>
              <textarea
                value={customInput[q.id] ?? ''}
                onChange={(e) => setCustomInput((p) => ({ ...p, [q.id]: e.target.value }))}
                rows={4}
                placeholder={t('publicExam.customInputPlaceholder')}
                className="w-full resize-y rounded-[8px] border border-white/[0.1] bg-[#0b0c0e] px-3 py-2.5 font-mono text-[12px] text-foreground placeholder:text-fog focus:border-electric focus:outline-none"
                aria-label={t('publicExam.customInputLabel')}
              />
              <div className="flex items-center gap-3">
                <Pill
                  variant="ghost"
                  onClick={() => runCustom(q.id)}
                  disabled={isCustomRunning}
                  className="gap-1.5"
                >
                  {isCustomRunning ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Play size={14} aria-hidden="true" />
                  )}
                  {t('publicExam.runCustomInput')}
                </Pill>
              </div>

              {/* Custom run output */}
              {cr && (
                <div
                  className={cn(
                    'rounded-[10px] border p-3 text-caption',
                    cr.timed_out || cr.exit_code !== 0
                      ? 'border-[rgba(230,113,79,0.25)] bg-[rgba(230,113,79,0.05)]'
                      : 'border-[rgba(39,201,63,0.25)] bg-[rgba(39,201,63,0.05)]',
                  )}
                >
                  <div className="flex items-center gap-1.5 font-medium text-foreground">
                    <Terminal size={12} aria-hidden="true" />
                    {t('publicExam.customRunOutput')}
                    {cr.timed_out && (
                      <span className="ml-1 text-ember">· {t('publicExam.timedOut')}</span>
                    )}
                    {!cr.timed_out && cr.exit_code !== null && cr.exit_code !== 0 && (
                      <span className="ml-1 text-ember">
                        · {t('publicExam.exitCode', { code: cr.exit_code })}
                      </span>
                    )}
                  </div>
                  {cr.error && (
                    <pre className="mt-1 whitespace-pre-wrap font-mono text-[12px] text-ember">
                      {cr.error}
                    </pre>
                  )}
                  {cr.stdout && (
                    <div className="mt-1.5">
                      <span className="text-fog">stdout</span>
                      <pre className="whitespace-pre-wrap font-mono text-[12px] text-mist">
                        {cr.stdout}
                      </pre>
                    </div>
                  )}
                  {cr.stderr && (
                    <div className="mt-1.5">
                      <span className="text-fog">stderr</span>
                      <pre className="whitespace-pre-wrap font-mono text-[12px] text-ember">
                        {cr.stderr}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </GlassCard>
      </motion.div>
    );
  });

  // ── Embedded mode: render question cards only, no wrapper shell ───────────
  if (props.mode === 'embedded') {
    return <div className="space-y-6">{questionCards}</div>;
  }

  // ── Standalone mode: full page shell with sticky bar + submit ─────────────
  const exam = props.exam;
  return (
    <div className="min-h-screen bg-midnight font-sans">
      {/* Sticky bar */}
      <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-white/[0.08] bg-obsidian/80 px-4 py-3 backdrop-blur-xl">
        <div className="min-w-0">
          <p className="truncate text-body-sm font-semibold text-foreground">{exam.title}</p>
          <p className="text-caption text-muted-foreground">
            {questions.length} {t('publicExam.codingProblems', { count: questions.length })}
          </p>
        </div>
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

      <div className="mx-auto max-w-3xl space-y-6 px-4 py-8">
        {questionCards}

        {/* Submit */}
        <div className="flex items-center justify-between gap-3 pt-2">
          <p className="text-caption text-muted-foreground">{t('publicExam.hiddenTestsNote')}</p>
          <Pill onClick={doSubmit} disabled={submitMut.isPending}>
            {submitMut.isPending ? (
              <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
            ) : null}
            {t('publicExam.submitSolution')}
          </Pill>
        </div>
        {submitMut.isError && (
          <p className="text-right text-body-sm text-ember">
            {submitMut.error instanceof Error
              ? submitMut.error.message
              : t('publicExam.submitFailed')}
          </p>
        )}
        {submitMut.isPending && (
          <p className="text-right text-caption text-muted-foreground">
            {t('publicExam.runningAllTests')}
          </p>
        )}
      </div>
    </div>
  );
}
