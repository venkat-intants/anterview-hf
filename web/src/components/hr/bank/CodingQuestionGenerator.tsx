// CodingQuestionGenerator — draft coding questions for a bank with AI.
//
// Three steps, in one panel:
//
//   1. DESCRIBE   topic, difficulty, statement language, programming
//                 languages, how many.
//   2. GENERATE   one request, usually under a minute. No percentage is shown
//                 because none is known; what is shown is the elapsed time and
//                 a Cancel that really abandons the request.
//   3. REVIEW     every draft, in full, with keep / discard, then one button
//                 that saves the kept ones.
//
// AI IS A PREVIEW, NEVER A SAVE. Generating stores nothing. "Add n to bank as
// drafts" is the only write, and what it writes are DRAFTS (origin
// `ai_draft`): each still has to be sent for review and approved by somebody
// other than the person who added it, exactly like a question typed by hand.
//
// NOBODY HAS RUN THE CODE. The tests (and any reference solution) come from the
// model and are not executed by the server. The review step says so once in
// words, and every draft carries the server's "Unverified" marker.
//
// The panel renders its own Card. The save is a mutation whose own `onSuccess`
// invalidates the bank's lists through the query client, so they refresh even if
// the panel was closed before the save resolved; `onGenerated` is the parent's
// notification and is skipped when the panel is no longer mounted.
//
// English-only by design (CLAUDE.md — staff consoles are not translated). The
// drafted STATEMENT is candidate-facing and is written in the language chosen
// here (EN / HI / TE), the same choice the MCQ generator offers.

import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Loader2, RefreshCw, Sparkles } from '@/design/components/icons';
import { Card } from '@/design/components/card';
import { Stepper, type Step } from '@/design/components/page';
import { Well } from '@/design/components/rows';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { toGenerationFailure, type GenerationFailure } from '@/lib/generationError';
import { GenerationError } from '@/components/bank/GenerationError';
import { codingLanguageLabel } from '@/lib/codingLanguages';
import {
  BANK_CODING_MAX_GENERATED,
  createBankQuestionsBulk,
  generateBankCodingQuestions,
  toBankQuestionInput,
  type BankCodingGenerateParams,
  type BankCodingGenerateResult,
  type BankDifficulty,
  type BankQuestion,
  type BankQuestionInput,
} from '@/api/questionBanks';
import { CODING_LANGUAGES, type ExamLanguage } from '@/api/exams';
import { BankCodingQuestionView } from './BankCodingQuestionView';

export interface CodingQuestionGeneratorProps {
  /** The bank the drafts are added to. */
  bankId: string;
  /**
   * Called once, after the kept drafts have been SAVED, with the saved rows
   * (status `draft`, origin `ai_draft`). Not called on generate, on cancel or
   * on discard. Refresh the bank's question list here.
   */
  onGenerated: (questions: BankQuestion[]) => void;
  /** Blocks generating and saving, e.g. while the bank itself is loading. */
  disabled?: boolean;
}

type StepKey = 'describe' | 'generating' | 'review';

const STEPS: Step[] = [
  { key: 'describe', label: 'Describe' },
  { key: 'generating', label: 'Generate' },
  { key: 'review', label: 'Review drafts' },
];

const DIFFICULTIES: BankDifficulty[] = ['easy', 'medium', 'hard'];
const STATEMENT_LANGUAGES: { value: ExamLanguage; label: string }[] = [
  { value: 'en', label: 'English' },
  { value: 'hi', label: 'Hindi' },
  { value: 'te', label: 'Telugu' },
];
const COUNTS = Array.from({ length: BANK_CODING_MAX_GENERATED }, (_, i) => i + 1);
const TOPIC_MIN = 2;
const TOPIC_MAX = 200;

const LABEL = 'mb-1.5 block text-[13px] font-semibold text-foreground';
const HELP = 'mt-1.5 text-[12.5px] leading-relaxed text-muted-foreground';
const FIELD_ERROR = 'mt-1.5 text-[12.5px] font-medium text-[var(--ui-danger)]';
// 40px tall, and at least 40px wide for the one-character count chips.
const CHIP =
  'inline-flex min-h-10 min-w-10 items-center justify-center rounded-full border px-4 text-[13px] font-medium ' +
  'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] ' +
  'focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50';
const CHIP_ON =
  'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.14)] text-[var(--ui-info)]';
const CHIP_OFF = 'border-border text-muted-foreground hover:text-foreground';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function plural(n: number, one: string, many: string = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** A group of toggle chips with a visible label that names the group. */
function ChipGroup({
  label,
  help,
  error,
  children,
}: {
  label: string;
  help?: string;
  error?: string;
  children: React.ReactNode;
}): JSX.Element {
  const id = useId();
  const errorId = `${id}-error`;
  return (
    <div role="group" aria-labelledby={id} aria-describedby={error ? errorId : undefined}>
      <p id={id} className={LABEL}>
        {label}
      </p>
      <div className="flex flex-wrap gap-2">{children}</div>
      {help ? <p className={HELP}>{help}</p> : null}
      {error ? (
        <p id={errorId} className={FIELD_ERROR}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

function SaveError({
  title,
  message,
  onRetry,
  retryDisabled,
}: {
  title: string;
  message: string;
  onRetry: () => void;
  retryDisabled?: boolean;
}): JSX.Element {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-start justify-between gap-3 rounded-[12px] bg-[var(--ui-danger-wash)] p-3.5"
    >
      <div className="flex min-w-0 items-start gap-2">
        <AlertTriangle
          size={16}
          className="mt-0.5 flex-none text-[var(--ui-danger)]"
          aria-hidden="true"
        />
        <div className="min-w-0">
          <p className="text-[13.5px] font-semibold text-foreground">{title}</p>
          <p className="mt-0.5 break-words text-[13px] leading-relaxed text-foreground">
            {message}
          </p>
        </div>
      </div>
      <Button type="button" variant="outline" onClick={onRetry} disabled={retryDisabled}>
        <RefreshCw aria-hidden="true" />
        Try again
      </Button>
    </div>
  );
}

export function CodingQuestionGenerator({
  bankId,
  onGenerated,
  disabled = false,
}: CodingQuestionGeneratorProps): JSX.Element {
  const topicId = useId();
  const roleId = useId();

  const [step, setStep] = useState<StepKey>('describe');
  const [topic, setTopic] = useState('');
  const [jobTitle, setJobTitle] = useState('');
  const [difficulty, setDifficulty] = useState<BankDifficulty>('medium');
  const [language, setLanguage] = useState<ExamLanguage>('en');
  const [langs, setLangs] = useState<string[]>(['python']);
  const [count, setCount] = useState(2);
  // Field errors appear only after the first attempt, then track the input.
  const [attempted, setAttempted] = useState(false);

  const [genError, setGenError] = useState<GenerationFailure | null>(null);
  const [result, setResult] = useState<BankCodingGenerateResult | null>(null);
  const [discarded, setDiscarded] = useState<ReadonlySet<number>>(new Set());
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedCount, setSavedCount] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const qc = useQueryClient();
  const mounted = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const firstRender = useRef(true);

  const topicError =
    topic.trim().length < TOPIC_MIN ? 'Say what the questions should be about.' : undefined;
  const langsError = langs.length === 0 ? 'Pick at least one programming language.' : undefined;

  // Abandon an in-flight request if the panel goes away, and remember that it
  // has, so a save that resolves later does not call into an unmounted parent.
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      abortRef.current?.abort();
    };
  }, []);

  const saveMut = useMutation({
    mutationFn: (questions: BankQuestionInput[]) => createBankQuestionsBulk(bankId, questions),
    // Independent of mount state: the lists are stale the moment the rows exist.
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['hr', 'question-banks'] });
      void qc.invalidateQueries({ queryKey: ['hr', 'bank-questions'] });
      void qc.invalidateQueries({ queryKey: ['hr', 'bank-review-queue'] });
    },
  });
  const saving = saveMut.isPending;

  // The elapsed clock is the only progress there is to report.
  useEffect(() => {
    if (step !== 'generating') return undefined;
    setElapsed(0);
    const started = Date.now();
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [step]);

  // Each step replaces the one before it, so focus follows to its heading —
  // otherwise a keyboard user is left on a button that no longer exists.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    headingRef.current?.focus();
  }, [step]);

  const generate = useCallback(async (): Promise<void> => {
    setAttempted(true);
    if (disabled || topic.trim().length < TOPIC_MIN || langs.length === 0) return;
    const params: BankCodingGenerateParams = {
      topic: topic.trim(),
      num_questions: count,
      difficulty,
      language,
      allowed_languages: langs,
      job_title: jobTitle.trim(),
    };
    const controller = new AbortController();
    abortRef.current = controller;
    setGenError(null);
    setSavedCount(null);
    setSaveError(null);
    setStep('generating');
    try {
      const out = await generateBankCodingQuestions(bankId, params, {
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setResult(out);
      setDiscarded(new Set());
      setStep('review');
    } catch (e) {
      if (controller.signal.aborted) return;
      setGenError(toGenerationFailure(e, 'The questions could not be generated.'));
      setStep('describe');
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, [bankId, count, difficulty, disabled, jobTitle, langs, language, topic]);

  const cancel = (): void => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStep('describe');
  };

  const startOver = (): void => {
    setResult(null);
    setDiscarded(new Set());
    setSaveError(null);
    setStep('describe');
  };

  const kept = result ? result.questions.filter((_, i) => !discarded.has(i)) : [];

  const save = async (): Promise<void> => {
    if (disabled || saving || kept.length === 0) return;
    setSaveError(null);
    try {
      const saved = await saveMut.mutateAsync(kept.map(toBankQuestionInput));
      if (!mounted.current) return;
      setSavedCount(saved.length);
      setResult(null);
      setDiscarded(new Set());
      setStep('describe');
      onGenerated(saved);
    } catch (e) {
      if (!mounted.current) return;
      setSaveError(errText(e, 'The drafts could not be added.'));
    }
  };

  const toggleLang = (slug: string): void =>
    setLangs((cur) => (cur.includes(slug) ? cur.filter((l) => l !== slug) : [...cur, slug]));

  const toggleDiscard = (index: number): void =>
    setDiscarded((cur) => {
      const next = new Set(cur);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });

  const stepHeading = (text: string): JSX.Element => (
    <h3
      ref={headingRef}
      tabIndex={-1}
      className="text-[15px] font-semibold text-foreground focus-visible:outline-none"
    >
      {text}
    </h3>
  );

  return (
    <Card
      as="section"
      icon={Sparkles}
      title="Draft coding questions with AI"
      description="Describe what to assess, read what comes back, and add the ones worth keeping. Nothing is saved until you add it, and everything you add is a draft that someone else must approve."
      data-testid="coding-question-generator"
    >
      <Stepper steps={STEPS} current={step} label="Drafting coding questions" />

      {/* ── 1. Describe ─────────────────────────────────────────────────── */}
      {step === 'describe' ? (
        <form
          className="mt-6 flex flex-col gap-5"
          noValidate
          data-testid="generator-step-describe"
          onSubmit={(e) => {
            e.preventDefault();
            void generate();
          }}
        >
          {stepHeading('What should the questions assess?')}

          {savedCount !== null ? (
            <Well role="status" data-testid="generator-saved">
              <p className="text-[13.5px] font-semibold text-foreground">
                {plural(savedCount, 'draft')} added to this bank.
              </p>
              <p className="mt-0.5 text-[13px] leading-relaxed text-muted-foreground">
                A draft cannot go into an exam yet. Send it for review, and someone other than you
                must approve it.
              </p>
            </Well>
          ) : null}

          {genError ? (
            <GenerationError
              title="No questions were generated"
              failure={genError}
              onRetry={() => void generate()}
              retryDisabled={disabled}
            />
          ) : null}

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor={topicId} className={LABEL}>
                Topic or skill
              </label>
              <Input
                id={topicId}
                value={topic}
                maxLength={TOPIC_MAX}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="Binary search, string parsing, SQL-style joins in code…"
                aria-invalid={attempted && topicError ? true : undefined}
                aria-describedby={attempted && topicError ? `${topicId}-error` : undefined}
              />
              {attempted && topicError ? (
                <p id={`${topicId}-error`} className={FIELD_ERROR}>
                  {topicError}
                </p>
              ) : null}
            </div>
            <div>
              <label htmlFor={roleId} className={LABEL}>
                Role (optional)
              </label>
              <Input
                id={roleId}
                value={jobTitle}
                maxLength={TOPIC_MAX}
                onChange={(e) => setJobTitle(e.target.value)}
                placeholder="Backend Engineer"
                aria-describedby={`${roleId}-help`}
              />
              <p id={`${roleId}-help`} className={HELP}>
                For software and data roles, spreads the questions across what the role is assessed
                on.
              </p>
            </div>
          </div>

          <ChipGroup label="Difficulty">
            {DIFFICULTIES.map((d) => (
              <button
                key={d}
                type="button"
                aria-pressed={difficulty === d}
                onClick={() => setDifficulty(d)}
                className={cn(CHIP, 'capitalize', difficulty === d ? CHIP_ON : CHIP_OFF)}
              >
                {d}
              </button>
            ))}
          </ChipGroup>

          <ChipGroup
            label="Programming languages"
            help="Candidates may solve in any of these. Every question must be solvable in all of them with the standard library."
            error={attempted ? langsError : undefined}
          >
            {CODING_LANGUAGES.map((slug) => (
              <button
                key={slug}
                type="button"
                aria-pressed={langs.includes(slug)}
                onClick={() => toggleLang(slug)}
                className={cn(CHIP, langs.includes(slug) ? CHIP_ON : CHIP_OFF)}
              >
                {codingLanguageLabel(slug)}
              </button>
            ))}
          </ChipGroup>

          <ChipGroup
            label="Statement language"
            help="The language the problem is written in for candidates. Technical terms stay in English."
          >
            {STATEMENT_LANGUAGES.map((l) => (
              <button
                key={l.value}
                type="button"
                aria-pressed={language === l.value}
                onClick={() => setLanguage(l.value)}
                className={cn(CHIP, language === l.value ? CHIP_ON : CHIP_OFF)}
              >
                {l.label}
              </button>
            ))}
          </ChipGroup>

          <ChipGroup
            label="How many"
            help={`Up to ${BANK_CODING_MAX_GENERATED} at a time. More questions take longer.`}
          >
            {COUNTS.map((n) => (
              <button
                key={n}
                type="button"
                aria-pressed={count === n}
                aria-label={plural(n, 'question')}
                onClick={() => setCount(n)}
                className={cn(CHIP, count === n ? CHIP_ON : CHIP_OFF)}
              >
                {n}
              </button>
            ))}
          </ChipGroup>

          <div>
            <Button type="submit" disabled={disabled}>
              <Sparkles aria-hidden="true" />
              Generate drafts
            </Button>
          </div>
        </form>
      ) : null}

      {/* ── 2. Generating ───────────────────────────────────────────────── */}
      {step === 'generating' ? (
        <div className="mt-6 flex flex-col gap-4" data-testid="generator-step-generating">
          {stepHeading(`Drafting ${plural(count, 'coding question')}`)}
          <Well className="flex items-start gap-3" role="status" aria-live="polite">
            <Loader2
              size={18}
              className="mt-0.5 flex-none animate-spin text-[var(--ui-info)] motion-reduce:animate-none"
              aria-hidden="true"
            />
            <div className="min-w-0">
              <p className="text-[13.5px] font-medium text-foreground">
                Waiting for the AI - this is one request and usually takes up to a minute.
              </p>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                Nothing is saved while this runs.
              </p>
            </div>
          </Well>
          {/* Outside the live region: a clock announced every second is noise. */}
          <p
            className="text-[13px] tabular-nums text-muted-foreground"
            data-testid="generator-elapsed"
          >
            {elapsed} s elapsed
          </p>
          <div>
            <Button type="button" variant="outline" onClick={cancel}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      {/* ── 3. Review ───────────────────────────────────────────────────── */}
      {step === 'review' && result ? (
        <div className="mt-6 flex flex-col gap-5" data-testid="generator-step-review">
          {stepHeading('Review the drafts')}

          <div role="status" data-testid="generator-summary">
            <p className="text-[13.5px] text-foreground">
              <span className="font-semibold">
                {result.questions.length} of {result.requested} drafted.
              </span>{' '}
              Nothing is saved yet.
            </p>
            {result.dropped.length > 0 || result.not_returned > 0 ? (
              <ul className="mt-1.5 flex flex-col gap-0.5 text-[13px] text-muted-foreground">
                {result.dropped.map((d) => (
                  <li key={`${d.index}-${d.reason}`}>Left out: {d.reason}.</li>
                ))}
                {result.not_returned > 0 ? (
                  <li>{plural(result.not_returned, 'question')} not returned by the AI.</li>
                ) : null}
              </ul>
            ) : null}
          </div>

          <Well className="flex items-start gap-2.5">
            <AlertTriangle
              size={16}
              className="mt-0.5 flex-none text-[var(--ui-warn)]"
              aria-hidden="true"
            />
            <p className="text-[13px] leading-relaxed text-foreground">
              <span className="font-semibold">Nobody has run this code.</span> The tests and
              any reference solution were written by the AI and have not been run. Read each one
              before you keep it.
            </p>
          </Well>

          <ol className="flex flex-col divide-y divide-border">
            {result.questions.map((q, i) => {
              const off = discarded.has(i);
              return (
                <li
                  key={`${i}-${q.prompt.slice(0, 24)}`}
                  className="py-5 first:pt-0 last:pb-0"
                  data-testid="generator-draft"
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="text-[15px] font-semibold text-foreground">
                        Question {i + 1}
                      </h4>
                      <Badge variant="outline" className="capitalize">
                        {q.difficulty}
                      </Badge>
                      <Badge variant="outline">{q.language.toUpperCase()}</Badge>
                      {off ? <Badge variant="secondary">Discarded</Badge> : null}
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      aria-pressed={off}
                      aria-label={off ? `Keep question ${i + 1}` : `Discard question ${i + 1}`}
                      onClick={() => toggleDiscard(i)}
                      disabled={saving}
                    >
                      {off ? 'Keep' : 'Discard'}
                    </Button>
                  </div>
                  {off ? (
                    <p className="mt-2 text-[13px] text-muted-foreground">
                      This draft will not be added.
                    </p>
                  ) : (
                    <>
                      {q.warnings.length > 0 ? (
                        <ul className="mt-2 flex flex-col gap-0.5 text-[12.5px] text-[var(--ui-warn)]">
                          {q.warnings.map((w) => (
                            <li key={w} className="flex items-start gap-1.5">
                              <AlertTriangle
                                size={13}
                                className="mt-0.5 flex-none"
                                aria-hidden="true"
                              />
                              <span>Changed before you saw it: {w}.</span>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      <BankCodingQuestionView question={q} headingAs="h5" className="mt-4" />
                    </>
                  )}
                </li>
              );
            })}
          </ol>

          {saveError ? (
            <SaveError
              title="The drafts were not added"
              message={saveError}
              onRetry={() => void save()}
              retryDisabled={disabled || saving || kept.length === 0}
            />
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              onClick={() => void save()}
              disabled={disabled || saving || kept.length === 0}
            >
              {saving ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
              {saving
                ? 'Adding…'
                : `Add ${kept.length} to bank as ${kept.length === 1 ? 'draft' : 'drafts'}`}
            </Button>
            <Button type="button" variant="ghost" onClick={startOver} disabled={saving}>
              Start over
            </Button>
          </div>
          {kept.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">
              Every draft is discarded. Keep at least one, or start over.
            </p>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}

export default CodingQuestionGenerator;
