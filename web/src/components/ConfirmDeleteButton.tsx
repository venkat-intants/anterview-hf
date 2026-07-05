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
}

export function ConfirmDeleteButton({
  onConfirm,
  pending = false,
  label,
  title = 'Delete',
}: ConfirmDeleteButtonProps) {
  const [armed, setArmed] = useState(false);

  if (armed) {
    return (
      <span className="flex items-center gap-1.5" role="group" aria-label="Confirm deletion">
        <span className="text-[11px] text-[#888b91]">Sure?</span>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onConfirm();
          }}
          disabled={pending}
          aria-busy={pending}
          className="rounded-[7px] bg-[#e6714f]/20 px-2 py-1 text-[11px] font-semibold text-[#ff8a66] transition-colors hover:bg-[#e6714f]/30 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#e6714f]"
        >
          {pending ? '…' : 'Delete'}
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setArmed(false);
          }}
          className="rounded-[7px] bg-white/[0.06] px-2 py-1 text-[11px] font-medium text-[#b8babf] transition-colors hover:bg-white/[0.1] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
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
        className="inline-flex items-center gap-1.5 rounded-[9px] border border-[#e6714f]/30 bg-[#e6714f]/10 px-3 py-1.5 text-[12.5px] font-medium text-[#ff8a66] transition-colors hover:bg-[#e6714f]/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#e6714f]"
      >
        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
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
      className="rounded-[7px] p-1.5 text-[#70757c] transition-colors hover:bg-[#e6714f]/15 hover:text-[#ff8a66] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#e6714f]"
    >
      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
    </button>
  );
}
