// ReviewParts — the one shape every super-admin review surface shares.
//
// A super admin reviews something an HR manager submitted: an opening, a
// workflow version, an offer, a bank question. Those four queues used to differ
// in card shape and in how the decision was laid out. The pattern is now:
//
//   queue    → ReviewQueueState (loading / error + retry / empty) around a list
//              of ReviewQueueItem rows: what it is, who submitted it, when, and
//              how long it has waited. Oldest first, always.
//   detail   → Breadcrumbs, a DataList of the facts, a WhatChanges callout that
//              says what approving does, then the DecisionPanel.
//   decision → Approve is the single primary action. Request changes / Send
//              back sits apart from it and is a two-step action with a reason.
//
// Presentation only: every callback and every minimum length is the caller's.

import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { CheckCircle2, ChevronRight, Info, Loader2, XCircle } from '@/design/components/icons';
import { GlassCard } from '@/design/components/primitives';
import {
  EmptyState,
  Page,
  PageHeader,
  SectionCard,
  Toolbar,
  type PageWidth,
} from '@/design/components/page';
import { ApprovalTrack, type ApprovalStep } from '@/design/components/approval-track';
import { Card } from '@/design/components/card';
import { cn } from '@/lib/utils';

/* ───────────────────────────────  Queue  ─────────────────────────────── */

interface QueueStateProps {
  loading: boolean;
  error: boolean;
  errorText: string;
  onRetry: () => void;
  count: number;
  emptyDescription: string;
  children: ReactNode;
}

/** Everything a queue needs around its rows: the toolbar, the three states. */
export function ReviewQueueState({
  loading,
  error,
  errorText,
  onRetry,
  count,
  emptyDescription,
  children,
}: QueueStateProps): JSX.Element {
  if (loading) {
    return (
      <div className="flex flex-col gap-3" role="status" aria-label="Loading">
        <Skeleton className="h-28 w-full rounded-card" />
        <Skeleton className="h-28 w-full rounded-card" />
        <span className="sr-only">Loading…</span>
      </div>
    );
  }
  if (error) {
    return (
      <div
        role="alert"
        className="rounded-card border border-[var(--ui-danger)]/35 bg-[var(--ui-danger-wash)] p-5 text-[14px] text-[var(--ui-danger)]"
      >
        <p className="font-semibold">{errorText}</p>
        <Button type="button" variant="outline" size="sm" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      </div>
    );
  }
  if (count === 0) {
    return (
      <GlassCard className="p-4">
        <EmptyState
          icon={CheckCircle2}
          title="Nothing is waiting on you."
          description={emptyDescription}
        />
      </GlassCard>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <Toolbar
        className="mb-0"
        trailing={
          <span className="text-[13px] tabular-nums text-muted-foreground">{count} waiting</span>
        }
      >
        <span className="px-1 text-[13px] text-muted-foreground">Sorted oldest request first</span>
      </Toolbar>
      <ul className="flex flex-col gap-3">{children}</ul>
    </div>
  );
}

interface QueueItemProps {
  to: string;
  title: string;
  /** What it is, in one line — the facts a reviewer triages on. */
  summary?: ReactNode;
  submittedBy?: string | null;
  waiting: string;
  note?: string | null;
  cta: string;
  children?: ReactNode;
}

export function ReviewQueueItem({
  to,
  title,
  summary,
  submittedBy,
  waiting,
  note,
  cta,
  children,
}: QueueItemProps): JSX.Element {
  return (
    <li>
      <GlassCard className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-2">
          <div className="min-w-0">
            <Link
              to={to}
              className="text-[16px] font-bold text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              {title}
            </Link>
            {summary ? <p className="mt-0.5 text-[13px] text-muted-foreground">{summary}</p> : null}
          </div>
          <div className="text-left sm:text-right">
            <p className="text-[13px] font-medium text-foreground">{waiting}</p>
            {submittedBy ? (
              <p className="text-[12.5px] text-muted-foreground">by {submittedBy}</p>
            ) : null}
          </div>
        </div>
        {note ? (
          <p className="mt-3 rounded-[10px] bg-[var(--ui-inset-soft)] px-3.5 py-2.5 text-[13px] text-foreground">
            {note}
          </p>
        ) : null}
        {children}
        <div className="mt-4">
          <Button asChild variant="outline" size="sm">
            <Link to={to}>
              {cta}
              <ChevronRight aria-hidden="true" />
            </Link>
          </Button>
        </div>
      </GlassCard>
    </li>
  );
}

/* ───────────────────────────────  Detail  ──────────────────────────────── */

export function ReviewDetailLoading(): JSX.Element {
  return (
    <div className="flex flex-col gap-4" role="status">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-40 w-full rounded-card" />
      <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Loading…
      </p>
    </div>
  );
}

export function ReviewDetailError({
  text,
  backTo,
  backLabel,
}: {
  text: string;
  backTo: string;
  backLabel: string;
}): JSX.Element {
  return (
    <div
      role="alert"
      className="rounded-card border border-[var(--ui-danger)]/35 bg-[var(--ui-danger-wash)] p-5"
    >
      <p className="text-[14px] font-semibold text-[var(--ui-danger)]">{text}</p>
      <Button asChild variant="outline" size="sm" className="mt-3">
        <Link to={backTo}>{backLabel}</Link>
      </Button>
    </div>
  );
}

/**
 * What your decision changes: one plain line above the detail, no box. The
 * consequence is stated once here and not repeated in the decision panel.
 */
export function WhatChanges({ children }: { children: ReactNode }): JSX.Element {
  return (
    <p className="flex items-start gap-2 text-[14px] leading-relaxed text-foreground">
      <Info size={16} className="mt-0.5 flex-none text-[var(--ui-info)]" aria-hidden="true" />
      <span className="min-w-0">
        <span className="font-semibold">What your decision changes: </span>
        {children}
      </span>
    </p>
  );
}

/* ───────────────────────  Where this item is (track)  ─────────────────────── */

const TRACK_STATE_LABELS = {
  done: 'Done',
  current: 'Waiting on you',
  todo: 'Next',
  returned: 'Sent back',
} as const;

interface ReviewTrackProps {
  /** Names the track for assistive tech. */
  label: string;
  /** What was submitted, e.g. "Offer", "Version 2". Names the first step. */
  submittedLabel: string;
  submittedBy: string | null | undefined;
  /** ISO time the item was submitted; the date shows under the step. */
  submittedAt: string | null | undefined;
  /** "Waiting 3 days" — from `waitingText`; shown on the decision step. */
  waiting: string;
  /** True while this item is still the approver's to decide. */
  pending: boolean;
  /** What approving unlocks, e.g. "Sent to candidate", "Published". */
  afterLabel: string;
}

function dateOnly(iso: string | null | undefined): string | undefined {
  return iso ? new Date(iso).toLocaleDateString() : undefined;
}

/**
 * Where an item sits on its way to a decision, built only from the submit
 * fields the record already carries: who submitted it, when, and how long it
 * has been waiting. Nothing is inferred.
 */
export function ReviewTrack({
  label,
  submittedLabel,
  submittedBy,
  submittedAt,
  waiting,
  pending,
  afterLabel,
}: ReviewTrackProps): JSX.Element {
  const steps: ApprovalStep[] = [
    {
      key: 'submitted',
      label: submittedLabel,
      state: 'done',
      actor: submittedBy ? `Submitted by ${submittedBy}` : 'Submitted',
      at: dateOnly(submittedAt),
    },
    {
      key: 'decision',
      label: 'Your decision',
      state: pending ? 'current' : 'done',
      actor: pending ? waiting || 'Waiting on you' : undefined,
    },
    { key: 'after', label: afterLabel, state: 'todo' },
  ];
  return (
    <ApprovalTrack
      data-testid="review-track"
      label={label}
      steps={steps}
      stateLabels={TRACK_STATE_LABELS}
    />
  );
}

interface ReviewDetailLayoutProps {
  width?: PageWidth;
  breadcrumbs: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  /** The `WhatChanges` line; omitted once the item is no longer pending. */
  whatChanges?: ReactNode;
  track: ReactNode;
  /** The evidence the approver decides on, as `Section`s. One raised card. */
  evidence: ReactNode;
  /** The decision panel (sticky) or the "nothing to decide" note. */
  decision: ReactNode;
}

/**
 * The one layout every review detail shares: header, a one-line statement of
 * what the decision changes, where the item is, the evidence in a single
 * raised card, and the decision pinned to the bottom of the screen.
 */
export function ReviewDetailLayout({
  width = 'form',
  breadcrumbs,
  title,
  description,
  whatChanges,
  track,
  evidence,
  decision,
}: ReviewDetailLayoutProps): JSX.Element {
  return (
    <Page width={width}>
      <PageHeader breadcrumbs={breadcrumbs} title={title} description={description} />
      {whatChanges}
      {track}
      <Card emphasis="raised" className="flex flex-col gap-6">
        {evidence}
      </Card>
      {decision}
    </Page>
  );
}

/* ─────────────────────────────  Decision bar  ───────────────────────────── */

export type DecisionMode = 'approve' | 'reject' | null;

interface DecisionPanelProps {
  mode: DecisionMode;
  onMode: (m: DecisionMode) => void;
  note: string;
  onNote: (v: string) => void;
  noteId: string;
  /** Minimum characters for the reason when sending back. */
  rejectNoteMin: number;
  /** Idle-state label: "Request changes" or "Send back". */
  rejectLabel: string;
  approvePending: boolean;
  rejectPending: boolean;
  onApprove: () => void;
  onReject: () => void;
  /** Pin the panel to the bottom of the screen so the decision is never scrolled away. */
  sticky?: boolean;
}

export function DecisionPanel({
  mode,
  onMode,
  note,
  onNote,
  noteId,
  rejectNoteMin,
  rejectLabel,
  approvePending,
  rejectPending,
  onApprove,
  onReject,
  sticky = false,
}: DecisionPanelProps): JSX.Element {
  const rejectReady = note.trim().length >= rejectNoteMin;
  const pending = mode === 'approve' ? approvePending : rejectPending;
  const cancel = (): void => {
    onMode(null);
    onNote('');
  };

  return (
    <SectionCard
      title="Your decision"
      description="Approving lets HR carry on. Sending it back needs a reason, which HR will see."
      data-testid="decision-panel"
      // Pinned only where there is room for it: on a phone, or a short window,
      // a ~180px panel would cover a quarter to a half of the evidence being
      // read. Elsewhere it stays in the flow, after the last row, so nothing
      // above it can ever be hidden behind it. When pinned it sits above the
      // floating "Ask assistant" button (bottom-right, up to 72px tall), so
      // "Send back" is never underneath it (audit AD-F29).
      className={cn(
        sticky &&
          'sm:[@media(min-height:720px)]:sticky sm:[@media(min-height:720px)]:bottom-20 z-10 shadow-elevated',
      )}
    >
      {mode === null ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" onClick={() => onMode('approve')}>
            <CheckCircle2 aria-hidden="true" />
            Approve
          </Button>
          <Button
            type="button"
            variant="outline"
            className="sm:ml-auto"
            onClick={() => onMode('reject')}
          >
            <XCircle aria-hidden="true" />
            {rejectLabel}
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <label htmlFor={noteId} className="text-[13px] font-medium text-foreground">
              {mode === 'approve' ? 'Note (optional)' : 'What needs to change (required)'}
            </label>
            <input
              id={noteId}
              value={note}
              onChange={(e) => onNote(e.target.value)}
              placeholder={mode === 'reject' ? `At least ${rejectNoteMin} characters` : undefined}
              aria-required={mode === 'reject'}
              aria-invalid={mode === 'reject' && !rejectReady ? true : undefined}
              aria-describedby={mode === 'reject' && !rejectReady ? `${noteId}-hint` : undefined}
              className="min-h-10 rounded-[10px] border border-input bg-card px-3.5 py-2 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            />
            {mode === 'reject' && !rejectReady ? (
              <p id={`${noteId}-hint`} className="text-[12.5px] text-[var(--ui-warn)]">
                Write at least {rejectNoteMin} characters.
              </p>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button type="button" variant="ghost" onClick={cancel}>
              Cancel
            </Button>
            <Button
              type="button"
              variant={mode === 'approve' ? 'default' : 'destructive'}
              onClick={mode === 'approve' ? onApprove : onReject}
              disabled={pending || (mode === 'reject' && !rejectReady)}
            >
              {mode === 'approve'
                ? approvePending
                  ? 'Approving…'
                  : 'Confirm approval'
                : rejectPending
                  ? 'Sending…'
                  : 'Send back'}
            </Button>
          </div>
        </div>
      )}
    </SectionCard>
  );
}

/** Shown in place of the decision bar once the item is no longer pending. */
export function NothingToDecide({ children }: { children: ReactNode }): JSX.Element {
  return (
    <GlassCard className="p-5">
      <p className="text-[14px] text-muted-foreground">{children}</p>
    </GlassCard>
  );
}
