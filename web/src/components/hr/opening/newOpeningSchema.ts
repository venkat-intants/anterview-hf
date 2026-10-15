// The create-opening form's contract, written before the form.
//
// Every field here is one `POST /hr/requisitions` really accepts
// (`RequisitionInput` in api/requisitions.ts). Form values are strings because
// they come from inputs; `toPayload` is the only place they become the API's
// types, so what is validated and what is sent cannot drift apart.
//
// THE BUDGET GOES IN A SECOND REQUEST. `POST /hr/requisitions` (RequisitionIn)
// has no budget fields; they exist only on `PATCH` (RequisitionPatch), which is
// also where the all-four rule is enforced. So creation is POST, then PATCH when
// a budget was entered, and a failed PATCH is reported without undoing the
// opening (see NewOpeningDialog).
//
// THE BUDGET RULE. The server refuses an amount without a currency, a basis and
// a period (a bare number is one nobody can act on), so the form treats the four
// as one unit: fill all four or leave all four empty. It says which are missing
// rather than refusing the whole.

import { z } from 'zod';
import {
  closingDateToIso,
  type BudgetBasis,
  type BudgetPeriod,
  type RequisitionInput,
} from '@/api/requisitions';

export const LEVELS = ['entry', 'mid', 'senior'] as const;
export const BASES: readonly BudgetBasis[] = ['per_hire', 'total'];
export const PERIODS: readonly BudgetPeriod[] = ['annual', 'monthly', 'one_time'];

export const BASIS_WORD: Record<BudgetBasis, string> = {
  per_hire: 'Per hire',
  total: 'Total for this opening',
};
export const PERIOD_WORD: Record<BudgetPeriod, string> = {
  annual: 'Annual',
  monthly: 'Monthly',
  one_time: 'One-time',
};

export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

const optionalWhole = (min: number, max: number, what: string) =>
  z
    .string()
    .refine(
      (v) => v === '' || (/^\d+$/.test(v) && Number(v) >= min && Number(v) <= max),
      `${what} must be a whole number from ${min} to ${max.toLocaleString('en-IN')}.`,
    );

export const newOpeningSchema = z
  .object({
    // step 1 — role basics
    title: z.string().trim().min(2, 'Give the role a title of at least 2 characters.'),
    level: z.enum(LEVELS),
    department: z.string().max(120, 'Keep the department under 120 characters.'),
    location: z.string().max(200, 'Keep the location under 200 characters.'),
    owner: z.string(),
    target: optionalWhole(1, 10000, 'Hires wanted'),
    closes: z.string().refine((v) => v === '' || v >= todayIso(), 'Pick today or a later date.'),
    // step 2 — budget, all four or none
    budgetAmount: z.string(),
    budgetCurrency: z.string(),
    budgetBasis: z.union([z.enum(['per_hire', 'total']), z.literal('')]),
    budgetPeriod: z.union([z.enum(['annual', 'monthly', 'one_time']), z.literal('')]),
  })
  .superRefine((v, ctx) => {
    const touched = [v.budgetAmount, v.budgetCurrency, v.budgetBasis, v.budgetPeriod].some(
      (x) => x.trim() !== '',
    );
    if (!touched) return;
    const issue = (path: string, message: string): void =>
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: [path], message });
    const amount = Number(v.budgetAmount);
    if (v.budgetAmount.trim() === '' || !Number.isFinite(amount) || amount <= 0) {
      issue('budgetAmount', 'Enter the amount as a number above 0.');
    }
    if (!/^[A-Za-z]{3}$/.test(v.budgetCurrency.trim())) {
      issue('budgetCurrency', 'Use a 3-letter currency code, such as INR.');
    }
    if (v.budgetBasis === '') issue('budgetBasis', 'Choose how the budget is counted.');
    if (v.budgetPeriod === '') issue('budgetPeriod', 'Choose the period it covers.');
  });

export type NewOpeningValues = z.infer<typeof newOpeningSchema>;

export const EMPTY_OPENING: NewOpeningValues = {
  title: '',
  level: 'mid',
  department: '',
  location: '',
  owner: '',
  target: '',
  closes: '',
  budgetAmount: '',
  budgetCurrency: '',
  budgetBasis: '',
  budgetPeriod: '',
};

/** Which fields belong to which step, for per-step validation. */
export const STEP_FIELDS: Record<'basics' | 'budget', (keyof NewOpeningValues)[]> = {
  basics: ['title', 'level', 'department', 'location', 'owner', 'target', 'closes'],
  budget: ['budgetAmount', 'budgetCurrency', 'budgetBasis', 'budgetPeriod'],
};

export function hasBudget(v: NewOpeningValues): boolean {
  return v.budgetAmount.trim() !== '';
}

/** The POST body. Empty optional fields are omitted, not sent as ''. */
export function toCreatePayload(v: NewOpeningValues): RequisitionInput {
  const body: RequisitionInput = { title: v.title.trim(), level: v.level };
  if (v.department.trim()) body.department = v.department.trim();
  if (v.location.trim()) body.location = v.location.trim();
  if (v.owner) body.owner_user_id = v.owner;
  if (v.target) body.target_hires = Number(v.target);
  if (v.closes) body.closes_at = closingDateToIso(v.closes);
  // The create endpoint takes the budget too (all four or none, as PATCH does).
  // An older server ignores unknown fields; the dialog checks the response and
  // falls back to a PATCH (see NewOpeningDialog).
  const budget = toBudgetPatch(v);
  if (budget) Object.assign(body, budget);
  return body;
}

/** The four budget fields as a PATCH body, or null when none was entered. */
export function toBudgetPatch(v: NewOpeningValues): Partial<RequisitionInput> | null {
  if (!hasBudget(v)) return null;
  return {
    budget_amount: Number(v.budgetAmount),
    budget_currency: v.budgetCurrency.trim().toUpperCase(),
    budget_basis: v.budgetBasis === '' ? null : v.budgetBasis,
    budget_period: v.budgetPeriod === '' ? null : v.budgetPeriod,
  };
}
