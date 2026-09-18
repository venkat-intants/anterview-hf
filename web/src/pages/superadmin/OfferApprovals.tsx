// OfferApprovals — PH4-A3, decision D4-2. The company super admin's queue of
// offers awaiting approval, and one offer in full — compensation included,
// which only HR managers and the approving super admin ever see.
//
// Mirrors WorkflowReviews.tsx: two views in one file, `/superadmin/offer-
// approvals` (the queue) and `/superadmin/offer-approvals/:offerId` (one
// offer). Separation of duties (nobody approves an offer they wrote or
// submitted) is enforced server-side; a 403 here is shown exactly as worded.

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, CheckCircle2, Loader2, XCircle } from '@/design/components/icons';
import { GlassCard } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { toast } from '@/lib/toast';
import {
  approveOffer,
  getAdminOffer,
  listOfferApprovals,
  rejectOffer,
  type OfferOut,
} from '@/api/offers';

const REJECT_NOTE_MIN = 10;

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function money(o: OfferOut): string {
  if (!o.base_salary) return '—';
  const amount = Number(o.base_salary).toLocaleString();
  return `${o.currency ?? ''} ${amount}${o.pay_period ? ` / ${o.pay_period}` : ''}`.trim();
}

function waiting(iso: string | null): string {
  if (!iso) return '';
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (Number.isNaN(days)) return '';
  if (days <= 0) return 'Submitted today';
  return days === 1 ? 'Waiting 1 day' : `Waiting ${days} days`;
}

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
  accepted: 'Accepted',
  declined: 'Declined',
};

/* ── The queue ──────────────────────────────────────────────────────────── */

function ApprovalQueueView(): JSX.Element {
  const queue = useQuery({ queryKey: ['admin', 'offer-approvals'], queryFn: listOfferApprovals });

  return (
    <div className="mx-auto w-full max-w-[860px] px-4 py-8">
      <Reveal>
        <h1 className="text-[22px] font-semibold text-foreground">Offer approvals</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          Offers your HR managers have submitted, waiting on your decision before they can be
          sent. Oldest request first.
        </p>
      </Reveal>

      {queue.isLoading ? (
        <p className="mt-6 flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Loading…
        </p>
      ) : queue.isError ? (
        <p className="mt-6 text-[13px] text-[var(--ui-danger)]">
          Could not load the offer approval queue.
        </p>
      ) : (queue.data ?? []).length === 0 ? (
        <GlassCard className="mt-6 p-6 text-center">
          <p className="text-[13.5px] text-foreground">Nothing is waiting on you.</p>
          <p className="mt-1 text-[12.5px] text-muted-foreground">
            An offer your HR managers submit for approval appears here.
          </p>
        </GlassCard>
      ) : (
        <div className="mt-6 flex flex-col gap-3">
          {(queue.data ?? []).map((o) => (
            <GlassCard key={o.id} className="p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <Link
                    to={`/superadmin/offer-approvals/${o.id}`}
                    className="text-[15px] font-semibold text-foreground hover:underline"
                  >
                    {o.candidate_name} · {o.job_title}
                  </Link>
                  <p className="mt-0.5 text-[12.5px] text-muted-foreground">{money(o)}</p>
                </div>
                <p className="text-[12px] text-muted-foreground">{waiting(o.submitted_at)}</p>
              </div>
              <Link
                to={`/superadmin/offer-approvals/${o.id}`}
                className="mt-3 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--accent)] hover:underline"
              >
                Review this offer
              </Link>
            </GlassCard>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── One offer, in full ─────────────────────────────────────────────────── */

function ApprovalDetailView({ offerId }: { offerId: string }): JSX.Element {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const [mode, setMode] = useState<'approve' | 'reject' | null>(null);

  const detail = useQuery({
    queryKey: ['admin', 'offer-approvals', offerId],
    queryFn: () => getAdminOffer(offerId),
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['admin', 'offer-approvals', offerId] });
    void qc.invalidateQueries({ queryKey: ['admin', 'offer-approvals'] });
  };

  const approveMut = useMutation({
    mutationFn: () => approveOffer(offerId, note),
    onSuccess: () => {
      setMode(null);
      setNote('');
      invalidate();
      toast.success('Approved — HR can send it now');
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not approve this offer')),
  });

  const rejectMut = useMutation({
    mutationFn: () => rejectOffer(offerId, note),
    onSuccess: () => {
      setMode(null);
      setNote('');
      invalidate();
      toast.success('Sent back with your note');
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not send this back')),
  });

  if (detail.isLoading) {
    return (
      <div className="mx-auto w-full max-w-[780px] px-4 py-16">
        <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading…
        </p>
      </div>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <div className="mx-auto w-full max-w-[780px] px-4 py-16">
        <p className="text-[13px] text-[var(--ui-danger)]">Could not load this offer.</p>
      </div>
    );
  }

  const o = detail.data;
  const canDecide = o.status === 'pending_approval';
  const rejectReady = note.trim().length >= REJECT_NOTE_MIN;

  return (
    <div className="mx-auto w-full max-w-[780px] px-4 py-8">
      <Reveal>
        <Link
          to="/superadmin/offer-approvals"
          className="mb-2 inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          Offer approvals
        </Link>
        <h1 className="text-[22px] font-semibold tracking-[-0.5px] text-foreground">
          {o.candidate_name} · {o.job_title}
        </h1>
        <p className="mt-1.5 text-[13px] text-muted-foreground">{money(o)}</p>
      </Reveal>

      <div className="mt-6 grid gap-5">
        <GlassCard className="p-5">
          <h2 className="mb-3 text-[14px] font-semibold text-foreground">Employment terms</h2>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-[12.5px]">
            <dt className="text-muted-foreground">Employment type</dt>
            <dd className="text-foreground">{o.employment_type?.replace('_', ' ') ?? '—'}</dd>
            <dt className="text-muted-foreground">Start date</dt>
            <dd className="text-foreground">{o.start_date ?? '—'}</dd>
            <dt className="text-muted-foreground">Location</dt>
            <dd className="text-foreground">{o.location ?? '—'}</dd>
            <dt className="text-muted-foreground">Probation</dt>
            <dd className="text-foreground">
              {o.probation_months != null ? `${o.probation_months} months` : '—'}
            </dd>
            <dt className="text-muted-foreground">Notice period</dt>
            <dd className="text-foreground">
              {o.notice_period_days != null ? `${o.notice_period_days} days` : '—'}
            </dd>
            <dt className="text-muted-foreground">Offer window</dt>
            <dd className="text-foreground">{o.valid_days ? `${o.valid_days} days` : '—'}</dd>
          </dl>
          {o.bonus ? <p className="mt-3 text-[12.5px] text-[var(--ui-soft)]">Bonus: {o.bonus}</p> : null}
          {o.equity ? (
            <p className="mt-1 text-[12.5px] text-[var(--ui-soft)]">Equity: {o.equity}</p>
          ) : null}
          {o.benefits ? (
            <p className="mt-1 text-[12.5px] text-[var(--ui-soft)]">Benefits: {o.benefits}</p>
          ) : null}
          {o.terms ? <p className="mt-1 text-[12.5px] text-[var(--ui-soft)]">Terms: {o.terms}</p> : null}
        </GlassCard>

        <GlassCard className="p-5">
          <h2 className="mb-3 text-[14px] font-semibold text-foreground">History</h2>
          {o.history.length === 0 ? (
            <p className="text-[12.5px] text-muted-foreground">No activity recorded yet.</p>
          ) : (
            <ol className="flex flex-col gap-1.5 border-l border-border pl-3">
              {o.history.map((h, i) => (
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

        {canDecide ? (
          <GlassCard className="p-5">
            <h2 className="mb-3 text-[14px] font-semibold text-foreground">Your decision</h2>
            {mode === null ? (
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setMode('approve')}
                  className="inline-flex items-center gap-1.5 rounded-[10px] border border-[var(--ui-ok)]/35 px-4 py-2 text-[13px] font-medium text-[var(--ui-ok)] hover:bg-[var(--ui-ok)]/10"
                >
                  <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                  Approve
                </button>
                <button
                  type="button"
                  onClick={() => setMode('reject')}
                  className="inline-flex items-center gap-1.5 rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[13px] text-muted-foreground hover:border-[var(--ui-warn)]/40 hover:text-[var(--ui-warn)]"
                >
                  <XCircle className="h-4 w-4" aria-hidden="true" />
                  Send back
                </button>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                <label
                  htmlFor="offer-decision-note"
                  className="text-[12px] font-medium text-[var(--ui-soft)]"
                >
                  {mode === 'approve' ? 'Note (optional)' : 'What needs to change (required)'}
                </label>
                <input
                  id="offer-decision-note"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder={mode === 'reject' ? `At least ${REJECT_NOTE_MIN} characters` : undefined}
                  className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
                />
                {mode === 'reject' && !rejectReady ? (
                  <p className="text-[11.5px] text-[var(--ui-warn)]">
                    Write at least {REJECT_NOTE_MIN} characters.
                  </p>
                ) : null}
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => (mode === 'approve' ? approveMut.mutate() : rejectMut.mutate())}
                    disabled={
                      (mode === 'approve' ? approveMut.isPending : rejectMut.isPending) ||
                      (mode === 'reject' && !rejectReady)
                    }
                    className="rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
                  >
                    {mode === 'approve'
                      ? approveMut.isPending
                        ? 'Approving…'
                        : 'Confirm approval'
                      : rejectMut.isPending
                        ? 'Sending…'
                        : 'Send back'}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setMode(null);
                      setNote('');
                    }}
                    className="rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[12.5px] text-[var(--ui-soft)] hover:text-foreground"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </GlassCard>
        ) : (
          <GlassCard className="p-5">
            <p className="text-[12.5px] text-muted-foreground">
              This offer is {o.status.replace('_', ' ')} — nothing for you to decide right now.
            </p>
          </GlassCard>
        )}
      </div>
    </div>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

export default function OfferApprovals(): JSX.Element {
  const { offerId } = useParams();
  return offerId ? <ApprovalDetailView offerId={offerId} /> : <ApprovalQueueView />;
}
