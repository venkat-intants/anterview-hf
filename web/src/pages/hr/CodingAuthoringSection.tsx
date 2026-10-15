// CodingAuthoringSection — section-scoped version of the coding question
// composer. Works identically to CodingAuthoring but posts to the
// section-scoped endpoints:
//   GET  /hr/exams/{examId}/sections/{sectionId}/coding-questions
//   POST /hr/exams/{examId}/sections/{sectionId}/coding-questions
// This file is lazy-loaded by ExamEditor so the CodeEditor import doesn't
// inflate the initial bundle.

import BankProvenanceChip from '@/components/bank/BankProvenanceChip';
import { Suspense, lazy, useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Plus,
  Trash2,
  Loader2,
  Lock,
  Code2,
  Library,
  Pencil,
  Sparkles,
  X,
} from '@/design/components/icons';
import {
  listSectionCodingQuestions,
  addSectionCodingQuestion,
  deleteSectionCodingQuestion,
  generateCodingQuestions,
  CODING_LANGUAGES,
  type CodingTestCase,
  type ExamDifficulty,
  type ExamLanguage,
  type GeneratedCodingQuestion,
} from '@/api/exams';
import { saveCodingQuestionToBank, type SaveToBankInput } from '@/api/questionBanks';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { StatusTag } from '@/design/components/primitives';
import { EmptyState } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { BankQuestionPicker } from '@/components/bank/BankQuestionPicker';
import { SaveToBankButton } from '@/components/bank/SaveToBankButton';
import { SourceBankUnverifiedBadge } from '@/components/bank/SourceBankUnverifiedBadge';
import { GenerationError } from '@/components/bank/GenerationError';
import { toGenerationFailure } from '@/lib/generationError';

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

// Native <select>/<textarea> in the same look as the shared Input.
const FIELD_CLS =
  'w-full rounded-[12px] border border-input bg-card px-3.5 py-2.5 text-[14px] text-foreground ' +
  'placeholder:text-muted-foreground hover:border-[var(--ui-line-strong)] ' +
  'focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 ' +
  'focus-visible:ring-[var(--accent)]/30';
const SELECT_CLS = cn(FIELD_CLS, 'h-11 py-0');
const LABEL_CLS = 'mb-1.5 block text-[13px] font-semibold text-foreground';
const CHIP =
  'inline-flex min-h-10 items-center rounded-full border px-4 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';
const ICON_BTN =
  'flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-[var(--ui-danger-wash)] hover:text-[var(--ui-danger)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';

const emptyTest = (): CodingTestCase => ({
  stdin: '',
  expected_output: '',
  is_sample: false,
  weight: 1,
});

/** The allowed-language chips, shared by the manual and AI composers. */
function LanguageChips({
  langs,
  onToggle,
}: {
  langs: string[];
  onToggle: (slug: string, on: boolean) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {CODING_LANGUAGES.map((slug) => {
        const on = langs.includes(slug);
        return (
          <button
            key={slug}
            type="button"
            onClick={() => onToggle(slug, !on)}
            aria-pressed={on}
            className={cn(
              CHIP,
              on
                ? 'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.14)] text-[var(--ui-info)]'
                : 'border-border text-muted-foreground hover:text-foreground',
            )}
          >
            {LANG_LABEL[slug]}
          </button>
        );
      })}
    </div>
  );
}

/** The repeatable test-case rows: stdin, expected stdout, sample flag, weight. */
function TestCasesEditor({
  tests,
  setTests,
}: {
  tests: CodingTestCase[];
  setTests: React.Dispatch<React.SetStateAction<CodingTestCase[]>>;
}) {
  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
        <span className="text-[13px] font-semibold text-foreground">Test cases</span>
        <Button
          type="button"
          variant="outline"
          onClick={() => setTests((p) => [...p, emptyTest()])}
        >
          <Plus aria-hidden="true" /> Add test
        </Button>
      </div>
      <div className="space-y-3">
        {tests.map((tc, ti) => (
          <div key={ti} className="rounded-[16px] border border-border bg-card p-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <textarea
                className={cn(FIELD_CLS, 'min-h-[56px] resize-y font-mono text-[13px]')}
                placeholder="stdin (input fed to the program)"
                value={tc.stdin}
                onChange={(e) =>
                  setTests((p) => p.map((t, j) => (j === ti ? { ...t, stdin: e.target.value } : t)))
                }
                aria-label={`Test ${ti + 1} input`}
              />
              <textarea
                className={cn(FIELD_CLS, 'min-h-[56px] resize-y font-mono text-[13px]')}
                placeholder="expected stdout"
                value={tc.expected_output}
                onChange={(e) =>
                  setTests((p) =>
                    p.map((t, j) => (j === ti ? { ...t, expected_output: e.target.value } : t)),
                  )
                }
                aria-label={`Test ${ti + 1} expected output`}
              />
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted-foreground">
              <label className="flex min-h-10 items-center gap-2">
                <input
                  type="checkbox"
                  checked={tc.is_sample}
                  onChange={(e) =>
                    setTests((p) =>
                      p.map((t, j) => (j === ti ? { ...t, is_sample: e.target.checked } : t)),
                    )
                  }
                  className="h-4 w-4 accent-[var(--accent)]"
                />
                Sample (shown to candidate)
              </label>
              <label className="flex items-center gap-2">
                Weight
                <Input
                  type="number"
                  min={1}
                  value={tc.weight}
                  onChange={(e) =>
                    setTests((p) =>
                      p.map((t, j) =>
                        j === ti ? { ...t, weight: Number(e.target.value) || 1 } : t,
                      ),
                    )
                  }
                  className="w-20"
                  aria-label={`Test ${ti + 1} weight`}
                />
              </label>
              {tests.length > 1 && (
                <button
                  type="button"
                  aria-label="Remove test case"
                  className={cn(ICON_BTN, 'ml-auto')}
                  onClick={() => setTests((p) => p.filter((_, j) => j !== ti))}
                >
                  <Trash2 size={16} aria-hidden="true" />
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-1.5 text-[12.5px] text-muted-foreground">
        Hidden (non-sample) tests grade the candidate without revealing the answer.
      </p>
    </div>
  );
}

interface Props {
  examId: string;
  sectionId: string;
  locked: boolean;
}

export default function CodingAuthoringSection({ examId, sectionId, locked }: Props) {
  const qc = useQueryClient();
  const qKey = ['hr', 'exam', examId, 'section', sectionId, 'coding-questions'];

  const questionsQuery = useQuery({
    queryKey: qKey,
    queryFn: () => listSectionCodingQuestions(examId, sectionId),
  });
  const questions = questionsQuery.data ?? [];

  const refresh = () => void qc.invalidateQueries({ queryKey: qKey });

  // PH4-D1 — the bank picker, and per-row "Save to bank".
  const [showBankPicker, setShowBankPicker] = useState(false);
  const [savingToBankId, setSavingToBankId] = useState<string | null>(null);

  const saveToBankMut = useMutation({
    mutationFn: ({ qid, body }: { qid: string; body: SaveToBankInput }) =>
      saveCodingQuestionToBank(examId, qid, body),
    onSuccess: () => {
      toast.success('Saved to bank as a new draft');
      setSavingToBankId(null);
    },
    onError: (e: unknown) => {
      toast.error(e instanceof Error ? e.message : 'Could not save this question to a bank');
      setSavingToBankId(null);
    },
  });

  // Composer state
  const [composerTab, setComposerTab] = useState<'manual' | 'ai'>('manual');
  const [prompt, setPrompt] = useState('');
  const [langs, setLangs] = useState<string[]>(['python']);
  const [starter, setStarter] = useState('');
  const [tests, setTests] = useState<CodingTestCase[]>([{ ...emptyTest(), is_sample: true }]);
  const [points, setPoints] = useState('100');

  // AI tab state (allowed languages are shared with the manual composer —
  // same concept, same chips)
  const [aiTopic, setAiTopic] = useState('');
  const [aiCount, setAiCount] = useState('2');
  const [aiDifficulty, setAiDifficulty] = useState<ExamDifficulty>('medium');
  const [aiLanguage, setAiLanguage] = useState<ExamLanguage>('en');
  const [aiPreview, setAiPreview] = useState<GeneratedCodingQuestion[]>([]);

  const editorLang = useMemo(() => langs[0] ?? 'python', [langs]);

  const generateMut = useMutation({
    mutationFn: () =>
      generateCodingQuestions(examId, {
        topic: aiTopic.trim(),
        num_questions: Math.min(8, Math.max(1, Number(aiCount) || 2)),
        difficulty: aiDifficulty,
        language: aiLanguage,
        allowed_languages: langs,
      }),
    onSuccess: (res) => {
      setAiPreview(res.questions);
      if (res.questions.length === 0) toast.error('The AI returned no usable problems.');
    },
    // No toast: the failure is shown inline, with the server's own sentence.
  });

  const addGeneratedMut = useMutation({
    mutationFn: async () => {
      for (const q of aiPreview) {
        await addSectionCodingQuestion(examId, sectionId, {
          prompt: q.prompt,
          allowed_languages: q.allowed_languages,
          starter_code: q.starter_code,
          reference_solution: q.reference_solution,
          test_cases: q.test_cases,
          time_limit_ms: q.time_limit_ms,
          points: q.points,
        });
      }
      return aiPreview.length;
    },
    onSuccess: (n) => {
      setAiPreview([]);
      toast.success(`${n} coding problem${n === 1 ? '' : 's'} added`);
      refresh();
    },
    onError: (e: unknown) => {
      toast.error(e instanceof Error ? e.message : 'Add failed');
      refresh(); // some drafts may have been persisted before the failure
    },
  });

  const addMut = useMutation({
    mutationFn: () =>
      addSectionCodingQuestion(examId, sectionId, {
        prompt: prompt.trim(),
        allowed_languages: langs,
        starter_code: starter.trim() || null,
        test_cases: tests.map((t) => ({
          stdin: t.stdin,
          expected_output: t.expected_output,
          is_sample: t.is_sample,
          weight: Math.max(1, Number(t.weight) || 1),
        })),
        points: Number(points) || 100,
      }),
    onSuccess: () => {
      setPrompt('');
      setLangs(['python']);
      setStarter('');
      setTests([{ ...emptyTest(), is_sample: true }]);
      setPoints('100');
      toast.success('Coding question added');
      refresh();
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Add failed'),
  });

  const delMut = useMutation({
    mutationFn: (qid: string) => deleteSectionCodingQuestion(examId, sectionId, qid),
    onSuccess: refresh,
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Delete failed'),
  });

  function toggleLang(slug: string, on: boolean) {
    setLangs((prev) => (on ? [...new Set([...prev, slug])] : prev.filter((l) => l !== slug)));
  }

  function submit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!prompt.trim()) return toast.error('A problem statement is required.');
    if (langs.length === 0) return toast.error('Pick at least one allowed language.');
    if (tests.length === 0) return toast.error('Add at least one test case.');
    if (tests.some((t) => !t.expected_output.trim())) {
      return toast.error('Every test case needs an expected output.');
    }
    if (!tests.some((t) => t.is_sample)) {
      return toast.error('Mark at least one test case as a sample (shown to the candidate).');
    }
    addMut.mutate();
  }

  return (
    <div className="mt-2 space-y-4">
      {/* Existing coding questions */}
      {questionsQuery.isLoading ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          <span className="sr-only">Loading…</span>
          <Skeleton className="h-20 rounded-[16px]" />
          <Skeleton className="h-20 rounded-[16px]" />
        </div>
      ) : questionsQuery.isError ? (
        <div role="alert" className="flex flex-wrap items-center gap-3">
          <p className="text-[13.5px] text-[var(--ui-danger)]">
            Could not load this section&apos;s coding questions.
          </p>
          <Button type="button" variant="outline" onClick={() => void questionsQuery.refetch()}>
            Try again
          </Button>
        </div>
      ) : questions.length === 0 ? (
        <EmptyState
          icon={Code2}
          title="No coding questions in this section yet"
          description={
            locked
              ? 'Questions are locked, so none can be added here.'
              : 'Write one below, draft some with AI, or add approved ones from a bank.'
          }
        />
      ) : (
        <ul className="flex flex-col gap-3">
          {questions.map((q, i) => {
            const sampleN = q.test_cases.filter((t) => t.is_sample).length;
            return (
              <li
                key={q.id}
                className="rounded-[16px] border border-border bg-[var(--ui-inset-soft)] p-3.5"
              >
                <div className="flex items-start gap-3">
                  <span className="flex h-7 w-7 flex-none items-center justify-center rounded-full bg-[var(--ui-inset)] font-mono text-[12px] text-[var(--ui-soft)]">
                    {i + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <p className="whitespace-pre-wrap break-words text-[14px] font-medium leading-snug text-foreground">
                        {q.prompt}{' '}
                        <span className="font-normal text-muted-foreground">({q.points} pt)</span>
                      </p>
                      <BankProvenanceChip
                        rootId={q.source_bank_root_id}
                        version={q.source_bank_version}
                      />
                      <SourceBankUnverifiedBadge bankQuestionId={q.source_bank_question_id} />
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      {q.allowed_languages.map((l) => (
                        <StatusTag key={l} tone="neutral" className="text-[12px]">
                          {LANG_LABEL[l] ?? l}
                        </StatusTag>
                      ))}
                      <span className="text-[12.5px] text-muted-foreground">
                        {q.test_cases.length} test{q.test_cases.length === 1 ? '' : 's'} · {sampleN}{' '}
                        sample
                      </span>
                    </div>
                    {/* One control, not two: SaveToBankButton renders its
                        own "Save to bank" link while closed. */}
                    <SaveToBankButton
                      className="mt-2"
                      saving={saveToBankMut.isPending && savingToBankId === q.id}
                      onSave={(body) => {
                        setSavingToBankId(q.id);
                        saveToBankMut.mutate({ qid: q.id, body });
                      }}
                    />
                  </div>
                  {!locked && (
                    <button
                      type="button"
                      aria-label="Delete question"
                      className={ICON_BTN}
                      onClick={() => delMut.mutate(q.id)}
                    >
                      <Trash2 size={16} aria-hidden="true" />
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {/* PH4-D1 — add an approved coding question straight from a bank */}
      {!locked && (
        <Button type="button" variant="outline" onClick={() => setShowBankPicker(true)}>
          <Library aria-hidden="true" /> Add from bank
        </Button>
      )}
      {showBankPicker && (
        <BankQuestionPicker
          examId={examId}
          sectionId={sectionId}
          sectionKind="coding"
          onClose={() => setShowBankPicker(false)}
          onAdded={refresh}
        />
      )}

      {/* Composer */}
      {locked ? (
        <div className="flex items-center gap-2 rounded-[16px] border border-border bg-[var(--ui-inset-soft)] px-4 py-3 text-[13.5px] text-muted-foreground">
          <Lock size={15} className="shrink-0" aria-hidden="true" /> Questions are locked — see the
          banner above.
        </div>
      ) : (
        <div className="rounded-[16px] border border-dashed border-[var(--ui-line-strong)] p-4">
          {/* Tab bar — mirrors the MCQ composer's Manual | AI switcher */}
          <div
            role="tablist"
            aria-label="How to add coding questions"
            className="mb-4 flex gap-1 rounded-full bg-[var(--ui-inset-strong)] p-1"
          >
            {[
              { key: 'manual', label: 'Manual', icon: Pencil },
              { key: 'ai', label: 'AI', icon: Sparkles },
            ].map((t) => {
              const Icon = t.icon;
              const active = composerTab === t.key;
              return (
                <button
                  key={t.key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setComposerTab(t.key as 'manual' | 'ai')}
                  className={cn(
                    'flex min-h-10 flex-1 items-center justify-center gap-1.5 rounded-full px-3 text-[13.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                    active
                      ? 'bg-card text-foreground shadow-card'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  <Icon size={14} aria-hidden="true" />
                  {t.label}
                </button>
              );
            })}
          </div>

          {/* AI generate */}
          {composerTab === 'ai' && (
            <div className="space-y-5" aria-label="AI generate coding questions">
              <div className="space-y-4">
                <h3 className="text-[15px] font-bold text-foreground">What to generate</h3>
                <Input
                  placeholder="Topic — e.g. arrays & strings, SQL joins, or Junior Python Developer"
                  value={aiTopic}
                  onChange={(e) => setAiTopic(e.target.value)}
                  aria-label="Topic"
                />
                <div className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-3">
                  <label className="flex flex-col text-[13px] font-semibold text-foreground">
                    <span className="mb-1.5">Problems</span>
                    <Input
                      type="number"
                      min={1}
                      max={8}
                      value={aiCount}
                      onChange={(e) => setAiCount(e.target.value)}
                      aria-label="Number of problems"
                    />
                  </label>
                  <label className="flex flex-col text-[13px] font-semibold text-foreground">
                    <span className="mb-1.5">Difficulty</span>
                    <select
                      value={aiDifficulty}
                      onChange={(e) => setAiDifficulty(e.target.value as ExamDifficulty)}
                      className={cn(SELECT_CLS, 'font-normal')}
                    >
                      <option value="easy">Easy</option>
                      <option value="medium">Medium</option>
                      <option value="hard">Hard</option>
                      <option value="mixed">Mixed</option>
                    </select>
                  </label>
                  <label className="flex flex-col text-[13px] font-semibold text-foreground">
                    <span className="mb-1.5">Statement language</span>
                    <select
                      value={aiLanguage}
                      onChange={(e) => setAiLanguage(e.target.value as ExamLanguage)}
                      className={cn(SELECT_CLS, 'font-normal')}
                    >
                      <option value="en">English</option>
                      <option value="hi">हिन्दी</option>
                      <option value="te">తెలుగు</option>
                    </select>
                  </label>
                </div>

                {/* Allowed languages (shared with the manual composer) */}
                <div>
                  <span className={LABEL_CLS}>Candidates may solve in</span>
                  <LanguageChips langs={langs} onToggle={toggleLang} />
                </div>

                <Button
                  type="button"
                  disabled={generateMut.isPending || !aiTopic.trim() || langs.length === 0}
                  aria-busy={generateMut.isPending}
                  onClick={() => generateMut.mutate()}
                >
                  {generateMut.isPending ? (
                    <Loader2 className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Sparkles aria-hidden="true" />
                  )}
                  {generateMut.isPending ? 'Generating…' : 'Generate'}
                </Button>

                {generateMut.isError ? (
                  <GenerationError
                    failure={toGenerationFailure(generateMut.error, 'AI generation failed.')}
                    onRetry={() => generateMut.mutate()}
                    retryDisabled={generateMut.isPending || !aiTopic.trim() || langs.length === 0}
                  />
                ) : null}
              </div>

              {/* Drafts preview */}
              {aiPreview.length > 0 && (
                <div className="space-y-3 border-t border-border pt-5">
                  <h3 className="text-[15px] font-bold text-foreground">Review the drafts</h3>
                  <p className="text-[13px] text-muted-foreground">
                    Review the drafts — check the sample cases and remove any problem you don&apos;t
                    want. Each problem ships with hidden test cases used for grading.
                  </p>
                  {aiPreview.map((q, i) => {
                    const sampleN = q.test_cases.filter((t) => t.is_sample).length;
                    const hiddenN = q.test_cases.length - sampleN;
                    return (
                      <div key={i} className="rounded-[16px] border border-border bg-card p-3">
                        <div className="flex items-start gap-2.5">
                          <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full bg-[var(--ui-inset)] font-mono text-[12px] text-[var(--ui-soft)]">
                            {i + 1}
                          </span>
                          <div className="min-w-0 flex-1">
                            <p className="line-clamp-4 whitespace-pre-wrap break-words text-[13.5px] leading-snug text-foreground">
                              {q.prompt}
                            </p>
                            <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[12.5px] text-muted-foreground">
                              <StatusTag tone="neutral" className="text-[12px]">
                                {sampleN} sample
                              </StatusTag>
                              <StatusTag tone="neutral" className="text-[12px]">
                                {hiddenN} hidden
                              </StatusTag>
                              {q.reference_solution ? (
                                <span>reference solution included</span>
                              ) : (
                                <span>no reference solution</span>
                              )}
                            </div>
                          </div>
                          <button
                            type="button"
                            aria-label={`Discard draft ${i + 1}`}
                            className={ICON_BTN}
                            onClick={() => setAiPreview((p) => p.filter((_, j) => j !== i))}
                          >
                            <X size={16} aria-hidden="true" />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      disabled={addGeneratedMut.isPending}
                      aria-busy={addGeneratedMut.isPending}
                      onClick={() => addGeneratedMut.mutate()}
                    >
                      {addGeneratedMut.isPending ? (
                        <Loader2 className="animate-spin" aria-hidden="true" />
                      ) : (
                        <Plus aria-hidden="true" />
                      )}
                      Add {aiPreview.length} problem{aiPreview.length === 1 ? '' : 's'}
                    </Button>
                    <Button type="button" variant="ghost" onClick={() => setAiPreview([])}>
                      Discard
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Manual */}
          {composerTab === 'manual' && (
            <form onSubmit={submit} className="space-y-5" aria-label="New coding question">
              <div>
                <h3 className="mb-3 text-[15px] font-bold text-foreground">Problem</h3>
                <textarea
                  className={cn(FIELD_CLS, 'resize-y')}
                  rows={3}
                  placeholder="Problem statement — describe the task, input format, and output format…"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  aria-label="Problem statement"
                />
              </div>

              <div className="space-y-4 border-t border-border pt-5">
                <h3 className="text-[15px] font-bold text-foreground">
                  Languages and starter code
                </h3>
                {/* Allowed languages */}
                <div>
                  <span className={LABEL_CLS}>Allowed languages</span>
                  <LanguageChips langs={langs} onToggle={toggleLang} />
                </div>

                {/* Starter code */}
                <div>
                  <span className={LABEL_CLS}>
                    Starter code (optional) — {LANG_LABEL[editorLang]}
                  </span>
                  <Suspense fallback={<Skeleton className="h-28 rounded-[12px]" />}>
                    <CodeEditor
                      language={editorLang}
                      value={starter}
                      onChange={setStarter}
                      minHeight={120}
                      placeholder="// pre-filled in the candidate's editor"
                      textareaId={`ce-section-${sectionId}`}
                    />
                  </Suspense>
                </div>
              </div>

              <div className="space-y-3 border-t border-border pt-5">
                <h3 className="text-[15px] font-bold text-foreground">Grading</h3>
                <TestCasesEditor tests={tests} setTests={setTests} />
              </div>

              {/* Points + submit */}
              <div className="flex flex-wrap items-center gap-3 border-t border-border pt-5">
                <label className="flex items-center gap-2 text-[13.5px] font-semibold text-foreground">
                  Points
                  <Input
                    type="number"
                    min={1}
                    value={points}
                    onChange={(e) => setPoints(e.target.value)}
                    className="w-24"
                    aria-label="Points"
                  />
                </label>
                <Button
                  type="submit"
                  className="ml-auto"
                  disabled={addMut.isPending}
                  aria-busy={addMut.isPending}
                >
                  {addMut.isPending ? (
                    <Loader2 className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Plus aria-hidden="true" />
                  )}
                  Add coding question
                </Button>
              </div>
            </form>
          )}
        </div>
      )}
    </div>
  );
}
