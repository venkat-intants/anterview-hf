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
import { GlassCard } from '@/design/components/primitives';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { Reveal } from '@/design/components/Reveal';
import { Loader2 } from '@/design/components/icons';

const inputCls =
  'mt-1 w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] ' +
  'text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none';
const labelCls = 'text-[12px] font-medium text-[var(--ui-soft)]';

const EMPLOYMENT_TYPES: EmploymentType[] = ['full_time', 'part_time', 'contract', 'internship'];
const PAY_PERIODS: PayPeriod[] = ['annual', 'monthly', 'hourly'];

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

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

const EMPTY: FormState = {
  name: '',
  employment_type: '',
  currency: '',
  pay_period: '',
  probation_months: '',
  notice_period_days: '',
  benefits: '',
  terms: '',
  valid_days: '',
};

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

function toBody(f: FormState): TemplateInput {
  return {
    name: f.name.trim() || undefined,
    employment_type: (f.employment_type || undefined) as EmploymentType | undefined,
    currency: f.currency.trim() || undefined,
    pay_period: (f.pay_period || undefined) as PayPeriod | undefined,
    probation_months: f.probation_months === '' ? undefined : Number(f.probation_months),
    notice_period_days: f.notice_period_days === '' ? undefined : Number(f.notice_period_days),
    benefits: f.benefits.trim() || undefined,
    terms: f.terms.trim() || undefined,
    valid_days: f.valid_days === '' ? undefined : Number(f.valid_days),
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

  return (
    <div className="flex flex-col gap-2.5">
      <div>
        <label htmlFor={`${uid}-name`} className={labelCls}>
          Name
        </label>
        <input
          id={`${uid}-name`}
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
            <option value="">—</option>
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
            <option value="">—</option>
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
          value={form.terms}
          onChange={(e) => set('terms', e.target.value)}
          className={inputCls}
        />
      </div>
      <button
        type="button"
        disabled={!form.name.trim() || saving}
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
  const invalidate = () => qc.invalidateQueries({ queryKey: ['hr', 'offer-templates'] });

  const saveMut = useMutation({
    mutationFn: (body: TemplateInput) => updateOfferTemplate(template.id, body),
    onSuccess: () => {
      toast.success('Template updated');
      setEditing(false);
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not update this template')),
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteOfferTemplate(template.id),
    onSuccess: () => {
      toast.success('Template deleted');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not delete this template')),
  });

  return (
    <GlassCard className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[14px] font-medium text-foreground">{template.name}</h3>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            className="text-[12px] text-[var(--ui-info)] hover:underline"
          >
            {editing ? 'Close' : 'Edit'}
          </button>
          <ConfirmDeleteButton
            title={`Delete ${template.name}`}
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

  const list = useQuery({ queryKey: ['hr', 'offer-templates'], queryFn: listOfferTemplates });

  const createMut = useMutation({
    mutationFn: (body: TemplateInput) => createOfferTemplate(body),
    onSuccess: () => {
      toast.success('Template created');
      setCreating(false);
      void qc.invalidateQueries({ queryKey: ['hr', 'offer-templates'] });
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not create this template')),
  });

  const rows = list.data ?? [];

  return (
    <div className="mx-auto w-full max-w-[720px] px-4 py-8">
      <Reveal>
        <h1 className="text-[24px] font-semibold tracking-[-0.6px] text-foreground">
          Offer templates
        </h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          Reusable starting points for an offer. Compensation is never part of a template — every
          offer sets its own base salary.
        </p>
      </Reveal>

      <div className="mt-5">
        <button
          type="button"
          onClick={() => setCreating((v) => !v)}
          className="text-[12.5px] text-[var(--ui-info)] hover:underline"
        >
          {creating ? 'Close' : 'New template'}
        </button>
        {creating ? (
          <GlassCard className="mt-3 p-4">
            <TemplateForm
              initial={EMPTY}
              onSave={(body) => createMut.mutate(body)}
              saving={createMut.isPending}
              submitLabel="Create template"
            />
          </GlassCard>
        ) : null}
      </div>

      {list.isLoading ? (
        <p className="mt-6 flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Loading…
        </p>
      ) : list.isError ? (
        <p className="mt-6 text-[13px] text-[var(--ui-danger)]">Could not load templates.</p>
      ) : rows.length === 0 ? (
        <p className="mt-6 text-[13px] text-muted-foreground">No templates yet.</p>
      ) : (
        <div className="mt-6 flex flex-col gap-3">
          {rows.map((t) => (
            <TemplateRow key={t.id} template={t} />
          ))}
        </div>
      )}
    </div>
  );
}
