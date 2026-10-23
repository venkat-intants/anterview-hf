// OfferDetail — PH4-A3/A4. One offer, in full: every field (editable only in
// draft or after it was sent back), the one next action the offer's own state
// makes legal, the approval note or decline reason, the history, and — once the
// offer is accepted — its documents, marking preboarding complete, and the
// signed HRMS handoff.
//
// Layout: header, ONE approval track (Draft → Submitted → Approved → Sent →
// Answered) that carries the single primary action and the quieter ones, then
// the terms in one raised card with a "glance" rail beside it. Documents and
// preboarding are a second tab that exists only for an accepted offer.
//
// Field names and the state machine match services/data_gateway/app/offers.py
// EXACTLY — EDITABLE = draft/rejected, BEFORE_ANSWER = the states withdraw
// still works from. Every refusal from the server is shown as written.
//
// The actions that reach the candidate or cannot be undone (send, re-send,
// mark preboarding complete, withdraw) each take a second, deliberate click,
// and only one lifecycle action can be in flight at a time (audit OF-F8/F9).

import { offerStatusWord, OFFER_STATUS_TONE } from '@/lib/offerStatus';
import { offerEntryWord } from '@/lib/offerHistory';
import { Collapsible, ProgressiveList } from '@/design/components/collapsible';
import { useScopedPersistKey } from '@/hooks/useScopedPersistKey';
import { effectiveOfferStatus, offerPrimary, offerSecondary, offerTrack } from '@/lib/offerTrack';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  completePreboarding,
  getOffer,
  getOfferDocuments,
  hrmsExport,
  recallOffer,
  reopenOffer,
  resendOffer,
  sendOffer,
  submitOffer,
  updateOffer,
  withdrawOffer,
  type EmploymentType,
  type HrmsExportResult,
  type OfferDetail as OfferDetailShape,
  type OfferFieldsInput,
  type OfferStatus,
  type PayPeriod,
} from '@/api/offers';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { errorMessage } from '@/lib/errorMessage';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { statusLabel } from '@/lib/statusLabel';
import { DataList, Page, PageBody } from '@/design/components/page';
import { ApprovalTrack } from '@/design/components/approval-track';
import { Card } from '@/design/components/card';
import { StatusTag } from '@/design/components/primitives';
import { Section, Well } from '@/design/components/rows';
import { EditorHeader, type SaveState } from '@/components/hr/EditorHeader';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { QueryErrorPanel } from '@/components/QueryState';
import OfferDocumentsPanel from '@/components/OfferDocumentsPanel';
import { Download, Loader2, XCircle } from '@/design/components/icons';
import { formatDate, formatDateTime, formatMoney } from '@/lib/formatters';

const inputCls =
  'mt-1 min-h-[40px] w-full rounded-[12px] border border-border bg-secondary px-3 py-2 text-[13px] ' +
  'text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-60';
const PRIMARY_BTN =
  'inline-flex min-h-[40px] items-center justify-center gap-1.5 rounded-full bg-primary px-5 py-2 text-[13px] font-semibold text-primary-foreground transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:opacity-40';
const SECONDARY_BTN =
  'inline-flex min-h-[40px] items-center justify-center gap-1.5 rounded-full border border-[var(--ui-line-strong)] px-5 py-2 text-[13px] font-medium text-foreground transition-colors hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-40';
const labelCls = 'text-[13px] font-medium text-[var(--ui-soft)]';

const EMPLOYMENT_TYPES: EmploymentType[] = ['full_time', 'part_time', 'contract', 'internship'];
const PAY_PERIODS: PayPeriod[] = ['annual', 'monthly', 'hourly'];

/** The server's limits (OfferFieldsIn and clean_fields), mirrored on the inputs
 *  so a person is told while typing, not by a refusal after saving. */
const TEXT_MAX = {
  job_title: 200,
  location: 200,
  bonus: 1000,
  equity: 1000,
  benefits: 4000,
  terms: 20000,
} as const;
const NOTE_MAX = 1000;
const SALARY_MAX = 1e12;

function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* ── Editable fields ─────────────────────────────────────────────────────── */

interface FieldsState {
  job_title: string;
  employment_type: string;
  start_date: string;
  location: string;
  base_salary: string;
  currency: string;
  pay_period: string;
  bonus: string;
  equity: string;
  benefits: string;
  terms: string;
  probation_months: string;
  notice_period_days: string;
  valid_days: string;
}

function fieldsFrom(o: OfferDetailShape): FieldsState {
  return {
    job_title: o.job_title ?? '',
    employment_type: o.employment_type ?? '',
    start_date: o.start_date ?? '',
    location: o.location ?? '',
    base_salary: o.base_salary ?? '',
    currency: o.currency ?? '',
    pay_period: o.pay_period ?? '',
    bonus: o.bonus ?? '',
    equity: o.equity ?? '',
    benefits: o.benefits ?? '',
    terms: o.terms ?? '',
    probation_months: o.probation_months != null ? String(o.probation_months) : '',
    notice_period_days: o.notice_period_days != null ? String(o.notice_period_days) : '',
    valid_days: o.valid_days != null ? String(o.valid_days) : '',
  };
}

function diff(current: FieldsState, saved: FieldsState): OfferFieldsInput {
  const out: OfferFieldsInput = {};
  if (current.job_title !== saved.job_title) out.job_title = current.job_title;
  // Employment type and pay period always hold a value (the columns have no
  // "unset"), so an empty one is never sent: an empty key used to make the form
  // dirty while JSON dropped it, leaving "unsaved" and Submit blocked for good
  // (audit OF-F2 / M10).
  if (current.employment_type && current.employment_type !== saved.employment_type) {
    out.employment_type = current.employment_type as EmploymentType;
  }
  if (current.start_date !== saved.start_date) out.start_date = current.start_date || null;
  if (current.location !== saved.location) out.location = current.location;
  if (current.base_salary !== saved.base_salary) out.base_salary = current.base_salary;
  if (current.currency !== saved.currency) out.currency = current.currency;
  if (current.pay_period && current.pay_period !== saved.pay_period) {
    out.pay_period = current.pay_period as PayPeriod;
  }
  if (current.bonus !== saved.bonus) out.bonus = current.bonus;
  if (current.equity !== saved.equity) out.equity = current.equity;
  if (current.benefits !== saved.benefits) out.benefits = current.benefits;
  if (current.terms !== saved.terms) out.terms = current.terms;
  if (current.probation_months !== saved.probation_months) {
    out.probation_months =
      current.probation_months === '' ? null : Number(current.probation_months);
  }
  if (current.notice_period_days !== saved.notice_period_days) {
    out.notice_period_days =
      current.notice_period_days === '' ? null : Number(current.notice_period_days);
  }
  if (current.valid_days !== saved.valid_days) out.valid_days = Number(current.valid_days);
  return out;
}

function wholeIn(value: string, min: number, max: number): boolean {
  if (!/^\d+$/.test(value.trim())) return false;
  const n = Number(value);
  return n >= min && n <= max;
}

/** The first thing wrong with the terms, in words, or null when they can be
 *  saved. A cleared "valid for" used to be sent as 0 (audit OF-L15). */
function fieldsProblem(f: FieldsState): string | null {
  if (!f.job_title.trim()) return 'Give the offer a job title.';
  const salary = Number(f.base_salary);
  if (f.base_salary.trim() === '' || !Number.isFinite(salary) || salary <= 0) {
    return 'The base salary must be more than zero.';
  }
  if (salary >= SALARY_MAX) return 'The base salary is too large.';
  if (!/^\d+(\.\d{1,2})?$/.test(f.base_salary.trim())) {
    return 'The base salary has at most two decimal places.';
  }
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

/** Why the terms cannot be changed, in the offer's own situation (audit OF-L2). */
function readOnlyNote(status: OfferStatus): string {
  switch (status) {
    case 'pending_approval':
      return 'This offer is waiting for approval. Recall it to make changes.';
    case 'approved':
      return 'This offer is approved. Reopen it for editing to make changes; it then needs approving again.';
    case 'sent':
      return 'This offer is with the candidate, so its terms are final. To change them, withdraw it and write a new offer.';
    case 'accepted':
      return 'The candidate accepted this offer, so its terms are final.';
    default:
      return `This offer is ${offerStatusWord(status)}, so its terms are final. A new offer can be written from the candidate's drawer.`;
  }
}

function OfferFields({
  offer,
  onSaveState,
}: {
  offer: OfferDetailShape;
  onSaveState: (state: SaveState | undefined) => void;
}) {
  const qc = useQueryClient();
  const editable = offer.status === 'draft' || offer.status === 'rejected';
  const saved = fieldsFrom(offer);
  const [fields, setFields] = useState<FieldsState>(saved);

  useEffect(() => {
    setFields(fieldsFrom(offer));
    // Re-sync only when the server's own values change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offer.id, offer.updated_at]);

  const set = <K extends keyof FieldsState>(key: K, value: FieldsState[K]) =>
    setFields((f) => ({ ...f, [key]: value }));

  const changed = diff(fields, saved);
  const dirty = Object.keys(changed).length > 0;
  const problem = editable ? fieldsProblem(fields) : null;

  const saveMut = useMutation({
    mutationFn: () => updateOffer(offer.id, changed),
    onSuccess: () => {
      toast.success('Offer updated');
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not update this offer.')),
  });

  // The header owns the save-state wording; this only reports it.
  const reportRef = useRef(onSaveState);
  reportRef.current = onSaveState;
  const reported: SaveState | undefined = !editable
    ? undefined
    : saveMut.isPending
      ? 'saving'
      : saveMut.isError && dirty
        ? 'error'
        : dirty
          ? 'unsaved'
          : 'saved';
  useEffect(() => {
    reportRef.current(reported);
  }, [reported]);

  return (
    <Card
      emphasis="raised"
      as="section"
      title="Offer terms"
      data-testid="offer-terms"
      className="flex flex-col"
    >
      {!editable ? (
        <Well className="mb-5 text-[13px] text-muted-foreground" data-testid="offer-read-only">
          {readOnlyNote(effectiveOfferStatus(offer))}
        </Well>
      ) : null}

      <div className="flex flex-col gap-6">
        <Section title="Role & start">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className={labelCls}>Job title</span>
              <input
                disabled={!editable}
                maxLength={TEXT_MAX.job_title}
                value={fields.job_title}
                onChange={(e) => set('job_title', e.target.value)}
                className={inputCls}
              />
            </label>
            <label className="block">
              <span className={labelCls}>Employment type</span>
              <select
                disabled={!editable}
                value={fields.employment_type}
                onChange={(e) => set('employment_type', e.target.value)}
                className={inputCls}
              >
                {EMPLOYMENT_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t.replace('_', ' ')}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className={labelCls}>Start date</span>
              <input
                type="date"
                disabled={!editable}
                value={fields.start_date}
                onChange={(e) => set('start_date', e.target.value)}
                className={inputCls}
              />
            </label>
            <label className="block">
              <span className={labelCls}>Location</span>
              <input
                disabled={!editable}
                maxLength={TEXT_MAX.location}
                value={fields.location}
                onChange={(e) => set('location', e.target.value)}
                className={inputCls}
              />
            </label>
          </div>
        </Section>

        <Section title="Pay">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className={labelCls}>Base salary</span>
              <input
                type="number"
                min={0.01}
                step={0.01}
                disabled={!editable}
                value={fields.base_salary}
                onChange={(e) => set('base_salary', e.target.value)}
                className={inputCls}
              />
            </label>
            <label className="block">
              <span className={labelCls}>Currency</span>
              <input
                disabled={!editable}
                maxLength={3}
                value={fields.currency}
                onChange={(e) => set('currency', e.target.value.toUpperCase())}
                className={inputCls}
              />
            </label>
            <label className="block">
              <span className={labelCls}>Pay period</span>
              <select
                disabled={!editable}
                value={fields.pay_period}
                onChange={(e) => set('pay_period', e.target.value)}
                className={inputCls}
              >
                {PAY_PERIODS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className={labelCls}>Bonus</span>
              <input
                disabled={!editable}
                maxLength={TEXT_MAX.bonus}
                value={fields.bonus}
                onChange={(e) => set('bonus', e.target.value)}
                className={inputCls}
              />
            </label>
            <label className="block">
              <span className={labelCls}>Equity</span>
              <input
                disabled={!editable}
                maxLength={TEXT_MAX.equity}
                value={fields.equity}
                onChange={(e) => set('equity', e.target.value)}
                className={inputCls}
              />
            </label>
          </div>
        </Section>

        <Section title="Conditions">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className={labelCls}>Offer valid for (days)</span>
              <input
                type="number"
                min={1}
                max={60}
                step={1}
                required
                disabled={!editable}
                value={fields.valid_days}
                onChange={(e) => set('valid_days', e.target.value)}
                className={inputCls}
              />
            </label>
            <label className="block">
              <span className={labelCls}>Probation (months)</span>
              <input
                type="number"
                min={0}
                max={24}
                step={1}
                disabled={!editable}
                value={fields.probation_months}
                onChange={(e) => set('probation_months', e.target.value)}
                className={inputCls}
              />
            </label>
            <label className="block">
              <span className={labelCls}>Notice period (days)</span>
              <input
                type="number"
                min={0}
                max={365}
                step={1}
                disabled={!editable}
                value={fields.notice_period_days}
                onChange={(e) => set('notice_period_days', e.target.value)}
                className={inputCls}
              />
            </label>
          </div>
          <label className="mt-3 block">
            <span className={labelCls}>Benefits</span>
            <textarea
              disabled={!editable}
              maxLength={TEXT_MAX.benefits}
              value={fields.benefits}
              onChange={(e) => set('benefits', e.target.value)}
              rows={2}
              className={inputCls}
            />
          </label>
          <label className="mt-3 block">
            <span className={labelCls}>Terms</span>
            <textarea
              disabled={!editable}
              maxLength={TEXT_MAX.terms}
              value={fields.terms}
              onChange={(e) => set('terms', e.target.value)}
              rows={4}
              className={inputCls}
            />
          </label>
        </Section>
      </div>

      {editable ? (
        <div className="mt-6 flex flex-wrap items-center justify-end gap-3 border-t border-border pt-4">
          {problem && dirty ? (
            <p
              id={`offer-fields-problem-${offer.id}`}
              className="mr-auto text-[12.5px] text-[var(--ui-warn)]"
            >
              {problem}
            </p>
          ) : null}
          <button
            type="button"
            disabled={!dirty || saveMut.isPending || problem !== null}
            aria-describedby={problem && dirty ? `offer-fields-problem-${offer.id}` : undefined}
            onClick={() => saveMut.mutate()}
            className={SECONDARY_BTN}
          >
            {saveMut.isPending ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      ) : null}
    </Card>
  );
}

/* ── A deliberate second click ───────────────────────────────────────────── */

/**
 * An action that reaches the candidate or cannot be undone: the first click
 * says what will happen, the second does it. Same keyboard behaviour as
 * ConfirmDeleteButton (focus moves to the safe button, Escape backs out, focus
 * returns to the trigger), but styled as the action it is, because sending an
 * offer is the page's main step, not a deletion.
 */
function ConfirmStep({
  label,
  confirmText,
  pendingText,
  explain,
  pending,
  disabled,
  describedBy,
  primary,
  onConfirm,
}: {
  label: string;
  confirmText: string;
  pendingText: string;
  explain: ReactNode;
  pending: boolean;
  disabled?: boolean;
  describedBy?: string;
  primary?: boolean;
  onConfirm: () => void;
}) {
  const [armed, setArmed] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const keepRef = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
  const awaiting = useRef(false);

  const disarm = () => {
    restoreFocus.current = true;
    setArmed(false);
  };

  useEffect(() => {
    if (armed) keepRef.current?.focus();
    else if (restoreFocus.current) {
      restoreFocus.current = false;
      triggerRef.current?.focus();
    }
  }, [armed]);

  useEffect(() => {
    if (awaiting.current && !pending) {
      awaiting.current = false;
      restoreFocus.current = true;
      setArmed(false);
    }
  }, [pending]);

  if (!armed) {
    return (
      <button
        ref={triggerRef}
        type="button"
        data-testid={primary ? 'offer-primary' : undefined}
        disabled={disabled || pending}
        aria-describedby={describedBy}
        onClick={() => setArmed(true)}
        className={primary ? PRIMARY_BTN : SECONDARY_BTN}
      >
        {pending ? pendingText : label}
      </button>
    );
  }
  return (
    <div
      role="group"
      aria-label={`Confirm: ${confirmText}`}
      className="flex flex-col gap-2 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-3 sm:max-w-[420px]"
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          disarm();
        }
      }}
    >
      <p className="text-[12.5px] text-[var(--ui-soft)]">{explain}</p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={pending}
          aria-busy={pending}
          onClick={() => {
            awaiting.current = true;
            onConfirm();
          }}
          className={PRIMARY_BTN}
        >
          {pending ? pendingText : confirmText}
        </button>
        <button
          ref={keepRef}
          type="button"
          disabled={pending}
          onClick={() => {
            awaiting.current = false;
            disarm();
          }}
          className={SECONDARY_BTN}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

/* ── Actions, legal only where the state machine allows them ─────────────── */

function useOfferLifecycle(offer: OfferDetailShape) {
  const qc = useQueryClient();
  const [withdrawReason, setWithdrawReason] = useState('');
  const [exportResult, setExportResult] = useState<HrmsExportResult | null>(null);
  // Every offer view lives under ['hr', ...] (this page, its documents, the
  // offers list, the drawer's list), and the approval queue under ['admin',
  // 'offer-approvals'] (audit OF-F7 / L9).
  const invalidate = () => invalidateAfterHrMutation(qc, ['admin', 'offer-approvals']);

  const submitMut = useMutation({
    mutationFn: () => submitOffer(offer.id),
    onSuccess: () => {
      toast.success('Submitted for approval');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not submit this offer.')),
  });
  const recallMut = useMutation({
    mutationFn: () => recallOffer(offer.id),
    onSuccess: () => {
      toast.success('Recalled to draft');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not recall this offer.')),
  });
  const reopenMut = useMutation({
    mutationFn: () => reopenOffer(offer.id),
    onSuccess: () => {
      toast.success('Reopened for editing');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not reopen this offer.')),
  });
  const sendMut = useMutation({
    mutationFn: () => sendOffer(offer.id),
    onSuccess: () => {
      toast.success('Sent to the candidate');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not send this offer.')),
  });
  const resendMut = useMutation({
    mutationFn: () => resendOffer(offer.id),
    onSuccess: () => {
      toast.success('Sent a fresh link', { description: 'The old one no longer works.' });
      void invalidate();
    },
    // A refusal (an offer that has just expired) changes the offer too.
    onError: (e: unknown) => {
      toast.error(errorMessage(e, 'Could not re-send this offer.'));
      void invalidate();
    },
  });
  const withdrawMut = useMutation({
    mutationFn: () => withdrawOffer(offer.id, withdrawReason.trim() || null),
    onSuccess: () => {
      toast.success('Offer withdrawn');
      setWithdrawReason('');
      void invalidate();
    },
    onError: (e: unknown) => {
      toast.error(errorMessage(e, 'Could not withdraw this offer.'));
      void invalidate();
    },
  });
  const completeMut = useMutation({
    mutationFn: () => completePreboarding(offer.id),
    onSuccess: () => {
      toast.success('Preboarding marked complete');
      void invalidate();
    },
    // The server names exactly which mandatory documents are not verified yet.
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not complete preboarding.')),
  });
  const exportMut = useMutation({
    mutationFn: () => hrmsExport(offer.id),
    onSuccess: (res) => {
      setExportResult(res);
      toast.success('Export prepared');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not prepare the export.')),
  });

  // One lifecycle action at a time: a quick second click could otherwise send
  // an offer and withdraw it, emailing the candidate twice (audit OF-F9).
  const busy =
    submitMut.isPending ||
    recallMut.isPending ||
    reopenMut.isPending ||
    sendMut.isPending ||
    resendMut.isPending ||
    withdrawMut.isPending ||
    completeMut.isPending;

  return {
    submitMut,
    recallMut,
    reopenMut,
    sendMut,
    resendMut,
    withdrawMut,
    completeMut,
    exportMut,
    exportResult,
    withdrawReason,
    setWithdrawReason,
    busy,
  };
}

type OfferLifecycle = ReturnType<typeof useOfferLifecycle>;

/** Why "Submit for approval" is unavailable while the form holds unsaved work. */
const SUBMIT_BLOCK_REASON: Partial<Record<SaveState, string>> = {
  unsaved: 'Save your changes first. Approval is requested for the saved offer.',
  saving: 'Saving your changes…',
  error: 'The last save failed. Fix it or save again before submitting.',
};

/** Why "Mark preboarding complete" is unavailable, from the documents' own
 *  state (audit OF-L5). Undefined while the documents load or when ready. */
function useCompleteBlockReason(offer: OfferDetailShape, wanted: boolean): string | undefined {
  const docs = useQuery({
    queryKey: ['hr', 'offer', offer.id, 'documents'],
    queryFn: () => getOfferDocuments(offer.id),
    enabled: wanted,
  });
  if (!wanted || !docs.data || docs.data.complete_ready) return undefined;
  const missing = docs.data.outstanding;
  return missing.length > 0
    ? `Not verified yet: ${missing.join(', ')}.`
    : 'Every mandatory document must be verified first.';
}

/**
 * The track plus its actions: the ONE primary the state allows, and the quiet
 * ones (recall, reopen, re-send, withdraw) beside it. Replaces the banners, the
 * Approval card and the Actions card.
 */
function OfferApprovalTrack({
  offer,
  life,
  saveState,
}: {
  offer: OfferDetailShape;
  life: OfferLifecycle;
  saveState: SaveState | undefined;
}) {
  const { steps, stateLabels } = offerTrack(offer);
  const primaryKind = offerPrimary(offer);
  const sec = offerSecondary(offer);
  const status = effectiveOfferStatus(offer);
  const { recallMut, reopenMut, resendMut, withdrawMut, withdrawReason, setWithdrawReason, busy } =
    life;
  const completeBlock = useCompleteBlockReason(offer, primaryKind === 'complete_preboarding');

  const blockReason =
    primaryKind === 'submit' && saveState ? SUBMIT_BLOCK_REASON[saveState] : undefined;
  const reasonId = `offer-submit-reason-${offer.id}`;
  const completeId = `offer-complete-reason-${offer.id}`;
  const candidate = offer.candidate_name ?? 'the candidate';

  const primary = ((): JSX.Element | undefined => {
    switch (primaryKind) {
      case 'submit':
        return (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
            <button
              type="button"
              data-testid="offer-primary"
              disabled={busy || Boolean(blockReason)}
              aria-describedby={blockReason ? reasonId : undefined}
              onClick={() => life.submitMut.mutate()}
              className={`${PRIMARY_BTN} w-full sm:w-auto`}
            >
              {life.submitMut.isPending ? 'Submitting…' : 'Submit for approval'}
            </button>
            {blockReason ? (
              <span id={reasonId} className="text-[12.5px] text-muted-foreground">
                {blockReason}
              </span>
            ) : !offer.start_date ? (
              // Not a rule the server enforces, but the candidate would read an
              // offer with no start date (audit OF-L14).
              <span className="text-[12.5px] text-[var(--ui-warn)]">
                No start date is set. The candidate will see the offer without one.
              </span>
            ) : null}
          </div>
        );
      case 'send':
        return (
          <ConfirmStep
            primary
            label="Send to candidate"
            confirmText="Send offer"
            pendingText="Sending…"
            explain={`Email this offer to ${candidate}. It stays open for ${offer.valid_days ?? 7} days from now, and its terms cannot change once sent.`}
            pending={life.sendMut.isPending}
            disabled={busy}
            onConfirm={() => life.sendMut.mutate()}
          />
        );
      case 'complete_preboarding':
        return (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
            <ConfirmStep
              primary
              label="Mark preboarding complete"
              confirmText="Mark complete"
              pendingText="Checking…"
              explain={`This cannot be undone. ${candidate} can no longer upload documents, and the HRMS export becomes available.`}
              pending={life.completeMut.isPending}
              disabled={busy || Boolean(completeBlock)}
              describedBy={completeBlock ? completeId : undefined}
              onConfirm={() => life.completeMut.mutate()}
            />
            {completeBlock ? (
              <span id={completeId} className="text-[12.5px] text-muted-foreground">
                {completeBlock}
              </span>
            ) : null}
          </div>
        );
      default:
        return undefined;
    }
  })();

  const anything = primary || sec.recall || sec.reopen || sec.resend || sec.withdraw;

  const secondary = anything ? (
    <>
      {sec.recall ? (
        <button
          type="button"
          disabled={busy}
          onClick={() => recallMut.mutate()}
          className={SECONDARY_BTN}
        >
          {recallMut.isPending ? 'Recalling…' : 'Recall'}
        </button>
      ) : null}
      {sec.reopen ? (
        <button
          type="button"
          disabled={busy}
          onClick={() => reopenMut.mutate()}
          className={SECONDARY_BTN}
        >
          {reopenMut.isPending ? 'Reopening…' : 'Reopen for editing'}
        </button>
      ) : null}
      {sec.resend ? (
        <ConfirmStep
          label="Re-send"
          confirmText="Re-send"
          pendingText="Sending…"
          explain={`Email ${candidate} a fresh link. The link they have now stops working.`}
          pending={resendMut.isPending}
          disabled={busy}
          onConfirm={() => resendMut.mutate()}
        />
      ) : null}
      {sec.withdraw ? (
        // A fieldset disables the reason and the two-step button together while
        // another action is in flight (ConfirmDeleteButton has no disabled prop).
        <fieldset
          disabled={busy && !withdrawMut.isPending}
          className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0 sm:flex-row sm:flex-wrap sm:items-center"
        >
          <input
            type="text"
            value={withdrawReason}
            maxLength={NOTE_MAX}
            onChange={(e) => setWithdrawReason(e.target.value)}
            placeholder="Withdraw reason (optional)"
            aria-label="Withdraw reason"
            className="min-h-[40px] w-full rounded-[12px] sm:w-[260px] border border-border bg-secondary px-3 py-1.5 text-[13px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-60"
          />
          <ConfirmDeleteButton
            label="Withdraw offer"
            icon={XCircle}
            confirmText="Withdraw"
            confirmGroupLabel="Confirm withdrawal"
            pending={withdrawMut.isPending}
            onConfirm={() => withdrawMut.mutate()}
          />
        </fieldset>
      ) : null}
    </>
  ) : undefined;

  return (
    <div className="flex flex-col gap-3">
      <ApprovalTrack
        data-testid="offer-track"
        label="Offer progress"
        steps={steps}
        stateLabels={stateLabels}
        primary={primary}
        secondary={secondary}
      />
      {sec.resend ? (
        <p className="text-[12px] text-[var(--ui-faint)]">
          Re-sending retires the old link and lifts a lock from too many wrong codes.
        </p>
      ) : null}
      {!anything ? (
        <p className="text-[12.5px] text-muted-foreground">
          {status === 'accepted' && offer.preboarding_completed_at
            ? // The HRMS export is still on the Documents tab, so this is not "nothing".
              'Preboarding is complete. The HRMS export is on the Documents tab.'
            : `This offer is ${offerStatusWord(status)}. There is nothing left to do here.`}
        </p>
      ) : null}
    </div>
  );
}

/* ── Documents + preboarding: only for an accepted offer ──────────────────── */

function DocumentsTab({ offer, life }: { offer: OfferDetailShape; life: OfferLifecycle }) {
  const { exportResult, exportMut } = life;
  return (
    <Card
      emphasis="raised"
      as="section"
      aria-label="Documents and preboarding"
      className="flex flex-col gap-6"
    >
      <Section title="Documents">
        <OfferDocumentsPanel
          offerId={offer.id}
          requisitionId={offer.requisition_id}
          preboardingComplete={Boolean(offer.preboarding_completed_at)}
        />
      </Section>

      <Section title="Preboarding">
        {offer.preboarding_completed_at ? (
          <p className="text-[12.5px] text-[var(--ui-ok)]">
            Preboarding completed on {formatDateTime(offer.preboarding_completed_at)}.
          </p>
        ) : (
          <p className="text-[12.5px] text-muted-foreground">
            Verify every mandatory document, then mark preboarding complete above.
          </p>
        )}

        {offer.preboarding_completed_at ? (
          <button
            type="button"
            disabled={exportMut.isPending}
            onClick={() => exportMut.mutate()}
            className={cn(SECONDARY_BTN, 'mt-3')}
          >
            {exportMut.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : null}
            Prepare HRMS export
          </button>
        ) : null}

        {exportResult ? (
          <Well className="mt-3">
            <p className="text-[12px] text-[var(--ui-soft)]">
              key_id <span className="text-foreground">{exportResult.key_id}</span> · algorithm{' '}
              <span className="text-foreground">{exportResult.algorithm}</span>
            </p>
            <p className="mt-1 break-all text-[12px] text-[var(--ui-faint)]">
              signature {exportResult.signature}
            </p>
            <pre className="mt-2 max-h-[240px] overflow-auto rounded-[8px] bg-[var(--ui-inset)] p-2.5 text-[12px] text-[var(--ui-soft)]">
              {JSON.stringify(exportResult.payload, null, 2)}
            </pre>
            <button
              type="button"
              onClick={() => downloadJson(`offer-${offer.id}-hrms-export.json`, exportResult)}
              className="mt-2 inline-flex items-center gap-1.5 text-[12px] text-[var(--ui-info)] hover:underline"
            >
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              Download JSON
            </button>
          </Well>
        ) : null}
      </Section>
    </Card>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

function money(o: OfferDetailShape): string {
  if (!o.base_salary) return '';
  const amount = formatMoney(o.base_salary, o.currency);
  return o.pay_period ? `${amount} / ${o.pay_period}` : amount;
}

/** The header's date line: the deadline while it matters, never "Expires" on
 *  an offer already answered (audit OF-L1). */
function expiryMeta(offer: OfferDetailShape): ReactNode {
  if (!offer.expires_at) return undefined;
  const status = effectiveOfferStatus(offer);
  if (status === 'sent') return <span>Expires {formatDateTime(offer.expires_at)}</span>;
  if (status === 'expired') return <span>Expired {formatDateTime(offer.expires_at)}</span>;
  return undefined;
}

type OfferTab = 'terms' | 'documents';

function OfferLoaded({ offer }: { offer: OfferDetailShape }) {
  const accepted = offer.status === 'accepted';
  // Documents are the work on an accepted offer, so they open first.
  const [tab, setTab] = useState<OfferTab>(accepted ? 'documents' : 'terms');
  const life = useOfferLifecycle(offer);
  const [saveState, setSaveState] = useState<SaveState | undefined>(undefined);
  const historyKey = useScopedPersistKey('offer-history');
  const status = effectiveOfferStatus(offer);

  // If the offer stops being accepted (it cannot, but the cache can change under
  // us), never leave the page on a tab that no longer exists.
  const activeTab: OfferTab = accepted ? tab : 'terms';

  // Newest entry, by time, for the one-line summary under the closed header.
  const newestEntry = offer.history.reduce<(typeof offer.history)[number] | null>(
    (n, h) => (n === null || h.at > n.at ? h : n),
    null,
  );
  const history = (
    <Collapsible
      variant="card"
      title="History"
      count={offer.history.length}
      noun="entry"
      nounPlural="entries"
      latest={
        newestEntry
          ? `${offerEntryWord(newestEntry)}${newestEntry.actor_name ? ` by ${newestEntry.actor_name}` : ''}, ${formatDate(newestEntry.at)}`
          : undefined
      }
      persistKey={historyKey}
      data-testid="offer-history"
    >
      {offer.history.length === 0 ? (
        <p className="text-[12.5px] text-muted-foreground">No activity recorded yet.</p>
      ) : (
        <ProgressiveList
          as="ol"
          items={offer.history}
          initial={5}
          noun="entries"
          getKey={(h, i) => `${h.at}-${i}`}
          className="flex flex-col gap-4 border-l border-border pl-5"
          renderItem={(h) => (
            <li className="relative text-[13px] text-foreground">
              <span
                aria-hidden="true"
                className="absolute -left-[25px] top-1.5 h-2 w-2 rounded-full border border-border bg-primary"
              />
              {offerEntryWord(h)}
              {h.actor_name ? ` - ${h.actor_name}` : ''}
              <span className="mt-0.5 block text-[12px] tabular-nums text-muted-foreground">
                {formatDateTime(h.at)}
              </span>
            </li>
          )}
        />
      )}
    </Collapsible>
  );

  const terms = <OfferFields offer={offer} onSaveState={setSaveState} />;

  return (
    <Page width="default">
      <EditorHeader
        breadcrumbs={[{ label: 'Offers', to: '/hr/offers' }, { label: 'Offer' }]}
        title={`${offer.candidate_name} · ${offer.job_title}`}
        status={
          <StatusTag tone={OFFER_STATUS_TONE[status]} dot>
            {statusLabel(status, 'offer')}
          </StatusTag>
        }
        meta={expiryMeta(offer)}
        saveState={saveState}
      />

      <OfferApprovalTrack offer={offer} life={life} saveState={saveState} />

      <PageBody
        asideLabel="Offer inspector"
        aside={
          <Card as="section" title="At a glance" density="compact" data-testid="offer-glance">
            <DataList
              columns={1}
              items={[
                { label: 'Base pay', value: <span className="tabular-nums">{money(offer)}</span> },
                { label: 'Start date', value: offer.start_date ?? '' },
                { label: 'Location', value: offer.location ?? '' },
                {
                  label: 'Employment type',
                  value: offer.employment_type ? offer.employment_type.replace('_', ' ') : '',
                },
                ...(offer.approval_note && offer.status !== 'rejected'
                  ? [{ label: 'Approver’s note', value: offer.approval_note }]
                  : []),
              ]}
            />
          </Card>
        }
      >
        {accepted ? (
          <Tabs value={activeTab} onValueChange={(v) => setTab(v as OfferTab)}>
            <TabsList aria-label="Offer sections">
              <TabsTrigger value="terms">Terms</TabsTrigger>
              <TabsTrigger value="documents">Documents &amp; preboarding</TabsTrigger>
            </TabsList>
            <TabsContent value="documents" className="mt-4">
              <DocumentsTab offer={offer} life={life} />
            </TabsContent>
            <TabsContent value="terms" className="mt-4">
              {terms}
            </TabsContent>
          </Tabs>
        ) : (
          terms
        )}
        {history}
      </PageBody>
    </Page>
  );
}

export default function OfferDetail() {
  const { offerId = '' } = useParams();

  const query = useQuery({
    queryKey: ['hr', 'offer', offerId],
    queryFn: () => getOffer(offerId),
    enabled: Boolean(offerId),
  });

  if (query.isLoading) {
    return (
      <Page width="default">
        <div className="flex flex-col gap-4" role="status" aria-label="Loading offer">
          <Skeleton className="h-9 w-2/3" />
          <Skeleton className="h-64 w-full rounded-card" />
          <Skeleton className="h-32 w-full rounded-card" />
        </div>
      </Page>
    );
  }
  if (query.isError || !query.data) {
    return (
      <Page width="form">
        <QueryErrorPanel
          title="Could not load this offer."
          error={query.error}
          onRetry={() => void query.refetch()}
          retrying={query.isFetching}
        />
        <Link to="/hr/offers" className="text-[13px] text-[var(--ui-info)] underline">
          Back to offers
        </Link>
      </Page>
    );
  }

  return <OfferLoaded offer={query.data} />;
}
