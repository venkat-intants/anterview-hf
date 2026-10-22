// ExamEditor — Rounds → Sections → Questions tree (HR Phase 2+).
// Each exam has ordered Rounds; each Round has Sections (mcq|coding).
// HR can add/rename/reorder/delete rounds; set pass_threshold, time_limit,
// advances_to_interview; publish/unpublish per round; add/rename/delete sections;
// author questions per section (MCQ inline, coding via CodingAuthoringSection).
// Per-round: an Assign/schedule panel that posts to the assignment endpoint
// with round_id + optional scheduled_at.
// Exam-level settings (title, retake, auto-advance, close/reopen) sit above the
// rounds.
// Legacy single-round/single-section exams render gracefully from GET /structure.
//
// English-only by design (CLAUDE.md - staff consoles are not translated).

import BankProvenanceChip from '@/components/bank/BankProvenanceChip';
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  useInfiniteQuery,
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  Plus,
  Check,
  Copy,
  Send,
  Ban,
  Lock,
  BarChart3,
  Loader2,
  ListChecks,
  Clock,
  Sparkles,
  Upload,
  Download,
  FileText,
  AlertTriangle,
  X,
  Pencil,
  ChevronDown,
  ChevronRight,
  Code2,
  Library,
  Search,
  Settings,
} from '@/design/components/icons';
import {
  getExam,
  getStructure,
  updateExam,
  // MCQ question helpers
  generateQuestions,
  importSectionQuestions,
  downloadQuestionTemplate,
  // Round helpers
  createRound,
  updateRound,
  deleteRound,
  reorderRounds,
  duplicateRound,
  // Section helpers
  createSection,
  updateSection,
  deleteSection,
  // Section-scoped question helpers
  listSectionQuestions,
  addSectionQuestion,
  deleteSectionQuestion,
  // Assignments
  listAssignments,
  assignExam,
  revokeAssignment,
  type ExamDetail,
  type Round,
  type Section,
  type AssignResult,
  type GeneratedQuestion,
  type ImportResult,
  type ExamDifficulty,
  type ExamLanguage,
  type ExamUpdateInput,
} from '@/api/exams';
import { saveExamQuestionToBank, type SaveToBankInput } from '@/api/questionBanks';
import { getPipeline, type PipelineRow } from '@/api/pipeline';
import { applicationKey } from '@/lib/applicationKey';
import { canUnpublishRound, roundLockReason } from '@/lib/examLocks';
import { examEditorMutationKey, minutesToSeconds, parseThreshold } from '@/lib/examEditor';
import { errorMessage } from '@/lib/errorMessage';
import { formatDateTime } from '@/lib/formatters';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { statusLabel } from '@/lib/statusLabel';
import { useTabKeys } from '@/lib/tabKeys';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { GlassCard, StatusTag, ToggleSwitch } from '@/design/components/primitives';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import { EmptyState, Page } from '@/design/components/page';
import { EditorHeader, DocStatusPill } from '@/components/hr/EditorHeader';
import { LockBanner } from '@/components/bank/LockBanner';
import { BankQuestionPicker } from '@/components/bank/BankQuestionPicker';
import { SaveToBankButton } from '@/components/bank/SaveToBankButton';
import { GenerationError } from '@/components/bank/GenerationError';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { QueryErrorPanel } from '@/components/QueryState';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { toGenerationFailure } from '@/lib/generationError';

// Lazy-load CodingAuthoringSection to avoid importing the code-editor eagerly
const CodingAuthoringSection = lazy(() => import('./CodingAuthoringSection'));

// ── Shared styles ─────────────────────────────────────────────────────────────

const inputCls =
  'w-full rounded-[12px] border border-border bg-secondary px-3 py-2 ' +
  'text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus:outline-none ' +
  'focus:border-[var(--accent)] focus-visible:ring-2 focus-visible:ring-[var(--accent)] transition-colors';

const smallInputCls =
  'h-10 rounded-[10px] border border-border bg-secondary px-2.5 text-[13px] text-foreground ' +
  'placeholder:text-[var(--ui-faint)] focus:outline-none focus:border-[var(--accent)] ' +
  'focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-60';

/** "1 question" / "3 questions". */
function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? '' : 's'}`;
}

// ── Status helpers ────────────────────────────────────────────────────────────

type TagToneKey = 'neutral' | 'forest' | 'amber';

function roundStatusTone(s: string): TagToneKey {
  return s === 'published' ? 'forest' : 'amber';
}

// ── MCQ section question list ─────────────────────────────────────────────────

interface McqSectionProps {
  examId: string;
  sectionId: string;
  locked: boolean;
}

const COMPOSER_TABS = ['manual', 'ai', 'excel'] as const;
type ComposerTab = (typeof COMPOSER_TABS)[number];
const COMPOSER_TAB_META: Record<ComposerTab, { label: string; icon: typeof Pencil }> = {
  manual: { label: 'Manual', icon: Pencil },
  ai: { label: 'AI', icon: Sparkles },
  // The tab takes .csv as well as .xlsx, so it is not called "Excel" (audit E21).
  excel: { label: 'Spreadsheet', icon: Upload },
};

export function McqSection({ examId, sectionId, locked }: McqSectionProps) {
  const qc = useQueryClient();
  const qKey = ['hr', 'exam', examId, 'section', sectionId, 'questions'];
  const editorKey = examEditorMutationKey(examId);

  const questionsQuery = useQuery({
    queryKey: qKey,
    queryFn: () => listSectionQuestions(examId, sectionId),
  });
  const questions = questionsQuery.data ?? [];

  // Composer: manual | ai | excel
  const [composerTab, setComposerTab] = useState<ComposerTab>('manual');
  const tabs = useTabKeys({
    idBase: `mcq-${sectionId}`,
    keys: COMPOSER_TABS,
    active: composerTab,
    onChange: setComposerTab,
  });
  const [prompt, setPrompt] = useState('');
  const [options, setOptions] = useState<string[]>(['', '', '', '']);
  // null: no option is marked correct (the marked one was removed).
  const [correctIdx, setCorrectIdx] = useState<number | null>(0);
  const [points, setPoints] = useState('1');

  // AI
  const [aiTopic, setAiTopic] = useState('');
  const [aiCount, setAiCount] = useState('5');
  const [aiDifficulty, setAiDifficulty] = useState<ExamDifficulty>('medium');
  const [aiLanguage, setAiLanguage] = useState<ExamLanguage>('en');
  const [aiPreview, setAiPreview] = useState<GeneratedQuestion[]>([]);
  // How many drafts the current "Add" has saved, for the partial-failure toast.
  const savedCount = useRef(0);

  // Excel
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // PH4-D1 — the bank picker, and per-row "Save to bank".
  const [showBankPicker, setShowBankPicker] = useState(false);
  const [savingToBankId, setSavingToBankId] = useState<string | null>(null);

  // Prefix invalidation: the question list, and the structure's question
  // counts that gate Publish, and the exam list's "N Qs".
  const refresh = () => void invalidateAfterHrMutation(qc);

  const saveToBankMut = useMutation({
    mutationFn: ({ qid, body }: { qid: string; body: SaveToBankInput }) =>
      saveExamQuestionToBank(examId, qid, body),
    onSuccess: () => {
      toast.success('Saved to bank as a new draft');
      setSavingToBankId(null);
    },
    onError: (e: unknown) => {
      toast.error(errorMessage(e, 'Could not save this question to a bank.'));
      setSavingToBankId(null);
    },
  });

  const addMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (q: { prompt: string; options: string[]; correct_index: number; points: number }) =>
      addSectionQuestion(examId, sectionId, q),
    onSuccess: () => {
      setPrompt('');
      setOptions(['', '', '', '']);
      setCorrectIdx(0);
      setPoints('1');
      toast.success('Question added');
      refresh();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not add the question.')),
  });

  const delMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (qid: string) => deleteSectionQuestion(examId, sectionId, qid),
    onSuccess: () => {
      toast.success('Question deleted');
      refresh();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not delete the question.')),
  });

  // AI generate uses legacy exam-level endpoint — the questions are added
  // section-by-section so we call addSectionQuestion per item
  const generateMut = useMutation({
    mutationFn: () =>
      generateQuestions(examId, {
        topic: aiTopic.trim(),
        num_questions: Math.max(1, Math.min(30, Number(aiCount) || 5)),
        difficulty: aiDifficulty,
        language: aiLanguage,
      }),
    onSuccess: (res) => {
      setAiPreview(res.questions);
      if (res.questions.length === 0) toast.error('No questions were generated. Try again.');
    },
    // No toast: the failure is shown inline, with the server's own sentence.
  });

  const addGeneratedMut = useMutation({
    mutationKey: editorKey,
    mutationFn: async (qs: GeneratedQuestion[]) => {
      savedCount.current = 0;
      for (const q of qs) {
        await addSectionQuestion(examId, sectionId, {
          prompt: q.prompt,
          options: q.options,
          correct_index: q.correct_index,
          points: q.points,
        });
        savedCount.current += 1;
        // Each draft leaves the preview the moment it is saved, so a failure
        // part-way leaves only the unsaved ones, and pressing Add again cannot
        // save the first ones twice (audit E7).
        setAiPreview((prev) => prev.filter((d) => d !== q));
      }
      return qs.length;
    },
    onSuccess: (n) => toast.success(`${plural(n, 'question')} added`),
    onError: (e: unknown) => {
      const base = errorMessage(e, 'Could not add the generated questions.');
      const saved = savedCount.current;
      toast.error(
        saved > 0
          ? `${base} ${plural(saved, 'question')} were added; the rest are still in the list.`
          : base,
      );
    },
    // The list refreshes on failure too: some drafts may already be saved.
    onSettled: refresh,
  });

  const importMut = useMutation({
    mutationKey: editorKey,
    // Into THIS section, checked against this section's round (audit E1).
    mutationFn: (file: File) => importSectionQuestions(examId, sectionId, file),
    onSuccess: (res) => {
      setImportResult(res);
      if (res.added > 0) toast.success(`${plural(res.added, 'question')} imported`);
      else toast.error('No questions were imported. Check the rows listed below.');
      refresh();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not import the questions.')),
  });

  function submitQuestion(ev: React.FormEvent) {
    ev.preventDefault();
    if (!prompt.trim()) return toast.error('Question prompt is required.');
    const filled = options.map((o) => o.trim());
    if (filled.some((o) => !o)) return toast.error('All option fields must be filled.');
    if (correctIdx === null) return toast.error('Mark which option is correct.');
    const pts = Number(points);
    if (!Number.isInteger(pts) || pts < 1 || pts > 100) {
      return toast.error('Points must be a whole number from 1 to 100.');
    }
    addMut.mutate({
      prompt: prompt.trim(),
      options: filled,
      correct_index: correctIdx,
      points: pts,
    });
  }

  function removeOption(oi: number) {
    setOptions((prev) => prev.filter((_, j) => j !== oi));
    // Removing the option marked correct clears the mark; it used to move it
    // silently onto a neighbour, so a different answer was saved as correct.
    setCorrectIdx((c) => (c === null || c === oi ? null : c > oi ? c - 1 : c));
  }

  async function handleDownloadTemplate() {
    try {
      await downloadQuestionTemplate();
    } catch (e) {
      toast.error(errorMessage(e, 'Could not download the template.'));
    }
  }

  return (
    <div className="mt-3 space-y-3">
      {questionsQuery.isLoading ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          <span className="sr-only">Loading questions</span>
          <Skeleton className="h-16 rounded-[14px]" />
        </div>
      ) : null}
      {questionsQuery.isError && !questionsQuery.data ? (
        <QueryErrorPanel
          title="Could not load this section's questions."
          error={questionsQuery.error}
          onRetry={() => void questionsQuery.refetch()}
          retrying={questionsQuery.isFetching}
        />
      ) : null}

      {/* Existing questions */}
      {questions.length > 0 && (
        <Stagger className="flex flex-col gap-2">
          {questions.map((q, i) => (
            <StaggerItem key={q.id}>
              <div
                className={cn(
                  'rounded-[14px] border border-border p-3.5',
                  locked ? 'bg-card' : 'bg-[var(--ui-inset-soft)]',
                )}
              >
                <div className="flex items-start gap-3">
                  <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full bg-[var(--ui-inset)] font-mono text-[12px] text-[var(--ui-soft)]">
                    {i + 1}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <p className="text-[13px] font-medium leading-snug text-foreground">
                        {q.prompt}{' '}
                        <span className="font-normal text-muted-foreground">({q.points} pt)</span>
                      </p>
                      <BankProvenanceChip
                        rootId={q.source_bank_root_id}
                        version={q.source_bank_version}
                      />
                    </div>
                    {/* Outline row facts: type, marks, and whether the row can
                        still change. Locked is a word and an icon, not just a
                        muted look. */}
                    <p className="mt-1 flex flex-wrap items-center gap-x-2 text-[12px] text-muted-foreground">
                      <span>MCQ</span>
                      <span aria-hidden="true">·</span>
                      <span className="tabular-nums">{plural(q.points, 'mark')}</span>
                      {locked ? (
                        <span className="inline-flex items-center gap-1 font-medium text-foreground">
                          <Lock size={11} aria-hidden="true" /> Locked
                        </span>
                      ) : null}
                    </p>
                    <ul className="mt-2 space-y-0.5 pl-0">
                      {q.options.map((opt, oi) => (
                        <li
                          key={oi}
                          className={cn(
                            'flex items-center gap-1.5 text-[12px]',
                            oi === q.correct_index
                              ? 'font-medium text-[var(--ui-ok)]'
                              : 'text-muted-foreground',
                          )}
                        >
                          {oi === q.correct_index ? (
                            <Check size={12} className="shrink-0" aria-hidden="true" />
                          ) : (
                            <span className="h-3 w-3 shrink-0" aria-hidden="true" />
                          )}
                          {opt}
                        </li>
                      ))}
                    </ul>
                    {/* SaveToBankButton owns its own open/closed state and
                        renders the "Save to bank" link itself, so gating it
                        behind a second one made the user click twice. */}
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
                      title={`Delete question ${i + 1}`}
                      confirmText="Delete question"
                      pending={delMut.isPending && delMut.variables === q.id}
                      onConfirm={() => delMut.mutate(q.id)}
                    />
                  )}
                </div>
              </div>
            </StaggerItem>
          ))}
        </Stagger>
      )}

      {/* PH4-D1 — add an approved MCQ question straight from a bank */}
      {!locked && (
        <Button type="button" variant="outline" onClick={() => setShowBankPicker(true)}>
          <Library aria-hidden="true" /> Add from bank
        </Button>
      )}
      {showBankPicker && (
        <BankQuestionPicker
          examId={examId}
          sectionId={sectionId}
          sectionKind="mcq"
          onClose={() => setShowBankPicker(false)}
          onAdded={refresh}
        />
      )}

      {/* Composer — three ways to add questions; hidden when locked */}
      {!locked && (
        <div className="rounded-[14px] border border-dashed border-border bg-[var(--ui-inset-soft)] p-3.5">
          {/* Tab bar */}
          <div
            role="tablist"
            aria-label="How to add questions"
            className="mb-3 flex gap-1 rounded-[10px] border border-border bg-[var(--ui-inset)] p-1"
            {...tabs.tabListProps}
          >
            {COMPOSER_TABS.map((key) => {
              const { label, icon: Icon } = COMPOSER_TAB_META[key];
              const active = composerTab === key;
              return (
                <button
                  key={key}
                  type="button"
                  {...tabs.tabProps(key)}
                  onClick={() => setComposerTab(key)}
                  className={cn(
                    'flex min-h-10 flex-1 items-center justify-center gap-1.5 rounded-[8px] px-2 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                    active
                      ? 'bg-[rgba(var(--accent-rgb),0.16)] text-[var(--ui-info)]'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  <Icon size={13} aria-hidden="true" />
                  {label}
                </button>
              );
            })}
          </div>

          {/* Manual */}
          {composerTab === 'manual' && (
            <div {...tabs.panelProps('manual')}>
              <form onSubmit={submitQuestion} className="space-y-2.5" aria-label="New question">
                <textarea
                  className={cn(inputCls, 'resize-none text-[13px]')}
                  rows={2}
                  maxLength={2000}
                  placeholder="Question prompt…"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  aria-label="Question prompt"
                />
                <div className="space-y-1.5">
                  {options.map((opt, oi) => (
                    <div key={oi} className="flex items-center gap-2">
                      <input
                        type="radio"
                        name={`correct-${sectionId}`}
                        checked={correctIdx === oi}
                        onChange={() => setCorrectIdx(oi)}
                        className="h-3.5 w-3.5 flex-none accent-[var(--accent)]"
                        aria-label={`Mark option ${oi + 1} correct`}
                      />
                      <input
                        className={cn(inputCls, 'text-[13px]')}
                        placeholder={`Option ${oi + 1}`}
                        value={opt}
                        onChange={(e) =>
                          setOptions((prev) => prev.map((o, j) => (j === oi ? e.target.value : o)))
                        }
                        aria-label={`Option ${oi + 1}`}
                      />
                      {options.length > 2 && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          aria-label={`Remove option ${oi + 1}`}
                          className="shrink-0 text-muted-foreground hover:text-[var(--ui-danger)]"
                          onClick={() => removeOption(oi)}
                        >
                          <X aria-hidden="true" />
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {options.length < 6 && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setOptions((prev) => [...prev, ''])}
                    >
                      <Plus aria-hidden="true" /> Add option
                    </Button>
                  )}
                  <label className="ml-auto flex items-center gap-1.5 text-[12px] text-muted-foreground">
                    Points
                    <input
                      type="number"
                      min={1}
                      max={100}
                      step={1}
                      value={points}
                      onChange={(e) => setPoints(e.target.value)}
                      className={cn(smallInputCls, 'w-16')}
                      aria-label="Points"
                    />
                  </label>
                  <Button type="submit" disabled={addMut.isPending} aria-busy={addMut.isPending}>
                    {addMut.isPending ? (
                      <Loader2 className="animate-spin" aria-hidden="true" />
                    ) : (
                      <Plus aria-hidden="true" />
                    )}
                    Add question
                  </Button>
                </div>
              </form>
            </div>
          )}

          {/* AI */}
          {composerTab === 'ai' && (
            <div className="space-y-2.5" {...tabs.panelProps('ai')}>
              <form
                onSubmit={(ev) => {
                  ev.preventDefault();
                  if (!aiTopic.trim()) return toast.error('Enter a topic for the AI.');
                  generateMut.mutate();
                }}
                className="space-y-2.5"
              >
                <input
                  className={cn(inputCls, 'text-[13px]')}
                  placeholder="Topic, e.g. React hooks or Junior Python Developer"
                  value={aiTopic}
                  onChange={(e) => setAiTopic(e.target.value)}
                  aria-label="AI topic"
                />
                <div className="flex flex-wrap items-end gap-2">
                  <label className="flex flex-col gap-1 text-[12px] text-muted-foreground">
                    Questions
                    <input
                      type="number"
                      min={1}
                      max={30}
                      value={aiCount}
                      onChange={(e) => setAiCount(e.target.value)}
                      className={cn(smallInputCls, 'w-16')}
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-[12px] text-muted-foreground">
                    Difficulty
                    <select
                      value={aiDifficulty}
                      onChange={(e) => setAiDifficulty(e.target.value as ExamDifficulty)}
                      className={smallInputCls}
                    >
                      <option value="easy">Easy</option>
                      <option value="medium">Medium</option>
                      <option value="hard">Hard</option>
                      <option value="mixed">Mixed</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1 text-[12px] text-muted-foreground">
                    Language
                    <select
                      value={aiLanguage}
                      onChange={(e) => setAiLanguage(e.target.value as ExamLanguage)}
                      className={smallInputCls}
                    >
                      <option value="en">English</option>
                      <option value="hi">हिन्दी</option>
                      <option value="te">తెలుగు</option>
                    </select>
                  </label>
                  <Button
                    type="submit"
                    className="ml-auto"
                    disabled={generateMut.isPending}
                    aria-busy={generateMut.isPending}
                  >
                    {generateMut.isPending ? (
                      <Loader2 className="animate-spin" aria-hidden="true" />
                    ) : (
                      <Sparkles aria-hidden="true" />
                    )}
                    {generateMut.isPending ? 'Generating…' : 'Generate'}
                  </Button>
                </div>
              </form>

              {generateMut.isError ? (
                <GenerationError
                  failure={toGenerationFailure(generateMut.error, 'Generation failed.')}
                  onRetry={() => generateMut.mutate()}
                  retryDisabled={generateMut.isPending || !aiTopic.trim()}
                />
              ) : null}

              {aiPreview.length > 0 && (
                <div className="space-y-2 rounded-[12px] border border-[rgba(var(--accent-rgb),0.25)] bg-[rgba(var(--accent-rgb),0.05)] p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-[12px] font-semibold text-[var(--ui-info)]">
                      {plural(aiPreview.length, 'draft question')}
                    </p>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={addGeneratedMut.isPending}
                      onClick={() => setAiPreview([])}
                    >
                      Discard all
                    </Button>
                  </div>
                  <ul className="space-y-1.5">
                    {aiPreview.map((q, qi) => (
                      <li
                        key={qi}
                        className="rounded-[9px] border border-border bg-[var(--ui-inset-soft)] p-2.5"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-[12px] font-medium text-foreground">
                            {qi + 1}. {q.prompt}
                          </p>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            aria-label={`Discard draft ${qi + 1}`}
                            className="shrink-0 text-muted-foreground hover:text-[var(--ui-danger)]"
                            disabled={addGeneratedMut.isPending}
                            onClick={() => setAiPreview((prev) => prev.filter((_, j) => j !== qi))}
                          >
                            <X aria-hidden="true" />
                          </Button>
                        </div>
                        <ul className="mt-1 space-y-0.5">
                          {q.options.map((opt, oi) => (
                            <li
                              key={oi}
                              className={cn(
                                'flex items-center gap-1 text-[12px]',
                                oi === q.correct_index
                                  ? 'font-medium text-[var(--ui-ok)]'
                                  : 'text-muted-foreground',
                              )}
                            >
                              {oi === q.correct_index ? (
                                <Check size={11} className="shrink-0" aria-hidden="true" />
                              ) : (
                                <span className="h-2.5 w-2.5 shrink-0" aria-hidden="true" />
                              )}
                              {opt}
                            </li>
                          ))}
                        </ul>
                      </li>
                    ))}
                  </ul>
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
                    Add {plural(aiPreview.length, 'question')}
                  </Button>
                </div>
              )}
            </div>
          )}

          {/* Spreadsheet */}
          {composerTab === 'excel' && (
            <div className="space-y-2.5" {...tabs.panelProps('excel')}>
              <p className="text-[12px] text-muted-foreground">
                Upload an <span className="text-foreground">.xlsx</span> or{' '}
                <span className="text-foreground">.csv</span> file of up to 2 MB and 1,000 rows,
                with the columns Question · Option A-D · Correct (A/B/C/D) · Points. Questions are
                added to this section.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button type="button" variant="outline" onClick={() => void handleDownloadTemplate()}>
                  <Download aria-hidden="true" /> Template
                </Button>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".xlsx,.csv"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      setImportResult(null);
                      importMut.mutate(file);
                    }
                    e.target.value = '';
                  }}
                  className="hidden"
                  aria-label="Choose spreadsheet file"
                />
                <Button
                  type="button"
                  disabled={importMut.isPending}
                  aria-busy={importMut.isPending}
                  onClick={() => fileRef.current?.click()}
                >
                  {importMut.isPending ? (
                    <Loader2 className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Upload aria-hidden="true" />
                  )}
                  {importMut.isPending ? 'Uploading…' : 'Upload file'}
                </Button>
              </div>
              {importResult && (
                <div className="rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-2.5 space-y-1.5">
                  <p className="flex items-center gap-1.5 text-[12px] font-medium text-[var(--ui-ok)]">
                    <FileText size={13} aria-hidden="true" />
                    {plural(importResult.added, 'question')} imported
                  </p>
                  {importResult.errors.length > 0 && (
                    <div>
                      <p className="flex items-center gap-1 text-[12px] text-[var(--ui-warn)]">
                        <AlertTriangle size={12} aria-hidden="true" />
                        {plural(importResult.errors.length, 'row')} skipped
                      </p>
                      <ul className="mt-1 max-h-24 space-y-0.5 overflow-y-auto pl-4 text-[12px] text-muted-foreground">
                        {importResult.errors.map((er, ei) => (
                          <li key={ei} className="list-disc">
                            Row {er.row}: {er.message}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {locked && (
        <div className="flex items-center gap-2 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2 text-[12px] text-muted-foreground">
          <Lock size={13} aria-hidden="true" /> Questions are locked. See the banner above.
        </div>
      )}
    </div>
  );
}

// ── Section panel ─────────────────────────────────────────────────────────────

interface SectionPanelProps {
  examId: string;
  section: Section;
  locked: boolean;
  onDelete: (sectionId: string) => void;
  deleting: boolean;
}

function SectionPanel({ examId, section, locked, onDelete, deleting }: SectionPanelProps) {
  const [expanded, setExpanded] = useState(true);
  const [editing, setEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState(section.title);
  const sectionMinutes = () =>
    section.time_limit_seconds ? String(Math.round(section.time_limit_seconds / 60)) : '';
  const [minutesDraft, setMinutesDraft] = useState(sectionMinutes);
  const qc = useQueryClient();

  const renameMut = useMutation({
    mutationKey: examEditorMutationKey(examId),
    mutationFn: (patch: { title: string; time_limit_seconds: number | null }) =>
      updateSection(examId, section.round_id, section.id, patch),
    onSuccess: () => {
      setEditing(false);
      toast.success('Section saved');
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not save the section.')),
  });

  const kindLabel = section.kind === 'coding' ? 'Coding' : 'MCQ';

  return (
    <div className="rounded-[16px] border border-border bg-[var(--ui-inset)]">
      {/* Section header */}
      <div className="flex flex-wrap items-center gap-2 px-4 py-2">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-label={`${expanded ? 'Collapse' : 'Expand'} section ${section.title}`}
          className="text-muted-foreground"
        >
          {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
        </Button>

        {section.kind === 'coding' ? (
          <Code2 size={14} className="shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
        ) : (
          <ListChecks size={14} className="shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
        )}

        {editing ? (
          <form
            className="flex flex-1 flex-wrap items-center gap-2"
            aria-label={`Edit section ${section.title}`}
            onSubmit={(e) => {
              e.preventDefault();
              if (!titleDraft.trim()) return toast.error('Give the section a title.');
              renameMut.mutate({
                title: titleDraft.trim(),
                time_limit_seconds: minutesToSeconds(minutesDraft),
              });
            }}
          >
            <input
              autoFocus
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              maxLength={200}
              className={cn(smallInputCls, 'min-w-[160px] flex-1')}
              aria-label="Section title"
            />
            <input
              type="number"
              min={1}
              placeholder="No limit"
              value={minutesDraft}
              onChange={(e) => setMinutesDraft(e.target.value)}
              className={cn(smallInputCls, 'w-28')}
              aria-label="Section time limit in minutes (blank for no limit)"
            />
            <Button type="submit" disabled={renameMut.isPending} aria-busy={renameMut.isPending}>
              {renameMut.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
              Save
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Cancel"
              onClick={() => {
                setEditing(false);
                setTitleDraft(section.title);
                setMinutesDraft(sectionMinutes());
              }}
            >
              <X aria-hidden="true" />
            </Button>
          </form>
        ) : (
          <>
            <span className="flex-1 text-[13px] font-medium text-foreground">{section.title}</span>
            <StatusTag tone="neutral" className="text-[12px]">
              {kindLabel}
            </StatusTag>
            {section.time_limit_seconds && (
              <span className="flex items-center gap-1 text-[12px] text-[var(--ui-faint)]">
                <Clock size={11} aria-hidden="true" />
                {Math.round(section.time_limit_seconds / 60)} min
              </span>
            )}
            {!locked && (
              <>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`Edit section ${section.title}`}
                  className="text-muted-foreground"
                  onClick={() => {
                    setTitleDraft(section.title);
                    setMinutesDraft(sectionMinutes());
                    setEditing(true);
                  }}
                >
                  <Pencil aria-hidden="true" />
                </Button>
                <ConfirmDeleteButton
                  title={`Delete section ${section.title}`}
                  confirmText="Delete section"
                  pending={deleting}
                  onConfirm={() => onDelete(section.id)}
                />
              </>
            )}
          </>
        )}
      </div>

      {/* Section body */}
      {expanded && (
        <div className="px-4 pb-3">
          {section.kind === 'coding' ? (
            <Suspense
              fallback={
                <div className="flex flex-col gap-2" aria-busy="true">
                  <span className="sr-only">Loading editor</span>
                  <Skeleton className="h-16 rounded-[14px]" />
                </div>
              }
            >
              <CodingAuthoringSection examId={examId} sectionId={section.id} locked={locked} />
            </Suspense>
          ) : (
            <McqSection examId={examId} sectionId={section.id} locked={locked} />
          )}
        </div>
      )}
    </div>
  );
}

// ── Round assign panel ────────────────────────────────────────────────────────

const ASSIGN_PAGE = 200;

interface RoundAssignPanelProps {
  examId: string;
  roundId: string;
  isPublished: boolean;
  /** Legacy links with no round belong to the first round. */
  isFirstRound: boolean;
}

export function RoundAssignPanel({
  examId,
  roundId,
  isPublished,
  isFirstRound,
}: RoundAssignPanelProps) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [scheduledAt, setScheduledAt] = useState('');
  const [minted, setMinted] = useState<AssignResult[]>([]);
  const [search, setSearch] = useState('');

  // One entry per APPLICATION (B5), so the exam is recorded against the opening
  // it is for — a person who applied to two openings is two entries, each
  // labelled with its opening. Someone filed under no opening is one entry.
  // Paged (audit E13): one fixed page of 200 silently hid everyone after it.
  const pipelineQuery = useInfiniteQuery({
    queryKey: ['hr', 'pipeline', 'assignable', 'pages'],
    queryFn: ({ pageParam }) => getPipeline({ limit: ASSIGN_PAGE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = last.offset + last.items.length;
      return last.items.length > 0 && next < last.count ? next : undefined;
    },
    enabled: isPublished,
  });
  const loadedRows = useMemo(
    () => (pipelineQuery.data?.pages ?? []).flatMap((p) => p.items),
    [pipelineQuery.data],
  );
  const total = pipelineQuery.data?.pages[0]?.count ?? 0;
  const applicants: PipelineRow[] = loadedRows.filter(
    (r) => r.status !== 'hired' && r.status !== 'rejected',
  );
  const needle = search.trim().toLowerCase();
  const shown = needle
    ? applicants.filter(
        (r) =>
          r.full_name.toLowerCase().includes(needle) ||
          (r.opening_title ?? '').toLowerCase().includes(needle),
      )
    : applicants;
  const chosen = applicants.filter((r) => selected.has(applicationKey(r)));

  const assignmentsQuery = useQuery({
    queryKey: ['hr', 'exam', examId, 'assignments'],
    queryFn: () => listAssignments(examId),
  });

  const roundAssignments = (assignmentsQuery.data ?? []).filter(
    (a) => a.round_id === roundId || (a.round_id == null && isFirstRound),
  );
  // People who already hold a live link to THIS round. Assigning them again
  // revokes that link (the server keeps one live link per person and round),
  // so the email they already have stops working (audit E12).
  const liveLink = new Set(
    roundAssignments.filter((a) => a.status === 'invited').map((a) => a.applicant_id),
  );
  const rotating = chosen.filter((r) => liveLink.has(r.applicant_id));

  const assignMut = useMutation({
    mutationFn: () =>
      assignExam(
        examId,
        // People filed under no opening go by person; everyone else by the
        // application chosen.
        chosen.filter((r) => !r.enrolment_id).map((r) => r.applicant_id),
        undefined,
        roundId,
        scheduledAt ? new Date(scheduledAt).toISOString() : undefined,
        chosen.flatMap((r) => (r.enrolment_id ? [r.enrolment_id] : [])),
      ),
    onSuccess: (res) => {
      setMinted(res);
      setSelected(new Set());
      setScheduledAt('');
      toast.success(`${plural(res.length, 'link')} created and emailed`);
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not assign the exam.')),
  });

  const revokeMut = useMutation({
    mutationFn: (aid: string) => revokeAssignment(examId, aid),
    onSuccess: () => {
      toast.success('Link revoked');
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not revoke the exam link.')),
  });

  async function copyLink(link: string) {
    try {
      await navigator.clipboard.writeText(link);
      toast.success('Link copied');
    } catch {
      toast.error('Could not copy the link. Select it and copy it by hand.');
    }
  }

  function toggleApplicant(id: string, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  if (!isPublished) {
    return (
      <p className="mt-3 text-[12.5px] text-muted-foreground">
        Publish this round first, then assign it to applicants.
      </p>
    );
  }

  return (
    <div className="mt-3 space-y-3">
      {/* Applicant search */}
      <div className="relative">
        <Search
          size={15}
          aria-hidden="true"
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
        />
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search applicants"
          aria-label="Search applicants"
          className={cn(smallInputCls, 'w-full pl-9')}
        />
      </div>

      {/* Applicant list */}
      {pipelineQuery.isError && !pipelineQuery.data ? (
        <QueryErrorPanel
          title="Could not load the applicants."
          error={pipelineQuery.error}
          onRetry={() => void pipelineQuery.refetch()}
          retrying={pipelineQuery.isFetching}
        />
      ) : (
        <div
          className="max-h-48 space-y-0.5 overflow-y-auto rounded-[14px] border border-border bg-[var(--ui-inset-soft)] p-2"
          role="group"
          aria-label="Select applicants to assign"
        >
          {pipelineQuery.isLoading ? (
            <Skeleton className="h-8 rounded-[9px]" />
          ) : applicants.length === 0 ? (
            <p className="px-2 py-2 text-[12px] text-muted-foreground">
              No applicants yet. Add them under Applicants.
            </p>
          ) : shown.length === 0 ? (
            <p className="px-2 py-2 text-[12px] text-muted-foreground">
              No loaded applicant matches that search.
            </p>
          ) : (
            shown.map((a) => (
              <label
                key={applicationKey(a)}
                className="flex min-h-10 cursor-pointer items-center gap-2 rounded-[9px] px-2 py-1.5 text-[12.5px] text-foreground hover:bg-[var(--ui-inset)] transition-colors"
              >
                <input
                  type="checkbox"
                  checked={selected.has(applicationKey(a))}
                  onChange={(e) => toggleApplicant(applicationKey(a), e.target.checked)}
                  className="h-4 w-4 accent-[var(--accent)]"
                />
                <span className="min-w-0 flex-1 truncate">
                  {a.full_name}
                  {a.opening_title ? (
                    <span className="text-[var(--ui-faint)]"> · {a.opening_title}</span>
                  ) : null}
                </span>
                {liveLink.has(a.applicant_id) ? (
                  <StatusTag tone="neutral" className="text-[12px]">
                    Link sent
                  </StatusTag>
                ) : null}
              </label>
            ))
          )}
        </div>
      )}
      {pipelineQuery.data ? (
        <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
          <span role="status">
            {loadedRows.length < total
              ? `Showing ${loadedRows.length} of ${total} applications. Search covers the loaded ones.`
              : `${plural(total, 'application')} loaded.`}
          </span>
          {pipelineQuery.hasNextPage ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={pipelineQuery.isFetchingNextPage}
              aria-busy={pipelineQuery.isFetchingNextPage}
              onClick={() => void pipelineQuery.fetchNextPage()}
            >
              {pipelineQuery.isFetchingNextPage ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : null}
              Load more
            </Button>
          ) : null}
        </div>
      ) : null}

      {/* Schedule (optional) */}
      <label className="block text-sm">
        <span className="mb-1 block text-[12px] font-medium uppercase tracking-[0.5px] text-[var(--ui-faint)]">
          Schedule (optional, your local time)
        </span>
        <input
          type="datetime-local"
          className={cn(inputCls, 'text-[13px]')}
          value={scheduledAt}
          onChange={(e) => setScheduledAt(e.target.value)}
          aria-label="Scheduled time for this round assignment"
        />
      </label>

      {rotating.length > 0 ? (
        <p
          role="status"
          className="flex items-start gap-1.5 rounded-[10px] border border-[var(--ui-warn)]/30 bg-[var(--ui-warn-wash)] px-3 py-2 text-[12.5px] text-[var(--ui-soft)]"
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />
          {rotating.length === 1
            ? `${rotating[0].full_name} already has a live link to this round. A new link replaces it, and the link in the email they already have stops working.`
            : `${rotating.length} of the people chosen already have a live link to this round. New links replace them, and the links in the emails they already have stop working.`}
        </p>
      ) : null}

      <Button
        type="button"
        disabled={assignMut.isPending || selected.size === 0}
        aria-busy={assignMut.isPending}
        onClick={() => assignMut.mutate()}
      >
        {assignMut.isPending ? (
          <Loader2 className="animate-spin" aria-hidden="true" />
        ) : (
          <Send aria-hidden="true" />
        )}
        Send links ({selected.size})
      </Button>

      {/* Once-shown magic links */}
      {minted.length > 0 && (
        <div
          role="region"
          aria-label="Generated magic links"
          className="space-y-1.5 rounded-[14px] border border-[var(--ui-ok)] bg-[var(--ui-ok-wash)] p-3"
        >
          <p className="text-[12px] font-semibold text-[var(--ui-ok)]">
            Emailed to each candidate. The links are shown here once, in case you also want to
            share one yourself.
          </p>
          {minted.map((m) => (
            <div key={m.assignment_id} className="flex items-center gap-2 text-[12px]">
              <span className="w-28 shrink-0 truncate font-medium text-foreground">
                {m.applicant_name}
              </span>
              <input
                readOnly
                value={m.magic_link}
                className="min-w-0 flex-1 rounded-[9px] border border-border bg-secondary px-2 py-1 text-muted-foreground focus:outline-none"
                aria-label={`Magic link for ${m.applicant_name}`}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`Copy link for ${m.applicant_name}`}
                className="shrink-0 text-[var(--ui-ok)]"
                onClick={() => void copyLink(m.magic_link)}
              >
                <Copy aria-hidden="true" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {/* Existing assignments for this round */}
      {assignmentsQuery.isError && !assignmentsQuery.data ? (
        <QueryErrorPanel
          title="Could not load the links already sent for this round."
          error={assignmentsQuery.error}
          onRetry={() => void assignmentsQuery.refetch()}
          retrying={assignmentsQuery.isFetching}
        />
      ) : null}
      {roundAssignments.length > 0 && (
        <div className="space-y-1">
          <p className="text-[12px] font-semibold uppercase tracking-[0.5px] text-[var(--ui-faint)]">
            Assigned
          </p>
          {roundAssignments.map((a) => (
            <div
              key={a.assignment_id}
              className="flex flex-wrap items-center gap-2 rounded-[11px] border border-border bg-[var(--ui-inset-soft)] px-3 py-1.5 text-[12.5px] text-foreground"
            >
              <span className="min-w-0 flex-1 truncate">{a.applicant_name}</span>
              {a.scheduled_at && (
                <span className="flex items-center gap-1 text-[12px] text-[var(--ui-faint)]">
                  <Clock size={10} aria-hidden="true" />
                  {formatDateTime(a.scheduled_at)}
                </span>
              )}
              <StatusTag tone="neutral" className="text-[12px]">
                {statusLabel(a.status, 'invite')}
              </StatusTag>
              {a.status === 'invited' && (
                <ConfirmDeleteButton
                  title={`Revoke link for ${a.applicant_name}`}
                  confirmText="Revoke link"
                  icon={Ban}
                  pending={revokeMut.isPending && revokeMut.variables === a.assignment_id}
                  onConfirm={() => revokeMut.mutate(a.assignment_id)}
                />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Round panel ───────────────────────────────────────────────────────────────

interface RoundPanelProps {
  examId: string;
  round: Round;
  totalRounds: number;
  isFirst: boolean;
  isLast: boolean;
  onDelete: (roundId: string) => void;
  deleting: boolean;
  onMove: (roundId: string, direction: -1 | 1) => void;
  reordering: boolean;
}

export function RoundPanel({
  examId,
  round,
  totalRounds,
  isFirst,
  isLast,
  onDelete,
  deleting,
  onMove,
  reordering,
}: RoundPanelProps) {
  const qc = useQueryClient();
  const editorKey = examEditorMutationKey(examId);
  const [expanded, setExpanded] = useState(true);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(round.title);
  const [thresholdDraft, setThresholdDraft] = useState(String(round.pass_threshold));
  const roundMinutes = round.time_limit_seconds
    ? String(Math.round(round.time_limit_seconds / 60))
    : '';
  const [minutesDraft, setMinutesDraft] = useState(roundMinutes);
  const [addingSection, setAddingSection] = useState(false);
  const [sectionTitle, setSectionTitle] = useState('');
  const [sectionKind, setSectionKind] = useState<'mcq' | 'coding'>('mcq');
  const [sectionTimeMin, setSectionTimeMin] = useState('');
  const [showAssign, setShowAssign] = useState(false);

  // The drafts follow the saved values whenever those change (a save, a
  // refetch, a duplicate), so the screen never shows a value that is not in
  // effect.
  useEffect(() => setThresholdDraft(String(round.pass_threshold)), [round.pass_threshold]);
  useEffect(() => setMinutesDraft(roundMinutes), [roundMinutes]);

  const invalidate = () => void invalidateAfterHrMutation(qc);

  const renameMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (title: string) => updateRound(examId, round.id, { title }),
    onSuccess: () => {
      setEditingTitle(false);
      toast.success('Round renamed');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not rename the round.')),
  });

  const publishMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (status: 'draft' | 'published') => updateRound(examId, round.id, { status }),
    onSuccess: (r) => {
      toast.success(r.status === 'published' ? 'Round published' : 'Round unpublished');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not change the round status.')),
  });

  const thresholdMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (pass_threshold: number) => updateRound(examId, round.id, { pass_threshold }),
    onSuccess: () => {
      toast.success('Pass mark saved');
      invalidate();
    },
    onError: (e: unknown) => {
      // Put the saved value back: the input kept the refused one, so the screen
      // showed a pass mark that was not in effect (audit E4).
      setThresholdDraft(String(round.pass_threshold));
      toast.error(errorMessage(e, 'Could not save the pass mark.'));
    },
  });

  const timeLimitMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (time_limit_seconds: number | null) =>
      updateRound(examId, round.id, { time_limit_seconds }),
    onSuccess: () => {
      toast.success('Time limit saved');
      invalidate();
    },
    onError: (e: unknown) => {
      setMinutesDraft(roundMinutes);
      toast.error(errorMessage(e, 'Could not save the time limit.'));
    },
  });

  const advancesMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (advances_to_interview: boolean) =>
      updateRound(examId, round.id, { advances_to_interview }),
    onSuccess: () => {
      toast.success('Saved');
      invalidate();
    },
    onError: (e: unknown) =>
      toast.error(errorMessage(e, 'Could not update the interview step setting.')),
  });

  // Camera proctoring is per round and OFF by default, which is the right
  // default — it is the setting that turns a written test into something that
  // watches the candidate, so it should be a deliberate act. It had no control
  // anywhere in the app, so it could not be turned on at all.
  const cameraMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (camera_proctoring_required: boolean) =>
      updateRound(examId, round.id, { camera_proctoring_required }),
    onSuccess: () => {
      toast.success('Saved');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not update camera proctoring.')),
  });

  const addSectionMut = useMutation({
    mutationKey: editorKey,
    mutationFn: () =>
      createSection(examId, round.id, {
        title: sectionTitle.trim() || (sectionKind === 'coding' ? 'Coding section' : 'MCQ section'),
        kind: sectionKind,
        time_limit_seconds: minutesToSeconds(sectionTimeMin),
      }),
    onSuccess: () => {
      setSectionTitle('');
      setSectionKind('mcq');
      setSectionTimeMin('');
      setAddingSection(false);
      toast.success('Section added');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not add the section.')),
  });

  const deleteSectionMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (sectionId: string) => deleteSection(examId, round.id, sectionId),
    onSuccess: () => {
      toast.success('Section deleted');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not delete the section.')),
  });

  // PH4-D1 — the way forward once this round is locked: duplicate it into a
  // fresh, editable draft round.
  const duplicateMut = useMutation({
    mutationKey: editorKey,
    mutationFn: () => duplicateRound(examId, round.id),
    onSuccess: (created) => {
      toast.success(`Duplicated as "${created.title}"`);
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not duplicate this round.')),
  });

  const isPublished = round.status === 'published';
  const attempts = round.attempt_count ?? 0;
  // Published, or taken. A taken round is never offered Unpublish: the server
  // refuses it (audit E2), so Duplicate is the only way forward.
  const lockReason = roundLockReason(round.status, attempts);
  const unpublishable = canUnpublishRound(round.status, attempts);
  const locked = Boolean(lockReason);

  return (
    <GlassCard className="p-0 overflow-hidden">
      {/* Round header */}
      <div className="flex flex-wrap items-center gap-2 px-5 py-3 border-b border-border">
        {/* Reorder */}
        <div className="flex shrink-0 items-center">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`Move round ${round.title} up`}
            disabled={isFirst || reordering}
            className="text-muted-foreground"
            onClick={() => onMove(round.id, -1)}
          >
            <ChevronDown className="rotate-180" aria-hidden="true" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`Move round ${round.title} down`}
            disabled={isLast || reordering}
            className="text-muted-foreground"
            onClick={() => onMove(round.id, 1)}
          >
            <ChevronDown aria-hidden="true" />
          </Button>
        </div>

        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-label={`${expanded ? 'Collapse' : 'Expand'} round ${round.title}`}
          className="text-muted-foreground"
        >
          {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
        </Button>

        <span
          title={`Round ${round.round_number}`}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[var(--ui-line-strong)] bg-[var(--ui-inset)] font-mono text-[12px] font-semibold text-foreground"
        >
          {round.round_number}
        </span>

        {editingTitle ? (
          <form
            className="flex flex-1 items-center gap-2"
            aria-label={`Rename round ${round.title}`}
            onSubmit={(e) => {
              e.preventDefault();
              if (titleDraft.trim()) renameMut.mutate(titleDraft.trim());
            }}
          >
            <input
              autoFocus
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              maxLength={200}
              className={cn(smallInputCls, 'flex-1 text-[14px]')}
              aria-label="Round title"
            />
            <Button type="submit" disabled={renameMut.isPending} aria-busy={renameMut.isPending}>
              {renameMut.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
              Save
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Cancel"
              onClick={() => {
                setEditingTitle(false);
                setTitleDraft(round.title);
              }}
            >
              <X aria-hidden="true" />
            </Button>
          </form>
        ) : (
          <>
            <span className="min-w-0 flex-1 break-words text-[15px] font-semibold text-foreground">
              {round.title}
            </span>
            <StatusTag tone={roundStatusTone(round.status)}>
              {statusLabel(round.status, 'round')}
            </StatusTag>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Rename round ${round.title}`}
              className="text-muted-foreground"
              onClick={() => {
                setTitleDraft(round.title);
                setEditingTitle(true);
              }}
            >
              <Pencil aria-hidden="true" />
            </Button>
            {!isPublished ? (
              // Publish is the round's one primary action (audit A4).
              <Button
                type="button"
                disabled={publishMut.isPending}
                aria-busy={publishMut.isPending}
                onClick={() => publishMut.mutate('published')}
              >
                {publishMut.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : null}
                Publish
              </Button>
            ) : unpublishable ? (
              <Button
                type="button"
                variant="outline"
                disabled={publishMut.isPending}
                aria-busy={publishMut.isPending}
                onClick={() => publishMut.mutate('draft')}
              >
                {publishMut.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : null}
                Unpublish
              </Button>
            ) : null}
            {/* A locked round cannot be deleted (the server refuses it), and
                an exam keeps at least one round, so the control only appears
                when the delete can succeed (audit E5). */}
            {!locked && totalRounds > 1 ? (
              <ConfirmDeleteButton
                title={`Delete round ${round.title}`}
                confirmText="Delete round"
                pending={deleting}
                onConfirm={() => onDelete(round.id)}
              />
            ) : null}
          </>
        )}
      </div>

      {expanded && (
        <div className="px-5 py-4 space-y-4">
          {/* PH4-D1 — this round's content is fixed; here is why, and the two
              ways forward. */}
          {lockReason ? (
            <LockBanner
              reason={lockReason}
              canUnpublish={unpublishable}
              onUnpublish={() => publishMut.mutate('draft')}
              unpublishing={publishMut.isPending}
              onDuplicate={() => duplicateMut.mutate()}
              duplicating={duplicateMut.isPending}
            />
          ) : null}

          {/* Settings row. The grading settings are frozen with the round
              (audit E4): disabled while locked, so nothing can be typed that
              the server would refuse. */}
          <div className="flex flex-wrap items-end gap-4">
            <label className="flex flex-col gap-1 text-[12px] text-muted-foreground">
              <span className="uppercase tracking-[0.5px]">Pass threshold %</span>
              <input
                type="number"
                min={0}
                max={100}
                value={thresholdDraft}
                disabled={locked || thresholdMut.isPending}
                onChange={(e) => setThresholdDraft(e.target.value)}
                onBlur={() => {
                  const v = parseThreshold(thresholdDraft, round.pass_threshold);
                  setThresholdDraft(String(v));
                  if (v !== round.pass_threshold) thresholdMut.mutate(v);
                }}
                className={cn(smallInputCls, 'w-24')}
                aria-label="Pass threshold"
              />
            </label>
            <label className="flex flex-col gap-1 text-[12px] text-muted-foreground">
              <span className="uppercase tracking-[0.5px]">Time limit (min)</span>
              <input
                type="number"
                min={1}
                placeholder="No limit"
                value={minutesDraft}
                disabled={locked || timeLimitMut.isPending}
                onChange={(e) => setMinutesDraft(e.target.value)}
                onBlur={() => {
                  const next = minutesToSeconds(minutesDraft);
                  if (next !== round.time_limit_seconds) timeLimitMut.mutate(next);
                }}
                className={cn(smallInputCls, 'w-28')}
                aria-label="Round time limit in minutes (blank for no limit)"
              />
            </label>
            <label className="flex min-h-10 items-center gap-2 text-[12.5px] text-[var(--ui-soft)]">
              <input
                type="checkbox"
                className="h-4 w-4 accent-[var(--accent)] disabled:cursor-not-allowed"
                checked={round.advances_to_interview}
                disabled={locked || advancesMut.isPending}
                onChange={(e) => advancesMut.mutate(e.target.checked)}
              />
              Advances to interview on pass
            </label>
          </div>

          {/* ── Camera proctoring. Its own row, not tucked in with the pass
              threshold: this is the setting that decides whether a written
              test watches the candidate, and it deserves to be read before it
              is ticked rather than found by accident. Applies to every section
              in the round — MCQ and coding alike, since proctoring attaches to
              the attempt, not the section kind. ── */}
          <div className="mt-2 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] p-3">
            <label className="flex items-start gap-2 text-[12.5px] text-[var(--ui-soft)]">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
                checked={round.camera_proctoring_required}
                disabled={cameraMut.isPending}
                onChange={(e) => cameraMut.mutate(e.target.checked)}
              />
              <span>
                <span className="font-medium text-foreground">Require camera proctoring</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-[var(--ui-faint)]">
                  Candidates are asked for camera access before the round starts and cannot begin
                  without it. Detection runs entirely in their browser, and no video or
                  image is recorded or sent anywhere, only timestamped events. Leave off and the
                  round still records fullscreen, tab-switch and clipboard activity.
                </span>
              </span>
            </label>
          </div>

          {/* Sections */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-[12px] font-semibold uppercase tracking-[0.5px] text-[var(--ui-faint)]">
                Sections ({round.sections.length})
              </p>
              {!locked && (
                <Button
                  type="button"
                  variant="link"
                  size="sm"
                  onClick={() => setAddingSection((v) => !v)}
                  aria-expanded={addingSection}
                >
                  <Plus aria-hidden="true" /> Add section
                </Button>
              )}
            </div>

            {/* Add-section form */}
            {addingSection && !locked && (
              <form
                className="mb-3 flex flex-wrap items-end gap-2 rounded-[14px] border border-dashed border-border bg-[var(--ui-inset-soft)] p-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  addSectionMut.mutate();
                }}
                aria-label="New section"
              >
                <div className="flex gap-1.5">
                  {(['mcq', 'coding'] as const).map((k) => (
                    <button
                      key={k}
                      type="button"
                      onClick={() => setSectionKind(k)}
                      className={cn(
                        'min-h-10 rounded-[9px] border px-3 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                        sectionKind === k
                          ? 'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.14)] text-[var(--ui-info)]'
                          : 'border-border text-muted-foreground hover:text-foreground',
                      )}
                      aria-pressed={sectionKind === k}
                    >
                      {k === 'mcq' ? 'MCQ' : 'Coding'}
                    </button>
                  ))}
                </div>
                <input
                  className={cn(smallInputCls, 'min-w-[160px] flex-1')}
                  placeholder="Section title (optional)"
                  value={sectionTitle}
                  maxLength={200}
                  onChange={(e) => setSectionTitle(e.target.value)}
                  aria-label="Section title"
                />
                <input
                  type="number"
                  min={1}
                  placeholder="Min limit"
                  value={sectionTimeMin}
                  onChange={(e) => setSectionTimeMin(e.target.value)}
                  className={cn(smallInputCls, 'w-24')}
                  aria-label="Section time limit in minutes"
                />
                <Button
                  type="submit"
                  disabled={addSectionMut.isPending}
                  aria-busy={addSectionMut.isPending}
                >
                  {addSectionMut.isPending ? (
                    <Loader2 className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Plus aria-hidden="true" />
                  )}
                  Add section
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="Cancel"
                  onClick={() => setAddingSection(false)}
                >
                  <X aria-hidden="true" />
                </Button>
              </form>
            )}

            {/* Section list */}
            {round.sections.length === 0 ? (
              <p className="text-[12.5px] text-muted-foreground">
                No sections yet. Add one above.
              </p>
            ) : (
              <div className="space-y-2">
                {round.sections.map((section) => (
                  <SectionPanel
                    key={section.id}
                    examId={examId}
                    section={section}
                    locked={locked}
                    onDelete={(sid) => deleteSectionMut.mutate(sid)}
                    deleting={deleteSectionMut.isPending && deleteSectionMut.variables === section.id}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Assign & share */}
          <div>
            <Button
              type="button"
              variant="link"
              size="sm"
              onClick={() => setShowAssign((v) => !v)}
              aria-expanded={showAssign}
            >
              <Send aria-hidden="true" />
              {showAssign ? 'Hide assign and share' : 'Assign and share'}
            </Button>
            {showAssign && (
              <RoundAssignPanel
                examId={examId}
                roundId={round.id}
                isPublished={isPublished}
                isFirstRound={isFirst}
              />
            )}
          </div>
        </div>
      )}
    </GlassCard>
  );
}

// ── Exam settings ─────────────────────────────────────────────────────────────

/** Exam-level settings, and closing the exam (audit E11: after creation
 *  nothing in the UI could change these, or close an exam at all). */
export function ExamSettingsPanel({ exam, hasLiveRound }: { exam: ExamDetail; hasLiveRound: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [titleDraft, setTitleDraft] = useState(exam.title);
  useEffect(() => setTitleDraft(exam.title), [exam.title]);

  const saveMut = useMutation({
    mutationKey: examEditorMutationKey(exam.id),
    mutationFn: ({ patch }: { patch: ExamUpdateInput; done: string }) => updateExam(exam.id, patch),
    onSuccess: (_e, { done }) => {
      toast.success(done);
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not save the exam settings.')),
  });

  const closed = exam.status === 'closed';

  return (
    <GlassCard className="p-0">
      <Button
        type="button"
        variant="ghost"
        className="w-full justify-start rounded-card px-5"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Settings aria-hidden="true" />
        Exam settings
        {open ? (
          <ChevronDown className="ml-auto" aria-hidden="true" />
        ) : (
          <ChevronRight className="ml-auto" aria-hidden="true" />
        )}
      </Button>
      {open ? (
        <div className="space-y-4 border-t border-border px-5 py-4">
          <form
            className="flex flex-wrap items-end gap-2"
            aria-label="Exam title"
            onSubmit={(e) => {
              e.preventDefault();
              const title = titleDraft.trim();
              if (!title) return toast.error('Give the exam a title.');
              saveMut.mutate({ patch: { title }, done: 'Exam renamed' });
            }}
          >
            <label className="flex min-w-[220px] flex-1 flex-col gap-1 text-[12px] text-muted-foreground">
              <span className="uppercase tracking-[0.5px]">Title</span>
              <input
                value={titleDraft}
                maxLength={300}
                onChange={(e) => setTitleDraft(e.target.value)}
                className={cn(smallInputCls, 'w-full text-[14px]')}
              />
            </label>
            <Button
              type="submit"
              variant="outline"
              disabled={saveMut.isPending || titleDraft.trim() === exam.title}
            >
              Save title
            </Button>
          </form>

          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-3">
              <ToggleSwitch
                checked={exam.allow_retake}
                disabled={saveMut.isPending}
                onChange={(v) =>
                  saveMut.mutate({
                    patch: { allow_retake: v },
                    done: v ? 'Retakes allowed' : 'Retakes turned off',
                  })
                }
                label="Allow retake"
              />
              <span className="text-[13px] text-[var(--ui-soft)]">Allow retake</span>
            </div>
            <div className="flex items-center gap-3">
              <ToggleSwitch
                checked={exam.auto_advance_on_pass}
                disabled={saveMut.isPending}
                onChange={(v) =>
                  saveMut.mutate({
                    patch: { auto_advance_on_pass: v },
                    done: v ? 'Auto-advance turned on' : 'Auto-advance turned off',
                  })
                }
                label="Auto-advance on pass"
              />
              <span className="text-[13px] text-[var(--ui-soft)]">
                Auto-advance on pass (invite to the interview when the last round is passed)
              </span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
            <p className="min-w-[220px] flex-1 text-[12.5px] text-muted-foreground">
              {closed
                ? 'This exam is closed. Nobody can start or continue it, including people who already have a link.'
                : 'Closing the exam stops anyone starting or continuing it, including people who already have a link. You can reopen it.'}
            </p>
            {closed ? (
              <Button
                type="button"
                variant="outline"
                disabled={saveMut.isPending}
                onClick={() =>
                  saveMut.mutate({
                    patch: { status: hasLiveRound ? 'published' : 'draft' },
                    done: 'Exam reopened',
                  })
                }
              >
                Reopen exam
              </Button>
            ) : (
              <ConfirmDeleteButton
                label="Close exam"
                icon={Lock}
                pending={saveMut.isPending}
                onConfirm={() => saveMut.mutate({ patch: { status: 'closed' }, done: 'Exam closed' })}
              />
            )}
          </div>
        </div>
      ) : null}
    </GlassCard>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ExamEditor() {
  const { examId = '' } = useParams<{ examId: string }>();
  const qc = useQueryClient();
  const editorKey = examEditorMutationKey(examId);

  const examQuery = useQuery({
    queryKey: ['hr', 'exam', examId],
    queryFn: () => getExam(examId),
  });
  const exam = examQuery.data;

  const structureQuery = useQuery({
    queryKey: ['hr', 'exam', examId, 'structure'],
    queryFn: () => getStructure(examId),
  });
  const structure = structureQuery.data;

  // Every edit on this screen saves on its own; the header reports whether
  // any of THIS exam's edits is in flight (audit E22).
  const saving = useIsMutating({ mutationKey: editorKey });

  // Add round state
  const [addingRound, setAddingRound] = useState(false);
  const [roundTitle, setRoundTitle] = useState('');
  const [roundThreshold, setRoundThreshold] = useState('60');
  const [roundTimeMin, setRoundTimeMin] = useState('');
  const [roundAdvances, setRoundAdvances] = useState(false);

  const invalidateAll = () => void invalidateAfterHrMutation(qc);

  const addRoundMut = useMutation({
    mutationKey: editorKey,
    mutationFn: () =>
      createRound(examId, {
        title: roundTitle.trim() || `Round ${(structure?.rounds.length ?? 0) + 1}`,
        pass_threshold: parseThreshold(roundThreshold, 60),
        time_limit_seconds: minutesToSeconds(roundTimeMin),
        advances_to_interview: roundAdvances,
      }),
    onSuccess: () => {
      setRoundTitle('');
      setRoundThreshold('60');
      setRoundTimeMin('');
      setRoundAdvances(false);
      setAddingRound(false);
      toast.success('Round added');
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not add the round.')),
  });

  const deleteRoundMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (roundId: string) => deleteRound(examId, roundId),
    onSuccess: () => {
      toast.success('Round deleted');
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not delete the round.')),
  });

  // A mutation, not a bare promise (audit E6): the failure is reported, and
  // the arrows are disabled while a move is in flight, so a double click
  // cannot send two racing reorders.
  const reorderMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (ids: string[]) => reorderRounds(examId, ids),
    onSuccess: invalidateAll,
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not reorder the rounds.')),
  });

  const rounds = useMemo(
    () => [...(structure?.rounds ?? [])].sort((a, b) => a.position - b.position),
    [structure],
  );

  function handleMove(roundId: string, direction: -1 | 1) {
    // By index in the sorted list, not by stored position: positions are not
    // compacted after a delete, so "position <= 1" did not mean "first".
    const idx = rounds.findIndex((r) => r.id === roundId);
    const target = idx + direction;
    if (idx < 0 || target < 0 || target >= rounds.length) return;
    const ids = rounds.map((r) => r.id);
    [ids[idx], ids[target]] = [ids[target], ids[idx]];
    reorderMut.mutate(ids);
  }

  // ── Failed / loading ─────────────────────────────────────────────────────────
  // A failed load used to leave the editor on skeletons for ever (audit E14).
  const failed = (examQuery.isError && !exam) || (structureQuery.isError && !structure);
  if (failed) {
    const err = examQuery.isError ? examQuery.error : structureQuery.error;
    return (
      <Page width="default">
        <Link
          to="/hr/exams"
          className="inline-flex min-h-10 self-start items-center text-[13px] font-semibold text-[var(--ui-info)] hover:underline"
        >
          Back to exams
        </Link>
        <QueryErrorPanel
          title="Could not load this exam."
          error={err}
          onRetry={() => {
            void examQuery.refetch();
            void structureQuery.refetch();
          }}
          retrying={examQuery.isFetching || structureQuery.isFetching}
        />
      </Page>
    );
  }

  if (!exam || !structure) {
    return (
      <Page width="default">
        <div aria-busy="true" className="flex flex-col gap-4">
          <span className="sr-only">Loading exam</span>
          <Skeleton className="h-5 w-28" />
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-40 w-full rounded-card" />
          <Skeleton className="h-48 w-full rounded-card" />
        </div>
      </Page>
    );
  }

  const hasLiveRound = rounds.some((r) => r.status === 'published');

  return (
    <Page width="default">
      {/* Sticky document header: title, status and the screen's one action
          stay in view while a long exam is being authored. */}
      <EditorHeader
        breadcrumbs={[{ label: 'Exams', to: '/hr/exams' }, { label: exam.title }]}
        title={exam.title}
        status={
          <DocStatusPill
            status={
              exam.status === 'closed' ? 'locked' : exam.status === 'published' ? 'live' : 'draft'
            }
          >
            {statusLabel(exam.status, 'exam')}
          </DocStatusPill>
        }
        meta={
          <>
            <span>{plural(rounds.length, 'round')}</span>
            {exam.auto_advance_on_pass && <span>Auto-advance on pass</span>}
          </>
        }
        saveState={saving > 0 ? 'saving' : 'saved'}
        secondary={
          <Button asChild variant="outline">
            <Link to={`/hr/exams/${examId}/results`}>
              <BarChart3 aria-hidden="true" /> Results
            </Link>
          </Button>
        }
        primary={
          <Button
            type="button"
            onClick={() => {
              setAddingRound(true);
              window.setTimeout(
                () =>
                  document.getElementById('new-round-form')?.scrollIntoView?.({ block: 'nearest' }),
                0,
              );
            }}
            disabled={addingRound}
          >
            <Plus aria-hidden="true" /> Add round
          </Button>
        }
      />

      <div className="grid items-start gap-6 lg:grid-cols-[200px_minmax(0,1fr)]">
        {/* Outline: the exam's rounds as in-page anchors, so a long exam can
            be jumped through. */}
        <nav aria-label="Exam outline" className="hidden lg:sticky lg:top-[200px] lg:block">
          <p className="mb-2 text-[12.5px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
            Outline
          </p>
          <ol className="flex flex-col gap-0.5">
            {rounds.map((r) => (
              <li key={r.id}>
                <a
                  href={`#round-${r.id}`}
                  className="flex min-h-[36px] items-start gap-2 rounded-[8px] px-2 py-1.5 text-[13px] text-muted-foreground hover:bg-[var(--ui-inset-soft)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                >
                  <span className="font-mono tabular-nums">{r.round_number}.</span>
                  <span className="min-w-0 flex-1 break-words">
                    {r.title}
                    <span className="block text-[12px]">
                      {r.status === 'published' ? 'Published · locked' : 'Draft'}
                    </span>
                  </span>
                </a>
              </li>
            ))}
          </ol>
        </nav>

        <div className="min-w-0 space-y-4">
          <ExamSettingsPanel exam={exam} hasLiveRound={hasLiveRound} />

          {/* Rounds */}
          <div className="space-y-4">
            {rounds.length === 0 ? (
              <Reveal delay={0.04}>
                <EmptyState
                  icon={Sparkles}
                  title="No rounds yet"
                  description="Add the first round with the Add round button."
                />
              </Reveal>
            ) : (
              <Stagger>
                {rounds.map((round, idx) => (
                  <StaggerItem key={round.id}>
                    <div id={`round-${round.id}`} className="scroll-mt-52">
                      <RoundPanel
                        examId={examId}
                        round={round}
                        totalRounds={rounds.length}
                        isFirst={idx === 0}
                        isLast={idx === rounds.length - 1}
                        onDelete={(rid) => deleteRoundMut.mutate(rid)}
                        deleting={deleteRoundMut.isPending && deleteRoundMut.variables === round.id}
                        onMove={handleMove}
                        reordering={reorderMut.isPending}
                      />
                    </div>
                  </StaggerItem>
                ))}
              </Stagger>
            )}

            {/* Add round */}
            {addingRound ? (
              <Reveal delay={0.02}>
                <div id="new-round-form" className="scroll-mt-52">
                  <GlassCard className="p-5" role="group" aria-label="New round">
                    <h3 className="mb-4 text-[15px] font-semibold">New round</h3>
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        addRoundMut.mutate();
                      }}
                      className="space-y-3"
                      aria-label="New round form"
                    >
                      <input
                        className={inputCls}
                        placeholder="Round title (e.g. Technical screening)"
                        value={roundTitle}
                        maxLength={200}
                        onChange={(e) => setRoundTitle(e.target.value)}
                        aria-label="Round title"
                      />
                      <div className="grid gap-3 sm:grid-cols-3">
                        <label className="flex flex-col gap-1 text-[12px] text-muted-foreground">
                          <span className="uppercase tracking-[0.5px]">Pass threshold %</span>
                          <input
                            type="number"
                            min={0}
                            max={100}
                            value={roundThreshold}
                            onChange={(e) => setRoundThreshold(e.target.value)}
                            className={cn(inputCls, 'text-[13px]')}
                            aria-label="Pass threshold"
                          />
                        </label>
                        <label className="flex flex-col gap-1 text-[12px] text-muted-foreground">
                          <span className="uppercase tracking-[0.5px]">Time limit (min)</span>
                          <input
                            type="number"
                            min={1}
                            placeholder="No limit"
                            value={roundTimeMin}
                            onChange={(e) => setRoundTimeMin(e.target.value)}
                            className={cn(inputCls, 'text-[13px]')}
                            aria-label="Time limit in minutes"
                          />
                        </label>
                        <div className="flex items-end pb-1">
                          <label className="flex min-h-10 items-center gap-2 text-[12.5px] text-[var(--ui-soft)]">
                            <input
                              type="checkbox"
                              checked={roundAdvances}
                              onChange={(e) => setRoundAdvances(e.target.checked)}
                              className="h-4 w-4 accent-[var(--accent)]"
                            />
                            Advances to interview
                          </label>
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <Button
                          type="submit"
                          disabled={addRoundMut.isPending}
                          aria-busy={addRoundMut.isPending}
                        >
                          {addRoundMut.isPending ? (
                            <Loader2 className="animate-spin" aria-hidden="true" />
                          ) : (
                            <Plus aria-hidden="true" />
                          )}
                          {addRoundMut.isPending ? 'Adding…' : 'Add round'}
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          onClick={() => setAddingRound(false)}
                          disabled={addRoundMut.isPending}
                        >
                          Cancel
                        </Button>
                      </div>
                    </form>
                  </GlassCard>
                </div>
              </Reveal>
            ) : null}
          </div>
        </div>
      </div>
    </Page>
  );
}

