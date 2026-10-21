// ConfirmDeleteButton — an irreversible or outward action with an inline
// two-step confirm. The first click "arms" it ("Sure? [confirm] [keep]"); a
// second click on the confirm button fires onConfirm. Keeps the action one
// extra tap away without a modal.
//
// Despite the name it serves every such action, not only deletes: cancelling an
// interview, withdrawing an offer or a scorecard, archiving a bank, revoking an
// accommodation. So the wording is the caller's (audit X5): the confirm button
// repeats the action ("Cancel loop", "Withdraw offer"), never a stock "Delete",
// and when the action is itself called Cancel the button that backs out says
// "Keep" so "Cancel" never means two opposite things side by side.
//
// Pass `label` for a text button (e.g. "Delete company"); omit it for a compact
// icon-only button (e.g. per-row delete), which then needs `title` and an
// explicit `confirmText`.
//
// Keyboard: arming moves focus to the safe button, Escape backs out, and focus
// returns to the trigger after backing out or once the confirmed action ends.

import { useEffect, useRef, useState } from 'react';
import { Trash2, type LucideIcon } from '@/design/components/icons';

interface CommonProps {
  onConfirm: () => void;
  /**
   * The action's in-flight flag. While it is true the armed pair stays up with
   * the confirm button busy; when it falls back to false the control disarms and
   * returns focus to the trigger. Omit it and the control disarms on confirm.
   */
  pending?: boolean;
  /**
   * The trigger's icon. Defaults to the trash can, which is right only for a
   * deletion: pass the icon that matches the action (Ban for cancel, Archive
   * for archive, XCircle for withdraw), or null for none.
   */
  icon?: LucideIcon | null;
  /**
   * The button that backs out. Defaults to "Keep" when the action is itself a
   * cancel ("Cancel loop" next to "Cancel" reads as two ways to cancel), and to
   * "Cancel" otherwise.
   */
  keepText?: string;
  /** What the armed pair of buttons is called, for assistive tech. */
  confirmGroupLabel?: string;
}

interface TextTriggerProps extends CommonProps {
  /** The trigger's visible text, e.g. "Withdraw offer". */
  label: string;
  /** The confirm button's text. Defaults to `label`. */
  confirmText?: string;
  title?: string;
}

interface IconTriggerProps extends CommonProps {
  label?: undefined;
  /** Accessible name and tooltip of the icon-only trigger, e.g. "Remove Asha". */
  title: string;
  /** The confirm button's text. Required: an icon has no words to repeat. */
  confirmText: string;
}

export type ConfirmDeleteButtonProps = TextTriggerProps | IconTriggerProps;

/** "Keep" when the action itself is a cancel, so Cancel never means both. */
function defaultKeepText(confirmText: string): string {
  return /^cancel\b/i.test(confirmText.trim()) ? 'Keep' : 'Cancel';
}

export function ConfirmDeleteButton(props: ConfirmDeleteButtonProps) {
  const { onConfirm, pending, label, icon = Trash2 } = props;
  const confirmText = props.confirmText ?? props.label ?? '';
  const keepText = props.keepText ?? defaultKeepText(confirmText);
  const confirmGroupLabel = props.confirmGroupLabel ?? `Confirm: ${confirmText}`;
  const title = props.title;
  const Icon = icon;

  const [armed, setArmed] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const keepRef = useRef<HTMLButtonElement>(null);
  // Set when the control disarms because of the user, so the trigger, which
  // mounts again on that render, gets focus back.
  const restoreFocus = useRef(false);
  // Set between a confirm click and the end of the action it started.
  const awaiting = useRef(false);

  const disarm = () => {
    restoreFocus.current = true;
    setArmed(false);
  };

  useEffect(() => {
    if (armed) {
      keepRef.current?.focus();
    } else if (restoreFocus.current) {
      restoreFocus.current = false;
      triggerRef.current?.focus();
    }
  }, [armed]);

  useEffect(() => {
    if (awaiting.current && !pending) {
      awaiting.current = false;
      restoreFocus.current = true;
      setArmed(false);
    }
  }, [pending]);

  if (armed) {
    return (
      <span
        className="flex items-center gap-1.5"
        role="group"
        aria-label={confirmGroupLabel}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation();
            disarm();
          }
        }}
      >
        <span className="text-[13px] text-muted-foreground">Sure?</span>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onConfirm();
            if (pending === undefined) disarm();
            else awaiting.current = true;
          }}
          disabled={pending}
          aria-busy={pending}
          className="min-h-10 rounded-full bg-destructive-solid px-3.5 text-[13px] font-semibold text-destructive-foreground transition-colors hover:brightness-95 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ui-danger)] focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          {pending ? '…' : confirmText}
        </button>
        <button
          ref={keepRef}
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            awaiting.current = false;
            disarm();
          }}
          className="min-h-10 rounded-full bg-[var(--ui-inset)] px-3.5 text-[13px] font-semibold text-[var(--ui-soft)] transition-colors hover:bg-[var(--ui-inset-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          {keepText}
        </button>
      </span>
    );
  }

  if (label) {
    return (
      <button
        ref={triggerRef}
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setArmed(true);
        }}
        title={title}
        className="inline-flex min-h-10 items-center gap-1.5 rounded-full border border-[var(--ui-danger)]/40 bg-[var(--ui-danger-wash)] px-3.5 text-[13px] font-semibold text-[var(--ui-danger)] transition-colors hover:brightness-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ui-danger)]"
      >
        {Icon ? <Icon className="h-4 w-4" aria-hidden="true" /> : null}
        {label}
      </button>
    );
  }

  const IconOnly = Icon ?? Trash2;
  return (
    <button
      ref={triggerRef}
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        setArmed(true);
      }}
      title={title}
      aria-label={title}
      className="flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-[var(--ui-danger-wash)] hover:text-[var(--ui-danger)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ui-danger)]"
    >
      <IconOnly className="h-4 w-4" aria-hidden="true" />
    </button>
  );
}
