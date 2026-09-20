// BankQuestionEditor — PH4-D1. Authors or edits one bank question, either
// kind. Mirrors the MCQ and coding composers in ExamEditor.tsx /
// CodingAuthoringSection.tsx (option rows with a radio for the correct
// answer; test-case rows with stdin/expected/is_sample/weight) so a bank
// question and an exam question look like the same idea, and reuses the same
// lazy-loaded CodeEditor for starter/reference code.
//
// Content fields are editable only while the question is a draft — a new
// question (`question` omitted) is always editable; anything else (in_review,
// approved, retired) renders read-only, matching the server's own rule
// (`update_question` 409s on anything but draft).

import { Suspense, lazy, useState } from 'react';
import {
  CODING_LANGUAGES,
  type CodingTestCase,
  type ExamLanguage,
} from '@/api/exams';
import type {
  BankDifficulty,
  BankQuestion,
  BankQuestionInput,
  BankQuestionKind,
  Competency,
} from '@/api/questionBanks';
import { CompetencyTagger } from './CompetencyTagger';
import { Loader2, Plus, Trash2 } from '@/design/components/icons';
import { cn } from '@/lib/utils';

const CodeEditor = lazy(() => import('@/components/CodeEditor'));

const LANG_LABEL: Record<string, string> = {
  python: 'Python', javascript: 'JavaScript', typescript: 'TypeScript', java: 'Java',
  cpp: 'C++', c: 'C', go: 'Go', csharp: 'C#', ruby: 'Ruby', rust: 'Rust',
};

const inputCls =
  'w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] ' +
  'text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] ' +
  'focus:outline-none disabled:opacity-60';
const labelCls = 'text-[12px] font-medium text-[var(--ui-soft)]';

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
    tags: (q?.tags ?? []).join(', '),
    options: q?.options && q.options.length > 0 ? q.options : ['', '', '', ''],
    correctIndex: q?.correct_index ?? 0,
    starterCode: q?.starter_code ?? '',
    referenceSolution: q?.reference_solution ?? '',
    allowedLanguages: q?.allowed_languages && q.allowed_languages.length > 0
      ? q.allowed_languages
      : ['python'],
    testCases: q?.test_cases && q.test_cases.length > 0 ? q.test_cases : [{ ...emptyTest(), is_sample: true }],
    timeLimitMs: String(q?.time_limit_ms ?? 5000),
  };
}

function parseTags(raw: string): string[] {
  return raw
    .split(',')
    .map((t) => t.trim().toLowerCase())
    .filter(Boolean);
}

function formToBankQuestionInput(f: FormState): BankQuestionInput {
  const base: BankQuestionInput = {
    kind: f.kind,
    prompt: f.prompt.trim(),
    points: Math.max(1, Number(f.points) || 1),
    difficulty: f.difficulty,
    language: f.language,
    competencies: f.competencies,
    tags: parseTags(f.tags),
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
}

export function BankQuestionEditor({
  question,
  saving = false,
  onSave,
  onCancel,
}: BankQuestionEditorProps): JSX.Element {
  const [form, setForm] = useState<FormState>(() => stateFrom(question));
  const editable = !question || question.status === 'draft';
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

  const [error, setError] = useState<string | null>(null);

  function submit(ev: React.FormEvent) {
    ev.preventDefault();
    const err = validate();
    if (err) {
      setError(err);
      return;
    }
    setError(null);
    onSave(formToBankQuestionInput(form));
  }

  if (!editable) {
    return (
      <div className="space-y-3 text-[13px]">
        <p className="rounded-[10px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2 text-[12.5px] text-muted-foreground">
          This question is {question?.status.replace('_', ' ')} — only a draft can be edited.
          {question?.status === 'approved' || question?.status === 'retired'
            ? ' Start a new version to change it.'
            : ''}
        </p>
        <p className="whitespace-pre-wrap font-medium text-foreground">{question?.prompt}</p>
        {question?.kind === 'mcq' ? (
          <ul className="space-y-1">
            {(question.options ?? []).map((o, i) => (
              <li
                key={i}
                className={cn(
                  'text-[12.5px]',
                  i === question.correct_index ? 'font-medium text-[var(--ui-ok)]' : 'text-muted-foreground',
                )}
              >
                {o}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[12.5px] text-muted-foreground">
            {(question?.test_cases ?? []).length} test case
            {(question?.test_cases ?? []).length === 1 ? '' : 's'} ·{' '}
            {(question?.allowed_languages ?? []).join(', ')}
          </p>
        )}
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" aria-label="Bank question">
      {!question ? (
        <div className="flex gap-1.5">
          {(['mcq', 'coding'] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => set('kind', k)}
              aria-pressed={form.kind === k}
              className={cn(
                'rounded-[9px] border px-3 py-1.5 text-[12px] font-medium transition-colors',
                form.kind === k
                  ? 'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.14)] text-[var(--ui-info)]'
                  : 'border-border text-muted-foreground hover:text-foreground',
              )}
            >
              {k === 'mcq' ? 'MCQ' : 'Coding'}
            </button>
          ))}
        </div>
      ) : null}

      <label className="block">
        <span className={labelCls}>Prompt</span>
        <textarea
          value={form.prompt}
          onChange={(e) => set('prompt', e.target.value)}
          rows={3}
          className={cn(inputCls, 'resize-y')}
          aria-label="Prompt"
        />
      </label>

      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block">
          <span className={labelCls}>Difficulty</span>
          <select
            value={form.difficulty}
            onChange={(e) => set('difficulty', e.target.value as BankDifficulty)}
            className={inputCls}
          >
            <option value="easy">Easy</option>
            <option value="medium">Medium</option>
            <option value="hard">Hard</option>
          </select>
        </label>
        <label className="block">
          <span className={labelCls}>Language</span>
          <select
            value={form.language}
            onChange={(e) => set('language', e.target.value as ExamLanguage)}
            className={inputCls}
          >
            <option value="en">English</option>
            <option value="hi">हिन्दी</option>
            <option value="te">తెలుగు</option>
          </select>
        </label>
        <label className="block">
          <span className={labelCls}>Points</span>
          <input
            type="number"
            min={1}
            value={form.points}
            onChange={(e) => set('points', e.target.value)}
            className={inputCls}
            aria-label="Points"
          />
        </label>
      </div>

      <CompetencyTagger
        value={form.competencies}
        onChange={(next) => set('competencies', next)}
      />

      <label className="block">
        <span className={labelCls}>Tags (comma-separated)</span>
        <input
          value={form.tags}
          onChange={(e) => set('tags', e.target.value)}
          placeholder="e.g. sql, arrays"
          className={inputCls}
        />
      </label>

      {form.kind === 'mcq' ? (
        <div className="space-y-1.5">
          <span className={labelCls}>Options</span>
          {form.options.map((opt, oi) => (
            <div key={oi} className="flex items-center gap-2">
              <input
                type="radio"
                name="bank-question-correct"
                checked={form.correctIndex === oi}
                onChange={() => set('correctIndex', oi)}
                aria-label={`Mark option ${oi + 1} correct`}
                className="h-3.5 w-3.5 accent-[var(--accent)]"
              />
              <input
                value={opt}
                onChange={(e) =>
                  set(
                    'options',
                    form.options.map((o, j) => (j === oi ? e.target.value : o)),
                  )
                }
                placeholder={`Option ${oi + 1}`}
                aria-label={`Option ${oi + 1}`}
                className={inputCls}
              />
              {form.options.length > 2 ? (
                <button
                  type="button"
                  aria-label={`Remove option ${oi + 1}`}
                  onClick={() =>
                    set(
                      'options',
                      form.options.filter((_, j) => j !== oi),
                    )
                  }
                  className="shrink-0 text-muted-foreground hover:text-[var(--ui-danger)]"
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              ) : null}
            </div>
          ))}
          {form.options.length < 6 ? (
            <button
              type="button"
              onClick={() => set('options', [...form.options, ''])}
              className="inline-flex items-center gap-1 text-[12px] text-[var(--ui-info)] hover:underline"
            >
              <Plus className="h-3 w-3" aria-hidden="true" /> Add option
            </button>
          ) : null}
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <span className={labelCls}>Allowed languages</span>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {CODING_LANGUAGES.map((slug) => {
                const on = form.allowedLanguages.includes(slug);
                return (
                  <button
                    key={slug}
                    type="button"
                    onClick={() => toggleAllowedLang(slug, !on)}
                    className={cn(
                      'rounded-full border px-2.5 py-0.5 text-[11.5px] transition-colors',
                      on
                        ? 'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.14)] text-[var(--ui-info)]'
                        : 'border-border text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {LANG_LABEL[slug] ?? slug}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <span className={labelCls}>Starter code (optional)</span>
            <Suspense
              fallback={
                <div className="flex h-24 items-center justify-center rounded-[10px] border border-border bg-card">
                  <Loader2 className="h-4 w-4 animate-spin text-[var(--ui-info)]" aria-hidden="true" />
                </div>
              }
            >
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
            <span className={labelCls}>Reference solution (optional)</span>
            <Suspense
              fallback={
                <div className="flex h-24 items-center justify-center rounded-[10px] border border-border bg-card">
                  <Loader2 className="h-4 w-4 animate-spin text-[var(--ui-info)]" aria-hidden="true" />
                </div>
              }
            >
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
            <div className="flex items-center justify-between">
              <span className={labelCls}>Test cases</span>
              <button
                type="button"
                onClick={() => set('testCases', [...form.testCases, emptyTest()])}
                className="inline-flex items-center gap-1 text-[12px] text-[var(--ui-info)] hover:underline"
              >
                <Plus className="h-3 w-3" aria-hidden="true" /> Add test
              </button>
            </div>
            <div className="mt-1.5 space-y-2">
              {form.testCases.map((tc, ti) => (
                <div key={ti} className="rounded-[10px] border border-border p-2.5">
                  <div className="grid gap-2 sm:grid-cols-2">
                    <textarea
                      value={tc.stdin}
                      onChange={(e) =>
                        set(
                          'testCases',
                          form.testCases.map((t, j) => (j === ti ? { ...t, stdin: e.target.value } : t)),
                        )
                      }
                      placeholder="stdin"
                      aria-label={`Test ${ti + 1} input`}
                      className={cn(inputCls, 'min-h-[44px] resize-y font-mono text-[12px]')}
                    />
                    <textarea
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
                      className={cn(inputCls, 'min-h-[44px] resize-y font-mono text-[12px]')}
                    />
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2.5 text-[11.5px] text-muted-foreground">
                    <label className="flex items-center gap-1.5">
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
                        className="h-3.5 w-3.5 accent-[var(--accent)]"
                      />
                      Sample
                    </label>
                    {form.testCases.length > 1 ? (
                      <button
                        type="button"
                        aria-label={`Remove test case ${ti + 1}`}
                        onClick={() =>
                          set(
                            'testCases',
                            form.testCases.filter((_, j) => j !== ti),
                          )
                        }
                        className="ml-auto text-muted-foreground hover:text-[var(--ui-danger)]"
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      </button>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <label className="block max-w-[180px]">
            <span className={labelCls}>Time limit (ms)</span>
            <input
              type="number"
              min={100}
              value={form.timeLimitMs}
              onChange={(e) => set('timeLimitMs', e.target.value)}
              className={inputCls}
            />
          </label>
        </div>
      )}

      {error ? <p className="text-[12px] text-[var(--ui-danger)]">{error}</p> : null}

      <div className="flex justify-end gap-2">
        {onCancel ? (
          <button
            type="button"
            onClick={onCancel}
            className="rounded-[10px] border border-border px-3.5 py-2 text-[12.5px] text-foreground"
          >
            Cancel
          </button>
        ) : null}
        <button
          type="submit"
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-50"
        >
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
          {question ? 'Save changes' : 'Create question'}
        </button>
      </div>
    </form>
  );
}
