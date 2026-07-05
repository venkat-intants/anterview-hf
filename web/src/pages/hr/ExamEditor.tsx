// ExamEditor — Rounds → Sections → Questions tree (HR Phase 2+).
// Each exam has ordered Rounds; each Round has Sections (mcq|coding).
// HR can add/rename/reorder/delete rounds; set pass_threshold, time_limit,
// advances_to_interview; publish/unpublish per round; add/rename/delete sections;
// author questions per section (MCQ inline, coding via CodingAuthoring).
// Per-round: an Assign/schedule panel that posts to the assignment endpoint
// with round_id + optional scheduled_at.
// Legacy single-round/single-section exams render gracefully from GET /structure.

import { Suspense, lazy, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  Plus,
  Trash2,
  Check,
  Copy,
  Send,
  Ban,
  Lock,
  BarChart3,
  Loader2,
  ListChecks,
  ClipboardList,
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
  GripVertical,
  Code2,
} from '@/design/components/icons';
import {
  getExam,
  getStructure,
  // MCQ question helpers
  generateQuestions,
  importQuestions,
  downloadQuestionTemplate,
  // Round helpers
  createRound,
  updateRound,
  deleteRound,
  reorderRounds,
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
  type Round,
  type Section,
  type AssignResult,
  type GeneratedQuestion,
  type ImportResult,
  type ExamDifficulty,
  type ExamLanguage,
  type ExamQuestion,
  type QuestionInput,
} from '@/api/exams';
import { listApplicants } from '@/api/applicants';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import {
  GlassCard,
  Pill,
  StatusTag,
} from '@/design/components/primitives';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';

// Lazy-load CodingAuthoring to avoid importing the code-editor eagerly
const CodingAuthoringSection = lazy(() => import('./CodingAuthoringSection'));

// ── Shared styles ─────────────────────────────────────────────────────────────

const inputCls =
  'w-full rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3 py-2 ' +
  'text-[14px] text-white placeholder:text-[#5a5f66] focus:outline-none ' +
  'focus:border-[var(--accent)] transition-colors';

// ── Status helpers ────────────────────────────────────────────────────────────

type TagToneKey = 'neutral' | 'forest' | 'amber';

function roundStatusTone(s: string): { label: string; tone: TagToneKey } {
  return s === 'published'
    ? { label: 'Published', tone: 'forest' }
    : { label: 'Draft', tone: 'amber' };
}

// ── MCQ section question list ─────────────────────────────────────────────────

interface McqSectionProps {
  examId: string;
  sectionId: string;
  locked: boolean;
}

function McqSection({ examId, sectionId, locked }: McqSectionProps) {
  const qc = useQueryClient();
  const qKey = ['hr', 'exam', examId, 'section', sectionId, 'questions'];

  const { data: questions = [] } = useQuery({
    queryKey: qKey,
    queryFn: () => listSectionQuestions(examId, sectionId),
  });

  // Composer: manual | ai | excel
  const [composerTab, setComposerTab] = useState<'manual' | 'ai' | 'excel'>('manual');
  const [prompt, setPrompt] = useState('');
  const [options, setOptions] = useState<string[]>(['', '', '', '']);
  const [correctIdx, setCorrectIdx] = useState(0);
  const [points, setPoints] = useState('1');

  // AI
  const [aiTopic, setAiTopic] = useState('');
  const [aiCount, setAiCount] = useState('5');
  const [aiDifficulty, setAiDifficulty] = useState<ExamDifficulty>('medium');
  const [aiLanguage, setAiLanguage] = useState<ExamLanguage>('en');
  const [aiPreview, setAiPreview] = useState<GeneratedQuestion[]>([]);

  // Excel
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = () => void qc.invalidateQueries({ queryKey: qKey });

  const addMut = useMutation({
    mutationFn: (q: QuestionInput) => addSectionQuestion(examId, sectionId, q),
    onSuccess: () => {
      setPrompt('');
      setOptions(['', '', '', '']);
      setCorrectIdx(0);
      setPoints('1');
      refresh();
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Add failed'),
  });

  const delMut = useMutation({
    mutationFn: (qid: string) => deleteSectionQuestion(examId, sectionId, qid),
    onSuccess: refresh,
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Delete failed'),
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
      if (res.questions.length === 0) toast.error('No questions generated — try again.');
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Generation failed'),
  });

  const addGeneratedMut = useMutation({
    mutationFn: async (qs: GeneratedQuestion[]) => {
      const results: ExamQuestion[] = [];
      for (const q of qs) {
        const created = await addSectionQuestion(examId, sectionId, {
          prompt: q.prompt,
          options: q.options,
          correct_index: q.correct_index,
          points: q.points,
        });
        results.push(created);
      }
      return results;
    },
    onSuccess: (created) => {
      setAiPreview([]);
      toast.success(`${created.length} question(s) added`);
      refresh();
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Add failed'),
  });

  const importMut = useMutation({
    mutationFn: (file: File) => importQuestions(examId, file),
    onSuccess: (res) => {
      setImportResult(res);
      if (res.added > 0) toast.success(`${res.added} question(s) imported`);
      else toast.error('No questions imported — check the file.');
      refresh();
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Import failed'),
  });

  function submitQuestion(ev: React.FormEvent) {
    ev.preventDefault();
    if (!prompt.trim()) return toast.error('Question prompt is required.');
    const filled = options.map((o) => o.trim());
    if (filled.some((o) => !o)) return toast.error('All option fields must be filled.');
    addMut.mutate({
      prompt: prompt.trim(),
      options: filled,
      correct_index: correctIdx,
      points: Number(points) || 1,
    });
  }

  async function handleDownloadTemplate() {
    try {
      await downloadQuestionTemplate();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Template download failed');
    }
  }

  return (
    <div className="mt-3 space-y-3">
      {/* Existing questions */}
      {questions.length > 0 && (
        <Stagger className="flex flex-col gap-2">
          {questions.map((q, i) => (
            <StaggerItem key={q.id}>
              <div className="rounded-[14px] border border-white/[0.08] bg-[rgba(28,29,31,0.5)] p-3.5">
                <div className="flex items-start gap-3">
                  <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full bg-white/[0.06] font-mono text-[11px] text-[#b8babf]">
                    {i + 1}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-[13px] font-medium leading-snug text-white">
                      {q.prompt}{' '}
                      <span className="font-normal text-[#888b91]">({q.points} pt)</span>
                    </p>
                    <ul className="mt-2 space-y-0.5 pl-0">
                      {q.options.map((opt, oi) => (
                        <li
                          key={oi}
                          className={cn(
                            'flex items-center gap-1.5 text-[12px]',
                            oi === q.correct_index
                              ? 'font-medium text-[#27c93f]'
                              : 'text-[#888b91]',
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
                  </div>
                  {!locked && (
                    <button
                      type="button"
                      aria-label="Delete question"
                      className="shrink-0 flex h-7 w-7 items-center justify-center rounded-[8px] border border-white/[0.1] text-[#888b91] hover:border-[rgba(230,113,79,0.4)] hover:text-[#e6714f] transition-colors"
                      onClick={() => delMut.mutate(q.id)}
                    >
                      <Trash2 size={13} aria-hidden="true" />
                    </button>
                  )}
                </div>
              </div>
            </StaggerItem>
          ))}
        </Stagger>
      )}

      {/* Composer — three ways to add questions; hidden when locked */}
      {!locked && (
        <div className="rounded-[14px] border border-dashed border-white/[0.1] bg-[rgba(28,29,31,0.3)] p-3.5">
          {/* Tab bar */}
          <div
            role="tablist"
            aria-label="How to add questions"
            className="mb-3 flex gap-1 rounded-[10px] border border-white/[0.08] bg-[rgba(20,21,23,0.6)] p-1"
          >
            {[
              { key: 'manual', label: 'Manual', icon: Pencil },
              { key: 'ai', label: 'AI', icon: Sparkles },
              { key: 'excel', label: 'Excel', icon: Upload },
            ].map((t) => {
              const Icon = t.icon;
              const active = composerTab === t.key;
              return (
                <button
                  key={t.key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setComposerTab(t.key as 'manual' | 'ai' | 'excel')}
                  className={cn(
                    'flex flex-1 items-center justify-center gap-1.5 rounded-[8px] px-2 py-1.5 text-[12px] font-medium transition-colors',
                    active
                      ? 'bg-[rgba(var(--accent-rgb),0.16)] text-[#60a5fa]'
                      : 'text-[#888b91] hover:text-white',
                  )}
                >
                  <Icon size={13} aria-hidden="true" />
                  {t.label}
                </button>
              );
            })}
          </div>

          {/* Manual */}
          {composerTab === 'manual' && (
            <form onSubmit={submitQuestion} className="space-y-2.5" aria-label="New question">
              <textarea
                className={cn(inputCls, 'resize-none text-[13px]')}
                rows={2}
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
                      <button
                        type="button"
                        aria-label="Remove option"
                        className="shrink-0 flex h-7 w-7 items-center justify-center rounded-[8px] border border-white/[0.1] text-[#888b91] hover:text-[#e6714f] transition-colors"
                        onClick={() => {
                          setOptions((prev) => prev.filter((_, j) => j !== oi));
                          setCorrectIdx((c) => (c >= oi && c > 0 ? c - 1 : c));
                        }}
                      >
                        <Trash2 size={12} aria-hidden="true" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {options.length < 6 && (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 rounded-[8px] border border-white/[0.1] px-2.5 py-1 text-[12px] text-[#888b91] hover:text-white transition-colors"
                    onClick={() => setOptions((prev) => [...prev, ''])}
                  >
                    <Plus size={12} aria-hidden="true" /> Add option
                  </button>
                )}
                <label className="ml-auto flex items-center gap-1.5 text-[12px] text-[#888b91]">
                  Points
                  <input
                    type="number"
                    min={1}
                    value={points}
                    onChange={(e) => setPoints(e.target.value)}
                    className="w-14 rounded-[7px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2 py-1 text-[12px] text-white focus:outline-none focus:border-[var(--accent)]"
                    aria-label="Points"
                  />
                </label>
                <Pill
                  type="submit"
                  variant="accent"
                  className="gap-1 px-3 py-1.5 text-[12px]"
                  disabled={addMut.isPending}
                  aria-busy={addMut.isPending}
                >
                  {addMut.isPending ? (
                    <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Plus size={12} aria-hidden="true" />
                  )}
                  Add
                </Pill>
              </div>
            </form>
          )}

          {/* AI */}
          {composerTab === 'ai' && (
            <div className="space-y-2.5">
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
                  placeholder="Topic — e.g. React hooks or Junior Python Developer"
                  value={aiTopic}
                  onChange={(e) => setAiTopic(e.target.value)}
                  aria-label="AI topic"
                />
                <div className="flex flex-wrap items-end gap-2">
                  <label className="flex flex-col gap-1 text-[11.5px] text-[#888b91]">
                    Questions
                    <input
                      type="number"
                      min={1}
                      max={30}
                      value={aiCount}
                      onChange={(e) => setAiCount(e.target.value)}
                      className="w-16 rounded-[7px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2 py-1 text-[12px] text-white focus:outline-none focus:border-[var(--accent)]"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-[11.5px] text-[#888b91]">
                    Difficulty
                    <select
                      value={aiDifficulty}
                      onChange={(e) => setAiDifficulty(e.target.value as ExamDifficulty)}
                      className="rounded-[7px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2 py-1 text-[12px] text-white focus:outline-none focus:border-[var(--accent)]"
                    >
                      <option value="easy">Easy</option>
                      <option value="medium">Medium</option>
                      <option value="hard">Hard</option>
                      <option value="mixed">Mixed</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1 text-[11.5px] text-[#888b91]">
                    Language
                    <select
                      value={aiLanguage}
                      onChange={(e) => setAiLanguage(e.target.value as ExamLanguage)}
                      className="rounded-[7px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2 py-1 text-[12px] text-white focus:outline-none focus:border-[var(--accent)]"
                    >
                      <option value="en">English</option>
                      <option value="hi">हिन्दी</option>
                      <option value="te">తెలుగు</option>
                    </select>
                  </label>
                  <Pill
                    type="submit"
                    variant="accent"
                    className="ml-auto gap-1 px-3 py-1.5 text-[12px]"
                    disabled={generateMut.isPending}
                    aria-busy={generateMut.isPending}
                  >
                    {generateMut.isPending ? (
                      <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                    ) : (
                      <Sparkles size={12} aria-hidden="true" />
                    )}
                    {generateMut.isPending ? 'Generating…' : 'Generate'}
                  </Pill>
                </div>
              </form>

              {aiPreview.length > 0 && (
                <div className="space-y-2 rounded-[12px] border border-[rgba(var(--accent-rgb),0.25)] bg-[rgba(var(--accent-rgb),0.05)] p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-[12px] font-semibold text-[#60a5fa]">
                      {aiPreview.length} draft question{aiPreview.length !== 1 ? 's' : ''}
                    </p>
                    <button
                      type="button"
                      onClick={() => setAiPreview([])}
                      className="text-[12px] text-[#888b91] hover:text-white transition-colors"
                    >
                      Discard
                    </button>
                  </div>
                  <ul className="space-y-1.5">
                    {aiPreview.map((q, qi) => (
                      <li
                        key={qi}
                        className="rounded-[9px] border border-white/[0.08] bg-[rgba(28,29,31,0.5)] p-2.5"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-[12px] font-medium text-white">
                            {qi + 1}. {q.prompt}
                          </p>
                          <button
                            type="button"
                            aria-label="Remove draft"
                            className="shrink-0 text-[#888b91] hover:text-[#e6714f] transition-colors"
                            onClick={() => setAiPreview((prev) => prev.filter((_, j) => j !== qi))}
                          >
                            <X size={12} aria-hidden="true" />
                          </button>
                        </div>
                        <ul className="mt-1 space-y-0.5">
                          {q.options.map((opt, oi) => (
                            <li
                              key={oi}
                              className={cn(
                                'flex items-center gap-1 text-[11.5px]',
                                oi === q.correct_index
                                  ? 'font-medium text-[#27c93f]'
                                  : 'text-[#888b91]',
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
                  <Pill
                    variant="accent"
                    className="gap-1 px-3 py-1.5 text-[12px]"
                    disabled={addGeneratedMut.isPending}
                    aria-busy={addGeneratedMut.isPending}
                    onClick={() => addGeneratedMut.mutate(aiPreview)}
                  >
                    {addGeneratedMut.isPending ? (
                      <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                    ) : (
                      <Plus size={12} aria-hidden="true" />
                    )}
                    Add {aiPreview.length} question{aiPreview.length !== 1 ? 's' : ''}
                  </Pill>
                </div>
              )}
            </div>
          )}

          {/* Excel */}
          {composerTab === 'excel' && (
            <div className="space-y-2.5">
              <p className="text-[12px] text-[#888b91]">
                Upload <span className="text-white">.xlsx</span> or{' '}
                <span className="text-white">.csv</span>: Question · Option A–D · Correct
                (A/B/C/D) · Points.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => void handleDownloadTemplate()}
                  className="inline-flex items-center gap-1.5 rounded-[8px] border border-white/[0.12] px-3 py-1.5 text-[12px] text-[#b8babf] hover:text-white hover:border-white/[0.25] transition-colors"
                >
                  <Download size={13} aria-hidden="true" /> Template
                </button>
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
                <Pill
                  variant="accent"
                  className="gap-1 px-3 py-1.5 text-[12px]"
                  disabled={importMut.isPending}
                  aria-busy={importMut.isPending}
                  onClick={() => fileRef.current?.click()}
                >
                  {importMut.isPending ? (
                    <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Upload size={12} aria-hidden="true" />
                  )}
                  {importMut.isPending ? 'Uploading…' : 'Upload file'}
                </Pill>
              </div>
              {importResult && (
                <div className="rounded-[12px] border border-white/[0.1] bg-[rgba(28,29,31,0.5)] p-2.5 space-y-1.5">
                  <p className="flex items-center gap-1.5 text-[12px] font-medium text-[#27c93f]">
                    <FileText size={13} aria-hidden="true" />
                    {importResult.added} question{importResult.added !== 1 ? 's' : ''} imported
                  </p>
                  {importResult.errors.length > 0 && (
                    <div>
                      <p className="flex items-center gap-1 text-[11.5px] text-[#ffb764]">
                        <AlertTriangle size={12} aria-hidden="true" />
                        {importResult.errors.length} row{importResult.errors.length !== 1 ? 's' : ''} skipped
                      </p>
                      <ul className="mt-1 max-h-24 space-y-0.5 overflow-y-auto pl-4 text-[11px] text-[#888b91]">
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
        <div className="flex items-center gap-2 rounded-[12px] border border-white/[0.08] bg-[rgba(28,29,31,0.3)] px-3 py-2 text-[12px] text-[#888b91]">
          <Lock size={13} aria-hidden="true" /> Questions are locked — attempts exist.
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
  const qc = useQueryClient();

  const renameMut = useMutation({
    mutationFn: (title: string) =>
      updateSection(examId, section.round_id, section.id, { title }),
    onSuccess: () => {
      setEditing(false);
      void qc.invalidateQueries({ queryKey: ['hr', 'exam', examId, 'structure'] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Rename failed'),
  });

  const kindLabel = section.kind === 'coding' ? 'Coding' : 'MCQ';

  return (
    <div className="rounded-[16px] border border-white/[0.07] bg-[rgba(20,21,23,0.5)]">
      {/* Section header */}
      <div className="flex items-center gap-2 px-4 py-2.5">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-label={`${expanded ? 'Collapse' : 'Expand'} section ${section.title}`}
          className="flex items-center gap-1.5 text-[#888b91] hover:text-white transition-colors"
        >
          {expanded ? (
            <ChevronDown size={15} aria-hidden="true" />
          ) : (
            <ChevronRight size={15} aria-hidden="true" />
          )}
        </button>

        {section.kind === 'coding' ? (
          <Code2 size={14} className="shrink-0 text-[#60a5fa]" aria-hidden="true" />
        ) : (
          <ListChecks size={14} className="shrink-0 text-[#60a5fa]" aria-hidden="true" />
        )}

        {editing ? (
          <form
            className="flex flex-1 items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (titleDraft.trim()) renameMut.mutate(titleDraft.trim());
            }}
          >
            <input
              autoFocus
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              className="flex-1 rounded-[8px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2 py-1 text-[13px] text-white focus:outline-none focus:border-[var(--accent)]"
              aria-label="Section title"
            />
            <Pill
              type="submit"
              variant="accent"
              className="px-2 py-1 text-[12px]"
              disabled={renameMut.isPending}
            >
              {renameMut.isPending ? <Loader2 size={12} className="animate-spin" aria-hidden="true" /> : 'Save'}
            </Pill>
            <button
              type="button"
              className="text-[#888b91] hover:text-white transition-colors"
              onClick={() => { setEditing(false); setTitleDraft(section.title); }}
            >
              <X size={14} aria-hidden="true" />
            </button>
          </form>
        ) : (
          <>
            <span className="flex-1 text-[13px] font-medium text-white">{section.title}</span>
            <StatusTag tone="neutral" className="text-[10.5px]">{kindLabel}</StatusTag>
            {section.time_limit_seconds && (
              <span className="flex items-center gap-1 text-[11.5px] text-[#70757c]">
                <Clock size={11} aria-hidden="true" />
                {Math.round(section.time_limit_seconds / 60)} min
              </span>
            )}
            <button
              type="button"
              aria-label={`Rename section ${section.title}`}
              className="flex h-6 w-6 items-center justify-center rounded-[6px] text-[#888b91] hover:text-white transition-colors"
              onClick={() => setEditing(true)}
            >
              <Pencil size={13} aria-hidden="true" />
            </button>
            <button
              type="button"
              aria-label={`Delete section ${section.title}`}
              className="flex h-6 w-6 items-center justify-center rounded-[6px] text-[#888b91] hover:text-[#e6714f] transition-colors"
              disabled={deleting}
              onClick={() => onDelete(section.id)}
            >
              <Trash2 size={13} aria-hidden="true" />
            </button>
          </>
        )}
      </div>

      {/* Section body */}
      {expanded && (
        <div className="px-4 pb-3">
          {section.kind === 'coding' ? (
            <Suspense
              fallback={
                <div className="flex h-16 items-center justify-center text-[13px] text-[#888b91]">
                  <Loader2 size={16} className="animate-spin mr-2" aria-hidden="true" /> Loading editor…
                </div>
              }
            >
              <CodingAuthoringSection
                examId={examId}
                sectionId={section.id}
                locked={locked}
              />
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

interface RoundAssignPanelProps {
  examId: string;
  roundId: string;
  isPublished: boolean;
}

function RoundAssignPanel({ examId, roundId, isPublished }: RoundAssignPanelProps) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [scheduledAt, setScheduledAt] = useState('');
  const [minted, setMinted] = useState<AssignResult[]>([]);

  const { data: applicants } = useQuery({
    queryKey: ['hr', 'applicants'],
    queryFn: () => listApplicants(),
  });

  const { data: assignments } = useQuery({
    queryKey: ['hr', 'exam', examId, 'assignments'],
    queryFn: () => listAssignments(examId),
  });

  const assignMut = useMutation({
    mutationFn: () =>
      assignExam(
        examId,
        [...selected],
        undefined,
        roundId,
        scheduledAt ? new Date(scheduledAt).toISOString() : undefined,
      ),
    onSuccess: (res) => {
      setMinted(res);
      setSelected(new Set());
      setScheduledAt('');
      toast.success(`${res.length} link(s) created`);
      void qc.invalidateQueries({ queryKey: ['hr', 'exam', examId, 'assignments'] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Assign failed'),
  });

  const revokeMut = useMutation({
    mutationFn: (aid: string) => revokeAssignment(examId, aid),
    onSuccess: () => {
      toast.success('Link revoked');
      void qc.invalidateQueries({ queryKey: ['hr', 'exam', examId, 'assignments'] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Revoke failed'),
  });

  async function copyLink(link: string) {
    try {
      await navigator.clipboard.writeText(link);
      toast.success('Link copied');
    } catch {
      toast.error('Could not copy — select and copy manually.');
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

  const roundAssignments = (assignments ?? []).filter(
    (a) => a.round_id === roundId || a.round_id === null,
  );

  if (!isPublished) {
    return (
      <p className="mt-3 text-[12.5px] text-[#888b91]">
        Publish this round first, then assign it to applicants.
      </p>
    );
  }

  return (
    <div className="mt-3 space-y-3">
      {/* Applicant list */}
      <div
        className="max-h-36 space-y-0.5 overflow-y-auto rounded-[14px] border border-white/[0.08] bg-[rgba(28,29,31,0.5)] p-2"
        aria-label="Select applicants to assign"
      >
        {(applicants ?? []).length === 0 ? (
          <p className="px-2 py-2 text-[12px] text-[#888b91]">
            No applicants yet — add them under Applicants.
          </p>
        ) : (
          (applicants ?? []).map((a) => (
            <label
              key={a.id}
              className="flex cursor-pointer items-center gap-2 rounded-[9px] px-2 py-1.5 text-[12.5px] text-white hover:bg-white/[0.04] transition-colors"
            >
              <input
                type="checkbox"
                checked={selected.has(a.id)}
                onChange={(e) => toggleApplicant(a.id, e.target.checked)}
                className="h-3.5 w-3.5 accent-[var(--accent)]"
              />
              <span className="truncate">{a.full_name}</span>
            </label>
          ))
        )}
      </div>

      {/* Schedule (optional) */}
      <label className="block text-sm">
        <span className="mb-1 block text-[11.5px] font-medium uppercase tracking-[0.5px] text-[#70757c]">
          Schedule (optional)
        </span>
        <input
          type="datetime-local"
          className={cn(inputCls, 'text-[13px]')}
          value={scheduledAt}
          onChange={(e) => setScheduledAt(e.target.value)}
          aria-label="Scheduled time for this round assignment"
        />
      </label>

      <Pill
        variant="accent"
        className="gap-1.5 px-4 py-2"
        disabled={assignMut.isPending || selected.size === 0}
        aria-busy={assignMut.isPending}
        onClick={() => assignMut.mutate()}
      >
        {assignMut.isPending ? (
          <Loader2 size={14} className="animate-spin" aria-hidden="true" />
        ) : (
          <Send size={14} aria-hidden="true" />
        )}
        Generate links ({selected.size})
      </Pill>

      {/* Once-shown magic links */}
      {minted.length > 0 && (
        <div
          role="region"
          aria-label="Generated magic links"
          className="space-y-1.5 rounded-[14px] border border-[rgba(39,201,63,0.3)] bg-[rgba(39,201,63,0.08)] p-3"
        >
          <p className="text-[12px] font-semibold text-[#27c93f]">
            Share these links — shown once only:
          </p>
          {minted.map((m) => (
            <div key={m.assignment_id} className="flex items-center gap-2 text-[12px]">
              <span className="w-28 shrink-0 truncate font-medium text-white">
                {m.applicant_name}
              </span>
              <input
                readOnly
                value={m.magic_link}
                className="min-w-0 flex-1 rounded-[9px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2 py-1 text-[#888b91] focus:outline-none"
                aria-label={`Magic link for ${m.applicant_name}`}
              />
              <button
                type="button"
                aria-label={`Copy link for ${m.applicant_name}`}
                className="shrink-0 flex h-6 w-6 items-center justify-center rounded-[7px] text-[#27c93f] hover:bg-[rgba(39,201,63,0.15)] transition-colors"
                onClick={() => void copyLink(m.magic_link)}
              >
                <Copy size={13} aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Existing assignments for this round */}
      {roundAssignments.length > 0 && (
        <div className="space-y-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.5px] text-[#70757c]">
            Assigned
          </p>
          {roundAssignments.map((a) => (
            <div
              key={a.assignment_id}
              className="flex items-center gap-2 rounded-[11px] border border-white/[0.08] bg-[rgba(28,29,31,0.4)] px-3 py-1.5 text-[12.5px] text-white"
            >
              <span className="min-w-0 flex-1 truncate">{a.applicant_name}</span>
              {a.scheduled_at && (
                <span className="flex items-center gap-1 text-[11px] text-[#70757c]">
                  <Clock size={10} aria-hidden="true" />
                  {new Date(a.scheduled_at).toLocaleDateString('en-IN', {
                    day: 'numeric',
                    month: 'short',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </span>
              )}
              <StatusTag tone="neutral" className="text-[10.5px]">
                {a.status}
              </StatusTag>
              {a.status === 'invited' && (
                <button
                  type="button"
                  aria-label={`Revoke link for ${a.applicant_name}`}
                  className="shrink-0 flex h-6 w-6 items-center justify-center rounded-[7px] text-[#888b91] hover:text-[#e6714f] transition-colors"
                  onClick={() => revokeMut.mutate(a.assignment_id)}
                >
                  <Ban size={13} aria-hidden="true" />
                </button>
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
  onDelete: (roundId: string) => void;
  deleting: boolean;
  onMoveUp: (roundId: string) => void;
  onMoveDown: (roundId: string) => void;
}

function RoundPanel({
  examId,
  round,
  totalRounds,
  onDelete,
  deleting,
  onMoveUp,
  onMoveDown,
}: RoundPanelProps) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(true);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(round.title);
  const [thresholdDraft, setThresholdDraft] = useState(String(round.pass_threshold));
  const [addingSection, setAddingSection] = useState(false);
  const [sectionTitle, setSectionTitle] = useState('');
  const [sectionKind, setSectionKind] = useState<'mcq' | 'coding'>('mcq');
  const [sectionTimeMin, setSectionTimeMin] = useState('');
  const [showAssign, setShowAssign] = useState(false);

  const invalidateStructure = () =>
    void qc.invalidateQueries({ queryKey: ['hr', 'exam', examId, 'structure'] });

  const renameMut = useMutation({
    mutationFn: (title: string) => updateRound(examId, round.id, { title }),
    onSuccess: () => { setEditingTitle(false); invalidateStructure(); },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Rename failed'),
  });

  const publishMut = useMutation({
    mutationFn: (status: 'draft' | 'published') =>
      updateRound(examId, round.id, { status }),
    onSuccess: invalidateStructure,
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Update failed'),
  });

  const thresholdMut = useMutation({
    mutationFn: (pass_threshold: number) =>
      updateRound(examId, round.id, { pass_threshold }),
    onSuccess: () => { toast.success('Saved'); invalidateStructure(); },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Save failed'),
  });

  const advancesMut = useMutation({
    mutationFn: (advances_to_interview: boolean) =>
      updateRound(examId, round.id, { advances_to_interview }),
    onSuccess: invalidateStructure,
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Update failed'),
  });

  const addSectionMut = useMutation({
    mutationFn: () =>
      createSection(examId, round.id, {
        title: sectionTitle.trim() || (sectionKind === 'coding' ? 'Coding Section' : 'MCQ Section'),
        kind: sectionKind,
        time_limit_seconds: sectionTimeMin.trim()
          ? Math.max(1, Number(sectionTimeMin)) * 60
          : null,
      }),
    onSuccess: () => {
      setSectionTitle('');
      setSectionKind('mcq');
      setSectionTimeMin('');
      setAddingSection(false);
      invalidateStructure();
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Add section failed'),
  });

  const deleteSectionMut = useMutation({
    mutationFn: (sectionId: string) => deleteSection(examId, round.id, sectionId),
    onSuccess: invalidateStructure,
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Delete section failed'),
  });

  const isPublished = round.status === 'published';
  const { label: statusLabel, tone: statusToneKey } = roundStatusTone(round.status);

  return (
    <GlassCard className="p-0 overflow-hidden">
      {/* Round header */}
      <div className="flex items-center gap-2.5 px-5 py-3.5 border-b border-white/[0.06]">
        {/* Drag handle / reorder */}
        <div className="flex flex-col gap-0.5 shrink-0">
          <button
            type="button"
            aria-label={`Move round ${round.title} up`}
            disabled={round.position <= 1}
            className="flex h-5 w-5 items-center justify-center text-[#888b91] hover:text-white disabled:opacity-30 transition-colors"
            onClick={() => onMoveUp(round.id)}
          >
            <GripVertical size={14} aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label={`Move round ${round.title} down`}
            disabled={round.position >= totalRounds}
            className="flex h-5 w-5 items-center justify-center text-[#888b91] hover:text-white disabled:opacity-30 transition-colors"
            onClick={() => onMoveDown(round.id)}
          >
            <GripVertical size={14} className="rotate-180" aria-hidden="true" />
          </button>
        </div>

        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-label={`${expanded ? 'Collapse' : 'Expand'} round ${round.title}`}
          className="text-[#888b91] hover:text-white transition-colors"
        >
          {expanded ? (
            <ChevronDown size={16} aria-hidden="true" />
          ) : (
            <ChevronRight size={16} aria-hidden="true" />
          )}
        </button>

        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[rgba(var(--accent-rgb),0.14)] font-mono text-[12px] font-semibold text-[#60a5fa]">
          {round.round_number}
        </span>

        {editingTitle ? (
          <form
            className="flex flex-1 items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (titleDraft.trim()) renameMut.mutate(titleDraft.trim());
            }}
          >
            <input
              autoFocus
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              className="flex-1 rounded-[9px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2.5 py-1.5 text-[14px] text-white focus:outline-none focus:border-[var(--accent)]"
              aria-label="Round title"
            />
            <Pill
              type="submit"
              variant="accent"
              className="px-3 py-1.5 text-[12px]"
              disabled={renameMut.isPending}
            >
              {renameMut.isPending ? (
                <Loader2 size={12} className="animate-spin" aria-hidden="true" />
              ) : (
                'Save'
              )}
            </Pill>
            <button
              type="button"
              className="text-[#888b91] hover:text-white transition-colors"
              onClick={() => { setEditingTitle(false); setTitleDraft(round.title); }}
            >
              <X size={15} aria-hidden="true" />
            </button>
          </form>
        ) : (
          <>
            <span className="flex-1 text-[15px] font-semibold text-white">{round.title}</span>
            <StatusTag tone={statusToneKey}>{statusLabel}</StatusTag>
            <button
              type="button"
              aria-label={`Rename round ${round.title}`}
              className="flex h-7 w-7 items-center justify-center rounded-[8px] text-[#888b91] hover:text-white transition-colors"
              onClick={() => setEditingTitle(true)}
            >
              <Pencil size={14} aria-hidden="true" />
            </button>
            <Pill
              variant={isPublished ? 'ghost' : 'primary'}
              className="px-3 py-1.5 text-[12px]"
              disabled={publishMut.isPending}
              aria-busy={publishMut.isPending}
              onClick={() => publishMut.mutate(isPublished ? 'draft' : 'published')}
            >
              {publishMut.isPending ? (
                <Loader2 size={12} className="animate-spin" aria-hidden="true" />
              ) : null}
              {isPublished ? 'Unpublish' : 'Publish'}
            </Pill>
            <button
              type="button"
              aria-label={`Delete round ${round.title}`}
              disabled={deleting || totalRounds <= 1}
              className="flex h-7 w-7 items-center justify-center rounded-[8px] text-[#888b91] hover:text-[#e6714f] disabled:opacity-30 transition-colors"
              onClick={() => onDelete(round.id)}
            >
              <Trash2 size={14} aria-hidden="true" />
            </button>
          </>
        )}
      </div>

      {expanded && (
        <div className="px-5 py-4 space-y-4">
          {/* Settings row */}
          <div className="flex flex-wrap items-end gap-4">
            <label className="flex flex-col gap-1 text-[12px] text-[#888b91]">
              <span className="uppercase tracking-[0.5px]">Pass threshold %</span>
              <input
                type="number"
                min={0}
                max={100}
                value={thresholdDraft}
                onChange={(e) => setThresholdDraft(e.target.value)}
                onBlur={() => {
                  const v = Number(thresholdDraft);
                  if (v !== round.pass_threshold && v >= 0 && v <= 100) {
                    thresholdMut.mutate(v);
                  }
                }}
                className="w-24 rounded-[9px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2.5 py-1.5 text-[13px] text-white focus:outline-none focus:border-[var(--accent)]"
                aria-label="Pass threshold"
              />
            </label>
            {round.time_limit_seconds !== null && (
              <span className="flex items-center gap-1 text-[12.5px] text-[#888b91]">
                <Clock size={13} aria-hidden="true" />
                {Math.round(round.time_limit_seconds / 60)} min limit
              </span>
            )}
            <label className="flex items-center gap-2 text-[12.5px] text-[#b8babf]">
              <input
                type="checkbox"
                className="h-3.5 w-3.5 accent-[var(--accent)]"
                checked={round.advances_to_interview}
                onChange={(e) => advancesMut.mutate(e.target.checked)}
              />
              Advances to interview on pass
            </label>
          </div>

          {/* Sections */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-[12px] font-semibold uppercase tracking-[0.5px] text-[#70757c]">
                Sections ({round.sections.length})
              </p>
              <button
                type="button"
                className="inline-flex items-center gap-1 text-[12px] text-[#60a5fa] hover:underline"
                onClick={() => setAddingSection((v) => !v)}
                aria-expanded={addingSection}
              >
                <Plus size={13} aria-hidden="true" /> Add section
              </button>
            </div>

            {/* Add-section form */}
            {addingSection && (
              <form
                className="mb-3 flex flex-wrap items-end gap-2 rounded-[14px] border border-dashed border-white/[0.1] bg-[rgba(28,29,31,0.3)] p-3"
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
                        'rounded-[9px] border px-3 py-1.5 text-[12px] font-medium transition-colors',
                        sectionKind === k
                          ? 'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]'
                          : 'border-white/[0.1] text-[#888b91] hover:text-white',
                      )}
                      aria-pressed={sectionKind === k}
                    >
                      {k === 'mcq' ? 'MCQ' : 'Coding'}
                    </button>
                  ))}
                </div>
                <input
                  className={cn(inputCls, 'flex-1 text-[13px]')}
                  placeholder="Section title (optional)"
                  value={sectionTitle}
                  onChange={(e) => setSectionTitle(e.target.value)}
                  aria-label="Section title"
                />
                <input
                  type="number"
                  min={1}
                  placeholder="Min limit"
                  value={sectionTimeMin}
                  onChange={(e) => setSectionTimeMin(e.target.value)}
                  className="w-24 rounded-[9px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2.5 py-2 text-[13px] text-white placeholder:text-[#5a5f66] focus:outline-none focus:border-[var(--accent)]"
                  aria-label="Section time limit in minutes"
                />
                <Pill
                  type="submit"
                  variant="accent"
                  className="gap-1 px-3 py-2 text-[12px]"
                  disabled={addSectionMut.isPending}
                  aria-busy={addSectionMut.isPending}
                >
                  {addSectionMut.isPending ? (
                    <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Plus size={13} aria-hidden="true" />
                  )}
                  Add
                </Pill>
                <button
                  type="button"
                  className="text-[#888b91] hover:text-white transition-colors"
                  onClick={() => setAddingSection(false)}
                >
                  <X size={15} aria-hidden="true" />
                </button>
              </form>
            )}

            {/* Section list */}
            {round.sections.length === 0 ? (
              <p className="text-[12.5px] text-[#888b91]">
                No sections yet — add one above.
              </p>
            ) : (
              <div className="space-y-2">
                {round.sections.map((section) => (
                  <SectionPanel
                    key={section.id}
                    examId={examId}
                    section={section}
                    locked={false}
                    onDelete={(sid) => deleteSectionMut.mutate(sid)}
                    deleting={deleteSectionMut.isPending}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Assign & share */}
          <div>
            <button
              type="button"
              onClick={() => setShowAssign((v) => !v)}
              aria-expanded={showAssign}
              className="flex items-center gap-1.5 text-[12.5px] font-semibold text-[#60a5fa] hover:underline"
            >
              <Send size={13} aria-hidden="true" />
              {showAssign ? 'Hide' : 'Show'} Assign &amp; share
            </button>
            {showAssign && (
              <RoundAssignPanel
                examId={examId}
                roundId={round.id}
                isPublished={isPublished}
              />
            )}
          </div>
        </div>
      )}
    </GlassCard>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ExamEditor() {
  const { examId = '' } = useParams<{ examId: string }>();
  const qc = useQueryClient();

  const { data: exam, isLoading: examLoading } = useQuery({
    queryKey: ['hr', 'exam', examId],
    queryFn: () => getExam(examId),
  });

  const { data: structure, isLoading: structureLoading } = useQuery({
    queryKey: ['hr', 'exam', examId, 'structure'],
    queryFn: () => getStructure(examId),
  });

  const isLoading = examLoading || structureLoading;

  // Add round state
  const [addingRound, setAddingRound] = useState(false);
  const [roundTitle, setRoundTitle] = useState('');
  const [roundThreshold, setRoundThreshold] = useState('60');
  const [roundTimeMin, setRoundTimeMin] = useState('');
  const [roundAdvances, setRoundAdvances] = useState(false);

  const invalidateAll = () => {
    void qc.invalidateQueries({ queryKey: ['hr', 'exam', examId, 'structure'] });
    void qc.invalidateQueries({ queryKey: ['hr', 'exam', examId] });
    void qc.invalidateQueries({ queryKey: ['hr', 'exams'] });
  };

  const addRoundMut = useMutation({
    mutationFn: () =>
      createRound(examId, {
        title: roundTitle.trim() || `Round ${(structure?.rounds.length ?? 0) + 1}`,
        pass_threshold: Number(roundThreshold) || 60,
        time_limit_seconds: roundTimeMin.trim()
          ? Math.max(1, Number(roundTimeMin)) * 60
          : null,
        advances_to_interview: roundAdvances,
      }),
    onSuccess: () => {
      setRoundTitle('');
      setRoundThreshold('60');
      setRoundTimeMin('');
      setRoundAdvances(false);
      setAddingRound(false);
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Add round failed'),
  });

  const deleteRoundMut = useMutation({
    mutationFn: (roundId: string) => deleteRound(examId, roundId),
    onSuccess: invalidateAll,
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Delete failed'),
  });

  function handleMoveUp(roundId: string) {
    if (!structure) return;
    const sorted = [...structure.rounds].sort((a, b) => a.position - b.position);
    const idx = sorted.findIndex((r) => r.id === roundId);
    if (idx <= 0) return;
    const newOrder = sorted.map((r) => r.id);
    // Swap with the one before
    [newOrder[idx - 1], newOrder[idx]] = [newOrder[idx], newOrder[idx - 1]];
    void reorderRounds(examId, newOrder).then(() => invalidateAll());
  }

  function handleMoveDown(roundId: string) {
    if (!structure) return;
    const sorted = [...structure.rounds].sort((a, b) => a.position - b.position);
    const idx = sorted.findIndex((r) => r.id === roundId);
    if (idx < 0 || idx >= sorted.length - 1) return;
    const newOrder = sorted.map((r) => r.id);
    [newOrder[idx], newOrder[idx + 1]] = [newOrder[idx + 1], newOrder[idx]];
    void reorderRounds(examId, newOrder).then(() => invalidateAll());
  }

  // ── Loading ──────────────────────────────────────────────────────────────────
  if (isLoading || !exam || !structure) {
    return (
      <div className="mx-auto max-w-[960px] px-6 py-8 lg:px-8 space-y-4">
        <div className="h-5 w-28 rounded-xl bg-white/[0.07] animate-pulse" />
        <div className="h-10 w-64 rounded-xl bg-white/[0.07] animate-pulse" />
        <div className="h-40 w-full rounded-[24px] bg-white/[0.05] animate-pulse" />
        <div className="h-48 w-full rounded-[24px] bg-white/[0.05] animate-pulse" />
      </div>
    );
  }

  const rounds = [...structure.rounds].sort((a, b) => a.position - b.position);

  return (
    <div className="mx-auto max-w-[960px] px-6 py-8 lg:px-8">
      {/* Breadcrumb */}
      <Reveal>
        <Link
          to="/hr/exams"
          className="inline-flex items-center gap-1.5 text-[13px] text-[#888b91] hover:text-white transition-colors"
        >
          <ArrowLeft size={15} aria-hidden="true" /> Back to exams
        </Link>

        {/* Header */}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]">
              <ClipboardList size={20} aria-hidden="true" />
            </span>
            <div>
              <h1 className="text-[26px] font-semibold tracking-[-0.8px] text-white">
                {exam.title}
              </h1>
            </div>
          </div>
          <div className="flex items-center gap-2.5">
            <Link to={`/hr/exams/${examId}/results`}>
              <Pill variant="ghost" className="px-4 py-2.5 gap-2">
                <BarChart3 size={15} aria-hidden="true" /> Results
              </Pill>
            </Link>
          </div>
        </div>

        {/* Meta */}
        <div className="mt-4 flex items-center flex-wrap gap-4 text-[13px] text-[#888b91]">
          <span>{rounds.length} round{rounds.length !== 1 ? 's' : ''}</span>
          {exam.auto_advance_on_pass && (
            <span className="flex items-center gap-1 text-[#60a5fa]">
              Auto-advance on pass
            </span>
          )}
        </div>
      </Reveal>

      {/* Rounds */}
      <div className="mt-6 space-y-4">
        {rounds.length === 0 ? (
          <Reveal delay={0.04}>
            <div className="flex flex-col items-center gap-3 rounded-[24px] border border-dashed border-white/[0.1] bg-[rgba(15,15,16,0.6)] py-12 text-center">
              <span className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-white/[0.06] text-[#888b91]">
                <Sparkles size={22} aria-hidden="true" />
              </span>
              <p className="text-[14px] text-[#888b91]">
                No rounds yet — add the first round below.
              </p>
            </div>
          </Reveal>
        ) : (
          <Stagger>
            {rounds.map((round) => (
              <StaggerItem key={round.id}>
                <RoundPanel
                  examId={examId}
                  round={round}
                  totalRounds={rounds.length}
                  onDelete={(rid) => deleteRoundMut.mutate(rid)}
                  deleting={deleteRoundMut.isPending}
                  onMoveUp={handleMoveUp}
                  onMoveDown={handleMoveDown}
                />
              </StaggerItem>
            ))}
          </Stagger>
        )}

        {/* Add round */}
        {addingRound ? (
          <Reveal delay={0.02}>
            <GlassCard feature className="p-5">
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
                  placeholder="Round title (e.g. Technical Screening)"
                  value={roundTitle}
                  onChange={(e) => setRoundTitle(e.target.value)}
                  aria-label="Round title"
                />
                <div className="grid gap-3 sm:grid-cols-3">
                  <label className="flex flex-col gap-1 text-[12px] text-[#888b91]">
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
                  <label className="flex flex-col gap-1 text-[12px] text-[#888b91]">
                    <span className="uppercase tracking-[0.5px]">Time limit (min)</span>
                    <input
                      type="number"
                      min={1}
                      placeholder="none"
                      value={roundTimeMin}
                      onChange={(e) => setRoundTimeMin(e.target.value)}
                      className={cn(inputCls, 'text-[13px]')}
                      aria-label="Time limit in minutes"
                    />
                  </label>
                  <div className="flex items-end pb-1">
                    <label className="flex items-center gap-2 text-[12.5px] text-[#b8babf]">
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
                  <Pill
                    type="submit"
                    disabled={addRoundMut.isPending}
                    aria-busy={addRoundMut.isPending}
                    className="gap-1.5"
                  >
                    {addRoundMut.isPending ? (
                      <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                    ) : (
                      <Plus size={15} aria-hidden="true" />
                    )}
                    {addRoundMut.isPending ? 'Adding…' : 'Add round'}
                  </Pill>
                  <Pill
                    type="button"
                    variant="ghost"
                    onClick={() => setAddingRound(false)}
                    disabled={addRoundMut.isPending}
                  >
                    Cancel
                  </Pill>
                </div>
              </form>
            </GlassCard>
          </Reveal>
        ) : (
          <button
            type="button"
            onClick={() => setAddingRound(true)}
            className={cn(
              'flex w-full items-center justify-center gap-2 rounded-[20px]',
              'border-[1.5px] border-dashed border-[rgba(var(--accent-rgb),0.35)]',
              'bg-[rgba(var(--accent-rgb),0.04)] py-4 text-[13.5px] font-medium text-[#60a5fa]',
              'transition-colors hover:bg-[rgba(var(--accent-rgb),0.08)]',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
            )}
          >
            <Plus size={17} aria-hidden="true" /> Add round
          </button>
        )}
      </div>
    </div>
  );
}
