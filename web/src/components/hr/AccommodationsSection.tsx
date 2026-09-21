// AccommodationsSection — PH4-D2. The candidate drawer's view of one
// applicant's assessment accommodations: record, revise, revoke, and the
// full history.
//
// THIS IS DISABILITY-ADJACENT DATA. Three text fields, three different
// audiences (services/data_gateway/app/accommodations.py module docstring):
//   - `interviewer_note` — the ONE thing an assigned interviewer ever sees
//     about an accommodation (as `adjustments_note` on their own scorecard).
//   - `internal_note` — HR only. Never leaves this record.
//   - `other_adjustment` — describes a free-text adjustment. Neither the
//     candidate nor an interviewer ever sees this text; the candidate is told
//     only the fact that *an* adjustment exists (email + the exam banner),
//     never this wording.
// The candidate never sees `basis` either. Each field below carries a plain
// statement of who reads it, in the house voice for this kind of notice (see
// PublicOffer.tsx's `offer.acceptConsent` — a statement of what happens, not
// a caveat).
//
// Scope: whole applicant, one application (enrolment), one workflow round, or
// one exam round. The last two REQUIRE an application — the server 422s "A
// round-scoped adjustment needs an application." / "An exam-round-scoped
// adjustment needs an application." otherwise — so those two scopes are
// disabled here whenever there is no enrolment to scope them to, rather than
// letting the request go out to fail.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getEffectiveAccommodation,
  listAccommodations,
  recordAccommodation,
  reviseAccommodation,
  revokeAccommodation,
  type Accommodation,
  type AccommodationBasis,
} from '@/api/accommodations';
import { getWorkflow, listWorkflows, type Round } from '@/api/workflows';
import { formatDate } from '@/lib/formatters';
import { localInputToIso, toLocalInputValue } from '@/lib/localDatetime';
import { toast } from '@/lib/toast';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { StatusTag } from '@/design/components/primitives';
import { Clock, Info, Loader2 } from '@/design/components/icons';

const inputCls =
  'w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] ' +
  'text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] ' +
  'focus:outline-none disabled:opacity-60';
const labelCls = 'text-[12px] font-medium text-[var(--ui-soft)]';
const noteWarnCls = 'mt-1 flex items-start gap-1.5 text-[11.5px] leading-relaxed text-[var(--ui-faint)]';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

type Scope = 'applicant' | 'application' | 'round' | 'exam_round';

/** Rounds are fetched from the application's own published workflow, the
 *  same way HumanInterviewSection does — the round PICKER for a round or
 *  exam-round scope needs each round's id and, for exam rounds, the
 *  `exam_round_id` a round carries when it is exam-backed. */
function useApplicationRounds(requisitionId: string | null) {
  const workflows = useQuery({
    queryKey: ['hr', 'workflows', requisitionId],
    queryFn: () => listWorkflows(requisitionId as string),
    enabled: Boolean(requisitionId),
  });
  const publishedId = workflows.data?.find((w) => w.status === 'published')?.id ?? null;
  const workflow = useQuery({
    queryKey: ['hr', 'workflow', publishedId],
    queryFn: () => getWorkflow(publishedId as string),
    enabled: Boolean(publishedId),
  });
  const rounds = workflow.data?.rounds ?? [];
  return {
    rounds,
    examRounds: rounds.filter((r) => r.exam_round_id),
    isLoading: workflows.isLoading || (Boolean(publishedId) && workflow.isLoading),
    isError: workflows.isError || workflow.isError,
  };
}

function roundTitle(rounds: Round[], roundId: string): string | null {
  return rounds.find((r) => r.id === roundId)?.title ?? null;
}

function examRoundTitle(rounds: Round[], examRoundId: string): string | null {
  return rounds.find((r) => r.exam_round_id === examRoundId)?.title ?? null;
}

/** A short, human description of what a row is scoped to — falls back to a
 *  truncated id when the round can no longer be resolved (an archived or
 *  since-changed workflow), rather than showing nothing. */
function scopeLabel(row: Accommodation, rounds: Round[]): string {
  if (row.round_id) {
    return `Workflow round — ${roundTitle(rounds, row.round_id) ?? `round ${row.round_id.slice(0, 8)}`}`;
  }
  if (row.exam_round_id) {
    return `Exam round — ${examRoundTitle(rounds, row.exam_round_id) ?? `exam ${row.exam_round_id.slice(0, 8)}`}`;
  }
  if (row.enrolment_id) return 'This application';
  return 'Whole applicant';
}

function adjustmentSummary(row: Pick<
  Accommodation,
  'extra_time_percent' | 'deadline_extension_days' | 'relax_auto_submit' | 'other_adjustment'
>): string[] {
  const parts: string[] = [];
  if (row.extra_time_percent) parts.push(`+${row.extra_time_percent}% time`);
  if (row.deadline_extension_days) {
    parts.push(`+${row.deadline_extension_days} day${row.deadline_extension_days === 1 ? '' : 's'} deadline`);
  }
  if (row.relax_auto_submit) parts.push('Auto-submit relaxed');
  if (row.other_adjustment) parts.push('Other adjustment');
  return parts;
}

// ── Shared adjustment fields (record + revise) ──────────────────────────────

interface FieldsState {
  extraTimePercent: string;
  deadlineExtensionDays: string;
  relaxAutoSubmit: boolean;
  otherAdjustment: string;
  interviewerNote: string;
  internalNote: string;
  effectiveFrom: string;
  effectiveUntil: string;
}

function emptyFields(): FieldsState {
  return {
    extraTimePercent: '',
    deadlineExtensionDays: '',
    relaxAutoSubmit: false,
    otherAdjustment: '',
    interviewerNote: '',
    internalNote: '',
    effectiveFrom: '',
    effectiveUntil: '',
  };
}

function fieldsFromRow(row: Accommodation): FieldsState {
  return {
    extraTimePercent: row.extra_time_percent != null ? String(row.extra_time_percent) : '',
    deadlineExtensionDays: row.deadline_extension_days != null ? String(row.deadline_extension_days) : '',
    relaxAutoSubmit: row.relax_auto_submit,
    otherAdjustment: row.other_adjustment ?? '',
    interviewerNote: row.interviewer_note ?? '',
    internalNote: row.internal_note ?? '',
    // The window is carried over, not blanked. `revise` writes whatever it is
    // given, so a blank here silently turned a time-boxed adjustment into one
    // that never ends -- which keeps the interviewer_note being served past
    // the date HR chose, and stops the retention purge's effective_until
    // branch from ever matching the row.
    effectiveFrom: toLocalInputValue(row.effective_from),
    effectiveUntil: toLocalInputValue(row.effective_until),
  };
}

function hasAnyAdjustment(f: FieldsState): boolean {
  return Boolean(
    f.extraTimePercent.trim() || f.deadlineExtensionDays.trim() || f.relaxAutoSubmit || f.otherAdjustment.trim(),
  );
}

function AdjustmentFields({
  uidPrefix,
  fields,
  onChange,
  disabled,
}: {
  uidPrefix: string;
  fields: FieldsState;
  onChange: (next: FieldsState) => void;
  disabled: boolean;
}) {
  const set = <K extends keyof FieldsState>(key: K, value: FieldsState[K]) =>
    onChange({ ...fields, [key]: value });

  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-2.5 sm:grid-cols-2">
        <label className="block">
          <span className={labelCls}>Extra time (10–200%)</span>
          <input
            type="number"
            min={10}
            max={200}
            disabled={disabled}
            value={fields.extraTimePercent}
            onChange={(e) => set('extraTimePercent', e.target.value)}
            placeholder="e.g. 50"
            className={inputCls}
            aria-label="Extra time percent"
          />
        </label>
        <label className="block">
          <span className={labelCls}>Deadline extension (1–30 days)</span>
          <input
            type="number"
            min={1}
            max={30}
            disabled={disabled}
            value={fields.deadlineExtensionDays}
            onChange={(e) => set('deadlineExtensionDays', e.target.value)}
            placeholder="e.g. 3"
            className={inputCls}
            aria-label="Deadline extension days"
          />
        </label>
      </div>

      <label className="flex items-center gap-2 text-[12.5px] text-[var(--ui-soft)]">
        <input
          type="checkbox"
          disabled={disabled}
          checked={fields.relaxAutoSubmit}
          onChange={(e) => set('relaxAutoSubmit', e.target.checked)}
          className="h-3.5 w-3.5 accent-[var(--accent)]"
        />
        Relax auto-submit on proctoring flags
      </label>

      <label className="block">
        <span className={labelCls}>Other adjustment</span>
        <textarea
          disabled={disabled}
          value={fields.otherAdjustment}
          onChange={(e) => set('otherAdjustment', e.target.value)}
          maxLength={500}
          rows={2}
          placeholder="A plain description of the adjustment itself"
          className={`${inputCls} resize-y`}
        />
        <p className={noteWarnCls}>
          <Info size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
          HR only. The candidate is told only that an adjustment was recorded, never this text —
          and an interviewer never sees it either.
        </p>
      </label>

      <label className="block">
        <span className={labelCls}>Interviewer note (optional)</span>
        <textarea
          id={`${uidPrefix}-ivnote`}
          aria-describedby={`${uidPrefix}-ivnote-warn`}
          disabled={disabled}
          value={fields.interviewerNote}
          onChange={(e) => set('interviewerNote', e.target.value)}
          maxLength={500}
          rows={2}
          placeholder="What an interviewer needs to know to run this round fairly"
          className={`${inputCls} resize-y`}
        />
        <p id={`${uidPrefix}-ivnote-warn`} className={noteWarnCls}>
          <Info size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
          Shown to interviewers assigned to this candidate&rsquo;s rounds. Write only what the
          interview needs — never a diagnosis. The candidate never sees it.
        </p>
      </label>

      <label className="block">
        <span className={labelCls}>Internal note (optional)</span>
        <textarea
          aria-describedby={`${uidPrefix}-inote-warn`}
          disabled={disabled}
          value={fields.internalNote}
          onChange={(e) => set('internalNote', e.target.value)}
          maxLength={1000}
          rows={2}
          placeholder="For HR's own record"
          className={`${inputCls} resize-y`}
        />
        <p id={`${uidPrefix}-inote-warn`} className={noteWarnCls}>
          <Info size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
          HR only. Interviewers do not see this note, and neither does the candidate — it never
          leaves this record.
        </p>
      </label>

      <div className="grid gap-2.5 sm:grid-cols-2">
        <label className="block">
          <span className={labelCls}>Effective from (optional)</span>
          <input
            type="datetime-local"
            disabled={disabled}
            value={fields.effectiveFrom}
            onChange={(e) => set('effectiveFrom', e.target.value)}
            className={inputCls}
          />
        </label>
        <label className="block">
          <span className={labelCls}>Effective until (optional)</span>
          <input
            type="datetime-local"
            disabled={disabled}
            value={fields.effectiveUntil}
            onChange={(e) => set('effectiveUntil', e.target.value)}
            className={inputCls}
          />
        </label>
      </div>
    </div>
  );
}

// ── Record form ──────────────────────────────────────────────────────────────

function RecordForm({
  applicantId,
  enrolmentId,
  rounds,
  examRounds,
  roundsLoading,
  onDone,
}: {
  applicantId: string;
  enrolmentId: string | null | undefined;
  rounds: Round[];
  examRounds: Round[];
  roundsLoading: boolean;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [scope, setScope] = useState<Scope>('applicant');
  const [roundId, setRoundId] = useState('');
  const [examRoundId, setExamRoundId] = useState('');
  const [basis, setBasis] = useState<AccommodationBasis>('hr_initiated');
  const [fields, setFields] = useState<FieldsState>(emptyFields());
  const [error, setError] = useState<string | null>(null);

  const hasApplication = Boolean(enrolmentId);

  const recordMut = useMutation({
    mutationFn: () =>
      recordAccommodation(applicantId, {
        enrolment_id: scope === 'applicant' ? null : (enrolmentId as string),
        round_id: scope === 'round' ? roundId : null,
        exam_round_id: scope === 'exam_round' ? examRoundId : null,
        extra_time_percent: fields.extraTimePercent ? Number(fields.extraTimePercent) : null,
        deadline_extension_days: fields.deadlineExtensionDays
          ? Number(fields.deadlineExtensionDays)
          : null,
        relax_auto_submit: fields.relaxAutoSubmit,
        other_adjustment: fields.otherAdjustment.trim() || null,
        interviewer_note: fields.interviewerNote.trim() || null,
        internal_note: fields.internalNote.trim() || null,
        basis,
        effective_from: fields.effectiveFrom ? localInputToIso(fields.effectiveFrom) : null,
        effective_until: fields.effectiveUntil ? localInputToIso(fields.effectiveUntil) : null,
      }),
    onSuccess: () => {
      toast.success('Accommodation recorded');
      void qc.invalidateQueries({ queryKey: ['hr', 'applicant', applicantId, 'accommodations'] });
      onDone();
    },
    onError: (e) => {
      const msg = errText(e, 'Could not record this accommodation');
      setError(msg);
      toast.error(msg);
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if ((scope === 'round' && !roundId) || (scope === 'exam_round' && !examRoundId)) {
      setError('Choose a round.');
      return;
    }
    if (!hasAnyAdjustment(fields)) {
      setError('Record at least one adjustment.');
      return;
    }
    setError(null);
    recordMut.mutate();
  }

  return (
    <form
      onSubmit={submit}
      className="mt-3 flex flex-col gap-3 rounded-[12px] border border-border p-3"
      aria-label="Record an accommodation"
    >
      <fieldset className="flex flex-col gap-1.5">
        <legend className={labelCls}>Scope</legend>
        <label className="flex items-center gap-2 text-[12.5px] text-foreground">
          <input
            type="radio"
            name="accommodation-scope"
            checked={scope === 'applicant'}
            onChange={() => setScope('applicant')}
          />
          Whole applicant
        </label>
        <label className="flex items-center gap-2 text-[12.5px] text-foreground">
          <input
            type="radio"
            name="accommodation-scope"
            checked={scope === 'application'}
            disabled={!hasApplication}
            onChange={() => setScope('application')}
          />
          This application
        </label>
        <label className="flex items-center gap-2 text-[12.5px] text-foreground">
          <input
            type="radio"
            name="accommodation-scope"
            checked={scope === 'round'}
            disabled={!hasApplication}
            onChange={() => setScope('round')}
          />
          One workflow round
        </label>
        <label className="flex items-center gap-2 text-[12.5px] text-foreground">
          <input
            type="radio"
            name="accommodation-scope"
            checked={scope === 'exam_round'}
            disabled={!hasApplication}
            onChange={() => setScope('exam_round')}
          />
          One exam round
        </label>
        {!hasApplication ? (
          <p className="text-[11.5px] text-[var(--ui-faint)]">
            Open this from a specific application to scope an adjustment to a round — a
            round-scoped adjustment needs an application.
          </p>
        ) : null}
      </fieldset>

      {scope === 'round' ? (
        <label className="block">
          <span className={labelCls}>Round</span>
          <select
            value={roundId}
            onChange={(e) => setRoundId(e.target.value)}
            className={inputCls}
            aria-label="Workflow round"
          >
            <option value="">
              {roundsLoading ? 'Loading rounds…' : 'Choose a round…'}
            </option>
            {rounds.map((r) => (
              <option key={r.id} value={r.id}>
                {r.title}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {scope === 'exam_round' ? (
        <label className="block">
          <span className={labelCls}>Exam round</span>
          <select
            value={examRoundId}
            onChange={(e) => setExamRoundId(e.target.value)}
            className={inputCls}
            aria-label="Exam round"
          >
            <option value="">
              {roundsLoading ? 'Loading rounds…' : 'Choose an exam round…'}
            </option>
            {examRounds.map((r) => (
              <option key={r.id} value={r.exam_round_id as string}>
                {r.title}
              </option>
            ))}
          </select>
          {!roundsLoading && examRounds.length === 0 ? (
            <p className="mt-1 text-[11.5px] text-[var(--ui-faint)]">
              No exam-backed round found on this application&rsquo;s published workflow.
            </p>
          ) : null}
        </label>
      ) : null}

      <AdjustmentFields uidPrefix="record" fields={fields} onChange={setFields} disabled={recordMut.isPending} />

      <fieldset className="flex flex-col gap-1.5">
        <legend className={labelCls}>Basis</legend>
        <label className="flex items-center gap-2 text-[12.5px] text-foreground">
          <input
            type="radio"
            name="accommodation-basis"
            checked={basis === 'hr_initiated'}
            onChange={() => setBasis('hr_initiated')}
          />
          HR-initiated — recorded on HR&rsquo;s own initiative
        </label>
        <label className="flex items-center gap-2 text-[12.5px] text-foreground">
          <input
            type="radio"
            name="accommodation-basis"
            checked={basis === 'candidate_request'}
            onChange={() => setBasis('candidate_request')}
          />
          Candidate request — the candidate asked HR for this
        </label>
        <p className="text-[11.5px] text-[var(--ui-faint)]">
          Either way, this is not treated as consent captured through the product — it only
          records why HR is holding this adjustment.
        </p>
      </fieldset>

      {error ? <p className="text-[12px] text-[var(--ui-danger)]">{error}</p> : null}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onDone}
          className="rounded-[10px] border border-border px-3.5 py-2 text-[12.5px] text-foreground"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={recordMut.isPending}
          className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-50"
        >
          {recordMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
          Record accommodation
        </button>
      </div>
    </form>
  );
}

// ── Revise form ──────────────────────────────────────────────────────────────

function ReviseForm({
  applicantId,
  row,
  onDone,
}: {
  applicantId: string;
  row: Accommodation;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [fields, setFields] = useState<FieldsState>(() => fieldsFromRow(row));
  const [error, setError] = useState<string | null>(null);

  const reviseMut = useMutation({
    mutationFn: () =>
      reviseAccommodation(row.id, {
        extra_time_percent: fields.extraTimePercent ? Number(fields.extraTimePercent) : null,
        deadline_extension_days: fields.deadlineExtensionDays
          ? Number(fields.deadlineExtensionDays)
          : null,
        relax_auto_submit: fields.relaxAutoSubmit,
        other_adjustment: fields.otherAdjustment.trim() || null,
        interviewer_note: fields.interviewerNote.trim() || null,
        internal_note: fields.internalNote.trim() || null,
        effective_from: fields.effectiveFrom ? localInputToIso(fields.effectiveFrom) : null,
        effective_until: fields.effectiveUntil ? localInputToIso(fields.effectiveUntil) : null,
      }),
    onSuccess: () => {
      toast.success('Accommodation revised — the earlier record is kept');
      void qc.invalidateQueries({ queryKey: ['hr', 'applicant', applicantId, 'accommodations'] });
      onDone();
    },
    onError: (e) => {
      const msg = errText(e, 'Could not revise this accommodation');
      setError(msg);
      toast.error(msg);
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!hasAnyAdjustment(fields)) {
      setError('Record at least one adjustment.');
      return;
    }
    setError(null);
    reviseMut.mutate();
  }

  return (
    <form
      onSubmit={submit}
      className="mt-2 flex flex-col gap-3 rounded-[10px] border border-[var(--ui-line-strong)] bg-[var(--ui-inset-soft)] p-3"
      aria-label="Revise this accommodation"
    >
      <p className="text-[11.5px] text-[var(--ui-faint)]">
        The scope and basis stay the same — a revision replaces the parameters, not what this
        adjustment applies to. The current record is kept, not edited: anything already taken
        under it keeps what it had.
      </p>
      <AdjustmentFields
        uidPrefix={`revise-${row.id}`}
        fields={fields}
        onChange={setFields}
        disabled={reviseMut.isPending}
      />
      {error ? <p className="text-[12px] text-[var(--ui-danger)]">{error}</p> : null}
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onDone}
          className="rounded-[10px] border border-border px-3.5 py-2 text-[12.5px] text-foreground"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={reviseMut.isPending}
          className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-50"
        >
          {reviseMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
          Save revision
        </button>
      </div>
    </form>
  );
}

// ── History row ──────────────────────────────────────────────────────────────

function HistoryRow({
  applicantId,
  row,
  rounds,
}: {
  applicantId: string;
  row: Accommodation;
  rounds: Round[];
}) {
  const qc = useQueryClient();
  const [revising, setRevising] = useState(false);
  const [reason, setReason] = useState('');

  const isLive = row.status === 'active' && !row.superseded_at;

  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: ['hr', 'applicant', applicantId, 'accommodations'] });

  const revokeMut = useMutation({
    mutationFn: () => revokeAccommodation(row.id, reason),
    onSuccess: () => {
      toast.success('Accommodation revoked');
      invalidate();
    },
    onError: (e) => toast.error(errText(e, 'Could not revoke this accommodation')),
  });

  const parts = adjustmentSummary(row);

  return (
    <li className="rounded-[10px] border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[12.5px] font-medium text-foreground">{scopeLabel(row, rounds)}</span>
        <StatusTag tone={row.status === 'active' ? (isLive ? 'electric' : 'neutral') : 'ember'} dot>
          {row.status === 'active' ? (isLive ? 'active' : 'superseded') : 'revoked'}
        </StatusTag>
      </div>

      <p className="mt-1 text-[12.5px] text-[var(--ui-soft)]">
        {parts.length > 0 ? parts.join(' · ') : 'No structured adjustment recorded'}
      </p>

      <p className="mt-1 text-[11.5px] text-[var(--ui-faint)]">
        {row.basis === 'candidate_request' ? 'Candidate request' : 'HR-initiated'} · Recorded{' '}
        {formatDate(row.created_at)}
        {row.recorded_by_name ? ` by ${row.recorded_by_name}` : ''}
        {row.effective_until ? ` · effective until ${formatDate(row.effective_until)}` : ''}
      </p>

      {row.other_adjustment ? (
        <p className="mt-1.5 text-[12px] text-[var(--ui-soft)]">
          <span className="text-[var(--ui-faint)]">Other adjustment (HR only): </span>
          {row.other_adjustment}
        </p>
      ) : null}
      {row.interviewer_note ? (
        <p className="mt-1 text-[12px] text-[var(--ui-soft)]">
          <span className="text-[var(--ui-faint)]">Interviewer note: </span>
          {row.interviewer_note}
        </p>
      ) : null}
      {row.internal_note ? (
        <p className="mt-1 text-[12px] text-[var(--ui-soft)]">
          <span className="text-[var(--ui-faint)]">Internal note (HR only): </span>
          {row.internal_note}
        </p>
      ) : null}
      {row.status === 'revoked' ? (
        <p className="mt-1 text-[11.5px] text-muted-foreground">
          {row.revoked_at ? `Revoked ${formatDate(row.revoked_at)}` : 'Revoked'}
          {row.revoked_by_name
            ? ` by ${row.revoked_by_name}`
            : ' — ended by the platform (retention or an erasure request), not a person'}
          {row.revoke_reason ? `: ${row.revoke_reason}` : ''}
        </p>
      ) : null}
      {row.superseded_at ? (
        <p className="mt-1 text-[11.5px] text-[var(--ui-faint)]">
          Revised {formatDate(row.superseded_at)} — see the newer record.
        </p>
      ) : null}

      {isLive ? (
        revising ? (
          <ReviseForm applicantId={applicantId} row={row} onDone={() => setRevising(false)} />
        ) : (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setRevising(true)}
              className="text-[12px] text-[var(--ui-info)] hover:underline focus:outline-none focus-visible:underline"
            >
              Revise
            </button>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
              placeholder="Revoke reason (optional)"
              aria-label={`Revoke reason for ${scopeLabel(row, rounds)}`}
              className="w-full max-w-[220px] rounded-[8px] border border-border bg-secondary px-2 py-1 text-[11.5px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none"
            />
            <ConfirmDeleteButton
              label="Revoke"
              confirmText="Revoke"
              pending={revokeMut.isPending}
              onConfirm={() => revokeMut.mutate()}
            />
          </div>
        )
      ) : null}
    </li>
  );
}

// ── Section ──────────────────────────────────────────────────────────────────

export default function AccommodationsSection({
  applicantId,
  enrolmentId,
  requisitionId,
}: {
  applicantId: string;
  /** Absent when the drawer is opened without a specific application — the
   *  "this application" / round / exam-round scopes are then unavailable. */
  enrolmentId?: string | null;
  requisitionId?: string | null;
}): JSX.Element {
  const [recording, setRecording] = useState(false);

  const list = useQuery({
    queryKey: ['hr', 'applicant', applicantId, 'accommodations'],
    queryFn: () => listAccommodations(applicantId),
  });

  const { rounds, examRounds, isLoading: roundsLoading } = useApplicationRounds(
    requisitionId ?? null,
  );

  // A quick "what applies right now" line for this application, when one is
  // open — never the notes or the basis (EffectiveAccommodation carries
  // neither).
  const effective = useQuery({
    queryKey: ['hr', 'enrolment', enrolmentId, 'accommodations', 'effective'],
    queryFn: () => getEffectiveAccommodation(enrolmentId as string),
    enabled: Boolean(enrolmentId),
  });

  const rows = useMemo(() => list.data ?? [], [list.data]);

  return (
    <div className="mt-5">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-[13px] font-medium text-foreground">
          <Clock size={13} aria-hidden="true" />
          Accommodations{rows.length > 0 ? ` (${rows.length})` : ''}
        </h3>
        <button
          type="button"
          onClick={() => setRecording((v) => !v)}
          className="text-[12px] text-[var(--ui-info)] hover:underline focus:outline-none focus-visible:underline"
        >
          {recording ? 'Close' : 'Record adjustment'}
        </button>
      </div>

      {enrolmentId && effective.data?.effective ? (
        <p className="mt-1 text-[11.5px] text-[var(--ui-faint)]">
          Currently effective on this application:{' '}
          {[
            effective.data.extra_time_percent ? `+${effective.data.extra_time_percent}% time` : null,
            effective.data.deadline_extension_days
              ? `+${effective.data.deadline_extension_days} day${effective.data.deadline_extension_days === 1 ? '' : 's'}`
              : null,
            effective.data.relax_auto_submit ? 'auto-submit relaxed' : null,
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
      ) : null}

      {recording ? (
        <RecordForm
          applicantId={applicantId}
          enrolmentId={enrolmentId}
          rounds={rounds}
          examRounds={examRounds}
          roundsLoading={roundsLoading}
          onDone={() => setRecording(false)}
        />
      ) : null}

      {list.isLoading ? (
        <p className="mt-2 flex items-center gap-1.5 text-[12.5px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Loading…
        </p>
      ) : list.isError ? (
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          Could not load this applicant&rsquo;s accommodations.
        </p>
      ) : rows.length === 0 ? (
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          No accommodation recorded for this applicant.
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-2">
          {rows.map((row) => (
            <HistoryRow key={row.id} applicantId={applicantId} row={row} rounds={rounds} />
          ))}
        </ul>
      )}
    </div>
  );
}
