// LockBanner — PH4-D1. Shown on a round whose content is fixed: published, or
// taken. Two ways forward, never a dead end: Unpublish (only while nobody has
// taken it; the caller knows from the round's attempt count, and a refusal
// still shows the server's own sentence) and Duplicate round (always available
// while locked).

import { Copy, Loader2, Lock } from '@/design/components/icons';
import { Button } from '@/components/ui/button';

interface LockBannerProps {
  /** The exact sentence to show — PUBLISHED_REASON or TAKEN_REASON from
   *  '@/lib/examLocks', or a 409's own text when the lock was only found out
   *  about reactively (see the module comment in lib/examLocks.ts). */
  reason: string;
  /** Hide Unpublish for a round that cannot be unpublished: not published,
   *  or already taken (the server refuses that, audit E2). */
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
      className="flex flex-wrap items-center gap-3 rounded-[14px] border border-[var(--ui-warn)]/30 bg-[var(--ui-warn-wash)] px-3.5 py-3 text-[12.5px] text-[var(--ui-soft)]"
    >
      <Lock className="h-4 w-4 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />
      <p className="flex-1 min-w-[220px]">{reason}</p>
      <div className="flex items-center gap-2">
        {canUnpublish ? (
          <Button
            type="button"
            variant="outline"
            onClick={onUnpublish}
            disabled={unpublishing}
            aria-busy={unpublishing}
          >
            {unpublishing ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
            Unpublish
          </Button>
        ) : null}
        <Button
          type="button"
          variant="highlight"
          onClick={onDuplicate}
          disabled={duplicating}
          aria-busy={duplicating}
        >
          {duplicating ? (
            <Loader2 className="animate-spin" aria-hidden="true" />
          ) : (
            <Copy aria-hidden="true" />
          )}
          Duplicate round
        </Button>
      </div>
    </div>
  );
}
