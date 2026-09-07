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
import { GlassCard, Pill } from '@/design/components/primitives';
import { AlertCircle, Plus, X } from '@/design/components/icons';

const INPUT =
  'w-full rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3 py-2 text-[13.5px] text-white placeholder:text-[#5a5f66] focus:border-[var(--accent)] focus:outline-none';
const LABEL = 'mb-1.5 block text-[12.5px] font-medium text-[#b8babf]';

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
              className="flex items-start justify-between gap-2 rounded-[10px] border border-white/[0.07] bg-white/[0.02] px-3 py-1.5"
            >
              <span className="text-[13px] text-[#d5d7da]">{item}</span>
              <button
                type="button"
                aria-label={`Remove ${item}`}
                onClick={() => onChange(list.filter((_, j) => j !== i))}
                className="shrink-0 text-[#888b91] hover:text-[#e6714f]"
              >
                <X size={13} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex gap-2">
        <input
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
          className={INPUT}
        />
        <button
          type="button"
          onClick={add}
          disabled={full || !draft.trim()}
          className="shrink-0 rounded-[10px] border border-white/[0.1] px-3 text-[#b8babf] hover:text-white disabled:opacity-40"
          aria-label={`Add to ${label}`}
        >
          <Plus size={14} aria-hidden="true" />
        </button>
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
      client.setQueryData(['requisition', requisition.id], updated);
      toast.success('Posting updated.');
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : 'Could not save the posting.'),
  });

  const num = (v: string): number | null => (v === '' ? null : Number(v));

  return (
    <section aria-labelledby="posting-heading" className="flex flex-col gap-4">
      <div>
        <h2 id="posting-heading" className="text-[16px] font-semibold text-white">
          The posting
        </h2>
        <p className="mt-1 max-w-[62ch] text-[13px] leading-relaxed text-[#888b91]">
          What a candidate sees on the job board and before applying. Everything here
          is optional — leave a field blank and it simply is not shown.
        </p>
      </div>

      <GlassCard className="p-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="p-department" className={LABEL}>
              Department
            </label>
            <input
              id="p-department"
              value={form.department ?? ''}
              onChange={(e) => set('department', e.target.value || null)}
              placeholder="Engineering"
              className={INPUT}
            />
          </div>
          <div>
            <label htmlFor="p-location" className={LABEL}>
              Location
            </label>
            <input
              id="p-location"
              value={form.location ?? ''}
              onChange={(e) => set('location', e.target.value || null)}
              placeholder="Bengaluru"
              className={INPUT}
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
              className={INPUT}
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
              <input
                aria-label="Minimum experience in years"
                type="number"
                min={0}
                max={60}
                value={form.experience_min_years ?? ''}
                onChange={(e) => set('experience_min_years', num(e.target.value))}
                className={INPUT}
              />
              <span className="text-[#5a5f66]">to</span>
              <input
                aria-label="Maximum experience in years"
                type="number"
                min={0}
                max={60}
                value={form.experience_max_years ?? ''}
                onChange={(e) => set('experience_max_years', num(e.target.value))}
                className={INPUT}
              />
            </div>
            {experienceError ? (
              <p className="mt-1 flex items-center gap-1.5 text-[11.5px] text-[#e6714f]">
                <AlertCircle size={12} aria-hidden="true" />
                {experienceError}
              </p>
            ) : null}
          </div>
        </div>

        <div className="mt-4 rounded-[12px] border border-white/[0.07] p-3">
          <span className={LABEL}>Salary range</span>
          <div className="flex flex-wrap items-center gap-2">
            <input
              aria-label="Salary currency"
              value={form.salary_currency ?? ''}
              onChange={(e) => set('salary_currency', e.target.value || null)}
              placeholder="INR"
              className={`${INPUT} w-20`}
            />
            <input
              aria-label="Minimum salary"
              type="number"
              min={0}
              value={form.salary_min ?? ''}
              onChange={(e) => set('salary_min', num(e.target.value))}
              className={`${INPUT} w-36`}
            />
            <span className="text-[#5a5f66]">to</span>
            <input
              aria-label="Maximum salary"
              type="number"
              min={0}
              value={form.salary_max ?? ''}
              onChange={(e) => set('salary_max', num(e.target.value))}
              className={`${INPUT} w-36`}
            />
          </div>
          {salaryError ? (
            <p className="mt-1 flex items-center gap-1.5 text-[11.5px] text-[#e6714f]">
              <AlertCircle size={12} aria-hidden="true" />
              {salaryError}
            </p>
          ) : null}
          {/* Recording a band and advertising it are different decisions, and
              the switch sits beside the fields so that is obvious here rather
              than discovered on the job board. */}
          <label className="mt-3 flex cursor-pointer items-start gap-2 text-[12.5px] leading-relaxed text-[#b8babf]">
            <input
              type="checkbox"
              checked={form.salary_visible}
              onChange={(e) => set('salary_visible', e.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
            />
            <span>
              Show this range to candidates.
              {form.salary_visible ? null : (
                <span className="text-[#70757c]">
                  {' '}
                  Off — the range is kept for your planning and never sent to the job
                  board.
                </span>
              )}
            </span>
          </label>
        </div>

        <div className="mt-4 flex flex-col gap-4">
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

        <div className="mt-5 flex items-center gap-3">
          <Pill
            onClick={() => save.mutate()}
            disabled={save.isPending || Boolean(experienceError || salaryError)}
            className="px-5 py-2.5"
          >
            {save.isPending ? 'Saving…' : 'Save posting'}
          </Pill>
          {experienceError || salaryError ? (
            <span className="text-[12px] text-[#888b91]">
              Fix the range above to save.
            </span>
          ) : null}
        </div>
      </GlassCard>
    </section>
  );
}
