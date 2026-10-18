// NewOpeningDialog — creating an opening as three short steps instead of one
// row of seven fields.
//
//   1 Role basics      title, level, department, location, owner, hires, closing date
//   2 Budget           optional; the four budget fields are all-or-none
//   3 Review & create  what will be sent, and the one primary of the flow
//
// One <form>, one set of values (react-hook-form + the Zod schema beside this
// file), so Back never loses anything: the steps are views of the same state.
// Each step validates only its own fields before it lets you on; Create
// validates everything again, because the server does.
//
// On success the person lands on the new opening's Setup tab: an opening that
// has just been created has exactly one thing left to do, and that is where it
// is done.

import { useEffect, useState, type FormEvent } from 'react';
import { useForm, type FieldPath } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from '@/design/components/icons';
import { Stepper } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/lib/toast';
import { ApiError } from '@/api/client';
import {
  createRequisition,
  listTeam,
  updateRequisition,
  type Requisition,
  type RequisitionInput,
} from '@/api/requisitions';
import {
  BASES,
  BASIS_WORD,
  EMPTY_OPENING,
  LEVELS,
  PERIODS,
  PERIOD_WORD,
  STEP_FIELDS,
  hasBudget,
  newOpeningSchema,
  todayIso,
  toBudgetPatch,
  toCreatePayload,
  type NewOpeningValues,
} from './newOpeningSchema';

type StepKey = 'basics' | 'budget' | 'review';

const STEPS: { key: StepKey; label: string; hint: string }[] = [
  { key: 'basics', label: 'Role basics', hint: 'Title and plan' },
  { key: 'budget', label: 'Budget', hint: 'Optional' },
  { key: 'review', label: 'Review & create', hint: 'Check and create' },
];

const FIELD =
  'h-10 w-full rounded-[10px] border border-input bg-card px-3.5 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30 aria-[invalid=true]:border-[var(--ui-danger)]';
const LABEL = 'mb-1.5 block text-[13px] font-semibold text-foreground';

/** True when the created opening carries exactly the budget that was sent. */
function budgetEchoed(created: Requisition, sent: Partial<RequisitionInput>): boolean {
  return (
    created.budget_amount != null &&
    Number(created.budget_amount) === sent.budget_amount &&
    created.budget_currency === sent.budget_currency &&
    created.budget_basis === sent.budget_basis &&
    created.budget_period === sent.budget_period
  );
}

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called with the new opening's id once it exists. */
  onCreated: (id: string) => void;
}

export default function NewOpeningDialog({ open, onOpenChange, onCreated }: Props): JSX.Element {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? <DialogBody onOpenChange={onOpenChange} onCreated={onCreated} /> : null}
    </Dialog>
  );
}

function DialogBody({
  onOpenChange,
  onCreated,
}: Pick<Props, 'onOpenChange' | 'onCreated'>): JSX.Element {
  const qc = useQueryClient();
  const [step, setStep] = useState<StepKey>('basics');
  const {
    register,
    handleSubmit,
    trigger,
    getValues,
    setError,
    setFocus,
    formState: { errors },
  } = useForm<NewOpeningValues>({
    resolver: zodResolver(newOpeningSchema),
    defaultValues: EMPTY_OPENING,
    mode: 'onTouched',
  });
  const team = useQuery({ queryKey: ['hr', 'team'], queryFn: listTeam, staleTime: 60_000 });

  const mut = useMutation({
    mutationFn: async (v: NewOpeningValues) => {
      // The budget goes in the create request itself. If the server did not
      // echo it back (an older backend that ignores unknown fields) the opening
      // exists without it, so fall back to the PATCH. The opening exists either
      // way, so a failed PATCH is reported, not undone.
      const created = await createRequisition(toCreatePayload(v));
      const patch = toBudgetPatch(v);
      let budgetError: string | null = null;
      if (patch && !budgetEchoed(created, patch)) {
        try {
          await updateRequisition(created.id, patch);
        } catch (e) {
          budgetError = errText(e, 'The budget was not saved.');
        }
      }
      return { created, budgetError };
    },
    onSuccess: ({ created, budgetError }) => {
      void qc.invalidateQueries({ queryKey: ['hr', 'requisitions'] });
      if (budgetError) {
        toast.warning(
          `Opened “${created.title}”, but its budget was not saved: ${budgetError} Add it in Setup.`,
        );
      } else {
        toast.success(`Opened “${created.title}”`);
      }
      onOpenChange(false);
      onCreated(created.id);
    },
    // A 409 means an open requisition already uses that title, and the server
    // says which. Back to the title, with the reason beside it.
    onError: (e) => {
      const msg = errText(e, 'Could not create the opening');
      toast.error(msg);
      // The status is the contract (create answers 409 for a duplicate open
      // title); the wording is only a fallback for a proxy that eats the status.
      if ((e instanceof ApiError && e.status === 409) || /already exists/i.test(msg)) {
        setStep('basics');
        setError('title', { type: 'server', message: msg });
      }
    },
  });

  const index = STEPS.findIndex((s) => s.key === step);

  // Put the cursor on the first field of the step that just opened.
  useEffect(() => {
    const first: Record<StepKey, FieldPath<NewOpeningValues> | null> = {
      basics: 'title',
      budget: 'budgetAmount',
      review: null,
    };
    const f = first[step];
    if (f) setFocus(f);
  }, [step, setFocus]);

  async function goNext(): Promise<void> {
    if (step === 'basics') {
      if (await trigger(STEP_FIELDS.basics)) setStep('budget');
    } else if (step === 'budget') {
      if (await trigger(STEP_FIELDS.budget)) setStep('review');
    }
  }

  // Continue validates only its own step; only Create validates everything.
  function onSubmit(e: FormEvent): void {
    e.preventDefault();
    if (step !== 'review') {
      void goNext();
      return;
    }
    void handleSubmit(
      (v) => mut.mutate(v),
      (bad) => {
        // Something earlier no longer validates: take them to it.
        setStep(STEP_FIELDS.basics.some((f) => bad[f]) ? 'basics' : 'budget');
      },
    )();
  }

  const err = (name: keyof NewOpeningValues): string | undefined => errors[name]?.message;
  const described = (
    name: keyof NewOpeningValues,
  ): { 'aria-invalid'?: true; 'aria-describedby'?: string } =>
    errors[name] ? { 'aria-invalid': true, 'aria-describedby': `no-${name}-err` } : {};
  const renderErr = (name: keyof NewOpeningValues): JSX.Element | null =>
    err(name) ? (
      <p id={`no-${name}-err`} role="alert" className="mt-1 text-[12.5px] text-[var(--ui-danger)]">
        {err(name)}
      </p>
    ) : null;

  const v = getValues();

  return (
    <DialogContent className="max-w-xl gap-0 p-0" data-testid="new-opening-dialog">
      <DialogHeader className="gap-3 px-6 pb-4 pt-6">
        <DialogTitle className="text-[20px] font-bold">New opening</DialogTitle>
        <DialogDescription className="sr-only">
          Create an opening in three steps: role basics, an optional budget, then review.
        </DialogDescription>
        <Stepper
          steps={STEPS}
          current={step}
          label="New opening progress"
          compactLabel={`Step ${index + 1} of ${STEPS.length} · ${STEPS[index]?.label ?? ''}`}
        />
      </DialogHeader>

      <form onSubmit={onSubmit} noValidate className="flex flex-col">
        <div className="border-t border-border px-6 py-5" data-testid={`new-opening-step-${step}`}>
          {step === 'basics' ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <label htmlFor="new-title" className={LABEL}>
                  Job title
                </label>
                <input
                  id="new-title"
                  placeholder="e.g. Backend Engineer"
                  className={FIELD}
                  {...described('title')}
                  {...register('title')}
                />
                {renderErr('title')}
              </div>
              <div>
                <label htmlFor="new-level" className={LABEL}>
                  Level
                </label>
                <select id="new-level" className={FIELD} {...register('level')}>
                  {LEVELS.map((l) => (
                    <option key={l} value={l}>
                      {l}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="new-owner" className={LABEL}>
                  Owner
                </label>
                <select id="new-owner" className={FIELD} {...register('owner')}>
                  <option value="">Me</option>
                  {(team.data ?? []).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.full_name || m.email}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="new-department" className={LABEL}>
                  Department <span className="font-normal text-muted-foreground">(optional)</span>
                </label>
                <input
                  id="new-department"
                  className={FIELD}
                  {...described('department')}
                  {...register('department')}
                />
                {renderErr('department')}
              </div>
              <div>
                <label htmlFor="new-location" className={LABEL}>
                  Location <span className="font-normal text-muted-foreground">(optional)</span>
                </label>
                <input
                  id="new-location"
                  className={FIELD}
                  {...described('location')}
                  {...register('location')}
                />
                {renderErr('location')}
              </div>
              <div>
                <label htmlFor="new-target" className={LABEL}>
                  Hires wanted
                </label>
                <input
                  id="new-target"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={10000}
                  placeholder="-"
                  className={FIELD}
                  {...described('target')}
                  {...register('target')}
                />
                {renderErr('target')}
              </div>
              <div>
                <label htmlFor="new-closes" className={LABEL}>
                  Closes on
                </label>
                <input
                  id="new-closes"
                  type="date"
                  min={todayIso()}
                  className={FIELD}
                  {...described('closes')}
                  {...register('closes')}
                />
                {renderErr('closes')}
              </div>
            </div>
          ) : null}

          {step === 'budget' ? (
            <div className="flex flex-col gap-4">
              <p className="text-[13.5px] leading-relaxed text-muted-foreground">
                Optional. A budget is the amount, its currency, how it is counted and the period it
                covers, kept together: fill all four, or leave all four empty and add it later in
                Setup. It is never shown to candidates.
              </p>
              <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_160px]">
                <div>
                  <label htmlFor="new-budget-amount" className={LABEL}>
                    Budget amount
                  </label>
                  <input
                    id="new-budget-amount"
                    type="number"
                    inputMode="numeric"
                    min={0}
                    placeholder="e.g. 1800000"
                    className={FIELD}
                    {...described('budgetAmount')}
                    {...register('budgetAmount')}
                  />
                  {renderErr('budgetAmount')}
                </div>
                <div>
                  <label htmlFor="new-budget-currency" className={LABEL}>
                    Currency
                  </label>
                  <input
                    id="new-budget-currency"
                    maxLength={3}
                    placeholder="INR"
                    className={`${FIELD} uppercase`}
                    {...described('budgetCurrency')}
                    {...register('budgetCurrency')}
                  />
                  {renderErr('budgetCurrency')}
                </div>
                <div>
                  <label htmlFor="new-budget-basis" className={LABEL}>
                    Counted
                  </label>
                  <select
                    id="new-budget-basis"
                    className={FIELD}
                    {...described('budgetBasis')}
                    {...register('budgetBasis')}
                  >
                    <option value="">Choose…</option>
                    {BASES.map((b) => (
                      <option key={b} value={b}>
                        {BASIS_WORD[b]}
                      </option>
                    ))}
                  </select>
                  {renderErr('budgetBasis')}
                </div>
                <div>
                  <label htmlFor="new-budget-period" className={LABEL}>
                    Period
                  </label>
                  <select
                    id="new-budget-period"
                    className={FIELD}
                    {...described('budgetPeriod')}
                    {...register('budgetPeriod')}
                  >
                    <option value="">Choose…</option>
                    {PERIODS.map((p) => (
                      <option key={p} value={p}>
                        {PERIOD_WORD[p]}
                      </option>
                    ))}
                  </select>
                  {renderErr('budgetPeriod')}
                </div>
              </div>
            </div>
          ) : null}

          {step === 'review' ? (
            <div className="flex flex-col gap-4">
              <dl className="grid grid-cols-[104px_minmax(0,1fr)] gap-x-4 gap-y-2 text-[14px] sm:grid-cols-[150px_minmax(0,1fr)] sm:gap-x-6 sm:gap-y-2.5">
                <Fact label="Job title" value={v.title.trim()} />
                <Fact label="Level" value={v.level} />
                <Fact label="Department" value={v.department.trim()} />
                <Fact label="Location" value={v.location.trim()} />
                <Fact
                  label="Owner"
                  value={
                    v.owner
                      ? (team.data ?? []).find((m) => m.id === v.owner)?.full_name ||
                        (team.data ?? []).find((m) => m.id === v.owner)?.email ||
                        ''
                      : 'Me'
                  }
                />
                <Fact label="Hires wanted" value={v.target} />
                <Fact label="Closes on" value={v.closes} />
                <Fact
                  label="Budget"
                  value={
                    hasBudget(v)
                      ? `${v.budgetCurrency.trim().toUpperCase()} ${Number(v.budgetAmount).toLocaleString('en-IN')}, ${
                          v.budgetBasis ? BASIS_WORD[v.budgetBasis].toLowerCase() : ''
                        }, ${v.budgetPeriod ? PERIOD_WORD[v.budgetPeriod].toLowerCase() : ''}`
                      : ''
                  }
                  empty="Not set - add it in Setup"
                />
              </dl>
              <p className="rounded-[12px] bg-[var(--ui-inset-soft)] px-3.5 py-3 text-[13px] leading-relaxed text-muted-foreground">
                Creating it opens its Setup, where the job description, hiring process and approval
                are done in order. Nothing is published or visible to candidates until you say so.
              </p>
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-border px-6 py-4">
          {step === 'basics' ? (
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
          ) : (
            <Button
              type="button"
              variant="outline"
              disabled={mut.isPending}
              onClick={() => setStep(step === 'review' ? 'budget' : 'basics')}
            >
              Back
            </Button>
          )}
          {step === 'review' ? (
            <Button type="submit" data-testid="create-opening" disabled={mut.isPending}>
              {mut.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
              Create opening
            </Button>
          ) : (
            <Button type="submit" data-testid="new-opening-continue">
              Continue
            </Button>
          )}
        </div>
      </form>
    </DialogContent>
  );
}

function Fact({
  label,
  value,
  empty = '-',
}: {
  label: string;
  value: string;
  empty?: string;
}): JSX.Element {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words font-medium text-foreground">{value || empty}</dd>
    </>
  );
}
