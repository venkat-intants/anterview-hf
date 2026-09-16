// GovernancePanel — budget, approval, and when this opening goes live.
//
// Three things that belong together because they are one question: should this
// opening exist, at what cost, and from when. PH3-B2 put budget and approval in
// the same story for that reason, and PH3-B4a's schedule is the last step of
// the same decision.
//
// WHAT HR CAN AND CANNOT DO HERE
// HR fills in the budget and submits for approval. It cannot approve — the
// server refuses, because approving requires the super_admin role and a user
// holds exactly one role. That separation is not enforced by hiding a button;
// the button is absent because the endpoint would refuse it, and the queue
// lives in the super admin's own console.
//
// THE SCHEDULE TELLS THE TRUTH ABOUT ITS OWN PRECISION
// The publisher is an interval loop, not a clock trigger, because a clock
// trigger cannot fire while the container is suspended. So the server sends a
// sentence describing the real tolerance and this renders it next to the time.
// A UI that showed only "09:00" would be making a promise the architecture does
// not offer.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  cancelPublishSchedule,
  getPublishSchedule,
  setPublishSchedule,
  submitRequisitionForApproval,
  updateRequisition,
  type ApprovalStatus,
  type BudgetBasis,
  type BudgetPeriod,
  type Requisition,
} from '@/api/requisitions';
import { toast } from '@/lib/toast';
import { GlassCard, Pill } from '@/design/components/primitives';
import { AlertCircle } from '@/design/components/icons';

const INPUT =
  'w-full rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3 py-2 text-[13.5px] text-white placeholder:text-[#5a5f66] focus:border-[var(--accent)] focus:outline-none';
const LABEL = 'mb-1.5 block text-[12.5px] font-medium text-[#b8babf]';

const APPROVAL_LABEL: Record<ApprovalStatus, string> = {
  draft: 'Not submitted',
  pending_approval: 'Waiting for approval',
  approved: 'Approved',
  rejected: 'Changes requested',
};

// Never colour alone — the label carries the meaning and the tone reinforces it.
const APPROVAL_TONE: Record<ApprovalStatus, string> = {
  draft: 'border-white/[0.1] text-[#888b91]',
  pending_approval: 'border-[#d6a23d]/40 text-[#d6a23d]',
  approved: 'border-[#4f9e6a]/40 text-[#6fbf8d]',
  rejected: 'border-[#e6714f]/40 text-[#e6714f]',
};

const BASIS_LABEL: Record<BudgetBasis, string> = {
  per_hire: 'Per hire',
  total: 'Total for this opening',
};

const PERIOD_LABEL: Record<BudgetPeriod, string> = {
  annual: 'Annual',
  monthly: 'Monthly',
  one_time: 'One-time',
};

/** `YYYY-MM-DDTHH:mm` from a datetime-local input, as an absolute instant.
 *
 *  The server refuses a value with no offset, deliberately: "publish at 09:00"
 *  landing five and a half hours out is exactly the bug a scheduling feature
 *  must not have. `new Date(local)` interprets the string in the browser's own
 *  zone, which is the one the recruiter meant. */
function toInstant(local: string): string | null {
  if (!local) return null;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

function when(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString();
}

export default function GovernancePanel({ requisition }: { requisition: Requisition }) {
  const client = useQueryClient();
  const status = requisition.approval_status ?? 'draft';
  const approved = status === 'approved';

  const [amount, setAmount] = useState(requisition.budget_amount?.toString() ?? '');
  const [currency, setCurrency] = useState(requisition.budget_currency ?? 'INR');
  const [basis, setBasis] = useState<BudgetBasis>(requisition.budget_basis ?? 'total');
  const [period, setPeriod] = useState<BudgetPeriod>(requisition.budget_period ?? 'annual');
  const [notes, setNotes] = useState(requisition.budget_notes ?? '');
  const [submitNote, setSubmitNote] = useState('');
  const [publishAt, setPublishAt] = useState('');

  const schedule = useQuery({
    queryKey: ['publish-schedule', requisition.id],
    queryFn: () => getPublishSchedule(requisition.id),
  });

  function refresh(): void {
    void client.invalidateQueries({ queryKey: ['requisition', requisition.id] });
    void client.invalidateQueries({ queryKey: ['publish-schedule', requisition.id] });
  }

  const saveBudget = useMutation({
    mutationFn: () =>
      updateRequisition(requisition.id, {
        // All four together or none: an amount alone is a number nobody can
        // act on, and the server refuses it with a 422 rather than storing a
        // half-filled form.
        budget_amount: amount === '' ? null : Number(amount),
        budget_currency: amount === '' ? null : currency.toUpperCase(),
        budget_basis: amount === '' ? null : basis,
        budget_period: amount === '' ? null : period,
        budget_notes: notes || null,
      }),
    onSuccess: (updated) => {
      client.setQueryData(['requisition', requisition.id], updated);
      toast.success('Budget saved.');
    },
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : 'Could not save the budget.'),
  });

  const submit = useMutation({
    mutationFn: () => submitRequisitionForApproval(requisition.id, submitNote || undefined),
    onSuccess: () => {
      setSubmitNote('');
      refresh();
      toast.success('Sent to your company admin for approval.');
    },
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : 'Could not submit for approval.'),
  });

  const setSchedule = useMutation({
    mutationFn: () => {
      const instant = toInstant(publishAt);
      if (!instant) throw new Error('Choose a date and time in the future.');
      return setPublishSchedule(requisition.id, instant);
    },
    onSuccess: () => {
      setPublishAt('');
      refresh();
      toast.success('Scheduled.');
    },
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : 'Could not schedule publication.'),
  });

  const cancel = useMutation({
    mutationFn: () => cancelPublishSchedule(requisition.id),
    onSuccess: () => {
      refresh();
      toast.success('Scheduled publication cancelled.');
    },
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : 'Could not cancel the schedule.'),
  });

  const canSubmit = status === 'draft' || status === 'rejected';

  return (
    <section aria-labelledby="governance-heading" className="mt-5">
      <GlassCard className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="governance-heading" className="text-[15px] font-semibold text-white">
            Budget &amp; approval
          </h2>
          <span
            className={`rounded-full border px-2.5 py-0.5 text-[11px] ${APPROVAL_TONE[status]}`}
          >
            {APPROVAL_LABEL[status]}
          </span>
        </div>

        {requisition.approval_note ? (
          <p className="mt-2 rounded-[10px] border border-white/[0.07] bg-white/[0.02] px-3 py-2 text-[12.5px] text-[#b8babf]">
            {requisition.approval_note}
            {requisition.approval_decided_by_name
              ? ` — ${requisition.approval_decided_by_name}`
              : ''}
          </p>
        ) : null}

        {/* ── Budget ─────────────────────────────────────────────────── */}
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="b-amount" className={LABEL}>
              Budget amount
            </label>
            <div className="flex items-center gap-2">
              <input
                aria-label="Budget currency"
                value={currency}
                onChange={(e) => setCurrency(e.target.value)}
                maxLength={3}
                placeholder="INR"
                className={`${INPUT} w-20`}
              />
              <input
                id="b-amount"
                type="number"
                min={0}
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="5000000"
                className={INPUT}
              />
            </div>
            <p className="mt-1 text-[11.5px] text-[#6f7379]">
              Money, not headcount. Target hires stays where it is.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label htmlFor="b-basis" className={LABEL}>
                Basis
              </label>
              <select
                id="b-basis"
                value={basis}
                onChange={(e) => setBasis(e.target.value as BudgetBasis)}
                className={INPUT}
              >
                {(Object.keys(BASIS_LABEL) as BudgetBasis[]).map((b) => (
                  <option key={b} value={b}>
                    {BASIS_LABEL[b]}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="b-period" className={LABEL}>
                Period
              </label>
              <select
                id="b-period"
                value={period}
                onChange={(e) => setPeriod(e.target.value as BudgetPeriod)}
                className={INPUT}
              >
                {(Object.keys(PERIOD_LABEL) as BudgetPeriod[]).map((p) => (
                  <option key={p} value={p}>
                    {PERIOD_LABEL[p]}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <div className="mt-3">
          <label htmlFor="b-notes" className={LABEL}>
            Budget notes <span className="text-[#5a5f66]">(optional)</span>
          </label>
          <input
            id="b-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Approved in the Q3 plan"
            maxLength={2000}
            className={INPUT}
          />
        </div>

        <div className="mt-4">
          <Pill
            onClick={() => saveBudget.mutate()}
            disabled={saveBudget.isPending}
            className="px-4 py-2"
          >
            {saveBudget.isPending ? 'Saving…' : 'Save budget'}
          </Pill>
        </div>

        {/* ── Approval ───────────────────────────────────────────────── */}
        <div className="mt-6 border-t border-white/[0.07] pt-5">
          {canSubmit ? (
            <>
              <label htmlFor="a-note" className={LABEL}>
                Note for your company admin <span className="text-[#5a5f66]">(optional)</span>
              </label>
              <input
                id="a-note"
                value={submitNote}
                onChange={(e) => setSubmitNote(e.target.value)}
                placeholder="Backfill for the role Priya left"
                maxLength={1000}
                className={INPUT}
              />
              <div className="mt-3">
                <Pill
                  onClick={() => submit.mutate()}
                  disabled={submit.isPending}
                  className="px-4 py-2"
                >
                  {submit.isPending ? 'Submitting…' : 'Submit for approval'}
                </Pill>
              </div>
            </>
          ) : (
            <p className="text-[12.5px] text-[#888b91]">
              {status === 'pending_approval'
                ? `Submitted${
                    requisition.submitted_for_approval_at
                      ? ` ${when(requisition.submitted_for_approval_at)}`
                      : ''
                  }. Your company admin decides from their own console.`
                : `Approved${
                    requisition.approval_decided_at
                      ? ` ${when(requisition.approval_decided_at)}`
                      : ''
                  }.`}
            </p>
          )}
          {!approved ? (
            <p className="mt-2.5 flex items-start gap-1.5 text-[11.5px] text-[#d6a23d]">
              <AlertCircle size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
              Until this opening is approved it cannot accept public applications, and it
              cannot be scheduled to publish.
            </p>
          ) : null}
        </div>

        {/* ── Scheduled publishing ───────────────────────────────────── */}
        <div className="mt-6 border-t border-white/[0.07] pt-5">
          <h3 className="text-[13px] font-semibold text-[#d5d7da]">Publish automatically</h3>
          {schedule.data?.publish_at ? (
            <>
              <p className="mt-1.5 text-[12.5px] text-[#d5d7da]">
                Scheduled for {when(schedule.data.publish_at)}
              </p>
              <p className="mt-0.5 text-[11.5px] text-[#6f7379]">{schedule.data.tolerance}</p>
              <button
                type="button"
                onClick={() => cancel.mutate()}
                disabled={cancel.isPending}
                className="mt-3 rounded-[10px] border border-white/[0.1] px-3 py-1.5 text-[12px] text-[#b8babf] hover:text-white disabled:opacity-40"
              >
                Cancel scheduled publication
              </button>
            </>
          ) : (
            <>
              <label htmlFor="s-at" className={`${LABEL} mt-2`}>
                Go live at
              </label>
              <input
                id="s-at"
                type="datetime-local"
                value={publishAt}
                onChange={(e) => setPublishAt(e.target.value)}
                disabled={!approved || schedule.data?.public_apply_enabled}
                className={INPUT}
              />
              {schedule.data?.tolerance ? (
                <p className="mt-1 text-[11.5px] text-[#6f7379]">{schedule.data.tolerance}</p>
              ) : null}
              <div className="mt-3">
                <Pill
                  onClick={() => setSchedule.mutate()}
                  disabled={
                    setSchedule.isPending ||
                    !approved ||
                    !publishAt ||
                    Boolean(schedule.data?.public_apply_enabled)
                  }
                  className="px-4 py-2"
                >
                  {setSchedule.isPending ? 'Scheduling…' : 'Schedule'}
                </Pill>
              </div>
              {schedule.data?.public_apply_enabled ? (
                <p className="mt-2 text-[11.5px] text-[#6f7379]">
                  This opening is already accepting applications.
                </p>
              ) : null}
            </>
          )}
          {schedule.data?.published_at ? (
            <p className="mt-2 text-[11.5px] text-[#6f7379]">
              Last published automatically on {when(schedule.data.published_at)}.
            </p>
          ) : null}
        </div>
      </GlassCard>
    </section>
  );
}
