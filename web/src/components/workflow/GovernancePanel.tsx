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
import { invalidateAfterHrMutation, queryKeys } from '@/lib/queryKeys';
import { errorMessage } from '@/lib/errorMessage';
import { statusLabel } from '@/lib/statusLabel';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { GlassCard, Pill } from '@/design/components/primitives';
import { AlertCircle } from '@/design/components/icons';
import { formatDateTime } from '@/lib/formatters';

const INPUT =
  'w-full rounded-[10px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2 text-[13.5px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';
const LABEL = 'mb-1.5 block text-[12.5px] font-medium text-[var(--ui-soft)]';

// HR-L3: the shared approval labels, so this panel, the openings list and the
// HR home say the same words ("Sent back", "Awaiting approval") for one state.
const APPROVAL_LABEL: Record<ApprovalStatus, string> = {
  draft: statusLabel('draft', 'approval'),
  pending_approval: statusLabel('pending_approval', 'approval'),
  approved: statusLabel('approved', 'approval'),
  rejected: statusLabel('rejected', 'approval'),
};

// Never colour alone — the label carries the meaning and the tone reinforces it.
const APPROVAL_TONE: Record<ApprovalStatus, string> = {
  draft: 'border-border text-muted-foreground',
  pending_approval: 'border-[var(--ui-warn)] text-[var(--ui-warn)]',
  approved: 'border-[var(--ui-ok)] text-[var(--ui-ok)]',
  rejected: 'border-[var(--ui-danger)] text-[var(--ui-danger)]',
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

/**
 * `section` lets the opening hub place the two halves in different stages:
 * `approval` is budget and approval (stage 3), `schedule` is the publish
 * schedule (stage 4). The default shows both, as the workflow page once did.
 */
export type GovernanceSection = 'all' | 'approval' | 'schedule';

export default function GovernancePanel({
  requisition,
  section = 'all',
}: {
  requisition: Requisition;
  section?: GovernanceSection;
}) {
  const showApproval = section !== 'schedule';
  const showSchedule = section !== 'approval';
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

  // Only the schedule half reads the schedule, so the approval stage does not
  // spend a request on it.
  const schedule = useQuery({
    queryKey: queryKeys.publishSchedule(requisition.id),
    queryFn: () => getPublishSchedule(requisition.id),
    enabled: showSchedule,
  });

  // HR-L12: by prefix, so the openings list and the HR home's "Openings in
  // setup" badge refresh too, not only this opening's record.
  function refresh(): void {
    void invalidateAfterHrMutation(client, queryKeys.publishSchedule(requisition.id));
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
      client.setQueryData(queryKeys.requisition(requisition.id), updated);
      toast.success('Budget saved.');
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not save the budget.')),
  });

  const submit = useMutation({
    mutationFn: () => submitRequisitionForApproval(requisition.id, submitNote || undefined),
    onSuccess: () => {
      setSubmitNote('');
      refresh();
      toast.success('Sent to your company admin for approval.');
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not submit for approval.')),
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
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not schedule publication.')),
  });

  const cancel = useMutation({
    mutationFn: () => cancelPublishSchedule(requisition.id),
    onSuccess: () => {
      refresh();
      toast.success('Scheduled publication cancelled.');
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not cancel the schedule.')),
  });

  const canSubmit = status === 'draft' || status === 'rejected';

  return (
    <section aria-labelledby="governance-heading">
      <GlassCard className="p-6">
        {showApproval ? (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 id="governance-heading" className="text-[15px] font-semibold text-foreground">
                Budget &amp; approval
              </h2>
              <span
                className={`rounded-full border px-2.5 py-0.5 text-[12px] ${APPROVAL_TONE[status]}`}
              >
                {APPROVAL_LABEL[status]}
              </span>
            </div>

            {requisition.approval_note ? (
              <p className="mt-2 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2 text-[12.5px] text-[var(--ui-soft)]">
                {requisition.approval_note}
                {requisition.approval_decided_by_name
                  ? ` - ${requisition.approval_decided_by_name}`
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
                <p className="mt-1 text-[12px] text-[var(--ui-soft)]">
                  Money, not headcount. Target hires stays where it is.
                </p>
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-[3fr_2fr]">
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
                Budget notes <span className="text-[var(--ui-faint)]">(optional)</span>
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
                variant="outline"
                onClick={() => saveBudget.mutate()}
                disabled={saveBudget.isPending}
                className="px-4 py-2"
              >
                {saveBudget.isPending ? 'Saving…' : 'Save budget'}
              </Pill>
            </div>

            {/* ── Approval ───────────────────────────────────────────────── */}
            <div className="mt-6 border-t border-border pt-5">
              {canSubmit ? (
                <>
                  <label htmlFor="a-note" className={LABEL}>
                    Note for your company admin{' '}
                    <span className="text-[var(--ui-faint)]">(optional)</span>
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
                <p className="text-[12.5px] text-muted-foreground">
                  {status === 'pending_approval'
                    ? `Submitted${
                        requisition.submitted_for_approval_at
                          ? ` ${formatDateTime(requisition.submitted_for_approval_at)}`
                          : ''
                      }. Your company admin decides from their own console.`
                    : `Approved${
                        requisition.approval_decided_at
                          ? ` ${formatDateTime(requisition.approval_decided_at)}`
                          : ''
                      }.`}
                </p>
              )}
              {!approved ? (
                <p className="mt-2.5 flex items-start gap-1.5 text-[12px] text-[var(--ui-warn)]">
                  <AlertCircle size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
                  Until this opening is approved it cannot accept public applications, and it cannot
                  be scheduled to publish.
                </p>
              ) : null}
            </div>
          </>
        ) : null}

        {/* ── Scheduled publishing ───────────────────────────────────── */}
        {showSchedule ? (
          <div className={showApproval ? 'mt-6 border-t border-border pt-5' : undefined}>
            {showApproval ? (
              <h3 className="text-[13px] font-semibold text-foreground">Publish automatically</h3>
            ) : (
              <h2 id="governance-heading" className="text-[15px] font-semibold text-foreground">
                Publish automatically
              </h2>
            )}
            {schedule.data?.publish_at ? (
              <>
                <p className="mt-1.5 text-[12.5px] text-foreground">
                  Scheduled for {formatDateTime(schedule.data.publish_at)}
                </p>
                <p className="mt-0.5 text-[12px] text-[var(--ui-soft)]">
                  {schedule.data.tolerance}
                </p>
                <div className="mt-3">
                  <ConfirmDeleteButton
                    label="Cancel scheduled publication"
                    confirmText="Cancel publication"
                    icon={null}
                    onConfirm={() => cancel.mutate()}
                    pending={cancel.isPending}
                  />
                </div>
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
                  aria-describedby={!approved ? 'schedule-gate' : undefined}
                  className={INPUT}
                />
                {schedule.data?.tolerance ? (
                  <p className="mt-1 text-[12px] text-[var(--ui-soft)]">
                    {schedule.data.tolerance}
                  </p>
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
                    aria-describedby={!approved ? 'schedule-gate' : undefined}
                    className="px-4 py-2"
                  >
                    {setSchedule.isPending ? 'Scheduling…' : 'Schedule'}
                  </Pill>
                </div>
                {!approved ? (
                  <p
                    id="schedule-gate"
                    className="mt-2 flex items-start gap-1.5 text-[12px] text-[var(--ui-warn)]"
                  >
                    <AlertCircle size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
                    Scheduling needs budget approval (stage 3) first.
                  </p>
                ) : null}
                {schedule.data?.public_apply_enabled ? (
                  <p className="mt-2 text-[12px] text-[var(--ui-soft)]">
                    This opening is already accepting applications.
                  </p>
                ) : null}
              </>
            )}
            {schedule.data?.published_at ? (
              <p className="mt-2 text-[12px] text-[var(--ui-soft)]">
                Last published automatically on {formatDateTime(schedule.data.published_at)}.
              </p>
            ) : null}
          </div>
        ) : null}
      </GlassCard>
    </section>
  );
}
