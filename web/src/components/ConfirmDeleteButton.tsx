// ConfirmDeleteButton — a destructive action with an inline two-step confirm.
// First click "arms" it (shows Delete? / Yes / No); a second click on Yes fires
// onConfirm. Keeps destructive actions one extra tap away without a modal.
//
// Pass `label` for a text button (e.g. "Delete company"); omit it for a compact
// trash-icon button (e.g. per-row delete).

import { useState } from 'react';
import { Trash2 } from '@/design/components/icons';

interface ConfirmDeleteButtonProps {
  onConfirm: () => void;
  pending?: boolean;
  label?: string;
  /** Accessible label / tooltip for the icon-only variant. */
  title?: string;
  /** The word on the confirming button — "Delete" unless the action is not a
   *  deletion (withdrawing an offer keeps it, so it says "Withdraw"). */
  confirmText?: string;
  /** What the armed pair of buttons is called, for assistive tech. */
  confirmGroupLabel?: string;
}

export function ConfirmDeleteButton({
  onConfirm,
  pending = false,
  label,
  title = 'Delete',
  confirmText = 'Delete',
  confirmGroupLabel = 'Confirm deletion',
}: ConfirmDeleteButtonProps) {
  const [armed, setArmed] = useState(false);

  if (armed) {
    return (
      <span className="flex items-center gap-1.5" role="group" aria-label={confirmGroupLabel}>
        <span className="text-[13px] text-muted-foreground">Sure?</span>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onConfirm();
          }}
          disabled={pending}
          aria-busy={pending}
          className="min-h-10 rounded-full bg-destructive-solid px-3.5 text-[13px] font-semibold text-destructive-foreground transition-colors hover:brightness-95 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ui-danger)] focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          {pending ? '…' : confirmText}
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setArmed(false);
          }}
          className="min-h-10 rounded-full bg-[var(--ui-inset)] px-3.5 text-[13px] font-semibold text-[var(--ui-soft)] transition-colors hover:bg-[var(--ui-inset-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          Cancel
        </button>
      </span>
    );
  }

  if (label) {
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setArmed(true);
        }}
        className="inline-flex min-h-10 items-center gap-1.5 rounded-full border border-[var(--ui-danger)]/40 bg-[var(--ui-danger-wash)] px-3.5 text-[13px] font-semibold text-[var(--ui-danger)] transition-colors hover:brightness-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ui-danger)]"
      >
        <Trash2 className="h-4 w-4" aria-hidden="true" />
        {label}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        setArmed(true);
      }}
      title={title}
      aria-label={title}
      className="flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-[var(--ui-danger-wash)] hover:text-[var(--ui-danger)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ui-danger)]"
    >
      <Trash2 className="h-4 w-4" aria-hidden="true" />
    </button>
  );
}
