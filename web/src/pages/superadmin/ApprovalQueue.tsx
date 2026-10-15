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
import { GlassCard } from '@/design/components/primitives';
import { DataList, Page, PageHeader } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { AlertCircle, CheckCircle2, XCircle } from '@/design/components/icons';
import { ReviewQueueState } from '@/components/superadmin/ReviewParts';
import { waitingText } from '@/components/superadmin/waitingText';

const INPUT =
  'w-full rounded-[12px] border border-input bg-card px-3.5 py-2.5 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';

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
  const [rejecting, setRejecting] = useState(false);

  function done(message: string): void {
    void client.invalidateQueries({ queryKey: ['pending-approvals'] });
    toast.success(message);
  }

  const approve = useMutation({
    mutationFn: () => approveRequisition(row.id, note || undefined),
    onSuccess: () => done(`${row.title} approved.`),
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : 'Could not approve this opening.'),
  });

  const reject = useMutation({
    mutationFn: () => rejectRequisition(row.id, note || undefined),
    onSuccess: () => done(`${row.title} sent back.`),
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : 'Could not send this back.'),
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

      <div className="mt-3">
        <label htmlFor={`note-${row.id}`} className="sr-only">
          Note for {row.title}
        </label>
        <input
          id={`note-${row.id}`}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={rejecting ? 'What needs to change? (optional)' : 'Note (optional)'}
          maxLength={1000}
          className={INPUT}
        />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-border pt-4">
        <Button
          type="button"
          onClick={() => approve.mutate()}
          disabled={approve.isPending || reject.isPending}
        >
          <CheckCircle2 aria-hidden="true" />
          {approve.isPending ? 'Approving…' : 'Approve'}
        </Button>
        <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
          {rejecting && !reject.isPending ? (
            <Button type="button" variant="ghost" onClick={() => setRejecting(false)}>
              Cancel
            </Button>
          ) : null}
          <Button
            type="button"
            variant={rejecting ? 'destructive' : 'outline'}
            onClick={() => {
              // Two clicks, on purpose: sending an opening back is the action
              // that costs somebody a week, and it deserves a beat in which to
              // write down why.
              if (!rejecting) {
                setRejecting(true);
                return;
              }
              reject.mutate();
            }}
            disabled={approve.isPending || reject.isPending}
          >
            {!rejecting ? <XCircle aria-hidden="true" /> : null}
            {reject.isPending
              ? 'Sending back…'
              : rejecting
                ? 'Confirm — send back'
                : 'Request changes'}
          </Button>
        </div>
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
