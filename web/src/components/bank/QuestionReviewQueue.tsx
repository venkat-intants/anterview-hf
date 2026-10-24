// QuestionReviewQueue — PH4-D1. The shared body of the HR and super-admin
// review queues. The rules are the same on both: approve, or request changes
// with a note of at least 10 characters (the reason length the other review
// queues ask for; the server's own floor is 5, so this is never looser), and a
// reviewer's own question is disabled with the reason shown. The endpoints
// differ: HR reviews through `/hr` and the super admin through `/admin`, so a
// single-HR company is never blocked on a second HR approver. See
// pages/hr/QuestionReviews.tsx and pages/superadmin/QuestionReviews.tsx.
//
// The decision itself is ReviewParts' DecisionPanel, so Approve is the primary
// action here as on every other review surface (audit AD-F12). It renders
// bare, without its own card and heading, because it sits inside each row.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { Check, CheckCircle2 } from '@/design/components/icons';
import { StatusTag } from '@/design/components/primitives';
import { Card } from '@/design/components/card';
import { Collapsible, ProgressiveList } from '@/design/components/collapsible';
import { EmptyState } from '@/design/components/page';
import { BankCodingQuestionView } from '@/components/hr/bank/BankCodingQuestionView';
import { QuestionBadges } from '@/components/bank/QuestionBadges';
import { QueryState } from '@/components/QueryState';
import { DecisionPanel, type DecisionMode } from '@/components/superadmin/ReviewParts';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/errorMessage';
import { toast } from '@/lib/toast';
import { questionBrief } from '@/lib/bankFlow';
import type { BankQuestion, ReviewQueueRow } from '@/api/questionBanks';

/** The reason length every review queue asks for (ReviewParts' callers use 10). */
const CHANGES_NOTE_MIN = 10;
/** Mirrors the server's own cap so a save never round-trips into a 422. */
const NOTE_MAX_LENGTH = 1000;

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
  const [mode, setMode] = useState<DecisionMode>(null);
  const [note, setNote] = useState('');

  const approveMut = useMutation({
    mutationFn: () => approve(row.id, note || null),
    onSuccess: () => {
      toast.success('Approved');
      setMode(null);
      setNote('');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not approve this question.')),
  });

  const changesMut = useMutation({
    mutationFn: () => requestChanges(row.id, note),
    onSuccess: () => {
      toast.success('Sent back with your note');
      setMode(null);
      setNote('');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not send this back.')),
  });

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
      ) : (
        <div className="mt-3">
          <DecisionPanel
            mode={mode}
            onMode={setMode}
            note={note}
            // The panel's input has no cap of its own; the server's is 1000.
            onNote={(v) => setNote(v.slice(0, NOTE_MAX_LENGTH))}
            noteId={`review-note-${row.id}`}
            rejectNoteMin={CHANGES_NOTE_MIN}
            rejectLabel="Request changes"
            approvePending={approveMut.isPending}
            rejectPending={changesMut.isPending}
            onApprove={() => approveMut.mutate()}
            onReject={() => changesMut.mutate()}
            variant="bare"
            description="Approving adds this question to the bank. Sending it back needs a reason, which the author will see."
          />
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
  // The queue, and the bank views an approval or a send-back changes: a
  // bank's status counts and its question list (audit E20). They are HR keys;
  // on the super admin's console they are simply not mounted.
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey });
    void qc.invalidateQueries({ queryKey: ['hr', 'question-banks'] });
    void qc.invalidateQueries({ queryKey: ['hr', 'bank-questions'] });
  };

  return (
    <QueryState
      query={queue}
      errorTitle="Could not load the question review queue."
      loading={
        <div className="flex flex-col gap-3" aria-busy="true">
          <span className="sr-only">Loading…</span>
          <Skeleton className="h-28 rounded-card" />
          <Skeleton className="h-28 rounded-card" />
        </div>
      }
      isEmpty={(rows) => rows.length === 0}
      empty={
        <EmptyState
          icon={CheckCircle2}
          title="Nothing is waiting on you."
          description={emptyHint}
        />
      }
    >
      {(rows) => (
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
      )}
    </QueryState>
  );
}
