// QuestionBanks (/hr/question-banks) — PH4-D1. The company's reusable
// question banks: create one, rename it, archive it, and see how many
// questions it holds at each stage of the review lifecycle and how many
// exams draw on it.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Library, Plus } from '@/design/components/icons';
import { GlassCard, StatusTag, type TagTone } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { toast } from '@/lib/toast';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import {
  archiveQuestionBank,
  createQuestionBank,
  listQuestionBanks,
  updateQuestionBank,
  type BankOut,
  type BankQuestionStatus,
} from '@/api/questionBanks';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const STATUS_TONE: Record<BankQuestionStatus, TagTone> = {
  draft: 'neutral',
  in_review: 'amber',
  approved: 'forest',
  retired: 'ember',
};

const STATUS_ORDER: BankQuestionStatus[] = ['draft', 'in_review', 'approved', 'retired'];

export default function QuestionBanks(): JSX.Element {
  const qc = useQueryClient();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ['hr', 'question-banks'],
    queryFn: listQuestionBanks,
  });

  const invalidate = () => void qc.invalidateQueries({ queryKey: ['hr', 'question-banks'] });

  const createMut = useMutation({
    mutationFn: () =>
      createQuestionBank({ name: name.trim(), description: description.trim() || null }),
    onSuccess: () => {
      toast.success('Bank created');
      setName('');
      setDescription('');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not create this bank')),
  });

  const renameMut = useMutation({
    mutationFn: ({ id, name: next }: { id: string; name: string }) =>
      updateQuestionBank(id, { name: next }),
    onSuccess: () => {
      toast.success('Bank renamed');
      setRenaming(null);
      invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not rename this bank')),
  });

  const archiveMut = useMutation({
    mutationFn: (id: string) => archiveQuestionBank(id),
    onSuccess: () => {
      toast.success('Bank archived');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not archive this bank')),
  });

  const banks: BankOut[] = data ?? [];

  return (
    <div className="mx-auto max-w-[900px] px-0 py-2 space-y-6">
      <Reveal>
        <div>
          <h1 className="text-[28px] font-semibold tracking-[-1px] text-foreground">
            Question banks
          </h1>
          <p className="mt-1 text-[14px] text-muted-foreground">
            A reusable library of MCQ and coding questions, reviewed once and then copied into
            any exam. A published round&apos;s copy never changes when the bank question is edited or
            superseded.
          </p>
        </div>
      </Reveal>

      <Reveal delay={0.05}>
        <GlassCard className="p-5 space-y-3">
          <h3 className="flex items-center gap-2 text-[15px] font-semibold text-foreground">
            <Plus size={16} aria-hidden="true" />
            New bank
          </h3>
          <form
            className="grid gap-2.5 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
            onSubmit={(e) => {
              e.preventDefault();
              if (!name.trim()) {
                toast.error('A name is required.');
                return;
              }
              createMut.mutate();
            }}
          >
            <div className="flex flex-col gap-1.5">
              <label htmlFor="bank-name" className="text-[12px] font-medium text-[var(--ui-soft)]">
                Name
              </label>
              <input
                id="bank-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Backend Engineer — Core"
                className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="bank-description"
                className="text-[12px] font-medium text-[var(--ui-soft)]"
              >
                Description (optional)
              </label>
              <input
                id="bank-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
              />
            </div>
            <button
              type="submit"
              disabled={createMut.isPending}
              className="rounded-[10px] bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground disabled:opacity-50"
            >
              {createMut.isPending ? 'Creating…' : 'Create'}
            </button>
          </form>
        </GlassCard>
      </Reveal>

      <Reveal delay={0.08}>
        <GlassCard className="p-5">
          <h3 className="mb-4 flex items-center gap-2 text-[15px] font-semibold text-foreground">
            <Library size={17} aria-hidden="true" />
            Banks
          </h3>
          {isLoading ? (
            <p className="text-[13px] text-muted-foreground">Loading…</p>
          ) : isError ? (
            <p className="text-[13px] text-[var(--ui-danger)]">Could not load question banks.</p>
          ) : banks.length === 0 ? (
            <p className="py-6 text-center text-[13px] text-muted-foreground">
              No banks yet — create your first one above.
            </p>
          ) : (
            <div role="list" aria-label="Question banks" className="flex flex-col gap-2">
              {banks.map((b) => (
                <div
                  key={b.id}
                  role="listitem"
                  className="rounded-[14px] border border-border bg-[var(--ui-inset-soft)] px-3.5 py-3"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      {renaming?.id === b.id ? (
                        <form
                          className="flex items-center gap-2"
                          onSubmit={(e) => {
                            e.preventDefault();
                            const next = renaming.name.trim();
                            if (next.length < 1) {
                              toast.error('A name is required.');
                              return;
                            }
                            renameMut.mutate({ id: b.id, name: next });
                          }}
                        >
                          <input
                            aria-label={`New name for ${b.name}`}
                            value={renaming.name}
                            onChange={(e) => setRenaming({ id: b.id, name: e.target.value })}
                            className="min-w-0 flex-1 rounded-[9px] border border-border bg-secondary px-2.5 py-1.5 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
                          />
                          <button
                            type="submit"
                            disabled={renameMut.isPending}
                            className="rounded-[9px] bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground disabled:opacity-50"
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            onClick={() => setRenaming(null)}
                            className="rounded-[9px] border border-border px-3 py-1.5 text-[12px] text-muted-foreground hover:text-foreground"
                          >
                            Cancel
                          </button>
                        </form>
                      ) : (
                        <Link
                          to={`/hr/question-banks/${b.id}`}
                          className="text-[14.5px] font-medium text-foreground hover:underline"
                        >
                          {b.name}
                        </Link>
                      )}
                      {b.description ? (
                        <p className="mt-0.5 text-[12px] text-muted-foreground">{b.description}</p>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {renaming?.id === b.id ? null : (
                        <button
                          type="button"
                          onClick={() => setRenaming({ id: b.id, name: b.name })}
                          className="rounded-[9px] border border-border px-3 py-1.5 text-[12px] text-muted-foreground hover:text-foreground"
                        >
                          Rename
                        </button>
                      )}
                      <ConfirmDeleteButton
                        label="Archive"
                        pending={archiveMut.isPending}
                        onConfirm={() => archiveMut.mutate(b.id)}
                      />
                    </div>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {STATUS_ORDER.map((status) =>
                      b.counts[status] ? (
                        <StatusTag key={status} tone={STATUS_TONE[status]} className="text-[10.5px]">
                          {b.counts[status]} {status.replace('_', ' ')}
                        </StatusTag>
                      ) : null,
                    )}
                    <span className="text-[11.5px] text-[var(--ui-faint)]">
                      Used in {b.used_in_exams} exam{b.used_in_exams === 1 ? '' : 's'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </GlassCard>
      </Reveal>
    </div>
  );
}
