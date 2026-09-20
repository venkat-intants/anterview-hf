// QuestionReviewQueue — PH4-D1. The shared body of the HR and super-admin
// review queues: same rules (approve, or request changes with a note of at
// least 5 characters; disable a reviewer's own question with the reason
// shown), different endpoints — HR reviews through `/hr`, the super admin
// through `/admin`, so a single-HR company is never blocked on a second HR
// approver. See pages/hr/QuestionReviews.tsx and
// pages/superadmin/QuestionReviews.tsx.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { CheckCircle2, ClipboardCheck, Code2, Loader2, XCircle } from '@/design/components/icons';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { toast } from '@/lib/toast';
import type { BankQuestion, ReviewQueueRow } from '@/api/questionBanks';

const CHANGES_NOTE_MIN = 5;

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

interface RowProps {
  row: ReviewQueueRow;
  approve: (id: string, note?: string | null) => Promise<BankQuestion>;
  requestChanges: (id: string, note: string) => Promise<BankQuestion>;
  invalidate: () => void;
}

function ReviewRow({ row, approve, requestChanges, invalidate }: RowProps) {
  const [mode, setMode] = useState<'approve' | 'changes' | null>(null);
  const [note, setNote] = useState('');

  const approveMut = useMutation({
    mutationFn: () => approve(row.id, note || null),
    onSuccess: () => {
      toast.success('Approved');
      setMode(null);
      setNote('');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not approve this question')),
  });

  const changesMut = useMutation({
    mutationFn: () => requestChanges(row.id, note),
    onSuccess: () => {
      toast.success('Sent back with your note');
      setMode(null);
      setNote('');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not send this back')),
  });

  const changesReady = note.trim().length >= CHANGES_NOTE_MIN;

  return (
    <GlassCard className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            {row.kind === 'coding' ? (
              <Code2 className="h-4 w-4 shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
            ) : (
              <ClipboardCheck className="h-4 w-4 shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
            )}
            <p className="truncate text-[14px] font-medium text-foreground">{row.prompt}</p>
          </div>
          <p className="mt-1 text-[12px] text-muted-foreground">
            {row.bank_name} · v{row.version} · {row.difficulty} · {row.language}
          </p>
        </div>
        <StatusTag tone="amber">in review</StatusTag>
      </div>

      {row.own_submission ? (
        <p className="mt-3 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2 text-[12.5px] text-muted-foreground">
          {row.reason ?? 'You wrote or submitted this question — another reviewer must decide it.'}
        </p>
      ) : mode === null ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setMode('approve')}
            className="inline-flex items-center gap-1.5 rounded-[9px] border border-[var(--ui-ok)]/35 px-3.5 py-1.5 text-[12.5px] font-medium text-[var(--ui-ok)] hover:bg-[var(--ui-ok)]/10"
          >
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
            Approve
          </button>
          <button
            type="button"
            onClick={() => setMode('changes')}
            className="inline-flex items-center gap-1.5 rounded-[9px] border border-[var(--ui-line-strong)] px-3.5 py-1.5 text-[12.5px] text-muted-foreground hover:border-[var(--ui-warn)]/40 hover:text-[var(--ui-warn)]"
          >
            <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
            Request changes
          </button>
        </div>
      ) : (
        <div className="mt-3 flex flex-col gap-2">
          <label
            htmlFor={`review-note-${row.id}`}
            className="text-[12px] font-medium text-[var(--ui-soft)]"
          >
            {mode === 'approve' ? 'Note (optional)' : 'What needs to change (required)'}
          </label>
          <input
            id={`review-note-${row.id}`}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={mode === 'changes' ? `At least ${CHANGES_NOTE_MIN} characters` : undefined}
            className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
          />
          {mode === 'changes' && !changesReady ? (
            <p className="text-[11.5px] text-[var(--ui-warn)]">
              Write at least {CHANGES_NOTE_MIN} characters.
            </p>
          ) : null}
          <div className="flex gap-2">
            <button
              type="button"
              disabled={
                (mode === 'approve' ? approveMut.isPending : changesMut.isPending) ||
                (mode === 'changes' && !changesReady)
              }
              onClick={() => (mode === 'approve' ? approveMut.mutate() : changesMut.mutate())}
              className="inline-flex items-center gap-1.5 rounded-[9px] bg-primary px-3.5 py-1.5 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
            >
              {(mode === 'approve' ? approveMut.isPending : changesMut.isPending) ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : null}
              {mode === 'approve' ? 'Confirm approval' : 'Send back'}
            </button>
            <button
              type="button"
              onClick={() => {
                setMode(null);
                setNote('');
              }}
              className="rounded-[9px] border border-border px-3.5 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </GlassCard>
  );
}

export interface QuestionReviewQueueProps {
  queryKey: QueryKey;
  fetchQueue: () => Promise<ReviewQueueRow[]>;
  approve: (id: string, note?: string | null) => Promise<BankQuestion>;
  requestChanges: (id: string, note: string) => Promise<BankQuestion>;
  emptyHint: string;
}

export function QuestionReviewQueue({
  queryKey,
  fetchQueue,
  approve,
  requestChanges,
  emptyHint,
}: QuestionReviewQueueProps): JSX.Element {
  const qc = useQueryClient();
  const queue = useQuery({ queryKey, queryFn: fetchQueue });
  const invalidate = () => void qc.invalidateQueries({ queryKey });

  if (queue.isLoading) {
    return (
      <p className="mt-6 flex items-center gap-2 text-[13px] text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        Loading…
      </p>
    );
  }
  if (queue.isError) {
    return (
      <p className="mt-6 text-[13px] text-[var(--ui-danger)]">
        Could not load the question review queue.
      </p>
    );
  }
  const rows = queue.data ?? [];
  if (rows.length === 0) {
    return (
      <GlassCard className="mt-6 p-6 text-center">
        <p className="text-[13.5px] text-foreground">Nothing is waiting for review.</p>
        <p className="mt-1 text-[12.5px] text-muted-foreground">{emptyHint}</p>
      </GlassCard>
    );
  }
  return (
    <div className="mt-6 flex flex-col gap-3">
      {rows.map((row) => (
        <ReviewRow
          key={row.id}
          row={row}
          approve={approve}
          requestChanges={requestChanges}
          invalidate={invalidate}
        />
      ))}
    </div>
  );
}
