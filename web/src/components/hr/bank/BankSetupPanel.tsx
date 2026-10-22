// BankSetupPanel - stage 1 of the bank flow: name the bank and say what it
// assesses. The same form creates a bank (no `bank` yet) and edits one.
//
// The API holds exactly two things about a bank - a name and a description - so
// that is all this asks for. Difficulty and language belong to a question, not
// to the bank, and are chosen where questions are added.
//
// react-hook-form + Zod, as every form in the app is. A failed save keeps what
// was typed.

import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { errorMessage } from '@/lib/errorMessage';
import { toast } from '@/lib/toast';
import type { UnsavedGuard } from '@/components/bank/useUnsavedGuard';
import { bankSetupSchema, type BankSetupValues } from './bankSetupSchema';
import { createQuestionBank, updateQuestionBank, type BankOut } from '@/api/questionBanks';

const FIELD =
  'w-full rounded-[12px] border border-input bg-card px-3.5 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30 aria-[invalid=true]:border-[var(--ui-danger)]';
const LABEL = 'mb-1.5 block text-[13px] font-semibold text-foreground';

interface Props {
  /** Present when the bank exists; absent while it is being created. */
  bank?: BankOut;
  guard: UnsavedGuard;
  /** Called with the new bank once it exists; the page moves on to stage 2. */
  onCreated?: (bank: BankOut) => void;
}

export function BankSetupPanel({ bank, guard, onCreated }: Props): JSX.Element {
  const qc = useQueryClient();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isDirty },
  } = useForm<BankSetupValues>({
    resolver: zodResolver(bankSetupSchema),
    defaultValues: { name: bank?.name ?? '', description: bank?.description ?? '' },
    mode: 'onTouched',
  });

  // Edits to an existing bank are unsaved work; so is a half-typed new one.
  const { setDirty } = guard;
  useEffect(() => {
    setDirty(isDirty);
    return () => setDirty(false);
  }, [isDirty, setDirty]);

  const mut = useMutation({
    mutationFn: (v: BankSetupValues) =>
      bank
        ? updateQuestionBank(bank.id, { name: v.name, description: v.description || null })
        : createQuestionBank({ name: v.name, description: v.description || null }),
    onSuccess: (saved, v) => {
      void qc.invalidateQueries({ queryKey: ['hr', 'question-banks'] });
      if (bank) {
        toast.success('Bank saved');
        reset(v);
      } else {
        toast.success('Bank created');
        // Seed the list so the bank's own page does not flash "not found" while
        // the refetch above is still on its way.
        qc.setQueryData<BankOut[]>(['hr', 'question-banks'], (old) => [saved, ...(old ?? [])]);
        // Clear dirty first, or the page would ask before leaving the form it just submitted.
        setDirty(false);
        onCreated?.(saved);
      }
    },
    onError: (e: unknown) =>
      toast.error(errorMessage(e, bank ? 'Could not save this bank.' : 'Could not create this bank.')),
  });

  return (
    <form
      noValidate
      aria-label={bank ? 'Bank details' : 'New bank'}
      className="flex max-w-[560px] flex-col gap-4"
      onSubmit={(e) => void handleSubmit((v) => mut.mutate(v))(e)}
    >
      <div>
        <label htmlFor="bank-name" className={LABEL}>
          Name
        </label>
        <input
          id="bank-name"
          autoComplete="off"
          placeholder="e.g. Backend Engineer - Core"
          aria-invalid={errors.name ? true : undefined}
          aria-describedby={errors.name ? 'bank-name-error' : undefined}
          className={`${FIELD} h-11`}
          {...register('name')}
        />
        {errors.name ? (
          <p
            id="bank-name-error"
            role="alert"
            className="mt-1 text-[12.5px] text-[var(--ui-danger)]"
          >
            {errors.name.message}
          </p>
        ) : null}
      </div>
      <div>
        <label htmlFor="bank-description" className={LABEL}>
          What does it assess?
        </label>
        <textarea
          id="bank-description"
          rows={3}
          placeholder="e.g. SQL, REST design and caching for mid-level backend roles"
          aria-invalid={errors.description ? true : undefined}
          aria-describedby="bank-description-hint"
          className={`${FIELD} resize-y py-2.5`}
          {...register('description')}
        />
        <p id="bank-description-hint" className="mt-1 text-[12.5px] text-muted-foreground">
          Optional. The skill or topic, so a colleague can tell this bank from the others.
        </p>
        {errors.description ? (
          <p role="alert" className="mt-1 text-[12.5px] text-[var(--ui-danger)]">
            {errors.description.message}
          </p>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={mut.isPending || (Boolean(bank) && !isDirty)}>
          {mut.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
          {bank ? 'Save details' : 'Create bank and add questions'}
        </Button>
        {!bank ? (
          <span className="text-[13px] text-muted-foreground">
            Difficulty and language are chosen when you add questions.
          </span>
        ) : null}
      </div>
    </form>
  );
}
