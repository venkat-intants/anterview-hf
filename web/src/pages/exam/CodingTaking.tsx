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
import { GlassCard } from '@/design/components/primitives';
import { Button } from '@/components/ui/button';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useCandidateError } from '@/lib/candidateCopy';

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
  /** Code already written for these questions (restored after a reload). */
  initialSubmissions?: Record<string, CodingAnswer>;
  // Not used in embedded mode.
  exam?: undefined;
  attemptId?: undefined;
  deadline?: undefined;
  onResult?: undefined;
}

type Props = StandaloneProps | EmbeddedProps;

export default function CodingTaking(props: Props) {
  const { t } = useTranslation();
  const errorText = useCandidateError();

  // Resolve the question list regardless of mode.
  const questions = useMemo<PublicCodingQuestion[]>(() => {
    if (props.mode === 'embedded') {
      return [...props.questions].sort((a, b) => a.position - b.position);
    }
    return [...(props.exam?.coding_questions ?? [])].sort((a, b) => a.position - b.position);
  }, [props.mode, props.questions, props.exam]);

  // Read once, at mount: what the candidate had already written wins over the
  // starter code (audit CA-M2).
  const initial = props.mode === 'embedded' ? props.initialSubmissions : undefined;
  const [code, setCode] = useState<Record<string, string>>(() =>
    Object.fromEntries(questions.map((q) => [q.id, initial?.[q.id]?.source ?? q.starter_code ?? ''])),
  );
  const [lang, setLang] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      questions.map((q) => {
        const saved = initial?.[q.id]?.language;
        const usable = saved && q.allowed_languages.includes(saved) ? saved : undefined;
        return [q.id, usable ?? q.allowed_languages[0] ?? 'python'];
      }),
    ),
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
        <GlassCard className="space-y-4 p-4 sm:p-6">
          {/* Prompt + points */}
          <div className="flex items-start justify-between gap-3">
            <p className="whitespace-pre-wrap text-[16px] font-semibold leading-relaxed text-foreground">
              {i + 1}. {q.prompt}
            </p>
            <span className="shrink-0 rounded-full bg-[var(--ui-inset)] px-3 py-1 text-[13px] font-medium text-muted-foreground">
              {q.points} pts
            </span>
          </div>

          {/* Sample tests */}
          {q.sample_tests.length > 0 && (
            <div className="grid gap-2 sm:grid-cols-2">
              {q.sample_tests.map((s, si) => (
                <div
                  key={si}
                  className="rounded-[10px] border border-border bg-[var(--ui-inset-soft)] p-3 text-caption"
                >
                  <div className="text-[var(--ui-soft)]">{t('publicExam.codingInputLabel')}</div>
                  <pre className="mt-0.5 whitespace-pre-wrap font-mono text-[13px] text-foreground">
                    {s.stdin || '(none)'}
                  </pre>
                  <div className="mt-2 text-[var(--ui-soft)]">{t('publicExam.codingExpectedLabel')}</div>
                  <pre className="mt-0.5 whitespace-pre-wrap font-mono text-[13px] text-foreground">
                    {s.expected_output}
                  </pre>
                </div>
              ))}
            </div>
          )}

          {/* Language picker */}
          <div className="flex items-center gap-2">
            <label htmlFor={`lang-${q.id}`} className="text-[13px] font-medium text-foreground">
              {t('publicExam.codingLanguageLabel')}
            </label>
            <SelectControl
              id={`lang-${q.id}`}
              value={lang[q.id]}
              onChange={(e) => setLang((p) => ({ ...p, [q.id]: e.target.value }))}
              className="w-auto"
            >
              {q.allowed_languages.map((l) => (
                <option key={l} value={l}>
                  {LANG_LABEL[l] ?? l}
                </option>
              ))}
            </SelectControl>
          </div>

          {/* Editor */}
          <Suspense
            fallback={<Skeleton aria-hidden="true" className="h-40 w-full rounded-[16px]" />}
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
            <Button
              variant="outline"
              onClick={() => runSamples(q.id)}
              disabled={isRunning}
              className="h-11 gap-1.5"
            >
              {isRunning ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Play size={14} aria-hidden="true" />
              )}
              {t('publicExam.runSamples')}
            </Button>
            {results && (
              <span className="text-[13px] text-muted-foreground" role="status">
                {results.filter((r) => r.passed).length}/{results.length}{' '}
                {t('publicExam.samplesPassing', { count: results.length })}
              </span>
            )}
            {/* Custom input toggle */}
            <button
              type="button"
              onClick={() => setShowCustom((p) => ({ ...p, [q.id]: !isCustomOpen }))}
              className="ml-auto flex min-h-11 items-center gap-1.5 rounded-full border border-border px-4 text-[13px] font-medium text-muted-foreground transition-colors hover:border-[var(--ui-line-strong)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
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
                    'rounded-[12px] border p-3 text-[13px]',
                    r.passed
                      ? 'border-[var(--ui-ok)]/40 bg-[var(--ui-ok-wash)]'
                      : 'border-[var(--ui-danger)]/40 bg-[var(--ui-danger-wash)]',
                  )}
                >
                  <div className="flex items-center gap-1.5 font-medium">
                    {r.passed ? (
                      <CheckCircle2 size={13} className="text-[var(--ui-ok)]" aria-hidden="true" />
                    ) : (
                      <XCircle size={13} className="text-[var(--ui-danger)]" aria-hidden="true" />
                    )}
                    {t('publicExam.sampleN', { n: r.index + 1 })}
                    {r.timed_out ? ` · ${t('publicExam.timedOut')}` : ''}
                    {r.error ? ` · ${r.error}` : ''}
                  </div>
                  {!r.passed && (
                    <div className="mt-1.5 grid gap-1 font-mono text-[13px] text-foreground sm:grid-cols-2">
                      <div>
                        <span className="text-[var(--ui-soft)]">{t('publicExam.codingExpected')}</span>
                        <pre className="whitespace-pre-wrap">{r.expected_output}</pre>
                      </div>
                      <div>
                        <span className="text-[var(--ui-soft)]">{t('publicExam.codingGot')}</span>
                        <pre className="whitespace-pre-wrap">{r.actual_output || '(empty)'}</pre>
                      </div>
                      {r.stderr && (
                        <div className="sm:col-span-2">
                          <span className="text-[var(--ui-soft)]">stderr</span>
                          <pre className="whitespace-pre-wrap text-[var(--ui-danger)]">{r.stderr}</pre>
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
            <div className="space-y-2 rounded-[16px] border border-border bg-[var(--ui-inset-soft)] p-4">
              <label
                htmlFor={`custom-input-${q.id}`}
                className="block text-[13px] font-medium text-foreground"
              >
                {t('publicExam.customInputLabel')}
              </label>
              <Textarea
                id={`custom-input-${q.id}`}
                value={customInput[q.id] ?? ''}
                onChange={(e) => setCustomInput((p) => ({ ...p, [q.id]: e.target.value }))}
                rows={4}
                placeholder={t('publicExam.customInputPlaceholder')}
                className="font-mono md:text-[13px]"
                aria-label={t('publicExam.customInputLabel')}
              />
              <div className="flex items-center gap-3">
                <Button
                  variant="outline"
                  onClick={() => runCustom(q.id)}
                  disabled={isCustomRunning}
                  className="h-11 gap-1.5"
                >
                  {isCustomRunning ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Play size={14} aria-hidden="true" />
                  )}
                  {t('publicExam.runCustomInput')}
                </Button>
              </div>

              {/* Custom run output */}
              {cr && (
                <div
                  className={cn(
                    'rounded-[12px] border p-3 text-[13px]',
                    cr.timed_out || cr.exit_code !== 0
                      ? 'border-[var(--ui-danger)]/40 bg-[var(--ui-danger-wash)]'
                      : 'border-[var(--ui-ok)]/40 bg-[var(--ui-ok-wash)]',
                  )}
                >
                  <div className="flex items-center gap-1.5 font-medium text-foreground">
                    <Terminal size={12} aria-hidden="true" />
                    {t('publicExam.customRunOutput')}
                    {cr.timed_out && (
                      <span className="ml-1 text-[var(--ui-danger)]">· {t('publicExam.timedOut')}</span>
                    )}
                    {!cr.timed_out && cr.exit_code !== null && cr.exit_code !== 0 && (
                      <span className="ml-1 text-[var(--ui-danger)]">
                        · {t('publicExam.exitCode', { code: cr.exit_code })}
                      </span>
                    )}
                  </div>
                  {cr.error && (
                    <pre className="mt-1 whitespace-pre-wrap font-mono text-[13px] text-[var(--ui-danger)]">
                      {cr.error}
                    </pre>
                  )}
                  {cr.stdout && (
                    <div className="mt-1.5">
                      <span className="text-[var(--ui-soft)]">stdout</span>
                      <pre className="whitespace-pre-wrap font-mono text-[13px] text-foreground">
                        {cr.stdout}
                      </pre>
                    </div>
                  )}
                  {cr.stderr && (
                    <div className="mt-1.5">
                      <span className="text-[var(--ui-soft)]">stderr</span>
                      <pre className="whitespace-pre-wrap font-mono text-[13px] text-[var(--ui-danger)]">
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
    <div className="min-h-screen bg-background font-sans">
      {/* Sticky bar */}
      <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-border bg-card px-4 py-3">
        <div className="min-w-0">
          <p className="truncate text-body-sm font-semibold text-foreground">{exam.title}</p>
          <p className="text-caption text-muted-foreground">
            {t('publicExam.codingProblems', { count: questions.length })}
          </p>
        </div>
        {remaining !== null && (
          <div
            className={cn(
              'flex items-center gap-1.5 rounded-full px-3.5 py-2 text-[16px] font-semibold tabular-nums',
              remaining <= 30
                ? 'bg-[var(--ui-danger-wash)] text-[var(--ui-danger)]'
                : 'bg-[var(--ui-inset)] text-foreground',
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
        <div className="flex flex-col gap-3 pt-2 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-[13px] text-muted-foreground">{t('publicExam.hiddenTestsNote')}</p>
          <Button onClick={doSubmit} disabled={submitMut.isPending} className="h-12 sm:px-8">
            {submitMut.isPending ? (
              <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
            ) : null}
            {t('publicExam.submitSolution')}
          </Button>
        </div>
        {submitMut.isError && (
          <p role="alert" className="text-right text-[14px] text-[var(--ui-danger)]">
            {errorText(submitMut.error, t('publicExam.submitFailed'))}
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
