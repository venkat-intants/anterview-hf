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
import { GlassCard } from '@/design/components/primitives';
import { QueryState } from '@/components/QueryState';
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';
import { AlertCircle } from '@/design/components/icons';
import { formatDateTime } from '@/lib/formatters';
import { browserTimezone } from '@/lib/timezone';
import { toLocalInputValue } from '@/lib/localDatetime';

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

/** The earliest value the picker offers: the start of the next minute. The
 *  current minute is already partly past, so offering it would offer a time
 *  the past-time check then refuses. */
function nextMinuteLocal(): string {
  return toLocalInputValue(
    new Date(Math.floor(Date.now() / 60_000) * 60_000 + 60_000).toISOString(),
  );
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
  // The picker's `min` only steers the calendar; a typed past time is caught
  // here too, so Schedule is held and the reason shown before anything is sent.
  const publishInstant = toInstant(publishAt);
  const publishPast =
    publishAt !== '' &&
    (publishInstant === null || new Date(publishInstant).getTime() <= Date.now());

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
      toast.success('Budget saved');
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not save the budget.')),
  });

  const submit = useMutation({
    mutationFn: () => submitRequisitionForApproval(requisition.id, submitNote || undefined),
    onSuccess: () => {
      setSubmitNote('');
      refresh();
      toast.success('Sent to your company admin for approval');
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
      toast.success('Scheduled');
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not schedule publication.')),
  });

  const cancel = useMutation({
    mutationFn: () => cancelPublishSchedule(requisition.id),
    onSuccess: () => {
      refresh();
      toast.success('Scheduled publication cancelled');
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
              <FormField
                id="b-amount"
                label="Budget amount"
                hint="Money, not headcount. Target hires stays where it is."
              >
                {(p) => (
                  <div className="flex items-center gap-2">
                    <Input
                      aria-label="Budget currency"
                      value={currency}
                      onChange={(e) => setCurrency(e.target.value)}
                      maxLength={3}
                      placeholder="INR"
                      className="w-20 shrink-0"
                    />
                    <Input
                      {...p}
                      type="number"
                      min={0}
                      value={amount}
                      onChange={(e) => setAmount(e.target.value)}
                      placeholder="5000000"
                    />
                  </div>
                )}
              </FormField>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-[3fr_2fr]">
                <FormField id="b-basis" label="Basis">
                  {(p) => (
                    <SelectControl
                      {...p}
                      value={basis}
                      onChange={(e) => setBasis(e.target.value as BudgetBasis)}
                    >
                      {(Object.keys(BASIS_LABEL) as BudgetBasis[]).map((b) => (
                        <option key={b} value={b}>
                          {BASIS_LABEL[b]}
                        </option>
                      ))}
                    </SelectControl>
                  )}
                </FormField>
                <FormField id="b-period" label="Period">
                  {(p) => (
                    <SelectControl
                      {...p}
                      value={period}
                      onChange={(e) => setPeriod(e.target.value as BudgetPeriod)}
                    >
                      {(Object.keys(PERIOD_LABEL) as BudgetPeriod[]).map((pd) => (
                        <option key={pd} value={pd}>
                          {PERIOD_LABEL[pd]}
                        </option>
                      ))}
                    </SelectControl>
                  )}
                </FormField>
              </div>
            </div>

            <FormField
              id="b-notes"
              className="mt-3"
              label={
                <>
                  Budget notes <span className="text-[var(--ui-faint)]">(optional)</span>
                </>
              }
            >
              {(p) => (
                <Input
                  {...p}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Approved in the Q3 plan"
                  maxLength={2000}
                />
              )}
            </FormField>

            <div className="mt-4">
              <Button
                type="button"
                variant="outline"
                onClick={() => saveBudget.mutate()}
                disabled={saveBudget.isPending}
              >
                {saveBudget.isPending ? 'Saving…' : 'Save budget'}
              </Button>
            </div>

            {/* ── Approval ───────────────────────────────────────────────── */}
            <div className="mt-6 border-t border-border pt-5">
              {canSubmit ? (
                <>
                  <FormField
                    id="a-note"
                    label={
                      <>
                        Note for your company admin{' '}
                        <span className="text-[var(--ui-faint)]">(optional)</span>
                      </>
                    }
                  >
                    {(p) => (
                      <Input
                        {...p}
                        value={submitNote}
                        onChange={(e) => setSubmitNote(e.target.value)}
                        placeholder="Backfill for the role Priya left"
                        maxLength={1000}
                      />
                    )}
                  </FormField>
                  <div className="mt-3">
                    <Button
                      type="button"
                      onClick={() => submit.mutate()}
                      disabled={submit.isPending}
                    >
                      {submit.isPending ? 'Submitting…' : 'Submit for approval'}
                    </Button>
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
            <QueryState
              query={schedule}
              errorTitle="Could not load the publish schedule."
              errorClassName="mt-3"
              loading={
                <div className="mt-3 flex flex-col gap-2" aria-busy="true">
                  <span className="sr-only">Loading…</span>
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-10" />
                  <Skeleton className="h-10 w-28 rounded-full" />
                </div>
              }
            >
              {(sched) => (
                <>
                  {sched.publish_at ? (
                    <>
                      <p className="mt-1.5 text-[12.5px] text-foreground">
                        Scheduled for {formatDateTime(sched.publish_at)}
                      </p>
                      <p className="mt-0.5 text-[12px] text-[var(--ui-soft)]">{sched.tolerance}</p>
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
                      <FormField
                        id="s-at"
                        className="mt-2"
                        label={`Go live at (your time, ${browserTimezone()})`}
                        describedBy={!approved ? 'schedule-gate' : undefined}
                        error={publishPast ? 'Choose a date and time in the future.' : undefined}
                      >
                        {(p) => (
                          <Input
                            {...p}
                            type="datetime-local"
                            min={nextMinuteLocal()}
                            value={publishAt}
                            onChange={(e) => setPublishAt(e.target.value)}
                            disabled={!approved || sched.public_apply_enabled}
                          />
                        )}
                      </FormField>
                      {/* Not a FormField hint: the control is described only by
                          the gate, so the reason a screen reader hears is the
                          one that stops it. */}
                      {sched.tolerance ? (
                        <p className="mt-1 text-[12px] text-[var(--ui-soft)]">{sched.tolerance}</p>
                      ) : null}
                      <div className="mt-3">
                        {/* Primary only once it can act: before approval, Submit for
                      approval is the one primary action on this card. */}
                        <Button
                          type="button"
                          variant={approved ? 'default' : 'outline'}
                          onClick={() => setSchedule.mutate()}
                          disabled={
                            setSchedule.isPending ||
                            !approved ||
                            !publishAt ||
                            publishPast ||
                            sched.public_apply_enabled
                          }
                          aria-describedby={!approved ? 'schedule-gate' : undefined}
                        >
                          {setSchedule.isPending ? 'Scheduling…' : 'Schedule'}
                        </Button>
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
                      {sched.public_apply_enabled ? (
                        <p className="mt-2 text-[12px] text-[var(--ui-soft)]">
                          This opening is already accepting applications.
                        </p>
                      ) : null}
                    </>
                  )}
                  {sched.published_at ? (
                    <p className="mt-2 text-[12px] text-[var(--ui-soft)]">
                      Last published automatically on {formatDateTime(sched.published_at)}.
                    </p>
                  ) : null}
                </>
              )}
            </QueryState>
          </div>
        ) : null}
      </GlassCard>
    </section>
  );
}
