// OfferApprovals — PH4-A3, decision D4-2. The company super admin's queue of
// offers awaiting approval, and one offer in full — compensation included,
// which only HR managers and the approving super admin ever see.
//
// Mirrors WorkflowReviews.tsx: two views in one file, `/superadmin/offer-
// approvals` (the queue) and `/superadmin/offer-approvals/:offerId` (one
// offer). Separation of duties (nobody approves an offer they wrote or
// submitted) is enforced server-side; a 403 here is shown exactly as worded.
// Layout is the shared review pattern in components/superadmin/ReviewParts.

import { offerActionWord } from '@/lib/offerHistory';
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Breadcrumbs, DataList, Page, PageHeader } from '@/design/components/page';
import { Section } from '@/design/components/rows';
import { Collapsible, ProgressiveList } from '@/design/components/collapsible';
import { useScopedPersistKey } from '@/hooks/useScopedPersistKey';
import { toast } from '@/lib/toast';
import {
  DecisionPanel,
  NothingToDecide,
  ReviewDetailError,
  ReviewDetailLayout,
  ReviewDetailLoading,
  ReviewQueueItem,
  ReviewQueueState,
  ReviewTrack,
  WhatChanges,
  type DecisionMode,
} from '@/components/superadmin/ReviewParts';
import { waitingText } from '@/components/superadmin/waitingText';
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
  if (!o.base_salary) return '-';
  const amount = Number(o.base_salary).toLocaleString();
  return `${o.currency ?? ''} ${amount}${o.pay_period ? ` / ${o.pay_period}` : ''}`.trim();
}

/* ── The queue ──────────────────────────────────────────────────────────── */

function ApprovalQueueView(): JSX.Element {
  const queue = useQuery({ queryKey: ['admin', 'offer-approvals'], queryFn: listOfferApprovals });
  const rows = queue.data ?? [];

  return (
    <Page width="form">
      <PageHeader
        title="Offer approvals"
        description="Offers your HR managers have submitted, waiting on your decision before they can be sent. Oldest request first."
      />
      <ReviewQueueState
        loading={queue.isLoading}
        error={queue.isError}
        errorText="Could not load the offer approval queue."
        onRetry={() => void queue.refetch()}
        count={rows.length}
        emptyDescription="An offer your HR managers submit for approval appears here."
      >
        {rows.map((o) => (
          <ReviewQueueItem
            key={o.id}
            to={`/superadmin/offer-approvals/${o.id}`}
            title={`${o.candidate_name} · ${o.job_title}`}
            summary={money(o)}
            waiting={waitingText(o.submitted_at)}
            cta="Review this offer"
          />
        ))}
      </ReviewQueueState>
    </Page>
  );
}

/* ── One offer, in full ─────────────────────────────────────────────────── */

function ApprovalDetailView({ offerId }: { offerId: string }): JSX.Element {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const [mode, setMode] = useState<DecisionMode>(null);
  const historyKey = useScopedPersistKey('offer-approval-history');

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
      toast.success('Approved - HR can send it now');
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

  const crumbs = (
    <Breadcrumbs
      items={[
        { label: 'Offer approvals', to: '/superadmin/offer-approvals' },
        { label: 'Review offer' },
      ]}
    />
  );

  if (detail.isLoading) {
    return (
      <Page width="form">
        {crumbs}
        <ReviewDetailLoading />
      </Page>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <Page width="form">
        {crumbs}
        <ReviewDetailError
          text="Could not load this offer."
          backTo="/superadmin/offer-approvals"
          backLabel="Back to offer approvals"
        />
      </Page>
    );
  }

  const o = detail.data;
  const newestEntry = o.history.reduce<(typeof o.history)[number] | null>(
    (n, h) => (n === null || h.at > n.at ? h : n),
    null,
  );
  const canDecide = o.status === 'pending_approval';
  // Who submitted it, from the offer's own history (the latest submission).
  const submittedBy =
    [...o.history].reverse().find((h) => h.action === 'submitted')?.actor_name ?? null;

  return (
    <ReviewDetailLayout
      breadcrumbs={crumbs}
      title={`${o.candidate_name} · ${o.job_title}`}
      description={money(o)}
      whatChanges={
        canDecide ? (
          <WhatChanges>
            approving lets HR send this offer to {o.candidate_name}; sending it back returns it to
            HR to revise. Nothing reaches the candidate until you approve.
          </WhatChanges>
        ) : undefined
      }
      track={
        <ReviewTrack
          label="Offer approval progress"
          submittedLabel="Offer submitted"
          submittedBy={submittedBy}
          submittedAt={o.submitted_at}
          waiting={waitingText(o.submitted_at)}
          pending={canDecide}
          afterLabel="HR sends it"
        />
      }
      evidence={
        <>
          <Section
            title="Employment terms"
            description="Compensation is visible only to HR and the approving super admin."
          >
            <DataList
              items={[
                { label: 'Employment type', value: o.employment_type?.replace('_', ' ') ?? '-' },
                { label: 'Start date', value: o.start_date ?? '-' },
                { label: 'Location', value: o.location ?? '-' },
                {
                  label: 'Probation',
                  value: o.probation_months != null ? `${o.probation_months} months` : '-',
                },
                {
                  label: 'Notice period',
                  value: o.notice_period_days != null ? `${o.notice_period_days} days` : '-',
                },
                { label: 'Offer window', value: o.valid_days ? `${o.valid_days} days` : '-' },
                ...(o.bonus ? [{ label: 'Bonus', value: o.bonus }] : []),
                ...(o.equity ? [{ label: 'Equity', value: o.equity }] : []),
                ...(o.benefits ? [{ label: 'Benefits', value: o.benefits }] : []),
                ...(o.terms ? [{ label: 'Terms', value: o.terms }] : []),
              ]}
            />
          </Section>

          <Collapsible
            variant="card"
            title="History"
            count={o.history.length}
            noun="entry"
            nounPlural="entries"
            latest={
              newestEntry
                ? `${offerActionWord(newestEntry.action)}${newestEntry.actor_name ? ` by ${newestEntry.actor_name}` : ''}, ${new Date(newestEntry.at).toLocaleDateString()}`
                : undefined
            }
            persistKey={historyKey}
            data-testid="offer-approval-history"
          >
            {o.history.length === 0 ? (
              <p className="text-[13.5px] text-muted-foreground">No activity recorded yet.</p>
            ) : (
              <ProgressiveList
                as="ol"
                items={o.history}
                initial={5}
                noun="entries"
                getKey={(h, i) => `${h.at}-${i}`}
                className="flex flex-col gap-2 border-l border-border pl-4"
                renderItem={(h) => (
                  <li className="text-[13.5px] text-foreground">
                    {offerActionWord(h.action)}
                    {h.actor_name ? ` - ${h.actor_name}` : ''}
                    <span className="ml-2 text-[12.5px] text-muted-foreground">
                      {new Date(h.at).toLocaleString()}
                    </span>
                  </li>
                )}
              />
            )}
          </Collapsible>
        </>
      }
      decision={
        canDecide ? (
          <DecisionPanel
            sticky
            mode={mode}
            onMode={setMode}
            note={note}
            onNote={setNote}
            noteId="offer-decision-note"
            rejectNoteMin={REJECT_NOTE_MIN}
            rejectLabel="Send back"
            approvePending={approveMut.isPending}
            rejectPending={rejectMut.isPending}
            onApprove={() => approveMut.mutate()}
            onReject={() => rejectMut.mutate()}
          />
        ) : (
          <NothingToDecide>
            This offer is {o.status.replace('_', ' ')} - nothing for you to decide right now.
          </NothingToDecide>
        )
      }
    />
  );
}


/* ── Page ───────────────────────────────────────────────────────────────── */

export default function OfferApprovals(): JSX.Element {
  const { offerId } = useParams();
  return offerId ? <ApprovalDetailView offerId={offerId} /> : <ApprovalQueueView />;
}
