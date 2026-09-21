// QuestionBankDetail (/hr/question-banks/:bankId) — PH4-D1. A filterable
// table of one bank's questions, an editor for MCQ and coding content, and
// per-question lifecycle actions: submit, withdraw, new version, retire —
// plus its version history and where it is used.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  ClipboardList,
  Code2,
  Loader2,
  Plus,
} from '@/design/components/icons';
import { GlassCard, StatusTag, type TagTone } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { toast } from '@/lib/toast';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { cn } from '@/lib/utils';
import { useAuth } from '@/context/AuthContext';
import { BankQuestionEditor } from '@/components/bank/BankQuestionEditor';
import {
  createBankQuestion,
  deleteBankQuestion,
  getBankQuestion,
  listBankCompetencies,
  listBankQuestions,
  listQuestionBanks,
  newBankQuestionVersion,
  retireBankQuestion,
  submitBankQuestion,
  updateBankQuestion,
  withdrawBankQuestion,
  type BankDifficulty,
  type BankQuestion,
  type BankQuestionFilters,
  type BankQuestionInput,
  type BankQuestionKind,
  type BankQuestionStatus,
} from '@/api/questionBanks';
import type { ExamLanguage } from '@/api/exams';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const STATUS_TONE: Record<BankQuestionStatus, TagTone> = {
  draft: 'neutral',
  in_review: 'amber',
  approved: 'forest',
  retired: 'ember',
};

const inputCls =
  'rounded-[9px] border border-border bg-secondary px-2.5 py-1.5 text-[12.5px] text-foreground ' +
  'placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none';

function FilterBar({
  filters,
  onChange,
}: {
  filters: BankQuestionFilters;
  onChange: (next: BankQuestionFilters) => void;
}) {
  const catalog = useQuery({ queryKey: ['hr', 'bank-competencies'], queryFn: listBankCompetencies });
  return (
    <div className="flex flex-wrap gap-2">
      <input
        value={filters.q ?? ''}
        onChange={(e) => onChange({ ...filters, q: e.target.value })}
        placeholder="Search prompt…"
        aria-label="Search"
        className={cn(inputCls, 'min-w-[160px] flex-1')}
      />
      <select
        value={filters.kind ?? ''}
        onChange={(e) => onChange({ ...filters, kind: (e.target.value || undefined) as BankQuestionKind | undefined })}
        aria-label="Filter by kind"
        className={inputCls}
      >
        <option value="">Any kind</option>
        <option value="mcq">MCQ</option>
        <option value="coding">Coding</option>
      </select>
      <select
        value={filters.difficulty ?? ''}
        onChange={(e) =>
          onChange({ ...filters, difficulty: (e.target.value || undefined) as BankDifficulty | undefined })
        }
        aria-label="Filter by difficulty"
        className={inputCls}
      >
        <option value="">Any difficulty</option>
        <option value="easy">Easy</option>
        <option value="medium">Medium</option>
        <option value="hard">Hard</option>
      </select>
      <select
        value={filters.language ?? ''}
        onChange={(e) =>
          onChange({ ...filters, language: (e.target.value || undefined) as ExamLanguage | undefined })
        }
        aria-label="Filter by language"
        className={inputCls}
      >
        <option value="">Any language</option>
        <option value="en">English</option>
        <option value="hi">हिन्दी</option>
        <option value="te">తెలుగు</option>
      </select>
      <select
        value={filters.competency ?? ''}
        onChange={(e) => onChange({ ...filters, competency: e.target.value || undefined })}
        aria-label="Filter by competency"
        className={inputCls}
      >
        <option value="">Any competency</option>
        {(catalog.data ?? []).map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
      <input
        value={filters.tag ?? ''}
        onChange={(e) => onChange({ ...filters, tag: e.target.value || undefined })}
        placeholder="Tag…"
        aria-label="Filter by tag"
        className={cn(inputCls, 'w-24')}
      />
      <select
        value={filters.status ?? ''}
        onChange={(e) =>
          onChange({ ...filters, status: (e.target.value || undefined) as BankQuestionStatus | undefined })
        }
        aria-label="Filter by status"
        className={inputCls}
      >
        <option value="">Any status</option>
        <option value="draft">Draft</option>
        <option value="in_review">In review</option>
        <option value="approved">Approved</option>
        <option value="retired">Retired</option>
      </select>
    </div>
  );
}

function QuestionRow({
  q,
  selected,
  onSelect,
}: {
  q: BankQuestion;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        'flex w-full items-start gap-2.5 rounded-[12px] border px-3 py-2.5 text-left transition-colors',
        selected
          ? 'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.08)]'
          : 'border-border hover:border-[var(--ui-line-strong)]',
      )}
    >
      {q.kind === 'coding' ? (
        <Code2 className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
      ) : (
        <ClipboardList className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-medium text-foreground">{q.prompt}</p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <StatusTag tone={STATUS_TONE[q.status]} className="text-[10px]">
            {q.status.replace('_', ' ')}
          </StatusTag>
          <span className="text-[11px] text-muted-foreground">
            v{q.version} · {q.difficulty} · {q.language}
          </span>
        </div>
      </div>
    </button>
  );
}

export default function QuestionBankDetail(): JSX.Element {
  const { bankId = '' } = useParams();
  const qc = useQueryClient();
  const { user } = useAuth();
  const [filters, setFilters] = useState<BankQuestionFilters>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // PH4-D1 review finding — approved/retired content had no editable path at
  // all ("New version" was unreachable). True while the user is editing an
  // approved/retired question's content to create the next draft version.
  const [newVersionMode, setNewVersionMode] = useState(false);

  function selectQuestion(id: string | null) {
    setCreating(false);
    setSelectedId(id);
    setNewVersionMode(false);
  }

  const banksQuery = useQuery({ queryKey: ['hr', 'question-banks'], queryFn: listQuestionBanks });
  const bank = (banksQuery.data ?? []).find((b) => b.id === bankId);

  const listKey = ['hr', 'question-banks', bankId, 'questions', filters];
  const listQuery = useQuery({
    queryKey: listKey,
    queryFn: () => listBankQuestions(bankId, filters),
    enabled: Boolean(bankId),
  });

  const detailQuery = useQuery({
    queryKey: ['hr', 'bank-questions', selectedId],
    queryFn: () => getBankQuestion(selectedId as string),
    enabled: Boolean(selectedId),
  });

  const invalidateAll = () => {
    void qc.invalidateQueries({ queryKey: listKey });
    void qc.invalidateQueries({ queryKey: ['hr', 'question-banks'] });
    if (selectedId) void qc.invalidateQueries({ queryKey: ['hr', 'bank-questions', selectedId] });
  };

  const createMut = useMutation({
    mutationFn: (input: BankQuestionInput) => createBankQuestion(bankId, input),
    onSuccess: (q) => {
      toast.success('Question created');
      setCreating(false);
      setSelectedId(q.id);
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not create this question')),
  });

  const updateMut = useMutation({
    mutationFn: (input: BankQuestionInput) =>
      updateBankQuestion(selectedId as string, input),
    onSuccess: () => {
      toast.success('Saved');
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not save this question')),
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteBankQuestion(selectedId as string),
    onSuccess: () => {
      toast.success('Question deleted');
      setSelectedId(null);
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not delete this question')),
  });

  const submitMut = useMutation({
    mutationFn: () => submitBankQuestion(selectedId as string),
    onSuccess: () => {
      toast.success('Submitted for review');
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not submit this question')),
  });

  const withdrawMut = useMutation({
    mutationFn: () => withdrawBankQuestion(selectedId as string),
    onSuccess: () => {
      toast.success('Withdrawn to draft');
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not withdraw this question')),
  });

  const retireMut = useMutation({
    mutationFn: () => retireBankQuestion(selectedId as string),
    onSuccess: () => {
      toast.success('Retired');
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not retire this question')),
  });

  const newVersionMut = useMutation({
    mutationFn: (input: BankQuestionInput) => newBankQuestionVersion(selectedId as string, input),
    onSuccess: (q) => {
      toast.success(`Version ${q.version} created as a draft`);
      setSelectedId(q.id);
      setNewVersionMode(false);
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not create a new version')),
  });

  const questions = listQuery.data ?? [];
  const selected = detailQuery.data?.question ?? null;

  return (
    <div className="mx-auto max-w-[1200px] px-6 py-8 lg:px-8">
      <Reveal>
        <Link
          to="/hr/question-banks"
          className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft size={15} aria-hidden="true" /> Question banks
        </Link>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-[24px] font-semibold tracking-[-0.6px] text-foreground">
              {bank?.name ?? 'Question bank'}
            </h1>
            {bank ? (
              <p className="mt-1 text-[12.5px] text-muted-foreground">
                Used in {bank.used_in_exams} exam{bank.used_in_exams === 1 ? '' : 's'}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={() => {
              setCreating(true);
              setSelectedId(null);
              setNewVersionMode(false);
            }}
            className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground"
          >
            <Plus size={15} aria-hidden="true" /> New question
          </button>
        </div>
      </Reveal>

      <div className="mt-6 grid gap-5 lg:grid-cols-[minmax(0,1fr)_420px]">
        <GlassCard className="p-5">
          <FilterBar filters={filters} onChange={setFilters} />
          <div className="mt-4">
            {listQuery.isLoading ? (
              <p className="flex items-center gap-2 py-6 text-[13px] text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Loading…
              </p>
            ) : listQuery.isError ? (
              <p className="py-6 text-[13px] text-[var(--ui-danger)]">
                Could not load this bank&apos;s questions.
              </p>
            ) : questions.length === 0 ? (
              <p className="py-8 text-center text-[13px] text-muted-foreground">
                No questions match these filters yet.
              </p>
            ) : (
              <div className="flex flex-col gap-2">
                {questions.map((q) => (
                  <QuestionRow
                    key={q.id}
                    q={q}
                    selected={selectedId === q.id}
                    onSelect={() => selectQuestion(q.id)}
                  />
                ))}
              </div>
            )}
          </div>
        </GlassCard>

        <GlassCard className="h-fit p-5">
          {creating ? (
            <>
              <h2 className="mb-3 text-[15px] font-semibold text-foreground">New question</h2>
              <BankQuestionEditor
                saving={createMut.isPending}
                onSave={(input) => createMut.mutate(input)}
                onCancel={() => setCreating(false)}
              />
            </>
          ) : !selectedId ? (
            <p className="text-[13px] text-muted-foreground">
              Select a question to view or edit it, or create a new one.
            </p>
          ) : detailQuery.isLoading ? (
            <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Loading…
            </p>
          ) : detailQuery.isError || !selected ? (
            <p className="text-[13px] text-[var(--ui-danger)]">Could not load this question.</p>
          ) : (
            <div className="flex flex-col gap-4">
              <div className="flex items-center justify-between">
                <h2 className="text-[15px] font-semibold text-foreground">
                  {selected.kind === 'mcq' ? 'MCQ' : 'Coding'} question · v{selected.version}
                </h2>
                <StatusTag tone={STATUS_TONE[selected.status]}>
                  {selected.status.replace('_', ' ')}
                </StatusTag>
              </div>

              {selected.review_note ? (
                <p className="rounded-[10px] border border-[var(--ui-warn)]/30 bg-[rgba(255,183,100,0.08)] px-3 py-2 text-[12.5px] text-[var(--ui-soft)]">
                  Review note: {selected.review_note}
                </p>
              ) : null}

              {newVersionMode ? (
                <p className="rounded-[10px] border border-[rgba(var(--accent-rgb),0.35)] bg-[rgba(var(--accent-rgb),0.08)] px-3 py-2 text-[12.5px] text-[var(--ui-soft)]">
                  Saving creates version {selected.version + 1} as a new draft — this{' '}
                  {selected.status} version is unchanged.
                </p>
              ) : null}

              <BankQuestionEditor
                question={selected}
                saving={updateMut.isPending || newVersionMut.isPending}
                forceEditable={newVersionMode}
                onCancel={newVersionMode ? () => setNewVersionMode(false) : undefined}
                onSave={(input) =>
                  selected.status === 'draft'
                    ? updateMut.mutate(input)
                    : newVersionMut.mutate(input)
                }
              />

              {!newVersionMode ? (
                <div className="flex flex-wrap gap-2 border-t border-border pt-3">
                  {selected.status === 'draft' ? (
                    <>
                      <button
                        type="button"
                        disabled={submitMut.isPending}
                        onClick={() => submitMut.mutate()}
                        className="rounded-[9px] bg-primary px-3.5 py-1.5 text-[12.5px] font-medium text-primary-foreground disabled:opacity-50"
                      >
                        Submit for review
                      </button>
                      <ConfirmDeleteButton
                        label="Delete"
                        pending={deleteMut.isPending}
                        onConfirm={() => deleteMut.mutate()}
                      />
                    </>
                  ) : null}
                  {selected.status === 'in_review' && selected.submitted_by_user_id === user?.user_id ? (
                    // Only the submitter may withdraw — the server 403s anyone
                    // else with "Only the person who submitted this question can
                    // withdraw it." Gating the control means that refusal is
                    // never something another reviewer runs into by surprise.
                    <button
                      type="button"
                      disabled={withdrawMut.isPending}
                      onClick={() => withdrawMut.mutate()}
                      className="rounded-[9px] border border-border px-3.5 py-1.5 text-[12.5px] text-foreground disabled:opacity-50"
                    >
                      Withdraw
                    </button>
                  ) : null}
                  {selected.status === 'approved' ? (
                    <button
                      type="button"
                      disabled={retireMut.isPending}
                      onClick={() => retireMut.mutate()}
                      className="rounded-[9px] border border-border px-3.5 py-1.5 text-[12.5px] text-foreground disabled:opacity-50"
                    >
                      Retire
                    </button>
                  ) : null}
                  {selected.status === 'approved' || selected.status === 'retired' ? (
                    <button
                      type="button"
                      onClick={() => setNewVersionMode(true)}
                      className="rounded-[9px] border border-border px-3.5 py-1.5 text-[12.5px] text-foreground"
                    >
                      New version
                    </button>
                  ) : null}
                </div>
              ) : null}

              {detailQuery.data && detailQuery.data.versions.length > 1 ? (
                <div className="border-t border-border pt-3">
                  <h3 className="mb-2 text-[12.5px] font-semibold uppercase tracking-[0.5px] text-[var(--ui-faint)]">
                    Version history
                  </h3>
                  <ul className="flex flex-col gap-1.5">
                    {detailQuery.data.versions.map((v) => (
                      <li key={v.id}>
                        <button
                          type="button"
                          onClick={() => selectQuestion(v.id)}
                          className={cn(
                            'flex w-full items-center justify-between rounded-[9px] border px-2.5 py-1.5 text-[12px]',
                            v.id === selectedId
                              ? 'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.08)]'
                              : 'border-border text-muted-foreground hover:text-foreground',
                          )}
                        >
                          <span>v{v.version}</span>
                          <StatusTag tone={STATUS_TONE[v.status]} className="text-[10px]">
                            {v.status.replace('_', ' ')}
                          </StatusTag>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {detailQuery.data && detailQuery.data.used_in_exams.length > 0 ? (
                <div className="border-t border-border pt-3">
                  <h3 className="mb-2 text-[12.5px] font-semibold uppercase tracking-[0.5px] text-[var(--ui-faint)]">
                    Used in {detailQuery.data.used_in_exams.length} exam
                    {detailQuery.data.used_in_exams.length === 1 ? '' : 's'}
                  </h3>
                  <ul className="flex flex-col gap-1">
                    {detailQuery.data.used_in_exams.map((u) => (
                      <li key={u.exam_id}>
                        <Link
                          to={`/hr/exams/${u.exam_id}`}
                          className="text-[12.5px] text-[var(--ui-info)] hover:underline"
                        >
                          {u.exam_title}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          )}
        </GlassCard>
      </div>
    </div>
  );
}
