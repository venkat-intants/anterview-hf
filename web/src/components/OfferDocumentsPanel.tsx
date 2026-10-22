// OfferDocumentsPanel — PH4-A4. HR's side of one offer's preboarding
// documents: each requirement's current version and status, review (verify /
// reject / ask for a replacement, with a reason for anything but verify),
// download through a signed link, and the activity history.
//
// Embedded in OfferDetail.tsx — this is the panel, not the page.

import { downloadUrl } from '@/lib/safeUrl';
import { useState } from 'react';
import { Link } from 'react-router-dom';
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
import { errorMessage } from '@/lib/errorMessage';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { formatDate, formatDateTime } from '@/lib/formatters';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { Download, FileText, Loader2 } from '@/design/components/icons';
import { EmptyState } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { QueryErrorPanel } from '@/components/QueryState';

const REASON_MIN = 5;
/** The server's limit on a review note (ReviewIn). */
const REASON_MAX = 1000;

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
  submitted: 'Submitted, awaiting review',
  verified: 'Verified',
  rejected: 'Rejected',
  replacement_requested: 'Replacement requested',
  expired: 'Expired',
};

/** Each document_events action in words. */
const ACTIVITY_WORD: Record<string, string> = {
  uploaded: 'Uploaded',
  verified: 'Verified',
  rejected: 'Rejected',
  replacement_requested: 'Replacement requested',
  downloaded: 'Downloaded',
};

function activityWord(action: string): string {
  const known = ACTIVITY_WORD[action];
  if (known) return known;
  const words = action.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Which review actions the service allows from this document's current state.
 * An expired document is still `verified` in the database, so a replacement can
 * be asked for (audit OF-F6). Once preboarding is complete nothing is reviewed:
 * the candidate could no longer act on it (audit OF-F5).
 */
function allowedActions(item: ChecklistItem, preboardingComplete: boolean): ReviewAction[] {
  if (preboardingComplete) return [];
  if (item.state === 'submitted') return ['verify', 'reject', 'request_replacement'];
  if (item.state === 'verified' || item.state === 'expired') return ['request_replacement'];
  return [];
}

const ACTION_LABEL: Record<ReviewAction, string> = {
  verify: 'Verify',
  reject: 'Reject',
  request_replacement: 'Ask for a replacement',
};

/**
 * Open a signed download in a new tab. The tab is opened synchronously, inside
 * the click, and pointed at the file once the link arrives: a tab opened only
 * after the awaited request is outside the click, and pop-up blockers (Safari
 * especially) stop it (audit OF-L11).
 */
function openDownloadTab(): Window | null {
  const tab = window.open('about:blank', '_blank');
  if (tab) tab.opener = null;
  return tab;
}

function DocumentRow({
  item,
  preboardingComplete,
}: {
  item: ChecklistItem;
  preboardingComplete: boolean;
}) {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const [pendingAction, setPendingAction] = useState<ReviewAction | null>(null);

  const reviewMut = useMutation({
    mutationFn: (action: ReviewAction) =>
      reviewDocument(item.document!.id, action, action === 'verify' ? null : note.trim() || null),
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
      // The documents, the offer's "complete" button and the list's counts.
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not update this document.')),
  });

  const downloadMut = useMutation({
    mutationFn: (_tab: Window | null) => downloadDocument(item.document!.id),
    onSuccess: (res, tab) => {
      const url = downloadUrl(res.url);
      if (!url) {
        tab?.close();
        toast.error('That download link could not be opened.');
        return;
      }
      if (tab) {
        tab.location.replace(url);
        return;
      }
      // The browser refused even the synchronous tab: offer the link as a
      // plain navigation in a new tab instead of failing silently.
      const a = document.createElement('a');
      a.href = url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      document.body.appendChild(a);
      a.click();
      a.remove();
    },
    onError: (e: unknown, tab) => {
      tab?.close();
      toast.error(errorMessage(e, 'Could not open this document.'));
    },
  });

  const actions = allowedActions(item, preboardingComplete);
  const needsReason = pendingAction !== null && pendingAction !== 'verify';
  const reasonReady = note.trim().length >= REASON_MIN;
  const noteId = `note-${item.requirement_id}`;

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
              {item.document.expires_on ? ` · expires ${formatDate(item.document.expires_on)}` : ''}
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
            onClick={() => downloadMut.mutate(openDownloadTab())}
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
              onClick={() => {
                setPendingAction((v) => (v === action ? null : action));
                setNote('');
              }}
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
          {/* Verifying stores no note, so none is asked for (audit OF-L4). */}
          {needsReason ? (
            <>
              <label htmlFor={noteId} className="text-[13px] font-semibold text-foreground">
                Tell the candidate what is wrong (required)
              </label>
              <Input
                id={noteId}
                value={note}
                maxLength={REASON_MAX}
                onChange={(e) => setNote(e.target.value)}
                aria-invalid={!reasonReady ? true : undefined}
              />
              {!reasonReady ? (
                <p className="text-[12.5px] text-[var(--ui-warn)]">
                  At least {REASON_MIN} characters.
                </p>
              ) : null}
            </>
          ) : (
            <p className="text-[13px] text-[var(--ui-soft)]">
              Mark this document as checked and accepted.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              disabled={(needsReason && !reasonReady) || reviewMut.isPending}
              onClick={() => reviewMut.mutate(pendingAction)}
            >
              {reviewMut.isPending ? 'Saving…' : `Confirm - ${ACTION_LABEL[pendingAction]}`}
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

export default function OfferDocumentsPanel({
  offerId,
  requisitionId = null,
  preboardingComplete = false,
}: {
  offerId: string;
  /** The opening the offer belongs to: its dashboard is where documents are set up. */
  requisitionId?: string | null;
  /** Once preboarding is complete the documents are a record, not a to-do. */
  preboardingComplete?: boolean;
}) {
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
      <QueryErrorPanel
        title="Could not load these documents."
        error={docs.error}
        onRetry={() => void docs.refetch()}
        retrying={docs.isFetching}
      />
    );
  }

  const data = docs.data;
  const complete = preboardingComplete || Boolean(data.preboarding_completed_at);

  return (
    <div>
      {data.items.length === 0 ? (
        requisitionId ? (
          <EmptyState
            icon={FileText}
            title="This opening asks for no documents"
            description={
              <>
                Documents are set up on the{' '}
                <Link
                  to={`/hr/requisitions/${requisitionId}`}
                  className="text-[var(--ui-info)] underline"
                >
                  opening&apos;s dashboard
                </Link>
                .
              </>
            }
          />
        ) : (
          // With no opening there is nowhere to ask for documents from.
          <EmptyState
            icon={FileText}
            title="No documents for this offer"
            description="This offer is not linked to an opening, so no documents are requested for it."
          />
        )
      ) : (
        <ul className="flex flex-col">
          {data.items.map((item) => (
            <DocumentRow key={item.requirement_id} item={item} preboardingComplete={complete} />
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
                {activityWord(h.action)}
                {h.actor_name ? ` - ${h.actor_name}` : ''}
                <span className="ml-1.5 text-[12px] text-muted-foreground">
                  {formatDateTime(h.at)}
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
