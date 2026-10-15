// OfferDocumentsPanel — PH4-A4. HR's side of one offer's preboarding
// documents: each requirement's current version and status, review (verify /
// reject / ask for a replacement, with a reason for anything but verify),
// download through a signed link, and the activity history.
//
// Embedded in OfferDetail.tsx — this is the panel, not the page.

import { downloadUrl } from '@/lib/safeUrl';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  downloadDocument,
  getOfferDocuments,
  reviewDocument,
  type ChecklistItem,
  type DocumentState,
  type ReviewAction,
} from '@/api/offers';
import { toast } from '@/lib/toast';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { Download, FileText, Loader2 } from '@/design/components/icons';
import { EmptyState } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';

const REASON_MIN = 5;

const STATE_TONE: Record<DocumentState, TagTone> = {
  outstanding: 'neutral',
  submitted: 'amber',
  verified: 'forest',
  rejected: 'ember',
  replacement_requested: 'amber',
  expired: 'ember',
};

const STATE_LABEL: Record<DocumentState, string> = {
  outstanding: 'Outstanding',
  submitted: 'Submitted — awaiting review',
  verified: 'Verified',
  rejected: 'Rejected',
  replacement_requested: 'Replacement requested',
  expired: 'Expired',
};

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/** Which review actions the service allows from this document's current state. */
function allowedActions(item: ChecklistItem): ReviewAction[] {
  if (item.state === 'submitted') return ['verify', 'reject', 'request_replacement'];
  if (item.state === 'verified') return ['request_replacement'];
  return [];
}

const ACTION_LABEL: Record<ReviewAction, string> = {
  verify: 'Verify',
  reject: 'Reject',
  request_replacement: 'Ask for a replacement',
};

function DocumentRow({ offerId, item }: { offerId: string; item: ChecklistItem }) {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const [pendingAction, setPendingAction] = useState<ReviewAction | null>(null);

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ['hr', 'offer', offerId, 'documents'] });

  const reviewMut = useMutation({
    mutationFn: (action: ReviewAction) => reviewDocument(item.document!.id, action, note || null),
    onSuccess: (_res, action) => {
      toast.success(
        action === 'verify'
          ? 'Document verified'
          : action === 'reject'
            ? 'Document rejected'
            : 'Replacement requested',
      );
      setNote('');
      setPendingAction(null);
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not update this document')),
  });

  const downloadMut = useMutation({
    mutationFn: () => downloadDocument(item.document!.id),
    onSuccess: (res) => {
      const url = downloadUrl(res.url);
      if (!url) {
        toast.error('That download link could not be opened.');
        return;
      }
      window.open(url, '_blank', 'noopener,noreferrer');
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not open this document')),
  });

  const actions = allowedActions(item);
  const needsReason = pendingAction !== null && pendingAction !== 'verify';
  const reasonReady = note.trim().length >= REASON_MIN;

  return (
    <li
      role="group"
      aria-label={item.name}
      className="border-b border-border py-4 first:pt-0 last:border-b-0 last:pb-0"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="break-words text-[14px] font-semibold text-foreground">
            {item.name}
            {item.mandatory ? '' : ' (optional)'}
          </p>
          {item.document ? (
            <p className="mt-0.5 break-all text-[13px] text-muted-foreground">
              v{item.document.version} · {item.document.file_name ?? 'file'}
              {item.document.expires_on ? ` · expires ${item.document.expires_on}` : ''}
            </p>
          ) : (
            <p className="mt-0.5 text-[13px] text-muted-foreground">Not uploaded yet.</p>
          )}
        </div>
        <StatusTag tone={STATE_TONE[item.state]} dot>
          {STATE_LABEL[item.state]}
        </StatusTag>
      </div>

      {item.document?.review_note ? (
        <p className="mt-2 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-2.5 text-[13px] text-[var(--ui-soft)]">
          {item.document.review_note}
        </p>
      ) : null}

      {item.document ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => downloadMut.mutate()}
            disabled={downloadMut.isPending}
          >
            {downloadMut.isPending ? (
              <Loader2 className="animate-spin" aria-hidden="true" />
            ) : (
              <Download aria-hidden="true" />
            )}
            Download
          </Button>
          {actions.map((action) => (
            <Button
              key={action}
              type="button"
              variant="outline"
              onClick={() => setPendingAction((v) => (v === action ? null : action))}
              aria-pressed={pendingAction === action}
              className={
                action === 'reject'
                  ? 'border-[var(--ui-danger)]/40 text-[var(--ui-danger)] hover:bg-[var(--ui-danger-wash)] hover:text-[var(--ui-danger)]'
                  : undefined
              }
            >
              {ACTION_LABEL[action]}
            </Button>
          ))}
        </div>
      ) : null}

      {pendingAction ? (
        <div className="mt-3 flex flex-col gap-2 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-3">
          <label
            htmlFor={`note-${item.requirement_id}`}
            className="text-[13px] font-semibold text-foreground"
          >
            {needsReason ? 'Tell the candidate what is wrong (required)' : 'Note (optional)'}
          </label>
          <Input
            id={`note-${item.requirement_id}`}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            aria-invalid={needsReason && !reasonReady ? true : undefined}
          />
          {needsReason && !reasonReady ? (
            <p className="text-[12.5px] text-[var(--ui-warn)]">At least {REASON_MIN} characters.</p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              disabled={(needsReason && !reasonReady) || reviewMut.isPending}
              onClick={() => reviewMut.mutate(pendingAction)}
            >
              {reviewMut.isPending ? 'Saving…' : `Confirm — ${ACTION_LABEL[pendingAction]}`}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setPendingAction(null);
                setNote('');
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
    </li>
  );
}

export default function OfferDocumentsPanel({ offerId }: { offerId: string }) {
  const qc = useQueryClient();
  const docs = useQuery({
    queryKey: ['hr', 'offer', offerId, 'documents'],
    queryFn: () => getOfferDocuments(offerId),
  });

  if (docs.isLoading) {
    return (
      <div className="flex flex-col gap-2" aria-busy="true">
        <span className="sr-only">Loading…</span>
        <Skeleton className="h-16 rounded-[16px]" />
        <Skeleton className="h-16 rounded-[16px]" />
      </div>
    );
  }
  if (docs.isError || !docs.data) {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-3">
        <p className="text-[13.5px] text-muted-foreground">Could not load these documents.</p>
        <Button type="button" variant="outline" onClick={() => void docs.refetch()}>
          Try again
        </Button>
      </div>
    );
  }

  const data = docs.data;

  return (
    <div>
      {data.items.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="This opening asks for no documents"
          description="Set them up on the opening's dashboard."
        />
      ) : (
        <ul className="flex flex-col">
          {data.items.map((item) => (
            <DocumentRow key={item.requirement_id} offerId={offerId} item={item} />
          ))}
        </ul>
      )}

      {data.history.length > 0 ? (
        <details className="mt-4">
          <summary className="inline-flex min-h-10 cursor-pointer items-center rounded-full px-1 text-[13.5px] font-semibold text-[var(--ui-info)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]">
            Document activity ({data.history.length})
          </summary>
          <ol className="mt-2 flex flex-col gap-2 border-l border-border pl-3">
            {data.history.map((h, i) => (
              <li key={i} className="text-[13px] text-[var(--ui-soft)]">
                {h.action.replace(/_/g, ' ')}
                {h.actor_name ? ` — ${h.actor_name}` : ''}
                <span className="ml-1.5 text-[12px] text-muted-foreground">
                  {new Date(h.at).toLocaleString()}
                </span>
              </li>
            ))}
          </ol>
        </details>
      ) : null}

      <Button
        type="button"
        variant="ghost"
        onClick={() =>
          void qc.invalidateQueries({ queryKey: ['hr', 'offer', offerId, 'documents'] })
        }
        className="-ml-5 mt-3 text-muted-foreground"
      >
        Refresh
      </Button>
    </div>
  );
}
