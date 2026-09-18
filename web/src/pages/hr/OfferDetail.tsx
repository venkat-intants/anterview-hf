// OfferDetail — PH4-A3/A4. One offer, in full: every field (editable only in
// draft or after it was sent back), the actions the offer's own state makes
// legal, the approval note or decline reason, the history timeline, its
// documents, marking preboarding complete, and preparing the signed HRMS
// handoff.
//
// Field names and the state machine match services/data_gateway/app/offers.py
// EXACTLY — EDITABLE = draft/rejected, BEFORE_ANSWER = the states withdraw
// still works from. Every refusal from the server is shown as written.

import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  completePreboarding,
  getOffer,
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
import { GlassCard, StatusTag, type TagTone } from '@/design/components/primitives';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import OfferDocumentsPanel from '@/components/OfferDocumentsPanel';
import { ArrowLeft, Download, Loader2 } from '@/design/components/icons';

const inputCls =
  'mt-1 w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] ' +
  'text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-60';
const labelCls = 'text-[12px] font-medium text-[var(--ui-soft)]';

const OFFER_TONE: Record<OfferStatus, TagTone> = {
  draft: 'neutral',
  pending_approval: 'amber',
  approved: 'electric',
  rejected: 'ember',
  sent: 'electric',
  accepted: 'forest',
  declined: 'ember',
  expired: 'neutral',
  withdrawn: 'neutral',
};

const ACTION_WORDS: Record<string, string> = {
  created: 'Created',
  updated: 'Updated',
  submitted: 'Submitted for approval',
  recalled: 'Recalled',
  reopened: 'Reopened for editing',
  approved: 'Approved',
  rejected: 'Sent back',
  sent: 'Sent to the candidate',
  resent: 'Re-sent to the candidate',
  withdrawn: 'Withdrawn',
  viewed: 'Opened by the candidate',
  code_requested: 'Candidate requested a code',
  accepted: 'Accepted',
  declined: 'Declined',
  expired: 'Expired',
  preboarding_completed: 'Preboarding marked complete',
  exported: 'HRMS export prepared',
};

const EMPLOYMENT_TYPES: EmploymentType[] = ['full_time', 'part_time', 'contract', 'internship'];
const PAY_PERIODS: PayPeriod[] = ['annual', 'monthly', 'hourly'];

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : '—';
}

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
  if (current.employment_type !== saved.employment_type) {
    out.employment_type = (current.employment_type || undefined) as EmploymentType | undefined;
  }
  if (current.start_date !== saved.start_date) out.start_date = current.start_date || null;
  if (current.location !== saved.location) out.location = current.location;
  if (current.base_salary !== saved.base_salary) out.base_salary = current.base_salary;
  if (current.currency !== saved.currency) out.currency = current.currency;
  if (current.pay_period !== saved.pay_period) {
    out.pay_period = (current.pay_period || undefined) as PayPeriod | undefined;
  }
  if (current.bonus !== saved.bonus) out.bonus = current.bonus;
  if (current.equity !== saved.equity) out.equity = current.equity;
  if (current.benefits !== saved.benefits) out.benefits = current.benefits;
  if (current.terms !== saved.terms) out.terms = current.terms;
  if (current.probation_months !== saved.probation_months) {
    out.probation_months = current.probation_months === '' ? null : Number(current.probation_months);
  }
  if (current.notice_period_days !== saved.notice_period_days) {
    out.notice_period_days =
      current.notice_period_days === '' ? null : Number(current.notice_period_days);
  }
  if (current.valid_days !== saved.valid_days) out.valid_days = Number(current.valid_days);
  return out;
}

function OfferFields({ offer }: { offer: OfferDetailShape }) {
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

  const saveMut = useMutation({
    mutationFn: () => updateOffer(offer.id, changed),
    onSuccess: () => {
      toast.success('Offer updated');
      void qc.invalidateQueries({ queryKey: ['hr', 'offer', offer.id] });
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not update this offer')),
  });

  return (
    <GlassCard className="p-5">
      <h2 className="text-[15px] font-semibold text-foreground">Offer details</h2>
      {!editable ? (
        <p className="mt-1 text-[12px] text-muted-foreground">
          This offer is {offer.status.replace('_', ' ')} — it can be changed only as a draft or
          once it has been sent back.
        </p>
      ) : null}

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className={labelCls}>Job title</span>
          <input
            disabled={!editable}
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
            <option value="">—</option>
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
            value={fields.location}
            onChange={(e) => set('location', e.target.value)}
            className={inputCls}
          />
        </label>
        <label className="block">
          <span className={labelCls}>Base salary</span>
          <input
            type="number"
            min={0}
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
            <option value="">—</option>
            {PAY_PERIODS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className={labelCls}>Offer valid for (days)</span>
          <input
            type="number"
            min={1}
            max={60}
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
            disabled={!editable}
            value={fields.notice_period_days}
            onChange={(e) => set('notice_period_days', e.target.value)}
            className={inputCls}
          />
        </label>
        <label className="block">
          <span className={labelCls}>Bonus</span>
          <input
            disabled={!editable}
            value={fields.bonus}
            onChange={(e) => set('bonus', e.target.value)}
            className={inputCls}
          />
        </label>
        <label className="block">
          <span className={labelCls}>Equity</span>
          <input
            disabled={!editable}
            value={fields.equity}
            onChange={(e) => set('equity', e.target.value)}
            className={inputCls}
          />
        </label>
      </div>
      <label className="mt-3 block">
        <span className={labelCls}>Benefits</span>
        <textarea
          disabled={!editable}
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
          value={fields.terms}
          onChange={(e) => set('terms', e.target.value)}
          rows={4}
          className={inputCls}
        />
      </label>

      {editable ? (
        <div className="mt-3 flex justify-end">
          <button
            type="button"
            disabled={!dirty || saveMut.isPending}
            onClick={() => saveMut.mutate()}
            className="rounded-[10px] bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground disabled:opacity-40"
          >
            {saveMut.isPending ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      ) : null}
    </GlassCard>
  );
}

/* ── Actions, legal only where the state machine allows them ─────────────── */

function ActionsPanel({ offer }: { offer: OfferDetailShape }) {
  const qc = useQueryClient();
  const [withdrawReason, setWithdrawReason] = useState('');
  const invalidate = () => qc.invalidateQueries({ queryKey: ['hr', 'offer', offer.id] });

  const submitMut = useMutation({
    mutationFn: () => submitOffer(offer.id),
    onSuccess: () => {
      toast.success('Submitted for approval');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not submit this offer')),
  });
  const recallMut = useMutation({
    mutationFn: () => recallOffer(offer.id),
    onSuccess: () => {
      toast.success('Recalled to draft');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not recall this offer')),
  });
  const reopenMut = useMutation({
    mutationFn: () => reopenOffer(offer.id),
    onSuccess: () => {
      toast.success('Reopened for editing');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not reopen this offer')),
  });
  const sendMut = useMutation({
    mutationFn: () => sendOffer(offer.id),
    onSuccess: () => {
      toast.success('Sent to the candidate');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not send this offer')),
  });
  const resendMut = useMutation({
    mutationFn: () => resendOffer(offer.id),
    onSuccess: () => {
      toast.success('Sent a fresh link — the old one no longer works');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not re-send this offer')),
  });
  const withdrawMut = useMutation({
    mutationFn: () => withdrawOffer(offer.id, withdrawReason || null),
    onSuccess: () => {
      toast.success('Offer withdrawn');
      setWithdrawReason('');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not withdraw this offer')),
  });

  const canSubmit = offer.status === 'draft' || offer.status === 'rejected';
  const canRecall = offer.status === 'pending_approval';
  const canReopen = offer.status === 'approved';
  const canSend = offer.status === 'approved';
  const canResend =
    (offer.status === 'sent' || offer.status === 'accepted') && !offer.preboarding_completed_at;
  const canWithdraw = ['draft', 'pending_approval', 'approved', 'rejected', 'sent'].includes(
    offer.status,
  );

  if (!canSubmit && !canRecall && !canReopen && !canSend && !canResend && !canWithdraw) {
    return (
      <p className="text-[12.5px] text-muted-foreground">
        This offer is {offer.status.replace('_', ' ')} — there is nothing left to do here.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        {canSubmit ? (
          <button
            type="button"
            disabled={submitMut.isPending}
            onClick={() => submitMut.mutate()}
            className="rounded-[10px] bg-primary px-3.5 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
          >
            Submit for approval
          </button>
        ) : null}
        {canRecall ? (
          <button
            type="button"
            disabled={recallMut.isPending}
            onClick={() => recallMut.mutate()}
            className="rounded-[10px] border border-border px-3.5 py-2 text-[12.5px] text-foreground disabled:opacity-40"
          >
            Recall
          </button>
        ) : null}
        {canReopen ? (
          <button
            type="button"
            disabled={reopenMut.isPending}
            onClick={() => reopenMut.mutate()}
            className="rounded-[10px] border border-border px-3.5 py-2 text-[12.5px] text-foreground disabled:opacity-40"
          >
            Reopen for editing
          </button>
        ) : null}
        {canSend ? (
          <button
            type="button"
            disabled={sendMut.isPending}
            onClick={() => sendMut.mutate()}
            className="rounded-[10px] bg-[var(--ui-ok)] px-3.5 py-2 text-[12.5px] font-medium text-white disabled:opacity-40"
          >
            Send to candidate
          </button>
        ) : null}
        {canResend ? (
          <button
            type="button"
            disabled={resendMut.isPending}
            onClick={() => resendMut.mutate()}
            className="rounded-[10px] border border-border px-3.5 py-2 text-[12.5px] text-foreground disabled:opacity-40"
          >
            Re-send
          </button>
        ) : null}
      </div>
      {canResend ? (
        <p className="text-[11.5px] text-[var(--ui-faint)]">
          Re-sending retires the old link and lifts a lock from too many wrong codes.
        </p>
      ) : null}

      {canWithdraw ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <input
            type="text"
            value={withdrawReason}
            onChange={(e) => setWithdrawReason(e.target.value)}
            placeholder="Withdraw reason (optional)"
            aria-label="Withdraw reason"
            className="w-[220px] rounded-[8px] border border-border bg-secondary px-2 py-1.5 text-[12px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none"
          />
          <ConfirmDeleteButton
            label="Withdraw offer"
            pending={withdrawMut.isPending}
            onConfirm={() => withdrawMut.mutate()}
          />
        </div>
      ) : null}
    </div>
  );
}

/* ── Preboarding + HRMS export ─────────────────────────────────────────────── */

function PreboardingPanel({ offer }: { offer: OfferDetailShape }) {
  const qc = useQueryClient();
  const [exportResult, setExportResult] = useState<HrmsExportResult | null>(null);

  const completeMut = useMutation({
    mutationFn: () => completePreboarding(offer.id),
    onSuccess: () => {
      toast.success('Preboarding marked complete');
      void qc.invalidateQueries({ queryKey: ['hr', 'offer', offer.id] });
      void qc.invalidateQueries({ queryKey: ['hr', 'offer', offer.id, 'documents'] });
    },
    // The server names exactly which mandatory documents are not verified yet.
    onError: (e: unknown) => toast.error(errText(e, 'Could not complete preboarding')),
  });

  const exportMut = useMutation({
    mutationFn: () => hrmsExport(offer.id),
    onSuccess: (res) => {
      setExportResult(res);
      toast.success('Export prepared');
      void qc.invalidateQueries({ queryKey: ['hr', 'offer', offer.id] });
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not prepare the export')),
  });

  if (offer.status !== 'accepted') {
    return (
      <p className="text-[12.5px] text-muted-foreground">
        Preboarding follows an accepted offer.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {offer.preboarding_completed_at ? (
        <p className="text-[12.5px] text-[var(--ui-ok)]">
          Preboarding completed on {fmt(offer.preboarding_completed_at)}.
        </p>
      ) : (
        <button
          type="button"
          disabled={completeMut.isPending}
          onClick={() => completeMut.mutate()}
          className="self-start rounded-[10px] bg-primary px-3.5 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
        >
          {completeMut.isPending ? 'Checking…' : 'Mark preboarding complete'}
        </button>
      )}

      {offer.preboarding_completed_at ? (
        <div className="border-t border-border pt-3">
          <button
            type="button"
            disabled={exportMut.isPending}
            onClick={() => exportMut.mutate()}
            className="inline-flex items-center gap-1.5 rounded-[10px] border border-border px-3.5 py-2 text-[12.5px] text-foreground disabled:opacity-40"
          >
            {exportMut.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : null}
            Prepare HRMS export
          </button>

          {exportResult ? (
            <div className="mt-3 rounded-[10px] border border-border p-3">
              <p className="text-[12px] text-[var(--ui-soft)]">
                key_id <span className="text-foreground">{exportResult.key_id}</span> · algorithm{' '}
                <span className="text-foreground">{exportResult.algorithm}</span>
              </p>
              <p className="mt-1 break-all text-[11px] text-[var(--ui-faint)]">
                signature {exportResult.signature}
              </p>
              <pre className="mt-2 max-h-[240px] overflow-auto rounded-[8px] bg-[var(--ui-inset)] p-2.5 text-[11px] text-[var(--ui-soft)]">
                {JSON.stringify(exportResult.payload, null, 2)}
              </pre>
              <button
                type="button"
                onClick={() =>
                  downloadJson(`offer-${offer.id}-hrms-export.json`, exportResult)
                }
                className="mt-2 inline-flex items-center gap-1.5 text-[12px] text-[var(--ui-info)] hover:underline"
              >
                <Download className="h-3.5 w-3.5" aria-hidden="true" />
                Download JSON
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

export default function OfferDetail() {
  const { offerId = '' } = useParams();

  const query = useQuery({
    queryKey: ['hr', 'offer', offerId],
    queryFn: () => getOffer(offerId),
    enabled: Boolean(offerId),
  });

  if (query.isLoading) {
    return (
      <div className="mx-auto w-full max-w-[860px] px-4 py-16">
        <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading…
        </p>
      </div>
    );
  }
  if (query.isError || !query.data) {
    return (
      <div className="mx-auto w-full max-w-[860px] px-4 py-16">
        <p className="text-[13px] text-[var(--ui-danger)]">
          {errText(query.error, 'Could not load this offer.')}
        </p>
      </div>
    );
  }

  const offer = query.data;

  return (
    <div className="mx-auto w-full max-w-[860px] px-4 py-8">
      <Link
        to="/hr/offers"
        className="mb-2 inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
        Offers
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-[22px] font-semibold tracking-[-0.6px] text-foreground">
          {offer.candidate_name} · {offer.job_title}
        </h1>
        <StatusTag tone={OFFER_TONE[offer.status]} dot>
          {offer.status.replace('_', ' ')}
        </StatusTag>
      </div>
      {offer.expires_at ? (
        <p className="mt-1 text-[12.5px] text-muted-foreground">
          Expires {fmt(offer.expires_at)}
        </p>
      ) : null}

      {offer.status === 'rejected' && offer.approval_note ? (
        <p className="mt-3 rounded-[10px] border border-[var(--ui-warn)]/35 bg-[rgba(255,183,100,0.08)] p-3 text-[12.5px] text-[var(--ui-soft)]">
          Sent back: {offer.approval_note}
        </p>
      ) : null}
      {offer.status === 'declined' && offer.decline_reason ? (
        <p className="mt-3 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] p-3 text-[12.5px] text-[var(--ui-soft)]">
          Decline reason: {offer.decline_reason}
        </p>
      ) : null}
      {offer.status === 'withdrawn' && offer.withdraw_reason ? (
        <p className="mt-3 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] p-3 text-[12.5px] text-[var(--ui-soft)]">
          Withdraw reason: {offer.withdraw_reason}
        </p>
      ) : null}

      <div className="mt-5 flex flex-col gap-5">
        <OfferFields offer={offer} />

        <GlassCard className="p-5">
          <h2 className="mb-3 text-[15px] font-semibold text-foreground">Actions</h2>
          <ActionsPanel offer={offer} />
        </GlassCard>

        <GlassCard className="p-5">
          <h2 className="mb-3 text-[15px] font-semibold text-foreground">Documents</h2>
          <OfferDocumentsPanel offerId={offer.id} />
        </GlassCard>

        <GlassCard className="p-5">
          <h2 className="mb-3 text-[15px] font-semibold text-foreground">Preboarding</h2>
          <PreboardingPanel offer={offer} />
        </GlassCard>

        <GlassCard className="p-5">
          <h2 className="mb-3 text-[15px] font-semibold text-foreground">History</h2>
          {offer.history.length === 0 ? (
            <p className="text-[12.5px] text-muted-foreground">No activity recorded yet.</p>
          ) : (
            <ol className="flex flex-col gap-1.5 border-l border-border pl-3">
              {offer.history.map((h, i) => (
                <li key={i} className="text-[12.5px] text-[var(--ui-soft)]">
                  {ACTION_WORDS[h.action] ?? h.action.replace(/_/g, ' ')}
                  {h.actor_name ? ` — ${h.actor_name}` : ''}
                  <span className="ml-1.5 text-[11px] text-[var(--ui-faint)]">
                    {new Date(h.at).toLocaleString()}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </GlassCard>
      </div>
    </div>
  );
}
