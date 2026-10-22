// ApprovalQueue — the openings this company's HR managers want to open. PH3-B2.
//
// For the company super admin, and only for them: the endpoint behind this
// requires the super_admin role, and a user holds exactly one role, so an HR
// manager cannot reach it. That is why there is no "you cannot approve your own"
// check anywhere — the person who raised the requisition and the person who
// decides on it are structurally different accounts.
//
// OLDEST FIRST, always. An approval queue is a waiting list, and sorting by
// anything else buries whoever has been waiting longest.
//
// THE BUDGET LEADS THE CARD. "Should this opening exist?" is not answerable
// without knowing what it costs, which is why budget and approval landed in one
// story rather than two.
//
// THE DECISION IS THE SHARED DecisionPanel (audit AD-F12), as on the workflow,
// offer and question queues: Approve is two steps with an optional note,
// sending back needs a reason of CHANGES_NOTE_MIN characters, and the two notes
// are separate (a half-typed rejection note used to become the approval note).
// The API itself does not require the reason; the UI asks for one.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  approveRequisition,
  listPendingApprovals,
  rejectRequisition,
  type PendingApproval,
} from '@/api/requisitions';
import { toast } from '@/lib/toast';
import { errorMessage } from '@/lib/errorMessage';
import { GlassCard } from '@/design/components/primitives';
import { DataList, Page, PageHeader } from '@/design/components/page';
import { AlertCircle, Loader2 } from '@/design/components/icons';
import {
  DecisionPanel,
  ReviewQueueState,
  type DecisionMode,
} from '@/components/superadmin/ReviewParts';
import { waitingText } from '@/components/superadmin/waitingText';

/** Same minimum as the workflow and offer queues. */
const CHANGES_NOTE_MIN = 10;

const BASIS_LABEL: Record<string, string> = {
  per_hire: 'per hire',
  total: 'total',
};
const PERIOD_LABEL: Record<string, string> = {
  annual: 'a year',
  monthly: 'a month',
  one_time: 'one-off',
};

/** Grouped in the Indian numbering system, because these are rupee figures read
 *  by people who count in lakhs. Falls back to the browser default for anything
 *  that is not INR. */
function money(amount: number | null, currency: string | null): string {
  if (amount === null) return '';
  const locale = (currency ?? 'INR') === 'INR' ? 'en-IN' : undefined;
  return `${currency ?? ''} ${amount.toLocaleString(locale)}`.trim();
}

function budgetLine(row: PendingApproval): string | null {
  if (row.budget_amount === null) return null;
  const basis = row.budget_basis ? BASIS_LABEL[row.budget_basis] : '';
  const period = row.budget_period ? PERIOD_LABEL[row.budget_period] : '';
  return `${money(row.budget_amount, row.budget_currency)} ${basis} ${period}`.trim();
}

function Row({ row }: { row: PendingApproval }): JSX.Element {
  const client = useQueryClient();
  const [note, setNote] = useState('');
  const [mode, setMode] = useState<DecisionMode>(null);
  // Set once a decision succeeds. The row stays locked until the refetch takes
  // it off the list (audit AD-F22): the buttons used to come back in between,
  // so a second click reached the server and earned a 409.
  const [decided, setDecided] = useState<string | null>(null);

  function done(message: string, verdict: string): void {
    setDecided(verdict);
    void client.invalidateQueries({ queryKey: ['pending-approvals'] });
    toast.success(message);
  }

  const approve = useMutation({
    mutationFn: () => approveRequisition(row.id, note.trim() || undefined),
    onSuccess: () => done(`${row.title} approved`, 'Approved'),
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not approve this opening.')),
  });

  const reject = useMutation({
    mutationFn: () => rejectRequisition(row.id, note.trim() || undefined),
    onSuccess: () => done(`${row.title} sent back`, 'Sent back'),
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not send this back.')),
  });

  const budget = budgetLine(row);

  return (
    <GlassCard className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            to={`/superadmin/requisitions/${row.id}`}
            className="text-[16px] font-bold text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            {row.title}
          </Link>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            {[row.level, row.department, row.location].filter(Boolean).join(' · ')}
          </p>
        </div>
        <div className="text-left sm:text-right">
          <p className="text-[13px] font-medium text-foreground">{waitingText(row.submitted_at)}</p>
          {row.submitted_by_name ? (
            <p className="text-[12px] text-muted-foreground">by {row.submitted_by_name}</p>
          ) : null}
        </div>
      </div>

      <DataList
        className="mt-4"
        items={[
          {
            label: 'Budget',
            value: budget ?? (
              <span className="flex items-center gap-1.5 text-[var(--ui-warn)]">
                <AlertCircle size={14} aria-hidden="true" />
                No budget recorded
              </span>
            ),
          },
          { label: 'Headcount', value: row.target_hires ?? 'Not specified' },
        ]}
      />

      {row.budget_notes ? (
        <p className="mt-2 text-[13px] text-muted-foreground">{row.budget_notes}</p>
      ) : null}
      {row.note ? (
        <p className="mt-2 rounded-[12px] bg-[var(--ui-inset-soft)] px-3.5 py-2.5 text-[13px] text-foreground">
          {row.note}
        </p>
      ) : null}

      <div className="mt-4">
        {decided ? (
          <p
            role="status"
            className="flex items-center gap-2 rounded-[12px] bg-[var(--ui-inset-soft)] px-3.5 py-2.5 text-[13px] text-foreground"
          >
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            {decided}. Updating the queue…
          </p>
        ) : (
          <DecisionPanel
            mode={mode}
            onMode={setMode}
            note={note}
            onNote={setNote}
            noteId={`note-${row.id}`}
            rejectNoteMin={CHANGES_NOTE_MIN}
            rejectLabel="Request changes"
            approvePending={approve.isPending}
            rejectPending={reject.isPending}
            onApprove={() => approve.mutate()}
            onReject={() => reject.mutate()}
          />
        )}
      </div>
    </GlassCard>
  );
}

export default function ApprovalQueue(): JSX.Element {
  const queue = useQuery({
    queryKey: ['pending-approvals'],
    queryFn: listPendingApprovals,
  });
  const rows = queue.data ?? [];

  return (
    <Page width="form">
      <PageHeader
        title="Openings awaiting approval"
        description="An opening cannot accept public applications until it is approved. Oldest request first."
      />
      <ReviewQueueState
        loading={queue.isLoading}
        error={queue.isError}
        errorText="Could not load the approval queue."
        onRetry={() => void queue.refetch()}
        count={rows.length}
        emptyDescription="Openings your HR managers submit for approval appear here."
      >
        {rows.map((row) => (
          <li key={row.id}>
            <Row row={row} />
          </li>
        ))}
      </ReviewQueueState>
    </Page>
  );
}
