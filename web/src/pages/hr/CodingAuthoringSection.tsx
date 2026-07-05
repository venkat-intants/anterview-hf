// CodingAuthoringSection — section-scoped version of the coding question
// composer. Works identically to CodingAuthoring but posts to the
// section-scoped endpoints:
//   GET  /hr/exams/{examId}/sections/{sectionId}/coding-questions
//   POST /hr/exams/{examId}/sections/{sectionId}/coding-questions
// This file is lazy-loaded by ExamEditor so the CodeEditor import doesn't
// inflate the initial bundle.

import { Suspense, lazy, useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2, Loader2, Lock, Code2, Pencil, Sparkles, X } from '@/design/components/icons';
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
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { Pill, StatusTag } from '@/design/components/primitives';
import { Stagger, StaggerItem } from '@/design/components/Reveal';

const CodeEditor = lazy(() => import('@/components/CodeEditor'));

const LANG_LABEL: Record<string, string> = {
  python: 'Python', javascript: 'JavaScript', typescript: 'TypeScript', java: 'Java',
  cpp: 'C++', c: 'C', go: 'Go', csharp: 'C#', ruby: 'Ruby', rust: 'Rust',
};

const inputCls =
  'w-full rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3 py-2 ' +
  'text-[14px] text-white placeholder:text-[#5a5f66] focus:outline-none ' +
  'focus:border-[var(--accent)] transition-colors';

const emptyTest = (): CodingTestCase => ({
  stdin: '',
  expected_output: '',
  is_sample: false,
  weight: 1,
});

interface Props {
  examId: string;
  sectionId: string;
  locked: boolean;
}

export default function CodingAuthoringSection({ examId, sectionId, locked }: Props) {
  const qc = useQueryClient();
  const qKey = ['hr', 'exam', examId, 'section', sectionId, 'coding-questions'];

  const { data: questions = [] } = useQuery({
    queryKey: qKey,
    queryFn: () => listSectionCodingQuestions(examId, sectionId),
  });

  const refresh = () => void qc.invalidateQueries({ queryKey: qKey });

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
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : 'AI generation failed'),
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
    <div className="mt-2 space-y-3">
      {/* Existing coding questions */}
      {questions.length > 0 && (
        <Stagger className="flex flex-col gap-2">
          {questions.map((q, i) => {
            const sampleN = q.test_cases.filter((t) => t.is_sample).length;
            return (
              <StaggerItem key={q.id}>
                <div className="rounded-[14px] border border-white/[0.08] bg-[rgba(28,29,31,0.5)] p-3.5">
                  <div className="flex items-start gap-3">
                    <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full bg-white/[0.06] font-mono text-[11px] text-[#b8babf]">
                      {i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="whitespace-pre-wrap text-[13px] font-medium leading-snug text-white">
                        {q.prompt}{' '}
                        <span className="font-normal text-[#888b91]">({q.points} pt)</span>
                      </p>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        {q.allowed_languages.map((l) => (
                          <StatusTag key={l} tone="neutral" className="text-[10.5px]">
                            {LANG_LABEL[l] ?? l}
                          </StatusTag>
                        ))}
                        <span className="text-[11px] text-[#70757c]">
                          {q.test_cases.length} test{q.test_cases.length === 1 ? '' : 's'} ·{' '}
                          {sampleN} sample
                        </span>
                      </div>
                    </div>
                    {!locked && (
                      <button
                        type="button"
                        aria-label="Delete question"
                        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[7px] border border-white/[0.1] text-[#888b91] transition-colors hover:border-[rgba(230,113,79,0.4)] hover:text-[#e6714f]"
                        onClick={() => delMut.mutate(q.id)}
                      >
                        <Trash2 size={13} aria-hidden="true" />
                      </button>
                    )}
                  </div>
                </div>
              </StaggerItem>
            );
          })}
        </Stagger>
      )}

      {/* Composer */}
      {locked ? (
        <div className="flex items-center gap-2 rounded-[14px] border border-white/[0.08] bg-[rgba(28,29,31,0.3)] px-3.5 py-2.5 text-[12.5px] text-[#888b91]">
          <Lock size={14} aria-hidden="true" /> Questions are locked once attempts exist.
        </div>
      ) : (
        <div className="rounded-[14px] border border-dashed border-white/[0.1] bg-[rgba(28,29,31,0.3)] p-3.5">
          {/* Tab bar — mirrors the MCQ composer's Manual | AI switcher */}
          <div
            role="tablist"
            aria-label="How to add coding questions"
            className="mb-3 flex gap-1 rounded-[10px] border border-white/[0.08] bg-[rgba(20,21,23,0.6)] p-1"
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

          {/* AI generate */}
          {composerTab === 'ai' && (
            <div className="space-y-3" aria-label="AI generate coding questions">
              <input
                className={cn(inputCls, 'text-[13px]')}
                placeholder="Topic — e.g. arrays & strings, SQL joins, or Junior Python Developer"
                value={aiTopic}
                onChange={(e) => setAiTopic(e.target.value)}
                aria-label="Topic"
              />
              <div className="flex flex-wrap items-end gap-2.5">
                <label className="flex flex-col gap-1 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]">
                  Problems
                  <input
                    type="number"
                    min={1}
                    max={8}
                    value={aiCount}
                    onChange={(e) => setAiCount(e.target.value)}
                    className="w-16 rounded-[7px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2 py-1.5 text-[12.5px] normal-case text-white focus:border-[var(--accent)] focus:outline-none"
                    aria-label="Number of problems"
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]">
                  Difficulty
                  <select
                    value={aiDifficulty}
                    onChange={(e) => setAiDifficulty(e.target.value as ExamDifficulty)}
                    className="rounded-[7px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2 py-1.5 text-[12.5px] normal-case text-white focus:border-[var(--accent)] focus:outline-none"
                  >
                    <option value="easy">Easy</option>
                    <option value="medium">Medium</option>
                    <option value="hard">Hard</option>
                    <option value="mixed">Mixed</option>
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]">
                  Statement language
                  <select
                    value={aiLanguage}
                    onChange={(e) => setAiLanguage(e.target.value as ExamLanguage)}
                    className="rounded-[7px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2 py-1.5 text-[12.5px] normal-case text-white focus:border-[var(--accent)] focus:outline-none"
                  >
                    <option value="en">English</option>
                    <option value="hi">हिन्दी</option>
                    <option value="te">తెలుగు</option>
                  </select>
                </label>
                <Pill
                  type="button"
                  variant="accent"
                  className="gap-1 px-3 py-1.5 text-[12px]"
                  disabled={generateMut.isPending || !aiTopic.trim() || langs.length === 0}
                  aria-busy={generateMut.isPending}
                  onClick={() => generateMut.mutate()}
                >
                  {generateMut.isPending ? (
                    <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Sparkles size={13} aria-hidden="true" />
                  )}
                  {generateMut.isPending ? 'Generating…' : 'Generate'}
                </Pill>
              </div>

              {/* Allowed languages (shared with the manual composer) */}
              <div>
                <p className="mb-1.5 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]">
                  Candidates may solve in
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {CODING_LANGUAGES.map((slug) => {
                    const on = langs.includes(slug);
                    return (
                      <button
                        key={slug}
                        type="button"
                        onClick={() => toggleLang(slug, !on)}
                        className={cn(
                          'rounded-full border px-2.5 py-0.5 text-[11.5px] transition-colors',
                          on
                            ? 'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]'
                            : 'border-white/[0.1] text-[#888b91] hover:text-white',
                        )}
                      >
                        {LANG_LABEL[slug]}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Drafts preview */}
              {aiPreview.length > 0 && (
                <div className="space-y-2">
                  <p className="text-[11.5px] text-[#888b91]">
                    Review the drafts — check the sample cases and remove any problem you
                    don&apos;t want. Each problem ships with hidden test cases used for grading.
                  </p>
                  {aiPreview.map((q, i) => {
                    const sampleN = q.test_cases.filter((t) => t.is_sample).length;
                    const hiddenN = q.test_cases.length - sampleN;
                    return (
                      <div
                        key={i}
                        className="rounded-[11px] border border-white/[0.08] bg-[rgba(28,29,31,0.5)] p-2.5"
                      >
                        <div className="flex items-start gap-2.5">
                          <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full bg-white/[0.06] font-mono text-[11px] text-[#b8babf]">
                            {i + 1}
                          </span>
                          <div className="min-w-0 flex-1">
                            <p className="line-clamp-4 whitespace-pre-wrap text-[12.5px] leading-snug text-white">
                              {q.prompt}
                            </p>
                            <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-[#70757c]">
                              <StatusTag tone="neutral" className="text-[10.5px]">
                                {sampleN} sample
                              </StatusTag>
                              <StatusTag tone="neutral" className="text-[10.5px]">
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
                            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] border border-white/[0.1] text-[#888b91] transition-colors hover:border-[rgba(230,113,79,0.4)] hover:text-[#e6714f]"
                            onClick={() =>
                              setAiPreview((p) => p.filter((_, j) => j !== i))
                            }
                          >
                            <X size={12} aria-hidden="true" />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                  <div className="flex items-center gap-2">
                    <Pill
                      type="button"
                      variant="accent"
                      className="gap-1 px-3 py-1.5 text-[12px]"
                      disabled={addGeneratedMut.isPending}
                      aria-busy={addGeneratedMut.isPending}
                      onClick={() => addGeneratedMut.mutate()}
                    >
                      {addGeneratedMut.isPending ? (
                        <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                      ) : (
                        <Plus size={13} aria-hidden="true" />
                      )}
                      Add {aiPreview.length} problem{aiPreview.length === 1 ? '' : 's'}
                    </Pill>
                    <button
                      type="button"
                      className="text-[12px] text-[#888b91] hover:text-white"
                      onClick={() => setAiPreview([])}
                    >
                      Discard
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Manual */}
          {composerTab === 'manual' && (
        <form
          onSubmit={submit}
          className="space-y-3"
          aria-label="New coding question"
        >
          <textarea
            className={cn(inputCls, 'resize-y text-[13px]')}
            rows={3}
            placeholder="Problem statement — describe the task, input format, and output format…"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            aria-label="Problem statement"
          />

          {/* Allowed languages */}
          <div>
            <p className="mb-1.5 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]">
              Allowed languages
            </p>
            <div className="flex flex-wrap gap-1.5">
              {CODING_LANGUAGES.map((slug) => {
                const on = langs.includes(slug);
                return (
                  <button
                    key={slug}
                    type="button"
                    onClick={() => toggleLang(slug, !on)}
                    className={cn(
                      'rounded-full border px-2.5 py-0.5 text-[11.5px] transition-colors',
                      on
                        ? 'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]'
                        : 'border-white/[0.1] text-[#888b91] hover:text-white',
                    )}
                  >
                    {LANG_LABEL[slug]}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Starter code */}
          <div>
            <p className="mb-1.5 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]">
              Starter code (optional) — {LANG_LABEL[editorLang]}
            </p>
            <Suspense
              fallback={
                <div className="flex h-28 items-center justify-center rounded-[10px] border border-white/[0.08] bg-[#0b0c0e]">
                  <Loader2 className="h-5 w-5 animate-spin text-[#60a5fa]" aria-hidden="true" />
                </div>
              }
            >
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

          {/* Test cases */}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <p className="text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]">Test cases</p>
              <button
                type="button"
                onClick={() => setTests((p) => [...p, emptyTest()])}
                className="inline-flex items-center gap-1 text-[12px] text-[#60a5fa] hover:underline"
              >
                <Plus size={12} aria-hidden="true" /> Add test
              </button>
            </div>
            <div className="space-y-2">
              {tests.map((tc, ti) => (
                <div
                  key={ti}
                  className="rounded-[11px] border border-white/[0.08] bg-[rgba(28,29,31,0.5)] p-2.5"
                >
                  <div className="grid gap-2 sm:grid-cols-2">
                    <textarea
                      className={cn(inputCls, 'min-h-[48px] resize-y font-mono text-[12px]')}
                      placeholder="stdin"
                      value={tc.stdin}
                      onChange={(e) =>
                        setTests((p) =>
                          p.map((t, j) => (j === ti ? { ...t, stdin: e.target.value } : t)),
                        )
                      }
                      aria-label={`Test ${ti + 1} input`}
                    />
                    <textarea
                      className={cn(inputCls, 'min-h-[48px] resize-y font-mono text-[12px]')}
                      placeholder="expected stdout"
                      value={tc.expected_output}
                      onChange={(e) =>
                        setTests((p) =>
                          p.map((t, j) =>
                            j === ti ? { ...t, expected_output: e.target.value } : t,
                          ),
                        )
                      }
                      aria-label={`Test ${ti + 1} expected output`}
                    />
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2.5 text-[11.5px] text-[#888b91]">
                    <label className="flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        checked={tc.is_sample}
                        onChange={(e) =>
                          setTests((p) =>
                            p.map((t, j) =>
                              j === ti ? { ...t, is_sample: e.target.checked } : t,
                            ),
                          )
                        }
                        className="h-3.5 w-3.5 accent-[var(--accent)]"
                      />
                      Sample (shown to candidate)
                    </label>
                    <label className="flex items-center gap-1.5">
                      Weight
                      <input
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
                        className="w-12 rounded-[6px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-1.5 py-0.5 text-white focus:border-[var(--accent)] focus:outline-none"
                        aria-label={`Test ${ti + 1} weight`}
                      />
                    </label>
                    {tests.length > 1 && (
                      <button
                        type="button"
                        aria-label="Remove test case"
                        className="ml-auto text-[#888b91] transition-colors hover:text-[#e6714f]"
                        onClick={() => setTests((p) => p.filter((_, j) => j !== ti))}
                      >
                        <Trash2 size={12} aria-hidden="true" />
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Points + submit */}
          <div className="flex flex-wrap items-center gap-2">
            <Code2 size={15} className="text-[#60a5fa]" aria-hidden="true" />
            <label className="ml-auto flex items-center gap-1.5 text-[12px] text-[#888b91]">
              Points
              <input
                type="number"
                min={1}
                value={points}
                onChange={(e) => setPoints(e.target.value)}
                className="w-16 rounded-[7px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2 py-1 text-[12px] text-white focus:border-[var(--accent)] focus:outline-none"
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
                <Loader2 size={13} className="animate-spin" aria-hidden="true" />
              ) : (
                <Plus size={13} aria-hidden="true" />
              )}
              Add coding question
            </Pill>
          </div>
        </form>
          )}
        </div>
      )}
    </div>
  );
}
