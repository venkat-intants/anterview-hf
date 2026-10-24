// BankQuestionEditor — PH4-D1. Authors or edits one bank question, either
// kind. Mirrors the MCQ and coding composers in ExamEditor.tsx /
// CodingAuthoringSection.tsx (option rows with a radio for the correct
// answer; test-case rows with stdin/expected/is_sample/weight) so a bank
// question and an exam question look like the same idea, and reuses the same
// lazy-loaded CodeEditor for starter/reference code.
//
// Content fields are editable while the question is a draft — a new question
// (`question` omitted) is always editable — or while the caller has forced
// the "new version" flow open via `forceEditable` for an approved/retired
// question (see QuestionBankDetail.tsx's "New version" action). Anything else
// (in_review, or approved/retired without `forceEditable`) renders read-only,
// matching the server's own rule (`update_question` 409s on anything but
// draft; `new_version` is the only route that accepts approved/retired).
//
// The form resets from `question` whenever `question?.id` changes — not on
// every render or every field in `question` — so a background refetch of the
// SAME question (e.g. after "Save changes" invalidates the query) never wipes
// mid-typing edits, but selecting a DIFFERENT question (including a different
// version through the history list) always starts from that question's own
// content instead of silently keeping the previous selection's draft in the
// form (content_hash then gets computed from, and PATCHes, the wrong row).

import { Suspense, lazy, useEffect, useState } from 'react';
import { CODING_LANGUAGES, type CodingTestCase, type ExamLanguage } from '@/api/exams';
import {
  REFERENCE_UNVERIFIED_TAG,
  UNVERIFIED_EXPLANATION,
  isReferenceUnverified,
  type BankDifficulty,
  BankQuestion,
  BankQuestionInput,
  BankQuestionKind,
  Competency,
} from '@/api/questionBanks';
import { CompetencyTagger } from './CompetencyTagger';
import { Loader2, Lock, Plus, Trash2 } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { fieldHintClass, fieldLabelClass } from '@/components/ui/control-styles';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { plural } from '@/lib/formatters';
import { cn } from '@/lib/utils';

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

// The heading over a group of controls that is not itself one control (the
// option rows, the language chips, a code editor), in the FormField label look.
const groupLabelCls = cn(fieldLabelClass, 'mb-1.5 block');
const helpCls = cn(fieldHintClass, 'mt-1.5');
const GROUP = 'flex flex-col gap-4 border-t border-border pt-5';
const GROUP_TITLE = 'text-[15px] font-bold text-foreground';
// A remove button: the ghost icon Button, tinted danger on hover.
const REMOVE_BTN =
  'shrink-0 text-muted-foreground hover:bg-[var(--ui-danger-wash)] hover:text-[var(--ui-danger)]';
const CHIP =
  'inline-flex min-h-10 items-center rounded-full border px-4 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';
const CHIP_ON =
  'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.14)] text-[var(--ui-info)]';
const CHIP_OFF = 'border-border text-muted-foreground hover:text-foreground';

const emptyTest = (): CodingTestCase => ({
  stdin: '',
  expected_output: '',
  is_sample: false,
  weight: 1,
});

interface FormState {
  kind: BankQuestionKind;
  prompt: string;
  points: string;
  difficulty: BankDifficulty;
  language: ExamLanguage;
  competencies: Competency[];
  tags: string;
  options: string[];
  correctIndex: number;
  starterCode: string;
  referenceSolution: string;
  allowedLanguages: string[];
  testCases: CodingTestCase[];
  timeLimitMs: string;
}

function stateFrom(q: BankQuestion | null | undefined): FormState {
  return {
    kind: q?.kind ?? 'mcq',
    prompt: q?.prompt ?? '',
    points: String(q?.points ?? 1),
    difficulty: q?.difficulty ?? 'medium',
    language: q?.language ?? 'en',
    competencies: q?.competencies ?? [],
    // The server's unverified marker is shown as a locked chip, not as text
    // the author could delete (the server would only put it back).
    tags: (q?.tags ?? []).filter((t) => t !== REFERENCE_UNVERIFIED_TAG).join(', '),
    options: q?.options && q.options.length > 0 ? q.options : ['', '', '', ''],
    correctIndex: q?.correct_index ?? 0,
    starterCode: q?.starter_code ?? '',
    referenceSolution: q?.reference_solution ?? '',
    allowedLanguages:
      q?.allowed_languages && q.allowed_languages.length > 0 ? q.allowed_languages : ['python'],
    testCases:
      q?.test_cases && q.test_cases.length > 0
        ? q.test_cases
        : [{ ...emptyTest(), is_sample: true }],
    timeLimitMs: String(q?.time_limit_ms ?? 5000),
  };
}

/** Mirrors the server's own cap so a save never round-trips into a 422. */
const MAX_TAGS = 20;
const MAX_PROMPT_LENGTH = 20000;

function parseTags(raw: string, locked: boolean): string[] {
  const typed = raw
    .split(',')
    .map((t) => t.trim().toLowerCase())
    // Never taken from the text box: it is locked-in or it is absent.
    .filter((t) => Boolean(t) && t !== REFERENCE_UNVERIFIED_TAG);
  return (locked ? [REFERENCE_UNVERIFIED_TAG, ...typed] : typed).slice(0, MAX_TAGS);
}

function formToBankQuestionInput(f: FormState, lockedUnverified: boolean): BankQuestionInput {
  const base: BankQuestionInput = {
    kind: f.kind,
    prompt: f.prompt.trim(),
    points: Math.max(1, Number(f.points) || 1),
    difficulty: f.difficulty,
    language: f.language,
    competencies: f.competencies,
    tags: parseTags(f.tags, lockedUnverified),
  };
  if (f.kind === 'mcq') {
    return {
      ...base,
      options: f.options.map((o) => o.trim()),
      correct_index: f.correctIndex,
    };
  }
  return {
    ...base,
    starter_code: f.starterCode.trim() || null,
    reference_solution: f.referenceSolution.trim() || null,
    allowed_languages: f.allowedLanguages,
    test_cases: f.testCases.map((t) => ({
      stdin: t.stdin,
      expected_output: t.expected_output,
      is_sample: t.is_sample,
      weight: Math.max(1, Number(t.weight) || 1),
    })),
    time_limit_ms: Math.max(100, Number(f.timeLimitMs) || 5000),
  };
}

export interface BankQuestionEditorProps {
  question?: BankQuestion | null;
  saving?: boolean;
  onSave: (input: BankQuestionInput) => void;
  onCancel?: () => void;
  /**
   * Forces the form open for a question that is not a draft — the "New
   * version" flow. The form still pre-fills from `question` (unchanged by
   * this flag) and `onSave` still fires with the edited content; the caller
   * is the one that decides that content becomes a new draft version rather
   * than a PATCH, by routing to `newBankQuestionVersion` instead of
   * `updateBankQuestion` (see QuestionBankDetail.tsx).
   */
  forceEditable?: boolean;
  /**
   * Reports whether the form holds edits that are not saved. Reported `false`
   * again when the editor goes away, so a page that listens never keeps a
   * stale "unsaved" flag for a form that no longer exists.
   */
  onDirtyChange?: (dirty: boolean) => void;
}

export function BankQuestionEditor({
  question,
  saving = false,
  onSave,
  onCancel,
  forceEditable = false,
  onDirtyChange,
}: BankQuestionEditorProps): JSX.Element {
  const [form, setForm] = useState<FormState>(() => stateFrom(question));
  const [error, setError] = useState<string | null>(null);
  const editable = !question || question.status === 'draft' || forceEditable;
  const unverifiedLocked = isReferenceUnverified({ tags: question?.tags });

  const dirty = editable && JSON.stringify(form) !== JSON.stringify(stateFrom(question));
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  // Re-seed the form when the QUESTION IDENTITY changes (a different id) —
  // never on every render, and never on a background refetch of the same id
  // (e.g. the invalidateQueries a successful save triggers), which would
  // otherwise clobber whatever the user is mid-typing. Without this, opening
  // draft A then draft B and re-selecting A left the form still holding B's
  // values, and "Save changes" PATCHed B's content onto A's row — a
  // well-formed, correctly authorised request the server has no way to catch.
  useEffect(() => {
    setForm(stateFrom(question));
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [question?.id]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  function toggleAllowedLang(slug: string, on: boolean) {
    set(
      'allowedLanguages',
      on
        ? [...new Set([...form.allowedLanguages, slug])]
        : form.allowedLanguages.filter((l) => l !== slug),
    );
  }

  function validate(): string | null {
    if (!form.prompt.trim()) return 'A prompt is required.';
    if (form.kind === 'mcq') {
      const filled = form.options.map((o) => o.trim());
      if (filled.some((o) => !o)) return 'Every option needs text.';
      if (filled.length < 2) return 'At least two options are required.';
    } else {
      if (form.allowedLanguages.length === 0) return 'Pick at least one allowed language.';
      if (form.testCases.length === 0) return 'Add at least one test case.';
      if (form.testCases.some((t) => !t.expected_output.trim())) {
        return 'Every test case needs an expected output.';
      }
    }
    return null;
  }

  function submit(ev: React.FormEvent) {
    ev.preventDefault();
    const err = validate();
    if (err) {
      setError(err);
      return;
    }
    setError(null);
    onSave(formToBankQuestionInput(form, unverifiedLocked));
  }

  if (!editable) {
    return (
      <div className="space-y-3 text-[14px]">
        <p className="flex items-start gap-2 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] px-3.5 py-3 text-[13px] text-muted-foreground">
          <Lock size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>
            This question is {question?.status.replace('_', ' ')} - only a draft can be edited.
            {question?.status === 'approved' || question?.status === 'retired'
              ? ' Start a new version to change it.'
              : ''}
          </span>
        </p>
        <p className="whitespace-pre-wrap break-words font-medium text-foreground">
          {question?.prompt}
        </p>
        {question?.kind === 'mcq' ? (
          <ul className="space-y-1">
            {(question.options ?? []).map((o, i) => (
              <li
                key={i}
                className={cn(
                  'break-words text-[13px]',
                  i === question.correct_index
                    ? 'font-medium text-[var(--ui-ok)]'
                    : 'text-muted-foreground',
                )}
              >
                {o}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[13px] text-muted-foreground">
            {plural((question?.test_cases ?? []).length, 'test case')} ·{' '}
            {(question?.allowed_languages ?? []).join(', ')}
          </p>
        )}
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5" aria-label="Bank question">
      <div className="flex flex-col gap-4">
        <h3 className={GROUP_TITLE}>Question</h3>
        {!question ? (
          <div className="flex flex-wrap gap-2">
            {(['mcq', 'coding'] as const).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => set('kind', k)}
                aria-pressed={form.kind === k}
                className={cn(CHIP, form.kind === k ? CHIP_ON : CHIP_OFF)}
              >
                {k === 'mcq' ? 'MCQ' : 'Coding'}
              </button>
            ))}
          </div>
        ) : null}

        <FormField label="Prompt">
          {(p) => (
            <Textarea
              {...p}
              value={form.prompt}
              onChange={(e) => set('prompt', e.target.value)}
              rows={3}
              maxLength={MAX_PROMPT_LENGTH}
              aria-label="Prompt"
            />
          )}
        </FormField>

        <div className="grid gap-4 sm:grid-cols-3">
          <FormField label="Difficulty">
            {(p) => (
              <SelectControl
                {...p}
                value={form.difficulty}
                onChange={(e) => set('difficulty', e.target.value as BankDifficulty)}
              >
                <option value="easy">Easy</option>
                <option value="medium">Medium</option>
                <option value="hard">Hard</option>
              </SelectControl>
            )}
          </FormField>
          <FormField label="Language">
            {(p) => (
              <SelectControl
                {...p}
                value={form.language}
                onChange={(e) => set('language', e.target.value as ExamLanguage)}
              >
                <option value="en">English</option>
                <option value="hi">हिन्दी</option>
                <option value="te">తెలుగు</option>
              </SelectControl>
            )}
          </FormField>
          <FormField label="Points">
            {(p) => (
              <Input
                {...p}
                type="number"
                min={1}
                value={form.points}
                onChange={(e) => set('points', e.target.value)}
                aria-label="Points"
              />
            )}
          </FormField>
        </div>
      </div>

      <div className={GROUP}>
        <h3 className={GROUP_TITLE}>Competencies and tags</h3>
        <CompetencyTagger
          value={form.competencies}
          onChange={(next) => set('competencies', next)}
        />

        <FormField label="Tags (comma-separated)">
          {(p) => (
            <Input
              {...p}
              value={form.tags}
              onChange={(e) => set('tags', e.target.value)}
              placeholder="e.g. sql, arrays"
            />
          )}
        </FormField>
        {unverifiedLocked ? (
          <div data-testid="locked-unverified-tag">
            <span
              className="inline-flex min-h-8 items-center gap-1.5 rounded-full border border-border bg-[var(--ui-inset-soft)] px-3 text-[12.5px] font-medium text-foreground"
              title={UNVERIFIED_EXPLANATION}
            >
              <Lock size={12} aria-hidden="true" />
              {REFERENCE_UNVERIFIED_TAG}
            </span>
            <p className={helpCls}>
              This tag is added by the system and cannot be removed. {UNVERIFIED_EXPLANATION} It
              clears when a version is approved.
            </p>
          </div>
        ) : null}
      </div>

      {form.kind === 'mcq' ? (
        <div className={GROUP}>
          <h3 className={GROUP_TITLE}>Answer options</h3>
          <div>
            <span className={groupLabelCls}>Options</span>
            <div className="space-y-1">
              {form.options.map((opt, oi) => (
                <div key={oi} className="flex items-center gap-1">
                  <label className="flex h-10 w-10 shrink-0 cursor-pointer items-center justify-center rounded-full focus-within:ring-2 focus-within:ring-[var(--accent)]">
                    <input
                      type="radio"
                      name="bank-question-correct"
                      checked={form.correctIndex === oi}
                      onChange={() => set('correctIndex', oi)}
                      aria-label={`Mark option ${oi + 1} correct`}
                      className="h-4 w-4 accent-[var(--accent)]"
                    />
                  </label>
                  <Input
                    value={opt}
                    onChange={(e) =>
                      set(
                        'options',
                        form.options.map((o, j) => (j === oi ? e.target.value : o)),
                      )
                    }
                    placeholder={`Option ${oi + 1}`}
                    aria-label={`Option ${oi + 1}`}
                    className="min-w-0 flex-1"
                  />
                  {form.options.length > 2 ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`Remove option ${oi + 1}`}
                      onClick={() =>
                        set(
                          'options',
                          form.options.filter((_, j) => j !== oi),
                        )
                      }
                      className={REMOVE_BTN}
                    >
                      <Trash2 aria-hidden="true" />
                    </Button>
                  ) : (
                    <span className="h-10 w-10 shrink-0" aria-hidden="true" />
                  )}
                </div>
              ))}
            </div>
            <p className={helpCls}>Select the radio next to the correct answer.</p>
          </div>
          {form.options.length < 6 ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => set('options', [...form.options, ''])}
              className="self-start"
            >
              <Plus aria-hidden="true" /> Add option
            </Button>
          ) : null}
        </div>
      ) : (
        <div className={GROUP}>
          <h3 className={GROUP_TITLE}>Coding setup</h3>
          <div>
            <span className={groupLabelCls}>Allowed languages</span>
            <div className="flex flex-wrap gap-2">
              {CODING_LANGUAGES.map((slug) => {
                const on = form.allowedLanguages.includes(slug);
                return (
                  <button
                    key={slug}
                    type="button"
                    onClick={() => toggleAllowedLang(slug, !on)}
                    aria-pressed={on}
                    className={cn(CHIP, on ? CHIP_ON : CHIP_OFF)}
                  >
                    {LANG_LABEL[slug] ?? slug}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <span className={groupLabelCls}>Starter code (optional)</span>
            <Suspense fallback={<Skeleton className="h-24 rounded-[12px]" />}>
              <CodeEditor
                language={form.allowedLanguages[0] ?? 'python'}
                value={form.starterCode}
                onChange={(v) => set('starterCode', v)}
                minHeight={100}
                placeholder="// pre-filled in the candidate's editor"
                textareaId="bank-question-starter"
              />
            </Suspense>
          </div>

          <div>
            <span className={groupLabelCls}>Reference solution (optional)</span>
            <Suspense fallback={<Skeleton className="h-24 rounded-[12px]" />}>
              <CodeEditor
                language={form.allowedLanguages[0] ?? 'python'}
                value={form.referenceSolution}
                onChange={(v) => set('referenceSolution', v)}
                minHeight={100}
                textareaId="bank-question-reference"
              />
            </Suspense>
          </div>

          <div>
            <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
              <span className="text-[13px] font-semibold text-foreground">Test cases</span>
              <Button
                type="button"
                variant="outline"
                onClick={() => set('testCases', [...form.testCases, emptyTest()])}
              >
                <Plus aria-hidden="true" /> Add test
              </Button>
            </div>
            <div className="space-y-3">
              {form.testCases.map((tc, ti) => (
                <div key={ti} className="rounded-[16px] border border-border p-3">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Textarea
                      rows={2}
                      value={tc.stdin}
                      onChange={(e) =>
                        set(
                          'testCases',
                          form.testCases.map((t, j) =>
                            j === ti ? { ...t, stdin: e.target.value } : t,
                          ),
                        )
                      }
                      placeholder="stdin"
                      aria-label={`Test ${ti + 1} input`}
                      className="min-h-[44px] font-mono md:text-[13px]"
                    />
                    <Textarea
                      rows={2}
                      value={tc.expected_output}
                      onChange={(e) =>
                        set(
                          'testCases',
                          form.testCases.map((t, j) =>
                            j === ti ? { ...t, expected_output: e.target.value } : t,
                          ),
                        )
                      }
                      placeholder="expected stdout"
                      aria-label={`Test ${ti + 1} expected output`}
                      className="min-h-[44px] font-mono md:text-[13px]"
                    />
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-2.5 text-[13px] text-muted-foreground">
                    <label className="flex min-h-10 items-center gap-2">
                      <input
                        type="checkbox"
                        checked={tc.is_sample}
                        onChange={(e) =>
                          set(
                            'testCases',
                            form.testCases.map((t, j) =>
                              j === ti ? { ...t, is_sample: e.target.checked } : t,
                            ),
                          )
                        }
                        className="h-4 w-4 accent-[var(--accent)]"
                      />
                      Sample
                    </label>
                    {form.testCases.length > 1 ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label={`Remove test case ${ti + 1}`}
                        onClick={() =>
                          set(
                            'testCases',
                            form.testCases.filter((_, j) => j !== ti),
                          )
                        }
                        className={cn(REMOVE_BTN, 'ml-auto')}
                      >
                        <Trash2 aria-hidden="true" />
                      </Button>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <FormField label="Time limit (ms)" className="max-w-[200px]">
            {(p) => (
              <Input
                {...p}
                type="number"
                min={100}
                value={form.timeLimitMs}
                onChange={(e) => set('timeLimitMs', e.target.value)}
              />
            )}
          </FormField>
        </div>
      )}

      {error ? (
        <p role="alert" className="text-[13px] font-medium text-[var(--ui-danger)]">
          {error}
        </p>
      ) : null}

      <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-5">
        {onCancel ? (
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
        <Button type="submit" disabled={saving}>
          {saving ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
          {!question
            ? 'Create question'
            : question.status !== 'draft'
              ? 'Save as new version'
              : 'Save changes'}
        </Button>
      </div>
    </form>
  );
}
