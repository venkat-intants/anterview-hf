// ReviewPanel — PH4-O6. Where a version stands in the approval lifecycle, and
// the actions available from each state (decision D4-2):
//
//   draft / changes_requested  → submit for review (optional note)
//   in_review                  → locked; withdraw from review
//   approved                   → publish (elsewhere, in the header) or reopen
//
// A 422 on submit carries either a ValidationReport or a SimulationResult
// alongside a message — rendered inline here rather than as a toast that
// scrolls away, the same reasoning WorkflowBuilder already applies to publish.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from '@/design/components/icons';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { Collapsible, ProgressiveList } from '@/design/components/collapsible';
import { useScopedPersistKey } from '@/hooks/useScopedPersistKey';
import { toast } from '@/lib/toast';
import {
  getReview,
  reopenForEdits,
  reviewErrorDetail,
  submitForReview,
  withdrawReview,
  type ReviewAction,
  type ReviewErrorDetail,
  type ReviewState,
} from '@/api/workflowReview';
import type { ReviewStatus } from '@/api/workflows';
import { errorMessage } from '@/lib/errorMessage';
import { formatDate, formatDateTime } from '@/lib/formatters';

const STATUS_META: Record<ReviewStatus, { label: string; tone: TagTone }> = {
  draft: { label: 'Draft', tone: 'neutral' },
  in_review: { label: 'In review', tone: 'electric' },
  changes_requested: { label: 'Changes requested', tone: 'amber' },
  approved: { label: 'Approved', tone: 'forest' },
};

const ACTION_WORDS: Record<ReviewAction, string> = {
  submitted: 'Submitted for review',
  withdrawn: 'Withdrawn from review',
  approved: 'Approved',
  changes_requested: 'Changes requested',
  reopened: 'Reopened for edits',
};

function errText(e: unknown, fallback: string): string {
  return errorMessage(e, fallback.endsWith('.') ? fallback : `${fallback}.`);
}

interface Props {
  workflowId: string;
  onChanged: (state: ReviewState) => void;
  /**
   * Why submitting cannot work yet (known blockers), or null. The button is
   * disabled and the reason sits beside it, so the user is told before the
   * request rather than by its failure.
   */
  submitBlockedReason?: string | null;
}

export default function ReviewPanel({
  workflowId,
  onChanged,
  submitBlockedReason = null,
}: Props): JSX.Element {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const historyKey = useScopedPersistKey('workflow-panel-history');
  const [errorDetail, setErrorDetail] = useState<ReviewErrorDetail | null>(null);

  const review = useQuery({
    queryKey: ['hr', 'workflow', workflowId, 'review'],
    queryFn: () => getReview(workflowId),
  });

  const applyState = (state: ReviewState) => {
    qc.setQueryData(['hr', 'workflow', workflowId, 'review'], state);
    onChanged(state);
  };

  const submitMut = useMutation({
    mutationFn: () => submitForReview(workflowId, note),
    onSuccess: (res) => {
      setErrorDetail(null);
      setNote('');
      applyState(res.review);
      qc.setQueryData(['hr', 'workflow', workflowId, 'simulation'], res.simulation);
      toast.success(
        res.approvers > 0
          ? `Submitted for review - ${res.approvers} approver${res.approvers === 1 ? '' : 's'} notified`
          : 'Submitted for review',
      );
    },
    onError: (e) => {
      const detail = reviewErrorDetail(e);
      if (detail) {
        setErrorDetail(detail);
        if (detail.simulation) {
          qc.setQueryData(['hr', 'workflow', workflowId, 'simulation'], detail.simulation);
        }
        toast.error(detail.message);
      } else {
        toast.error(errText(e, 'Could not submit for review'));
      }
    },
  });

  const withdrawMut = useMutation({
    mutationFn: () => withdrawReview(workflowId),
    onSuccess: (state) => {
      applyState(state);
      toast.success('Withdrawn from review');
    },
    onError: (e) => toast.error(errText(e, 'Could not withdraw this version from review')),
  });

  const reopenMut = useMutation({
    mutationFn: () => reopenForEdits(workflowId, note),
    onSuccess: (state) => {
      setNote('');
      applyState(state);
      toast.success('Reopened for edits - it will need approving again before it can be published');
    },
    onError: (e) => toast.error(errText(e, 'Could not reopen this version')),
  });

  if (review.isLoading) {
    return (
      <p className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        Loading review status…
      </p>
    );
  }
  if (review.isError || !review.data) {
    return (
      <p className="text-[12px] text-[var(--ui-danger)]">
        Could not load this version&rsquo;s review status.
      </p>
    );
  }

  const state = review.data;
  const meta = STATUS_META[state.review_status];
  const newestEntry = state.history.reduce<(typeof state.history)[number] | null>(
    (n, h) => (n === null || h.at > n.at ? h : n),
    null,
  );

  return (
    <div className="flex flex-col gap-3" data-testid="review-panel">
      <div className="flex flex-wrap items-center gap-2">
        <StatusTag tone={meta.tone} dot>
          {meta.label}
        </StatusTag>
        {state.review_status === 'in_review' && state.submitted_by_name ? (
          <span className="text-[12px] text-muted-foreground">
            submitted by {state.submitted_by_name}
          </span>
        ) : null}
        {(state.review_status === 'approved' || state.review_status === 'changes_requested') &&
        state.reviewed_by_name ? (
          <span className="text-[12px] text-muted-foreground">by {state.reviewed_by_name}</span>
        ) : null}
      </div>

      {state.review_status === 'changes_requested' && state.note ? (
        <p className="rounded-[10px] border border-[var(--ui-warn)]/30 bg-[var(--ui-warn)]/[0.06] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
          {state.note}
        </p>
      ) : null}

      {errorDetail ? (
        <div
          role="alert"
          className="rounded-[10px] border border-[var(--ui-danger)]/30 bg-[var(--ui-danger-wash)] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]"
        >
          <p className="font-medium text-foreground">{errorDetail.message}</p>
          {errorDetail.validation ? (
            <ul className="mt-1.5 flex flex-col gap-1">
              {errorDetail.validation.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          ) : null}
          {errorDetail.simulation ? (
            <ul className="mt-1.5 flex flex-col gap-1">
              {errorDetail.simulation.rounds
                .flatMap((r) => r.findings)
                .filter((f) => f.severity === 'error')
                .map((f, i) => (
                  <li key={`r-${i}`}>{f.message}</li>
                ))}
              {errorDetail.simulation.workflow_findings
                .filter((f) => f.severity === 'error')
                .map((f, i) => (
                  <li key={`w-${i}`}>{f.message}</li>
                ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {state.review_status === 'draft' || state.review_status === 'changes_requested' ? (
        <div className="flex flex-col gap-2">
          <label htmlFor="review-note" className="text-[12px] font-medium text-[var(--ui-soft)]">
            Note for the reviewer (optional)
          </label>
          <input
            id="review-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          />
          <button
            type="button"
            onClick={() => submitMut.mutate()}
            disabled={submitMut.isPending || submitBlockedReason !== null}
            aria-describedby={submitBlockedReason ? 'review-submit-blocked' : undefined}
            className="self-start rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
          >
            {submitMut.isPending ? 'Submitting…' : 'Submit for review'}
          </button>
          {submitBlockedReason ? (
            <p id="review-submit-blocked" className="text-[12px] text-muted-foreground">
              {submitBlockedReason}
            </p>
          ) : null}
        </div>
      ) : null}

      {state.review_status === 'in_review' ? (
        <div className="flex flex-col gap-2">
          <p className="text-[12px] text-muted-foreground">
            Locked while a company super admin reviews it.
          </p>
          <button
            type="button"
            onClick={() => withdrawMut.mutate()}
            disabled={withdrawMut.isPending}
            className="self-start rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[12.5px] text-[var(--ui-soft)] hover:text-foreground disabled:opacity-40"
          >
            {withdrawMut.isPending ? 'Withdrawing…' : 'Withdraw from review'}
          </button>
        </div>
      ) : null}

      {state.review_status === 'approved' ? (
        <div className="flex flex-col gap-2">
          <label htmlFor="reopen-note" className="text-[12px] font-medium text-[var(--ui-soft)]">
            Note (optional)
          </label>
          <input
            id="reopen-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          />
          <button
            type="button"
            onClick={() => reopenMut.mutate()}
            disabled={reopenMut.isPending}
            title="It will need approving again before it can be published"
            className="self-start rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[12.5px] text-[var(--ui-soft)] hover:text-foreground disabled:opacity-40"
          >
            {reopenMut.isPending ? 'Reopening…' : 'Reopen for edits'}
          </button>
          <p className="text-[12px] text-muted-foreground">
            Reopening clears the approval - it will need approving again before it can go live.
          </p>
        </div>
      ) : null}

      {state.history.length > 0 ? (
        <Collapsible
          title="History"
          headingLevel="h4"
          count={state.history.length}
          noun="entry"
          nounPlural="entries"
          latest={
            newestEntry
              ? `${ACTION_WORDS[newestEntry.action]}${newestEntry.actor_name ? ` by ${newestEntry.actor_name}` : ''}, ${formatDate(newestEntry.at)}`
              : undefined
          }
          persistKey={historyKey}
          data-testid="review-history"
        >
          <ProgressiveList
            as="ol"
            items={state.history}
            initial={5}
            noun="entries"
            getKey={(h, i) => `${h.at}-${i}`}
            className="flex flex-col gap-1.5 border-l border-border pl-3"
            renderItem={(h) => (
              <li className="text-[12px] text-[var(--ui-soft)]">
                {ACTION_WORDS[h.action]}
                {h.actor_name ? ` - ${h.actor_name}` : ''}
                <span className="ml-1.5 text-[12px] text-[var(--ui-faint)]">
                  {formatDateTime(h.at)}
                </span>
                {h.note ? (
                  <span className="block text-[12px] text-muted-foreground">{h.note}</span>
                ) : null}
              </li>
            )}
          />
        </Collapsible>
      ) : null}
    </div>
  );
}
