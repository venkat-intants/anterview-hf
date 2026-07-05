// CodingAuthoring — HR panel to author coding questions for an exam of
// kind='coding'. Rendered by ExamEditor in place of the MCQ composer. Mirrors the
// MCQ authoring patterns (GlassCard, Pill, attempt-lock gate, react-query) but
// for coding: a prompt, allowed languages, optional starter code, a repeatable
// test-case editor (sample vs hidden), and points.

import { Suspense, lazy, useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2, Loader2, Lock, Code2 } from '@/design/components/icons';
import {
  listCodingQuestions,
  addCodingQuestion,
  deleteCodingQuestion,
  CODING_LANGUAGES,
  type CodingTestCase,
} from '@/api/exams';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { GlassCard, Pill, StatusTag } from '@/design/components/primitives';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';

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
  locked: boolean;
}

export default function CodingAuthoring({ examId, locked }: Props) {
  const qc = useQueryClient();
  const { data: questions = [] } = useQuery({
    queryKey: ['hr', 'exam', examId, 'coding-questions'],
    queryFn: () => listCodingQuestions(examId),
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['hr', 'exam', examId, 'coding-questions'] });
    void qc.invalidateQueries({ queryKey: ['hr', 'exam', examId] });
    void qc.invalidateQueries({ queryKey: ['hr', 'exams'] });
  };

  // ── Composer state ─────────────────────────────────────────────────────────
  const [prompt, setPrompt] = useState('');
  const [langs, setLangs] = useState<string[]>(['python']);
  const [starter, setStarter] = useState('');
  const [tests, setTests] = useState<CodingTestCase[]>([{ ...emptyTest(), is_sample: true }]);
  const [points, setPoints] = useState('100');

  const editorLang = useMemo(() => langs[0] ?? 'python', [langs]);

  const addMut = useMutation({
    mutationFn: () =>
      addCodingQuestion(examId, {
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
    mutationFn: (qid: string) => deleteCodingQuestion(examId, qid),
    onSuccess: () => refresh(),
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
    <Reveal delay={0.1}>
      <GlassCard className="mt-5 p-5">
        <p className="flex items-center gap-2 text-[14px] font-semibold text-white">
          <Code2 size={16} className="text-[#60a5fa]" aria-hidden="true" />
          Coding questions ({questions.length})
        </p>
        <p className="mt-0.5 text-[12.5px] text-[#888b91]">
          Each is solved in an editor and graded by running the code against your test cases.
        </p>

        <div className="mt-4 flex flex-col gap-3">
          {/* Existing coding questions */}
          {questions.length > 0 && (
            <Stagger className="flex flex-col gap-2.5">
              {questions.map((q, i) => {
                const sampleN = q.test_cases.filter((t) => t.is_sample).length;
                return (
                  <StaggerItem key={q.id}>
                    <div className="rounded-[16px] border border-white/[0.08] bg-[rgba(28,29,31,0.5)] p-4">
                      <div className="flex items-start gap-3">
                        <span className="flex h-7 w-7 flex-none items-center justify-center rounded-full bg-white/[0.06] font-mono text-[12px] text-[#b8babf]">
                          {i + 1}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="whitespace-pre-wrap text-[14px] font-medium leading-snug text-white">
                            {q.prompt}{' '}
                            <span className="font-normal text-[#888b91]">({q.points} pt)</span>
                          </p>
                          <div className="mt-2 flex flex-wrap items-center gap-1.5">
                            {q.allowed_languages.map((l) => (
                              <StatusTag key={l} tone="neutral" className="text-[10.5px]">
                                {LANG_LABEL[l] ?? l}
                              </StatusTag>
                            ))}
                            <span className="text-[11.5px] text-[#70757c]">
                              {q.test_cases.length} test{q.test_cases.length === 1 ? '' : 's'} ·{' '}
                              {sampleN} sample
                            </span>
                          </div>
                        </div>
                        {!locked && (
                          <button
                            type="button"
                            aria-label="Delete question"
                            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] border border-white/[0.1] text-[#888b91] transition-colors hover:border-[rgba(230,113,79,0.4)] hover:text-[#e6714f]"
                            onClick={() => delMut.mutate(q.id)}
                          >
                            <Trash2 size={14} aria-hidden="true" />
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
            <div className="flex items-center gap-2 rounded-[16px] border border-white/[0.08] bg-[rgba(28,29,31,0.3)] px-4 py-3 text-[13px] text-[#888b91]">
              <Lock size={15} aria-hidden="true" /> Questions are locked once attempts exist.
            </div>
          ) : (
            <form
              onSubmit={submit}
              className="space-y-3 rounded-[16px] border border-dashed border-white/[0.1] bg-[rgba(28,29,31,0.3)] p-4"
              aria-label="New coding question"
            >
              <textarea
                className={cn(inputCls, 'resize-y')}
                rows={3}
                placeholder="Problem statement — describe the task, input format, and output format…"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                aria-label="Problem statement"
              />

              {/* Allowed languages */}
              <div>
                <p className="mb-1.5 text-[12px] uppercase tracking-[0.5px] text-[#70757c]">
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
                          'rounded-full border px-3 py-1 text-[12px] transition-colors',
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
                <p className="mb-1.5 text-[12px] uppercase tracking-[0.5px] text-[#70757c]">
                  Starter code (optional) — highlighted as {LANG_LABEL[editorLang]}
                </p>
                <Suspense
                  fallback={
                    <div className="flex h-32 items-center justify-center rounded-[12px] border border-white/[0.08] bg-[#0b0c0e]">
                      <Loader2 className="h-5 w-5 animate-spin text-[#60a5fa]" aria-hidden="true" />
                    </div>
                  }
                >
                  <CodeEditor
                    language={editorLang}
                    value={starter}
                    onChange={setStarter}
                    minHeight={140}
                    placeholder="// pre-filled in the candidate's editor"
                    textareaId="ce-starter"
                  />
                </Suspense>
              </div>

              {/* Test cases */}
              <div>
                <div className="mb-1.5 flex items-center justify-between">
                  <p className="text-[12px] uppercase tracking-[0.5px] text-[#70757c]">Test cases</p>
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
                      className="rounded-[12px] border border-white/[0.08] bg-[rgba(28,29,31,0.5)] p-3"
                    >
                      <div className="grid gap-2 sm:grid-cols-2">
                        <textarea
                          className={cn(inputCls, 'min-h-[56px] resize-y font-mono text-[12.5px]')}
                          placeholder="stdin (input fed to the program)"
                          value={tc.stdin}
                          onChange={(e) =>
                            setTests((p) =>
                              p.map((t, j) => (j === ti ? { ...t, stdin: e.target.value } : t)),
                            )
                          }
                          aria-label={`Test ${ti + 1} input`}
                        />
                        <textarea
                          className={cn(inputCls, 'min-h-[56px] resize-y font-mono text-[12.5px]')}
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
                      <div className="mt-2 flex flex-wrap items-center gap-3 text-[12px] text-[#888b91]">
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
                            className="w-14 rounded-[7px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-1.5 py-1 text-white focus:border-[var(--accent)] focus:outline-none"
                          />
                        </label>
                        {tests.length > 1 && (
                          <button
                            type="button"
                            aria-label="Remove test case"
                            className="ml-auto text-[#888b91] transition-colors hover:text-[#e6714f]"
                            onClick={() => setTests((p) => p.filter((_, j) => j !== ti))}
                          >
                            <Trash2 size={13} aria-hidden="true" />
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                <p className="mt-1.5 text-[11.5px] text-[#70757c]">
                  Hidden (non-sample) tests grade the candidate without revealing the answer.
                </p>
              </div>

              {/* Points + submit */}
              <div className="flex flex-wrap items-center gap-2">
                <label className="ml-auto flex items-center gap-2 text-[12px] text-[#888b91]">
                  Points
                  <input
                    type="number"
                    min={1}
                    value={points}
                    onChange={(e) => setPoints(e.target.value)}
                    className="w-20 rounded-[8px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2 py-1 text-[13px] text-white focus:border-[var(--accent)] focus:outline-none"
                    aria-label="Points"
                  />
                </label>
                <Pill
                  type="submit"
                  variant="accent"
                  className="gap-1.5 px-4 py-2"
                  disabled={addMut.isPending}
                  aria-busy={addMut.isPending}
                >
                  {addMut.isPending ? (
                    <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Plus size={14} aria-hidden="true" />
                  )}
                  Add coding question
                </Pill>
              </div>
            </form>
          )}
        </div>
      </GlassCard>
    </Reveal>
  );
}
