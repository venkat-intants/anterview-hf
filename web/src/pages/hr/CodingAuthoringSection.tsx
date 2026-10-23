// CodingAuthoringSection — section-scoped version of the coding question
// composer. Works identically to CodingAuthoring but posts to the
// section-scoped endpoints:
//   GET  /hr/exams/{examId}/sections/{sectionId}/coding-questions
//   POST /hr/exams/{examId}/sections/{sectionId}/coding-questions
// This file is lazy-loaded by ExamEditor so the CodeEditor import doesn't
// inflate the initial bundle.

import BankProvenanceChip from '@/components/bank/BankProvenanceChip';
import { Suspense, lazy, useMemo, useRef, useState } from 'react';
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
import { errorMessage } from '@/lib/errorMessage';
import { examEditorMutationKey } from '@/lib/examEditor';
import { plural } from '@/lib/formatters';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { useTabKeys } from '@/lib/tabKeys';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { QueryErrorPanel } from '@/components/QueryState';
import { StatusTag } from '@/design/components/primitives';
import { EmptyState } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { fieldLabelClass } from '@/components/ui/control-styles';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
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

// The heading over a group that is not one control (chips, a code editor),
// in the FormField label look.
const GROUP_LABEL = cn(fieldLabelClass, 'mb-1.5 block');
const CHIP =
  'inline-flex min-h-10 items-center rounded-full border px-4 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';
// A remove button: the ghost icon Button, tinted danger on hover.
const REMOVE_BTN =
  'shrink-0 text-muted-foreground hover:bg-[var(--ui-danger-wash)] hover:text-[var(--ui-danger)]';

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
              <Textarea
                rows={2}
                className="min-h-[56px] font-mono md:text-[13px]"
                placeholder="stdin (input fed to the program)"
                value={tc.stdin}
                onChange={(e) =>
                  setTests((p) => p.map((t, j) => (j === ti ? { ...t, stdin: e.target.value } : t)))
                }
                aria-label={`Test ${ti + 1} input`}
              />
              <Textarea
                rows={2}
                className="min-h-[56px] font-mono md:text-[13px]"
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
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="Remove test case"
                  className={cn(REMOVE_BTN, 'ml-auto')}
                  onClick={() => setTests((p) => p.filter((_, j) => j !== ti))}
                >
                  <Trash2 aria-hidden="true" />
                </Button>
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

const COMPOSER_TABS = ['manual', 'ai'] as const;
type ComposerTab = (typeof COMPOSER_TABS)[number];
const COMPOSER_TAB_META: Record<ComposerTab, { label: string; icon: typeof Pencil }> = {
  manual: { label: 'Manual', icon: Pencil },
  ai: { label: 'AI', icon: Sparkles },
};

interface Props {
  examId: string;
  sectionId: string;
  locked: boolean;
}

export default function CodingAuthoringSection({ examId, sectionId, locked }: Props) {
  const qc = useQueryClient();
  const qKey = ['hr', 'exam', examId, 'section', sectionId, 'coding-questions'];
  const editorKey = examEditorMutationKey(examId);

  const questionsQuery = useQuery({
    queryKey: qKey,
    queryFn: () => listSectionCodingQuestions(examId, sectionId),
  });
  const questions = questionsQuery.data ?? [];

  // Prefix invalidation: this list, and the structure's question counts that
  // gate Publish.
  const refresh = () => void invalidateAfterHrMutation(qc);

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
      toast.error(errorMessage(e, 'Could not save this question to a bank.'));
      setSavingToBankId(null);
    },
  });

  // Composer state
  const [composerTab, setComposerTab] = useState<ComposerTab>('manual');
  const tabs = useTabKeys({
    idBase: `coding-${sectionId}`,
    keys: COMPOSER_TABS,
    active: composerTab,
    onChange: setComposerTab,
  });
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
  // How many drafts the current "Add" has saved, for the partial-failure toast.
  const savedCount = useRef(0);

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
    mutationKey: editorKey,
    mutationFn: async (drafts: GeneratedCodingQuestion[]) => {
      savedCount.current = 0;
      for (const q of drafts) {
        await addSectionCodingQuestion(examId, sectionId, {
          prompt: q.prompt,
          allowed_languages: q.allowed_languages,
          starter_code: q.starter_code,
          reference_solution: q.reference_solution,
          test_cases: q.test_cases,
          time_limit_ms: q.time_limit_ms,
          points: q.points,
        });
        savedCount.current += 1;
        // Out of the preview as soon as it is saved: a failure part-way then
        // leaves only the unsaved drafts, and Add again cannot duplicate the
        // saved ones (audit E7).
        setAiPreview((p) => p.filter((d) => d !== q));
      }
      return drafts.length;
    },
    onSuccess: (n) => toast.success(`${plural(n, 'coding problem')} added`),
    onError: (e: unknown) => {
      const base = errorMessage(e, 'Could not add the generated problems.');
      const saved = savedCount.current;
      toast.error(
        saved > 0
          ? `${base} ${plural(saved, 'problem')} ${saved === 1 ? 'was' : 'were'} added; the rest are still in the list.`
          : base,
      );
    },
    onSettled: refresh, // some drafts may have been persisted before a failure
  });

  const addMut = useMutation({
    mutationKey: editorKey,
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
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not add the coding question.')),
  });

  const delMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (qid: string) => deleteSectionCodingQuestion(examId, sectionId, qid),
    onSuccess: () => {
      toast.success('Coding question deleted');
      refresh();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not delete the coding question.')),
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
      ) : questionsQuery.isError && !questionsQuery.data ? (
        <QueryErrorPanel
          title="Could not load this section's coding questions."
          error={questionsQuery.error}
          onRetry={() => void questionsQuery.refetch()}
          retrying={questionsQuery.isFetching}
        />
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
                        {plural(q.test_cases.length, 'test')} · {sampleN} sample
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
                    <ConfirmDeleteButton
                      title={`Delete coding question ${i + 1}`}
                      confirmText="Delete question"
                      pending={delMut.isPending && delMut.variables === q.id}
                      onConfirm={() => delMut.mutate(q.id)}
                    />
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
          <Lock size={15} className="shrink-0" aria-hidden="true" /> Questions are locked - see the
          banner above.
        </div>
      ) : (
        <div className="rounded-[16px] border border-dashed border-[var(--ui-line-strong)] p-4">
          {/* Tab bar — mirrors the MCQ composer's Manual | AI switcher */}
          <div
            role="tablist"
            aria-label="How to add coding questions"
            className="mb-4 flex gap-1 rounded-full bg-[var(--ui-inset-strong)] p-1"
            {...tabs.tabListProps}
          >
            {COMPOSER_TABS.map((key) => {
              const t = COMPOSER_TAB_META[key];
              const Icon = t.icon;
              const active = composerTab === key;
              return (
                <button
                  key={key}
                  type="button"
                  {...tabs.tabProps(key)}
                  onClick={() => setComposerTab(key)}
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
            <div className="space-y-5" {...tabs.panelProps('ai')}>
              <div className="space-y-4">
                <h3 className="text-[15px] font-bold text-foreground">What to generate</h3>
                <Input
                  placeholder="Topic - e.g. arrays & strings, SQL joins, or Junior Python Developer"
                  value={aiTopic}
                  onChange={(e) => setAiTopic(e.target.value)}
                  aria-label="Topic"
                />
                <div className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-3">
                  <FormField label="Problems">
                    {(p) => (
                      <Input
                        {...p}
                        type="number"
                        min={1}
                        max={8}
                        value={aiCount}
                        onChange={(e) => setAiCount(e.target.value)}
                        aria-label="Number of problems"
                      />
                    )}
                  </FormField>
                  <FormField label="Difficulty">
                    {(p) => (
                      <SelectControl
                        {...p}
                        value={aiDifficulty}
                        onChange={(e) => setAiDifficulty(e.target.value as ExamDifficulty)}
                      >
                        <option value="easy">Easy</option>
                        <option value="medium">Medium</option>
                        <option value="hard">Hard</option>
                        <option value="mixed">Mixed</option>
                      </SelectControl>
                    )}
                  </FormField>
                  <FormField label="Statement language">
                    {(p) => (
                      <SelectControl
                        {...p}
                        value={aiLanguage}
                        onChange={(e) => setAiLanguage(e.target.value as ExamLanguage)}
                      >
                        <option value="en">English</option>
                        <option value="hi">हिन्दी</option>
                        <option value="te">తెలుగు</option>
                      </SelectControl>
                    )}
                  </FormField>
                </div>

                {/* Allowed languages (shared with the manual composer) */}
                <div>
                  <span className={GROUP_LABEL}>Candidates may solve in</span>
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
                    failure={toGenerationFailure(generateMut.error, 'Could not generate a coding question.')}
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
                    Review the drafts - check the sample cases and remove any problem you don&apos;t
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
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            aria-label={`Discard draft ${i + 1}`}
                            className={REMOVE_BTN}
                            onClick={() => setAiPreview((p) => p.filter((_, j) => j !== i))}
                          >
                            <X aria-hidden="true" />
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      disabled={addGeneratedMut.isPending}
                      aria-busy={addGeneratedMut.isPending}
                      onClick={() => addGeneratedMut.mutate(aiPreview)}
                    >
                      {addGeneratedMut.isPending ? (
                        <Loader2 className="animate-spin" aria-hidden="true" />
                      ) : (
                        <Plus aria-hidden="true" />
                      )}
                      Add {plural(aiPreview.length, 'problem')}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      disabled={addGeneratedMut.isPending}
                      onClick={() => setAiPreview([])}
                    >
                      Discard all
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Manual */}
          {composerTab === 'manual' && (
            <div {...tabs.panelProps('manual')}>
            <form onSubmit={submit} className="space-y-5" aria-label="New coding question">
              <div>
                <h3 className="mb-3 text-[15px] font-bold text-foreground">Problem</h3>
                <Textarea
                  rows={3}
                  placeholder="Problem statement - describe the task, input format, and output format…"
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
                  <span className={GROUP_LABEL}>Allowed languages</span>
                  <LanguageChips langs={langs} onToggle={toggleLang} />
                </div>

                {/* Starter code */}
                <div>
                  <span className={GROUP_LABEL}>
                    Starter code (optional) - {LANG_LABEL[editorLang]}
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
            </div>
          )}
        </div>
      )}
    </div>
  );
}
