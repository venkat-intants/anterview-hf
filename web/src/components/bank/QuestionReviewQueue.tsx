// QuestionReviewQueue — PH4-D1. The shared body of the HR and super-admin
// review queues: same rules (approve, or request changes with a note of at
// least 5 characters; disable a reviewer's own question with the reason
// shown), different endpoints — HR reviews through `/hr`, the super admin
// through `/admin`, so a single-HR company is never blocked on a second HR
// approver. See pages/hr/QuestionReviews.tsx and
// pages/superadmin/QuestionReviews.tsx.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { Check, CheckCircle2, ClipboardCheck, Loader2, XCircle } from '@/design/components/icons';
import { StatusTag } from '@/design/components/primitives';
import { Card } from '@/design/components/card';
import { Collapsible, ProgressiveList } from '@/design/components/collapsible';
import { EmptyState } from '@/design/components/page';
import { BankCodingQuestionView } from '@/components/hr/bank/BankCodingQuestionView';
import { QuestionBadges } from '@/components/bank/QuestionBadges';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/lib/toast';
import { questionBrief } from '@/lib/bankFlow';
import type { BankQuestion, ReviewQueueRow } from '@/api/questionBanks';

const CHANGES_NOTE_MIN = 5;
/** Mirrors the server's own cap so a save never round-trips into a 422. */
const NOTE_MAX_LENGTH = 1000;

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

interface RowProps {
  row: ReviewQueueRow;
  approve: (id: string, note?: string | null) => Promise<BankQuestion>;
  requestChanges: (id: string, note: string) => Promise<BankQuestion>;
  invalidate: () => void;
  /** The first coding question opens with its tests and code showing. */
  openContent: boolean;
}

/** What a reviewer reads before deciding: the whole question, not a title. */
function ReviewContent({ row, open }: { row: ReviewQueueRow; open: boolean }): JSX.Element {
  if (row.kind === 'coding') {
    const tests = row.test_cases ?? [];
    const samples = tests.filter((t) => t.is_sample).length;
    return (
      <Collapsible
        title="Statement, tests and code"
        summary={`${tests.length} test case${tests.length === 1 ? '' : 's'} (${samples} sample, ${tests.length - samples} hidden)`}
        defaultOpen={open}
        headingLevel="h4"
        data-testid="review-content"
      >
        <BankCodingQuestionView question={row} headingAs="h5" />
      </Collapsible>
    );
  }
  return (
    <div data-testid="review-content" className="flex flex-col gap-2">
      <ol className="flex flex-col gap-1" aria-label="Options">
        {(row.options ?? []).map((o, i) => (
          <li key={i} className="flex items-start gap-2 text-[13.5px] text-foreground">
            <span className="font-semibold tabular-nums text-muted-foreground">
              {String.fromCharCode(65 + i)}.
            </span>
            <span className="min-w-0 flex-1 break-words">{o}</span>
            {i === row.correct_index ? (
              <span className="inline-flex flex-none items-center gap-1 text-[12.5px] font-semibold text-[var(--ui-ok)]">
                <Check size={13} aria-hidden="true" />
                Correct answer
              </span>
            ) : null}
          </li>
        ))}
      </ol>
    </div>
  );
}

function ReviewRow({ row, approve, requestChanges, invalidate, openContent }: RowProps) {
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
    <Card as="li" density="compact" data-testid="review-row" className="list-none">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="min-w-0 whitespace-pre-wrap break-words text-[14px] font-semibold text-foreground">
            {row.kind === 'coding' ? questionBrief(row.prompt, 140) : row.prompt}
          </h3>
          <p className="mt-1 text-[13px] text-muted-foreground">
            {row.bank_name} · v{row.version} · {row.difficulty} · {row.language}
          </p>
          <QuestionBadges question={row} className="mt-2" />
        </div>
        <StatusTag tone="amber">in review</StatusTag>
      </div>

      <div className="mt-3">
        <ReviewContent row={row} open={openContent} />
      </div>

      {row.own_submission ? (
        <p className="mt-3 rounded-[12px] bg-[var(--ui-inset-soft)] px-3.5 py-2.5 text-[13px] text-muted-foreground">
          {row.reason ?? 'You wrote or submitted this question - another reviewer must decide it.'}
        </p>
      ) : mode === null ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => setMode('approve')}
            className="border-[var(--ui-ok)]/40 text-[var(--ui-ok)] hover:text-[var(--ui-ok)]"
          >
            <CheckCircle2 aria-hidden="true" />
            Approve
          </Button>
          <Button type="button" variant="outline" onClick={() => setMode('changes')}>
            <XCircle aria-hidden="true" />
            Request changes
          </Button>
        </div>
      ) : (
        <div className="mt-3 flex flex-col gap-2">
          <label
            htmlFor={`review-note-${row.id}`}
            className="text-[13px] font-semibold text-foreground"
          >
            {mode === 'approve' ? 'Note (optional)' : 'What needs to change (required)'}
          </label>
          <Input
            id={`review-note-${row.id}`}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={mode === 'changes' ? `At least ${CHANGES_NOTE_MIN} characters` : undefined}
            maxLength={NOTE_MAX_LENGTH}
            aria-invalid={mode === 'changes' && !changesReady ? true : undefined}
          />
          {mode === 'changes' && !changesReady ? (
            <p className="text-[12.5px] text-[var(--ui-warn)]">
              Write at least {CHANGES_NOTE_MIN} characters.
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              disabled={
                (mode === 'approve' ? approveMut.isPending : changesMut.isPending) ||
                (mode === 'changes' && !changesReady)
              }
              onClick={() => (mode === 'approve' ? approveMut.mutate() : changesMut.mutate())}
            >
              {(mode === 'approve' ? approveMut.isPending : changesMut.isPending) ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : null}
              {mode === 'approve' ? 'Confirm approval' : 'Send back'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setMode(null);
                setNote('');
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
    </Card>
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
      <div className="flex flex-col gap-3" aria-busy="true">
        <span className="sr-only">Loading…</span>
        <Skeleton className="h-28 rounded-card" />
        <Skeleton className="h-28 rounded-card" />
      </div>
    );
  }
  if (queue.isError) {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-3">
        <p className="text-[14px] text-[var(--ui-danger)]">
          Could not load the question review queue.
        </p>
        <Button type="button" variant="outline" onClick={() => void queue.refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  const rows = queue.data ?? [];
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={ClipboardCheck}
        title="Nothing is waiting for review."
        description={emptyHint}
      />
    );
  }
  return (
    <ProgressiveList
      items={rows}
      initial={5}
      noun="questions"
      aria-label="Questions waiting for review"
      className="flex flex-col gap-3"
      getKey={(row) => row.id}
      renderItem={(row, i) => (
        <ReviewRow
          row={row}
          approve={approve}
          requestChanges={requestChanges}
          invalidate={invalidate}
          openContent={i === 0}
        />
      )}
    />
  );
}
