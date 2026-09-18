// OfferDocumentsPanel — PH4-A4. HR's side of one offer's preboarding
// documents: each requirement's current version and status, review (verify /
// reject / ask for a replacement, with a reason for anything but verify),
// download through a signed link, and the activity history.
//
// Embedded in OfferDetail.tsx — this is the panel, not the page.

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
import { Download, Loader2 } from '@/design/components/icons';

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

  const invalidate = () => qc.invalidateQueries({ queryKey: ['hr', 'offer', offerId, 'documents'] });

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
      window.open(res.url, '_blank', 'noopener,noreferrer');
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not open this document')),
  });

  const actions = allowedActions(item);
  const needsReason = pendingAction !== null && pendingAction !== 'verify';
  const reasonReady = note.trim().length >= REASON_MIN;

  return (
    <li role="group" aria-label={item.name} className="rounded-[10px] border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-medium text-foreground">
            {item.name}
            {item.mandatory ? '' : ' (optional)'}
          </p>
          {item.document ? (
            <p className="mt-0.5 text-[11.5px] text-muted-foreground">
              v{item.document.version} · {item.document.file_name ?? 'file'}
              {item.document.expires_on ? ` · expires ${item.document.expires_on}` : ''}
            </p>
          ) : (
            <p className="mt-0.5 text-[11.5px] text-muted-foreground">Not uploaded yet.</p>
          )}
        </div>
        <StatusTag tone={STATE_TONE[item.state]} dot>
          {STATE_LABEL[item.state]}
        </StatusTag>
      </div>

      {item.document?.review_note ? (
        <p className="mt-2 rounded-[8px] border border-border bg-[var(--ui-inset-soft)] p-2 text-[12px] text-[var(--ui-soft)]">
          {item.document.review_note}
        </p>
      ) : null}

      {item.document ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => downloadMut.mutate()}
            disabled={downloadMut.isPending}
            className="inline-flex items-center gap-1.5 rounded-[8px] border border-border px-2.5 py-1 text-[11.5px] text-foreground disabled:opacity-40"
          >
            {downloadMut.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
            ) : (
              <Download className="h-3 w-3" aria-hidden="true" />
            )}
            Download
          </button>
          {actions.map((action) => (
            <button
              key={action}
              type="button"
              onClick={() => setPendingAction((v) => (v === action ? null : action))}
              className="rounded-[8px] border border-border px-2.5 py-1 text-[11.5px] text-foreground"
            >
              {ACTION_LABEL[action]}
            </button>
          ))}
        </div>
      ) : null}

      {pendingAction ? (
        <div className="mt-2 flex flex-col gap-1.5">
          <label
            htmlFor={`note-${item.requirement_id}`}
            className="text-[11.5px] font-medium text-[var(--ui-soft)]"
          >
            {needsReason
              ? 'Tell the candidate what is wrong (required)'
              : 'Note (optional)'}
          </label>
          <input
            id={`note-${item.requirement_id}`}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="rounded-[8px] border border-border bg-secondary px-2.5 py-1.5 text-[12px] text-foreground focus:border-[var(--accent)] focus:outline-none"
          />
          {needsReason && !reasonReady ? (
            <p className="text-[11px] text-[var(--ui-warn)]">
              At least {REASON_MIN} characters.
            </p>
          ) : null}
          <div className="flex gap-2">
            <button
              type="button"
              disabled={(needsReason && !reasonReady) || reviewMut.isPending}
              onClick={() => reviewMut.mutate(pendingAction)}
              className="self-start rounded-[8px] bg-primary px-3 py-1.5 text-[11.5px] font-medium text-primary-foreground disabled:opacity-40"
            >
              {reviewMut.isPending ? 'Saving…' : `Confirm — ${ACTION_LABEL[pendingAction]}`}
            </button>
            <button
              type="button"
              onClick={() => {
                setPendingAction(null);
                setNote('');
              }}
              className="self-start rounded-[8px] px-3 py-1.5 text-[11.5px] text-muted-foreground hover:text-foreground"
            >
              Cancel
            </button>
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
    return <p className="text-[12.5px] text-muted-foreground">Loading…</p>;
  }
  if (docs.isError || !docs.data) {
    return (
      <p className="text-[12.5px] text-muted-foreground">Could not load these documents.</p>
    );
  }

  const data = docs.data;

  return (
    <div>
      {data.items.length === 0 ? (
        <p className="text-[12.5px] text-muted-foreground">
          This opening asks for no documents — set them up on the opening&apos;s dashboard.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {data.items.map((item) => (
            <DocumentRow key={item.requirement_id} offerId={offerId} item={item} />
          ))}
        </ul>
      )}

      {data.history.length > 0 ? (
        <details className="mt-4">
          <summary className="cursor-pointer text-[12.5px] text-[var(--ui-info)]">
            Document activity ({data.history.length})
          </summary>
          <ol className="mt-2 flex flex-col gap-1.5 border-l border-border pl-3">
            {data.history.map((h, i) => (
              <li key={i} className="text-[12px] text-[var(--ui-soft)]">
                {h.action.replace(/_/g, ' ')}
                {h.actor_name ? ` — ${h.actor_name}` : ''}
                <span className="ml-1.5 text-[11px] text-[var(--ui-faint)]">
                  {new Date(h.at).toLocaleString()}
                </span>
              </li>
            ))}
          </ol>
        </details>
      ) : null}

      <button
        type="button"
        onClick={() => void qc.invalidateQueries({ queryKey: ['hr', 'offer', offerId, 'documents'] })}
        className="mt-3 text-[11.5px] text-muted-foreground hover:text-foreground"
      >
        Refresh
      </button>
    </div>
  );
}
