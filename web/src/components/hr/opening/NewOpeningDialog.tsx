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
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
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
import { errorMessage } from '@/lib/errorMessage';
import { formatMoney } from '@/lib/formatters';

type StepKey = 'basics' | 'budget' | 'review';

const STEPS: { key: StepKey; label: string; hint: string }[] = [
  { key: 'basics', label: 'Role basics', hint: 'Title and plan' },
  { key: 'budget', label: 'Budget', hint: 'Optional' },
  { key: 'review', label: 'Review & create', hint: 'Check and create' },
];

/** A field label with a quiet "(optional)" after it. */
function OptionalLabel({ children }: { children: string }): JSX.Element {
  return (
    <>
      {children} <span className="font-normal text-muted-foreground">(optional)</span>
    </>
  );
}

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
          budgetError = errorMessage(e, 'The budget was not saved.');
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
      const msg = errorMessage(e, 'Could not create the opening.');
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
              <FormField
                id="new-title"
                label="Job title"
                error={err('title')}
                className="sm:col-span-2"
              >
                {(p) => <Input {...p} placeholder="e.g. Backend Engineer" {...register('title')} />}
              </FormField>
              <FormField id="new-level" label="Level">
                {(p) => (
                  <SelectControl {...p} {...register('level')}>
                    {LEVELS.map((l) => (
                      <option key={l} value={l}>
                        {l}
                      </option>
                    ))}
                  </SelectControl>
                )}
              </FormField>
              <FormField id="new-owner" label="Owner">
                {(p) => (
                  <SelectControl {...p} {...register('owner')}>
                    <option value="">Me</option>
                    {(team.data ?? []).map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.full_name || m.email}
                      </option>
                    ))}
                  </SelectControl>
                )}
              </FormField>
              <FormField
                id="new-department"
                label={<OptionalLabel>Department</OptionalLabel>}
                error={err('department')}
              >
                {(p) => <Input {...p} {...register('department')} />}
              </FormField>
              <FormField
                id="new-location"
                label={<OptionalLabel>Location</OptionalLabel>}
                error={err('location')}
              >
                {(p) => <Input {...p} {...register('location')} />}
              </FormField>
              <FormField id="new-target" label="Hires wanted" error={err('target')}>
                {(p) => (
                  <Input
                    {...p}
                    type="number"
                    inputMode="numeric"
                    min={1}
                    max={10000}
                    placeholder="-"
                    {...register('target')}
                  />
                )}
              </FormField>
              <FormField id="new-closes" label="Closes on" error={err('closes')}>
                {(p) => <Input {...p} type="date" min={todayIso()} {...register('closes')} />}
              </FormField>
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
                <FormField
                  id="new-budget-amount"
                  label="Budget amount"
                  error={err('budgetAmount')}
                >
                  {(p) => (
                    <Input
                      {...p}
                      type="number"
                      inputMode="numeric"
                      min={0}
                      placeholder="e.g. 1800000"
                      {...register('budgetAmount')}
                    />
                  )}
                </FormField>
                <FormField id="new-budget-currency" label="Currency" error={err('budgetCurrency')}>
                  {(p) => (
                    <Input
                      {...p}
                      maxLength={3}
                      placeholder="INR"
                      className="uppercase"
                      {...register('budgetCurrency')}
                    />
                  )}
                </FormField>
                <FormField id="new-budget-basis" label="Counted" error={err('budgetBasis')}>
                  {(p) => (
                    <SelectControl {...p} {...register('budgetBasis')}>
                      <option value="">Choose…</option>
                      {BASES.map((b) => (
                        <option key={b} value={b}>
                          {BASIS_WORD[b]}
                        </option>
                      ))}
                    </SelectControl>
                  )}
                </FormField>
                <FormField id="new-budget-period" label="Period" error={err('budgetPeriod')}>
                  {(p) => (
                    <SelectControl {...p} {...register('budgetPeriod')}>
                      <option value="">Choose…</option>
                      {PERIODS.map((period) => (
                        <option key={period} value={period}>
                          {PERIOD_WORD[period]}
                        </option>
                      ))}
                    </SelectControl>
                  )}
                </FormField>
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
                      ? `${formatMoney(v.budgetAmount, v.budgetCurrency)}, ${
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
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
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
