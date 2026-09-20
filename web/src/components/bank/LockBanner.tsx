// LockBanner — PH4-D1. Shown on a round whose content is fixed: published, or
// taken. Two ways forward, never a dead end: Unpublish (only while nobody has
// taken it — the server is the judge; a refusal here shows its own sentence,
// same as anywhere else) and Duplicate round (always available while locked).

import { Copy, Loader2, Lock } from '@/design/components/icons';

interface LockBannerProps {
  /** The exact sentence to show — PUBLISHED_REASON or TAKEN_REASON from
   *  '@/lib/examLocks', or a 409's own text when the lock was only found out
   *  about reactively (see the module comment in lib/examLocks.ts). */
  reason: string;
  /** Hide Unpublish for a round that is not currently published (a round
   *  locked only because it was taken has no "unpublish" to offer). */
  canUnpublish: boolean;
  onUnpublish?: () => void;
  unpublishing?: boolean;
  onDuplicate: () => void;
  duplicating?: boolean;
}

export function LockBanner({
  reason,
  canUnpublish,
  onUnpublish,
  unpublishing = false,
  onDuplicate,
  duplicating = false,
}: LockBannerProps): JSX.Element {
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-3 rounded-[14px] border border-[var(--ui-warn)]/30 bg-[rgba(255,183,100,0.08)] px-3.5 py-3 text-[12.5px] text-[var(--ui-soft)]"
    >
      <Lock className="h-4 w-4 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />
      <p className="flex-1 min-w-[220px]">{reason}</p>
      <div className="flex items-center gap-2">
        {canUnpublish ? (
          <button
            type="button"
            onClick={onUnpublish}
            disabled={unpublishing}
            className="inline-flex items-center gap-1.5 rounded-[9px] border border-border px-3 py-1.5 text-[12px] font-medium text-foreground transition-colors hover:border-[var(--ui-line-strong)] disabled:opacity-50"
          >
            {unpublishing ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
            Unpublish
          </button>
        ) : null}
        <button
          type="button"
          onClick={onDuplicate}
          disabled={duplicating}
          className="inline-flex items-center gap-1.5 rounded-[9px] border border-[rgba(var(--accent-rgb),0.4)] bg-[rgba(var(--accent-rgb),0.1)] px-3 py-1.5 text-[12px] font-medium text-[var(--ui-info)] transition-colors hover:bg-[rgba(var(--accent-rgb),0.16)] disabled:opacity-50"
        >
          {duplicating ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Copy className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          Duplicate round
        </button>
      </div>
    </div>
  );
}
