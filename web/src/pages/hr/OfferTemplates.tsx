// OfferTemplates — PH4-A3. Reusable starting points for an offer: employment
// type, currency, pay period, probation, notice, benefits and terms.
// Compensation itself (base salary) is never part of a template — every
// offer sets its own.

import { useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createOfferTemplate,
  deleteOfferTemplate,
  listOfferTemplates,
  updateOfferTemplate,
  type EmploymentType,
  type OfferTemplate,
  type PayPeriod,
  type TemplateInput,
} from '@/api/offers';
import { toast } from '@/lib/toast';
import { errorMessage } from '@/lib/errorMessage';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { GlassCard } from '@/design/components/primitives';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { QueryErrorPanel } from '@/components/QueryState';
import { Skeleton } from '@/components/ui/skeleton';
import { Page, PageHeader, Toolbar } from '@/design/components/page';

const inputCls =
  'mt-1 w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] ' +
  'text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';
const labelCls = 'text-[12px] font-medium text-[var(--ui-soft)]';

const EMPLOYMENT_TYPES: EmploymentType[] = ['full_time', 'part_time', 'contract', 'internship'];
const PAY_PERIODS: PayPeriod[] = ['annual', 'monthly', 'hourly'];

interface FormState {
  name: string;
  employment_type: string;
  currency: string;
  pay_period: string;
  probation_months: string;
  notice_period_days: string;
  benefits: string;
  terms: string;
  valid_days: string;
}

/** A new template starts from the values the server would give it anyway
 *  (the columns' defaults), so employment type, pay period, currency and the
 *  offer window always hold a value: none of them can be "unset". */
const EMPTY: FormState = {
  name: '',
  employment_type: 'full_time',
  currency: 'INR',
  pay_period: 'annual',
  probation_months: '',
  notice_period_days: '',
  benefits: '',
  terms: '',
  valid_days: '7',
};

/** The server's limits (TemplateIn and clean_fields), mirrored so a person is
 *  told before saving rather than by a refusal after it. */
const LIMITS = { name: 120, benefits: 4000, terms: 20000 } as const;

function wholeIn(value: string, min: number, max: number): boolean {
  if (!/^\d+$/.test(value.trim())) return false;
  const n = Number(value);
  return n >= min && n <= max;
}

/** The first thing wrong with the form, in words, or null when it can be saved. */
function templateProblem(f: FormState): string | null {
  if (!f.name.trim()) return 'Give the template a name.';
  if (!/^[A-Za-z]{3}$/.test(f.currency.trim()))
    return 'Currency is a three-letter code, such as INR.';
  if (!wholeIn(f.valid_days, 1, 60)) return 'An offer stays open for 1 to 60 days.';
  if (f.probation_months.trim() !== '' && !wholeIn(f.probation_months, 0, 24)) {
    return 'Probation is 0 to 24 months, or leave it empty.';
  }
  if (f.notice_period_days.trim() !== '' && !wholeIn(f.notice_period_days, 0, 365)) {
    return 'The notice period is 0 to 365 days, or leave it empty.';
  }
  return null;
}

/** Accepts either a saved OfferTemplate (fields are `T | null`) or a
 *  TemplateInput (fields are `T | undefined`) — the fallbacks below treat
 *  both the same way. */
interface TemplateLike {
  name?: string | null;
  employment_type?: EmploymentType | null;
  currency?: string | null;
  pay_period?: PayPeriod | null;
  probation_months?: number | null;
  notice_period_days?: number | null;
  benefits?: string | null;
  terms?: string | null;
  valid_days?: number | null;
}

function toInput(t: TemplateLike): FormState {
  return {
    name: t.name ?? '',
    employment_type: t.employment_type ?? '',
    currency: t.currency ?? '',
    pay_period: t.pay_period ?? '',
    probation_months: t.probation_months != null ? String(t.probation_months) : '',
    notice_period_days: t.notice_period_days != null ? String(t.notice_period_days) : '',
    benefits: t.benefits ?? '',
    terms: t.terms ?? '',
    valid_days: t.valid_days != null ? String(t.valid_days) : '',
  };
}

/**
 * The request body. A cleared optional field is sent as null, never left out:
 * the server updates only the fields it is given, so an omitted one kept its
 * old value while the page said "Template updated" (audit OF-F3 / M9).
 */
function toBody(f: FormState): TemplateInput {
  return {
    name: f.name.trim(),
    employment_type: f.employment_type as EmploymentType,
    currency: f.currency.trim().toUpperCase(),
    pay_period: f.pay_period as PayPeriod,
    probation_months: f.probation_months.trim() === '' ? null : Number(f.probation_months),
    notice_period_days: f.notice_period_days.trim() === '' ? null : Number(f.notice_period_days),
    benefits: f.benefits.trim() || null,
    terms: f.terms.trim() || null,
    valid_days: Number(f.valid_days),
  };
}

function TemplateForm({
  initial,
  onSave,
  saving,
  submitLabel,
}: {
  initial: FormState;
  onSave: (body: TemplateInput) => void;
  saving: boolean;
  submitLabel: string;
}) {
  const uid = useId();
  const [form, setForm] = useState(initial);
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));
  const problem = templateProblem(form);
  // Say what is wrong only once there is something typed; an untouched new
  // form simply has its button disabled.
  const showProblem = problem !== null && form.name.trim() !== '';

  return (
    <div className="flex flex-col gap-2.5">
      <div>
        <label htmlFor={`${uid}-name`} className={labelCls}>
          Name
        </label>
        <input
          id={`${uid}-name`}
          maxLength={LIMITS.name}
          value={form.name}
          onChange={(e) => set('name', e.target.value)}
          className={inputCls}
        />
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        <div>
          <label htmlFor={`${uid}-type`} className={labelCls}>
            Employment type
          </label>
          <select
            id={`${uid}-type`}
            value={form.employment_type}
            onChange={(e) => set('employment_type', e.target.value)}
            className={inputCls}
          >
            {EMPLOYMENT_TYPES.map((t) => (
              <option key={t} value={t}>
                {t.replace('_', ' ')}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${uid}-period`} className={labelCls}>
            Pay period
          </label>
          <select
            id={`${uid}-period`}
            value={form.pay_period}
            onChange={(e) => set('pay_period', e.target.value)}
            className={inputCls}
          >
            {PAY_PERIODS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${uid}-currency`} className={labelCls}>
            Currency
          </label>
          <input
            id={`${uid}-currency`}
            value={form.currency}
            onChange={(e) => set('currency', e.target.value.toUpperCase())}
            maxLength={3}
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor={`${uid}-valid`} className={labelCls}>
            Valid for (days)
          </label>
          <input
            id={`${uid}-valid`}
            type="number"
            min={1}
            max={60}
            step={1}
            required
            value={form.valid_days}
            onChange={(e) => set('valid_days', e.target.value)}
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor={`${uid}-probation`} className={labelCls}>
            Probation (months)
          </label>
          <input
            id={`${uid}-probation`}
            type="number"
            min={0}
            max={24}
            step={1}
            value={form.probation_months}
            onChange={(e) => set('probation_months', e.target.value)}
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor={`${uid}-notice`} className={labelCls}>
            Notice period (days)
          </label>
          <input
            id={`${uid}-notice`}
            type="number"
            min={0}
            max={365}
            step={1}
            value={form.notice_period_days}
            onChange={(e) => set('notice_period_days', e.target.value)}
            className={inputCls}
          />
        </div>
      </div>
      <div>
        <label htmlFor={`${uid}-benefits`} className={labelCls}>
          Benefits
        </label>
        <textarea
          id={`${uid}-benefits`}
          rows={2}
          maxLength={LIMITS.benefits}
          value={form.benefits}
          onChange={(e) => set('benefits', e.target.value)}
          className={inputCls}
        />
      </div>
      <div>
        <label htmlFor={`${uid}-terms`} className={labelCls}>
          Terms
        </label>
        <textarea
          id={`${uid}-terms`}
          rows={3}
          maxLength={LIMITS.terms}
          value={form.terms}
          onChange={(e) => set('terms', e.target.value)}
          className={inputCls}
        />
      </div>
      {showProblem ? (
        <p id={`${uid}-problem`} className="text-[12.5px] text-[var(--ui-warn)]">
          {problem}
        </p>
      ) : null}
      <button
        type="button"
        disabled={problem !== null || saving}
        aria-describedby={showProblem ? `${uid}-problem` : undefined}
        onClick={() => onSave(toBody(form))}
        className="self-start rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
      >
        {saving ? 'Saving…' : submitLabel}
      </button>
    </div>
  );
}

function TemplateRow({ template }: { template: OfferTemplate }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const invalidate = () => invalidateAfterHrMutation(qc);

  const saveMut = useMutation({
    mutationFn: (body: TemplateInput) => updateOfferTemplate(template.id, body),
    onSuccess: () => {
      toast.success('Template updated');
      setEditing(false);
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not update this template.')),
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteOfferTemplate(template.id),
    onSuccess: () => {
      toast.success('Template deleted');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not delete this template.')),
  });

  return (
    <GlassCard className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[14px] font-medium text-foreground">{template.name}</h3>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            aria-label={editing ? `Cancel editing ${template.name}` : `Edit ${template.name}`}
            aria-expanded={editing}
            className="min-h-[40px] rounded-full px-3 text-[13px] font-medium text-[var(--ui-info)] hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            {editing ? 'Cancel' : 'Edit'}
          </button>
          <ConfirmDeleteButton
            title={`Delete ${template.name}`}
            confirmText="Delete"
            pending={deleteMut.isPending}
            onConfirm={() => deleteMut.mutate()}
          />
        </div>
      </div>
      {!editing ? (
        <p className="mt-1 text-[12px] text-muted-foreground">
          {[
            template.employment_type?.replace('_', ' '),
            template.pay_period,
            template.currency,
            template.valid_days ? `${template.valid_days}-day offer window` : null,
          ]
            .filter(Boolean)
            .join(' · ') || 'No details set'}
        </p>
      ) : (
        <div className="mt-3">
          <TemplateForm
            initial={toInput(template)}
            onSave={(body) => saveMut.mutate(body)}
            saving={saveMut.isPending}
            submitLabel="Save"
          />
        </div>
      )}
    </GlassCard>
  );
}

export default function OfferTemplates() {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState('');

  const list = useQuery({ queryKey: ['hr', 'offer-templates'], queryFn: listOfferTemplates });

  const createMut = useMutation({
    mutationFn: (body: TemplateInput) => createOfferTemplate(body),
    onSuccess: () => {
      toast.success('Template created');
      setCreating(false);
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not create this template.')),
  });

  const needle = search.trim().toLowerCase();
  const rows = (list.data ?? []).filter((t) => !needle || t.name.toLowerCase().includes(needle));

  return (
    <Page width="form">
      <PageHeader
        title="Offer templates"
        description="Reusable starting points for an offer. Compensation is never part of a template - every offer sets its own base salary."
      />

      <Toolbar
        trailing={
          <>
            {list.data ? (
              <span className="text-[13px] tabular-nums text-muted-foreground" role="status">
                {rows.length} of {list.data.length} template{list.data.length === 1 ? '' : 's'}
              </span>
            ) : null}
            <button
              type="button"
              onClick={() => setCreating((v) => !v)}
              className="min-h-[40px] rounded-full border border-[var(--ui-line-strong)] px-4 py-1.5 text-[13px] font-medium text-foreground hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              {creating ? 'Cancel' : 'New template'}
            </button>
          </>
        }
      >
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search templates"
          aria-label="Search templates"
          className="min-h-[40px] w-full min-w-[200px] rounded-[10px] border border-border bg-secondary px-3 text-[13px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] sm:max-w-[320px]"
        />
      </Toolbar>

      {creating ? (
        <GlassCard className="p-4">
          <TemplateForm
            initial={EMPTY}
            onSave={(body) => createMut.mutate(body)}
            saving={createMut.isPending}
            submitLabel="Create template"
          />
        </GlassCard>
      ) : null}

      {list.isLoading ? (
        <div className="flex flex-col gap-3" role="status" aria-label="Loading templates">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[72px] w-full rounded-card" />
          ))}
        </div>
      ) : list.isError ? (
        <QueryErrorPanel
          title="Could not load templates."
          error={list.error}
          onRetry={() => void list.refetch()}
          retrying={list.isFetching}
        />
      ) : rows.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">
          {list.data && list.data.length > 0 ? 'No templates match.' : 'No templates yet.'}
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {rows.map((t) => (
            <TemplateRow key={t.id} template={t} />
          ))}
        </div>
      )}
    </Page>
  );
}
