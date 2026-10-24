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
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { plural } from '@/lib/formatters';
import { Page, PageHeader, Toolbar } from '@/design/components/page';

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
      <FormField id={`${uid}-name`} label="Name">
        {(p) => (
          <Input
            {...p}
            maxLength={LIMITS.name}
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
          />
        )}
      </FormField>
      <div className="grid grid-cols-2 gap-2.5">
        <FormField id={`${uid}-type`} label="Employment type">
          {(p) => (
            <SelectControl
              {...p}
              value={form.employment_type}
              onChange={(e) => set('employment_type', e.target.value)}
            >
              {EMPLOYMENT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t.replace('_', ' ')}
                </option>
              ))}
            </SelectControl>
          )}
        </FormField>
        <FormField id={`${uid}-period`} label="Pay period">
          {(p) => (
            <SelectControl
              {...p}
              value={form.pay_period}
              onChange={(e) => set('pay_period', e.target.value)}
            >
              {PAY_PERIODS.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </SelectControl>
          )}
        </FormField>
        <FormField id={`${uid}-currency`} label="Currency">
          {(p) => (
            <Input
              {...p}
              value={form.currency}
              onChange={(e) => set('currency', e.target.value.toUpperCase())}
              maxLength={3}
            />
          )}
        </FormField>
        <FormField id={`${uid}-valid`} label="Valid for (days)">
          {(p) => (
            <Input
              {...p}
              type="number"
              min={1}
              max={60}
              step={1}
              required
              value={form.valid_days}
              onChange={(e) => set('valid_days', e.target.value)}
            />
          )}
        </FormField>
        <FormField id={`${uid}-probation`} label="Probation (months)">
          {(p) => (
            <Input
              {...p}
              type="number"
              min={0}
              max={24}
              step={1}
              value={form.probation_months}
              onChange={(e) => set('probation_months', e.target.value)}
            />
          )}
        </FormField>
        <FormField id={`${uid}-notice`} label="Notice period (days)">
          {(p) => (
            <Input
              {...p}
              type="number"
              min={0}
              max={365}
              step={1}
              value={form.notice_period_days}
              onChange={(e) => set('notice_period_days', e.target.value)}
            />
          )}
        </FormField>
      </div>
      <FormField id={`${uid}-benefits`} label="Benefits">
        {(p) => (
          <Textarea
            {...p}
            rows={2}
            maxLength={LIMITS.benefits}
            value={form.benefits}
            onChange={(e) => set('benefits', e.target.value)}
          />
        )}
      </FormField>
      <FormField id={`${uid}-terms`} label="Terms">
        {(p) => (
          <Textarea
            {...p}
            rows={3}
            maxLength={LIMITS.terms}
            value={form.terms}
            onChange={(e) => set('terms', e.target.value)}
          />
        )}
      </FormField>
      {showProblem ? (
        <p id={`${uid}-problem`} className="text-[12.5px] text-[var(--ui-warn)]">
          {problem}
        </p>
      ) : null}
      <Button
        type="button"
        disabled={problem !== null || saving}
        aria-describedby={showProblem ? `${uid}-problem` : undefined}
        onClick={() => onSave(toBody(form))}
        className="self-start"
      >
        {saving ? 'Saving…' : submitLabel}
      </Button>
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
          <Button
            type="button"
            variant="ghost"
            onClick={() => setEditing((v) => !v)}
            aria-label={editing ? `Cancel editing ${template.name}` : `Edit ${template.name}`}
            aria-expanded={editing}
          >
            {editing ? 'Cancel' : 'Edit'}
          </Button>
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
                {rows.length} of {plural(list.data.length, 'template')}
              </span>
            ) : null}
            <Button
              type="button"
              variant={creating ? 'ghost' : 'outline'}
              aria-expanded={creating}
              onClick={() => setCreating((v) => !v)}
            >
              {creating ? 'Cancel' : 'New template'}
            </Button>
          </>
        }
      >
        <Input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search templates"
          aria-label="Search templates"
          className="min-w-[200px] sm:max-w-[320px]"
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
