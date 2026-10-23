// CheckinSection — the 90-day hire check-in (PH5 wave-1 follow-up A). Shared
// by CandidateDrawer (shown only once an application is hired) and the
// "Check-ins due" drawer opened from the analytics page's Quality of hire
// card — both know only an `enrolmentId`, which is all this needs.
//
// No free text anywhere: employment, the reason someone left, and their
// performance are each a closed choice, because this feeds the governed
// checkin_coverage/retention_90d/performance_90d metrics and a free-text
// field there would be unanalysable. A correction is the same form again,
// pre-filled — the row it replaces is kept, not explained (security review:
// no `correction_reason`). Nothing here can change an application or a
// decision (D-05) — said explicitly under the form.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createCheckin,
  correctCheckin,
  getEnrolmentCheckins,
  type CheckinEmployment,
  type CheckinInput,
  type CheckinLeftReason,
  type CheckinOut,
  type CheckinPerformance,
} from '@/api/checkins';
import { QueryErrorPanel } from '@/components/QueryState';
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/errorMessage';
import { formatDate } from '@/lib/formatters';

const EMPLOYMENT_OPTIONS: { value: CheckinEmployment; label: string }[] = [
  { value: 'employed', label: 'Still employed' },
  { value: 'left', label: 'Left' },
];

const LEFT_REASON_OPTIONS: { value: CheckinLeftReason; label: string }[] = [
  { value: 'voluntary', label: 'Voluntary' },
  { value: 'involuntary', label: 'Involuntary' },
  { value: 'unknown', label: 'Unknown' },
];

const PERFORMANCE_OPTIONS: { value: CheckinPerformance; label: string }[] = [
  { value: 'below', label: 'Below expectations' },
  { value: 'meets', label: 'Meets expectations' },
  { value: 'exceeds', label: 'Exceeds expectations' },
];

const LEFT_REASON_LABEL: Record<CheckinLeftReason, string> = {
  voluntary: 'Voluntary',
  involuntary: 'Involuntary',
  unknown: 'Unknown',
};

const PERFORMANCE_LABEL: Record<CheckinPerformance, string> = {
  below: 'Below expectations',
  meets: 'Meets expectations',
  exceeds: 'Exceeds expectations',
};

function summaryLine(c: CheckinOut): string {
  if (c.employment === 'left') {
    return `Left${c.left_reason ? ` - ${LEFT_REASON_LABEL[c.left_reason]}` : ''}`;
  }
  return `Still employed${c.performance ? ` - ${PERFORMANCE_LABEL[c.performance]}` : ''}`;
}

/** Never the raw id — a departed recorder's name goes null, not the UUID. */
function recordedByLine(c: CheckinOut): string {
  return `Recorded by ${c.recorded_by_name ?? 'a former team member'} on ${formatDate(c.recorded_at)}`;
}

interface FormState {
  employment: CheckinEmployment | '';
  leftReason: CheckinLeftReason | '';
  performance: CheckinPerformance | '';
}

const EMPTY_FORM: FormState = { employment: '', leftReason: '', performance: '' };

export default function CheckinSection({ enrolmentId }: { enrolmentId: string }): JSX.Element {
  const qc = useQueryClient();
  const [correcting, setCorrecting] = useState<CheckinOut | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);

  const checkins = useQuery({
    queryKey: ['hr', 'enrolment', enrolmentId, 'checkins'],
    queryFn: () => getEnrolmentCheckins(enrolmentId),
    retry: false,
  });

  function invalidate() {
    void qc.invalidateQueries({ queryKey: ['hr', 'enrolment', enrolmentId, 'checkins'] });
    // A check-in write feeds checkin_coverage/retention_90d/performance_90d
    // directly, and it is exactly what moves this hire off the "due" list —
    // both must refresh the moment one is recorded or corrected, from
    // whichever consumer (CandidateDrawer or the due-list drawer) this is
    // mounted under. Prefix match: covers every funnel query regardless of
    // its cohort/filter args (the pipeline funnel AND the quality-of-hire
    // "hire-cohort-standing" query share the ['hr','analytics','funnel', …]
    // prefix).
    void qc.invalidateQueries({ queryKey: ['hr', 'analytics', 'funnel'] });
    void qc.invalidateQueries({ queryKey: ['hr', 'checkins', 'due'] });
  }

  function bodyFromForm(): CheckinInput {
    return {
      employment: form.employment as CheckinEmployment,
      ...(form.employment === 'left' ? { left_reason: form.leftReason as CheckinLeftReason } : {}),
      ...(form.employment === 'employed'
        ? { performance: form.performance as CheckinPerformance }
        : {}),
    };
  }

  const createMut = useMutation({
    mutationFn: () => createCheckin(enrolmentId, bodyFromForm()),
    onSuccess: () => {
      setForm(EMPTY_FORM);
      setFormError(null);
      invalidate();
    },
    onError: (e: unknown) => setFormError(errorMessage(e, 'Could not save this check-in.')),
  });

  const correctMut = useMutation({
    mutationFn: ({ id }: { id: string }) => correctCheckin(id, bodyFromForm()),
    onSuccess: () => {
      setCorrecting(null);
      setForm(EMPTY_FORM);
      setFormError(null);
      invalidate();
    },
    onError: (e: unknown) => setFormError(errorMessage(e, 'Could not save this correction.')),
  });

  if (checkins.isLoading) {
    return (
      <div
        id="checkin-section"
        tabIndex={-1}
        className="mt-6 border-t border-border pt-5 outline-none"
      >
        <h3 className="text-[15px] font-bold text-foreground">90-day check-in</h3>
        <div className="mt-2 flex flex-col gap-2" aria-busy="true">
          <span className="sr-only">Loading…</span>
          <Skeleton className="h-3.5 w-3/4" />
          <Skeleton className="h-14" />
        </div>
      </div>
    );
  }
  if (checkins.isError || !checkins.data) {
    return (
      <div
        id="checkin-section"
        tabIndex={-1}
        className="mt-6 border-t border-border pt-5 outline-none"
      >
        <h3 className="text-[15px] font-bold text-foreground">90-day check-in</h3>
        <QueryErrorPanel
          className="mt-2"
          title="Could not load the check-in for this hire."
          error={checkins.error}
          onRetry={() => void checkins.refetch()}
          retrying={checkins.isFetching}
        />
      </div>
    );
  }

  const { notice, checkins: rows, window: win } = checkins.data;
  const live = rows.find((c) => !c.superseded) ?? null;
  const earlier = rows.filter((c) => c.superseded);
  const showingForm = win.open && (correcting !== null || live === null);

  // "Still employed" only from day 80; "left" is allowed any time the window
  // is open. Absent employed_from (no start date on record) is read as no
  // restriction rather than an invented one.
  const employedAvailable =
    !win.employed_from || Date.now() >= new Date(win.employed_from).getTime();

  function startCorrection(c: CheckinOut) {
    setCorrecting(c);
    setForm({
      employment: c.employment,
      leftReason: c.left_reason ?? '',
      performance: c.performance ?? '',
    });
    setFormError(null);
  }

  function cancelCorrection() {
    setCorrecting(null);
    setForm(EMPTY_FORM);
    setFormError(null);
  }

  const canSubmit =
    form.employment !== '' &&
    (form.employment === 'left'
      ? form.leftReason !== ''
      : form.performance !== '' && employedAvailable);

  const pending = createMut.isPending || correctMut.isPending;

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    if (correcting) correctMut.mutate({ id: correcting.checkin_id });
    else createMut.mutate();
  }

  return (
    <div
      id="checkin-section"
      tabIndex={-1}
      className="mt-6 border-t border-border pt-5 outline-none"
    >
      <h3 className="text-[15px] font-bold text-foreground">90-day check-in</h3>
      {/* Server copy, shown verbatim — not ours to paraphrase. */}
      <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">{notice}</p>

      {!win.open ? (
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          The check-in window for this hire closed
          {win.closes_at ? ` on ${formatDate(win.closes_at)}` : ''}.
          {rows.length === 0 ? ' No check-in was ever recorded.' : ''}
        </p>
      ) : null}

      {live && !showingForm ? (
        <div className="mt-2.5 rounded-[10px] border border-border p-3">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-foreground">{summaryLine(live)}</span>
            {win.open ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="shrink-0"
                onClick={() => startCorrection(live)}
              >
                Correct
              </Button>
            ) : null}
          </div>
          <p className="mt-1 text-[12px] text-muted-foreground">{recordedByLine(live)}</p>
        </div>
      ) : null}

      {showingForm ? (
        <form
          onSubmit={onSubmit}
          className="mt-2.5 flex flex-col gap-2.5 rounded-[12px] border border-border p-3"
        >
          {correcting ? (
            <p className="text-[12px] text-muted-foreground">
              The earlier record is kept as a previous version.
            </p>
          ) : null}

          <FormField
            id={`checkin-employment-${enrolmentId}`}
            label="Employment"
            hint={
              employedAvailable
                ? undefined
                : win.employed_from
                  ? `Available from ${formatDate(win.employed_from)}`
                  : 'Available from: not set'
            }
          >
            {(p) => (
              <SelectControl
                {...p}
                value={form.employment}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    employment: e.target.value as CheckinEmployment,
                    leftReason: '',
                    performance: '',
                  }))
                }
              >
                <option value="">Choose…</option>
                {EMPLOYMENT_OPTIONS.map((o) => (
                  <option
                    key={o.value}
                    value={o.value}
                    disabled={o.value === 'employed' && !employedAvailable}
                  >
                    {o.label}
                  </option>
                ))}
              </SelectControl>
            )}
          </FormField>

          {form.employment === 'left' ? (
            <FormField id={`checkin-reason-${enrolmentId}`} label="Reason">
              {(p) => (
                <SelectControl
                  {...p}
                  value={form.leftReason}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, leftReason: e.target.value as CheckinLeftReason }))
                  }
                >
                  <option value="">Choose…</option>
                  {LEFT_REASON_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </SelectControl>
              )}
            </FormField>
          ) : null}

          {form.employment === 'employed' ? (
            <FormField id={`checkin-performance-${enrolmentId}`} label="Performance">
              {(p) => (
                <SelectControl
                  {...p}
                  value={form.performance}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, performance: e.target.value as CheckinPerformance }))
                  }
                >
                  <option value="">Choose…</option>
                  {PERFORMANCE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </SelectControl>
              )}
            </FormField>
          ) : null}

          {formError ? (
            <p role="alert" className="text-[12px] text-[var(--ui-danger)]">
              {formError}
            </p>
          ) : null}

          <div className="flex items-center gap-2">
            <Button type="submit" disabled={!canSubmit || pending}>
              {pending ? 'Saving…' : correcting ? 'Save correction' : 'Save check-in'}
            </Button>
            {correcting ? (
              <Button type="button" variant="ghost" onClick={cancelCorrection} disabled={pending}>
                Cancel
              </Button>
            ) : null}
          </div>

          <p className="text-[12px] text-muted-foreground">
            This never changes the application or any decision.
          </p>
        </form>
      ) : null}

      {earlier.length > 0 ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-[12px] text-muted-foreground">
            Earlier versions ({earlier.length})
          </summary>
          <ul className="mt-1.5 flex flex-col gap-1.5 pl-2">
            {earlier.map((c) => (
              <li key={c.checkin_id} className="text-[12px] text-muted-foreground">
                {summaryLine(c)} · {formatDate(c.recorded_at)}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
