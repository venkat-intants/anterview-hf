// PostingEditor — what a candidate reads before deciding to apply.
//
// Builder steps 1 and 2 (Basics and Job description) on one panel rather than
// two wizard pages: this is an edit surface for an opening that already exists,
// not a creation flow, and paging a recruiter through two screens to change a
// location would be ceremony.
//
// Everything here is optional, and that is load-bearing rather than lazy.
// Several thousand openings already exist, some minted by the Group B backfill
// from nothing but a job title, and a form that required a department would
// make every one of them unsaveable.
//
// SALARY IS THE ONE FIELD WITH A SWITCH. A band can be recorded for internal
// planning and never advertised, so "we know the range" and "we publish the
// range" are separate — and the public endpoint omits the numbers entirely
// rather than sending nulls, so a candidate cannot tell a withheld salary from
// an unrecorded one. The switch is next to the fields so the distinction is
// obvious at the moment it matters.

import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  EMPLOYMENT_TYPE_LABELS,
  rangeError,
  updateRequisition,
  type EmploymentType,
  type Requisition,
} from '@/api/requisitions';
import { toast } from '@/lib/toast';
import { queryKeys } from '@/lib/queryKeys';
import { cn } from '@/lib/utils';
import { SectionCard } from '@/design/components/page';
import { AlertCircle, Plus, X } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

// Native <select> in the same look as the shared Input.
const SELECT =
  'h-10 w-full rounded-[10px] border border-input bg-card px-3.5 text-[14px] text-foreground hover:border-[var(--ui-line-strong)] focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30';
const LABEL = 'mb-1.5 block text-[13px] font-semibold text-foreground';
// A hairline-divided group inside the card, titled like the drawer's sections.
const GROUP = 'mt-6 border-t border-border pt-5';
const GROUP_TITLE = 'mb-3 text-[15px] font-bold text-foreground';

/** A repeatable list of short strings — responsibilities, skills. */
function ListField({
  id,
  label,
  hint,
  items,
  onChange,
}: {
  id: string;
  label: string;
  hint?: string;
  // Optional despite the server always sending an array: this renders inside
  // the builder page, and a payload without one — an older cached response,
  // a fixture, a future field rename — should degrade to an empty list rather
  // than white-screen the whole page.
  items?: string[];
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState('');
  const list = items ?? [];
  // The server caps these at twenty and refuses a longer list rather than
  // truncating, so the limit is shown here instead of being discovered.
  const full = list.length >= 20;

  function add(): void {
    const value = draft.trim();
    if (!value || full) return;
    onChange([...list, value]);
    setDraft('');
  }

  return (
    <div>
      <label htmlFor={id} className={LABEL}>
        {label}
      </label>
      {list.length > 0 ? (
        <ul className="mb-2 flex flex-col gap-1.5">
          {list.map((item, i) => (
            <li
              key={`${item}-${i}`}
              className="flex items-center justify-between gap-2 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] py-0.5 pl-3.5 pr-1"
            >
              <span className="min-w-0 break-words py-1.5 text-[14px] text-foreground">{item}</span>
              <button
                type="button"
                aria-label={`Remove ${item}`}
                onClick={() => onChange(list.filter((_, j) => j !== i))}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-[var(--ui-danger-wash)] hover:text-[var(--ui-danger)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                <X size={16} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex gap-2">
        <Input
          id={id}
          value={draft}
          disabled={full}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              add();
            }
          }}
          placeholder={full ? 'Twenty is the limit' : hint}
          className="min-w-0 flex-1"
        />
        <Button
          type="button"
          variant="outline"
          size="icon"
          onClick={add}
          disabled={full || !draft.trim()}
          aria-label={`Add to ${label}`}
        >
          <Plus aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}

export default function PostingEditor({ requisition }: { requisition: Requisition }) {
  const client = useQueryClient();
  const [form, setForm] = useState(requisition);

  // The requisition can change under us — the workflow page refetches it — and
  // an editor that ignores that shows stale values with no indication why.
  useEffect(() => setForm(requisition), [requisition]);

  const set = <K extends keyof Requisition>(key: K, value: Requisition[K]): void =>
    setForm((f) => ({ ...f, [key]: value }));

  const experienceError = rangeError(
    form.experience_min_years,
    form.experience_max_years,
    'experience',
  );
  const salaryError = rangeError(form.salary_min, form.salary_max, 'salary');

  const save = useMutation({
    mutationFn: () =>
      updateRequisition(requisition.id, {
        department: form.department,
        location: form.location,
        employment_type: form.employment_type,
        experience_min_years: form.experience_min_years,
        experience_max_years: form.experience_max_years,
        salary_min: form.salary_min,
        salary_max: form.salary_max,
        salary_currency: form.salary_currency,
        salary_visible: form.salary_visible,
        responsibilities: form.responsibilities ?? [],
        required_skills: form.required_skills ?? [],
        nice_to_have_skills: form.nice_to_have_skills ?? [],
      }),
    onSuccess: (updated) => {
      client.setQueryData(queryKeys.requisition(requisition.id), updated);
      // Saving the posting publishes a JD version, so the setup rail's JD tick
      // has to be re-read.
      void client.invalidateQueries({ queryKey: queryKeys.jdHistory(requisition.id) });
      toast.success('Posting updated.');
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : 'Could not save the posting.'),
  });

  const num = (v: string): number | null => (v === '' ? null : Number(v));

  return (
    <SectionCard
      title="The posting"
      description="What a candidate sees on the job board and before applying. Everything here is optional - leave a field blank and it simply is not shown."
    >
      <h3 className={GROUP_TITLE}>Basics</h3>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="p-department" className={LABEL}>
            Department
          </label>
          <Input
            id="p-department"
            value={form.department ?? ''}
            onChange={(e) => set('department', e.target.value || null)}
            placeholder="Engineering"
            className="h-10 rounded-[10px]"
          />
        </div>
        <div>
          <label htmlFor="p-location" className={LABEL}>
            Location
          </label>
          <Input
            id="p-location"
            value={form.location ?? ''}
            onChange={(e) => set('location', e.target.value || null)}
            placeholder="Bengaluru"
            className="h-10 rounded-[10px]"
          />
        </div>
        <div>
          <label htmlFor="p-type" className={LABEL}>
            Employment type
          </label>
          <select
            id="p-type"
            value={form.employment_type ?? ''}
            onChange={(e) =>
              set('employment_type', (e.target.value || null) as EmploymentType | null)
            }
            className={SELECT}
          >
            <option value="">Not specified</option>
            {(Object.keys(EMPLOYMENT_TYPE_LABELS) as EmploymentType[]).map((t) => (
              <option key={t} value={t}>
                {EMPLOYMENT_TYPE_LABELS[t]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <span className={LABEL}>Experience (years)</span>
          <div className="flex items-center gap-2">
            <Input
              aria-label="Minimum experience in years"
              type="number"
              min={0}
              max={60}
              value={form.experience_min_years ?? ''}
              onChange={(e) => set('experience_min_years', num(e.target.value))}
              className="h-10 min-w-0 flex-1 rounded-[10px]"
            />
            <span className="text-muted-foreground">to</span>
            <Input
              aria-label="Maximum experience in years"
              type="number"
              min={0}
              max={60}
              value={form.experience_max_years ?? ''}
              onChange={(e) => set('experience_max_years', num(e.target.value))}
              className="h-10 min-w-0 flex-1 rounded-[10px]"
            />
          </div>
          {experienceError ? (
            <p
              role="alert"
              className="mt-1.5 flex items-center gap-1.5 text-[12.5px] text-[var(--ui-danger)]"
            >
              <AlertCircle size={13} aria-hidden="true" />
              {experienceError}
            </p>
          ) : null}
        </div>
      </div>

      <div className={GROUP}>
        <h3 className={GROUP_TITLE}>Salary range</h3>
        {/* One row like the experience row above. The widths go through cn
            (tailwind-merge) — the shared Input carries w-full, and a plain
            concatenation let w-full win so every field took its own line. */}
        {/* Below sm the currency sits above the range (two lines that always
            fit); from sm up it is one row. The min / to / max trio is its own
            flex row so it never needs the phone's full width twice. */}
        <div
          className="flex flex-col gap-2 sm:flex-row sm:items-center"
          data-testid="salary-row"
        >
          <Input
            aria-label="Salary currency"
            value={form.salary_currency ?? ''}
            onChange={(e) => set('salary_currency', e.target.value || null)}
            placeholder="INR"
            className={cn('h-10 w-20 shrink-0 rounded-[10px]')}
          />
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <Input
              aria-label="Minimum salary"
              type="number"
              min={0}
              value={form.salary_min ?? ''}
              onChange={(e) => set('salary_min', num(e.target.value))}
              className={cn('w-auto min-w-0 flex-1')}
            />
            <span className="shrink-0 text-muted-foreground">to</span>
            <Input
              aria-label="Maximum salary"
              type="number"
              min={0}
              value={form.salary_max ?? ''}
              onChange={(e) => set('salary_max', num(e.target.value))}
              className={cn('w-auto min-w-0 flex-1')}
            />
          </div>
        </div>
        {salaryError ? (
          <p
            role="alert"
            className="mt-1.5 flex items-center gap-1.5 text-[12.5px] text-[var(--ui-danger)]"
          >
            <AlertCircle size={13} aria-hidden="true" />
            {salaryError}
          </p>
        ) : null}
        {/* Recording a band and advertising it are different decisions, and
            the switch sits beside the fields so that is obvious here rather
            than discovered on the job board. */}
        <label className="mt-3 flex min-h-10 cursor-pointer items-start gap-2.5 text-[13.5px] leading-relaxed text-[var(--ui-soft)]">
          <input
            type="checkbox"
            checked={form.salary_visible}
            onChange={(e) => set('salary_visible', e.target.checked)}
            className="mt-1 h-4 w-4 shrink-0 accent-[var(--accent)]"
          />
          <span>
            Show this range to candidates.
            {form.salary_visible ? null : (
              <span className="text-muted-foreground">
                {' '}
                Off - the range is kept for your planning and never sent to the job board.
              </span>
            )}
          </span>
        </label>
      </div>

      <div className={GROUP}>
        <h3 className={GROUP_TITLE}>The role</h3>
        <div className="flex flex-col gap-4">
          <ListField
            id="p-responsibilities"
            label="What they will do"
            hint="Ship and maintain the payments service"
            items={form.responsibilities}
            onChange={(v) => set('responsibilities', v)}
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <ListField
              id="p-required"
              label="Required skills"
              hint="Python"
              items={form.required_skills}
              onChange={(v) => set('required_skills', v)}
            />
            <ListField
              id="p-nice"
              label="Nice to have"
              hint="Kubernetes"
              items={form.nice_to_have_skills}
              onChange={(v) => set('nice_to_have_skills', v)}
            />
          </div>
        </div>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-border pt-5">
        <Button
          type="button"
          variant="outline"
          onClick={() => save.mutate()}
          disabled={save.isPending || Boolean(experienceError || salaryError)}
        >
          {save.isPending ? 'Saving…' : 'Save posting'}
        </Button>
        {experienceError || salaryError ? (
          <span className="text-[13px] text-muted-foreground">Fix the range above to save.</span>
        ) : null}
      </div>
    </SectionCard>
  );
}
