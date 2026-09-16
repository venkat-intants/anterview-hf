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
import { GlassCard, Pill } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { AlertCircle, Loader2 } from '@/design/components/icons';

const INPUT =
  'w-full rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3 py-2 text-[13px] text-white placeholder:text-[#5a5f66] focus:border-[var(--accent)] focus:outline-none';

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

function waiting(iso: string | null): string {
  if (!iso) return '';
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (Number.isNaN(days)) return '';
  if (days <= 0) return 'Submitted today';
  return days === 1 ? 'Waiting 1 day' : `Waiting ${days} days`;
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
    <GlassCard className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            to={`/superadmin/requisitions/${row.id}`}
            className="text-[15px] font-semibold text-white hover:underline"
          >
            {row.title}
          </Link>
          <p className="mt-0.5 text-[12.5px] text-[#888b91]">
            {[row.level, row.department, row.location].filter(Boolean).join(' · ')}
          </p>
        </div>
        <div className="text-right">
          <p className="text-[12px] text-[#b8babf]">{waiting(row.submitted_at)}</p>
          {row.submitted_by_name ? (
            <p className="text-[11.5px] text-[#6f7379]">by {row.submitted_by_name}</p>
          ) : null}
        </div>
      </div>

      <dl className="mt-3 grid gap-2 sm:grid-cols-2">
        <div>
          <dt className="text-[11.5px] text-[#6f7379]">Budget</dt>
          <dd className="text-[13px] text-[#d5d7da]">
            {budget ?? (
              <span className="flex items-center gap-1.5 text-[#d6a23d]">
                <AlertCircle size={12} aria-hidden="true" />
                No budget recorded
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-[11.5px] text-[#6f7379]">Headcount</dt>
          <dd className="text-[13px] text-[#d5d7da]">
            {row.target_hires ?? 'Not specified'}
          </dd>
        </div>
      </dl>

      {row.budget_notes ? (
        <p className="mt-2 text-[12px] text-[#888b91]">{row.budget_notes}</p>
      ) : null}
      {row.note ? (
        <p className="mt-2 rounded-[10px] border border-white/[0.07] bg-white/[0.02] px-3 py-2 text-[12.5px] text-[#b8babf]">
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
          placeholder={
            rejecting ? 'What needs to change?' : 'Note (optional)'
          }
          maxLength={1000}
          className={INPUT}
        />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2.5">
        <Pill
          onClick={() => approve.mutate()}
          disabled={approve.isPending || reject.isPending}
          className="px-4 py-2"
        >
          {approve.isPending ? 'Approving…' : 'Approve'}
        </Pill>
        <button
          type="button"
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
          className="rounded-[10px] border border-white/[0.1] px-3 py-2 text-[12.5px] text-[#b8babf] hover:text-white disabled:opacity-40"
        >
          {reject.isPending
            ? 'Sending back…'
            : rejecting
              ? 'Confirm — send back'
              : 'Request changes'}
        </button>
        {rejecting && !reject.isPending ? (
          <button
            type="button"
            onClick={() => setRejecting(false)}
            className="text-[12px] text-[#6f7379] hover:text-[#b8babf]"
          >
            Cancel
          </button>
        ) : null}
      </div>
    </GlassCard>
  );
}

export default function ApprovalQueue(): JSX.Element {
  const queue = useQuery({
    queryKey: ['pending-approvals'],
    queryFn: listPendingApprovals,
  });

  return (
    <div className="mx-auto w-full max-w-[860px] px-4 py-8">
      <Reveal>
        <h1 className="text-[22px] font-semibold text-white">Openings awaiting approval</h1>
        <p className="mt-1 text-[13px] text-[#888b91]">
          An opening cannot accept public applications until it is approved. Oldest request
          first.
        </p>
      </Reveal>

      {queue.isLoading ? (
        <p className="mt-6 flex items-center gap-2 text-[13px] text-[#888b91]">
          <Loader2 size={14} aria-hidden="true" className="animate-spin" />
          Loading…
        </p>
      ) : queue.isError ? (
        <p className="mt-6 text-[13px] text-[var(--ui-danger)]">
          Could not load the approval queue.
        </p>
      ) : (queue.data ?? []).length === 0 ? (
        <GlassCard className="mt-6 p-6 text-center">
          <p className="text-[13.5px] text-[#b8babf]">Nothing is waiting on you.</p>
          <p className="mt-1 text-[12.5px] text-[#6f7379]">
            Openings your HR managers submit for approval appear here.
          </p>
        </GlassCard>
      ) : (
        <div className="mt-6 flex flex-col gap-3">
          {(queue.data ?? []).map((row) => (
            <Row key={row.id} row={row} />
          ))}
        </div>
      )}
    </div>
  );
}
