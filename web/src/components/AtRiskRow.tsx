// AtRiskRow — one application against its stage SLA, said in words, as a
// button that opens the candidate's drawer (where a person acts). Shared by the
// HR-home widget and the full Stages at risk page so they cannot disagree.

import { AlertTriangle, Clock } from '@/design/components/icons';
import { cn } from '@/lib/utils';
import type { SlaBoardRow } from '@/api/stageSla';

const STATE_WORD: Record<SlaBoardRow['state'], string> = {
  overdue: 'overdue',
  due_soon: 'due soon',
  on_track: 'on track',
};

function dueWords(row: SlaBoardRow): string {
  const hours = Math.abs(Math.round(row.hours_remaining));
  const span = hours >= 48 ? `${Math.round(hours / 24)} days` : `${hours} h`;
  return row.hours_remaining < 0 ? `${span} over` : `${span} left`;
}

export default function AtRiskRow({
  row,
  onOpen,
  detailed = false,
}: {
  row: SlaBoardRow;
  onOpen: (row: SlaBoardRow) => void;
  /** The full page adds the time left or over and the open exceptions. */
  detailed?: boolean;
}): JSX.Element {
  const Icon = row.state === 'on_track' ? Clock : AlertTriangle;
  return (
    <button
      type="button"
      onClick={() => onOpen(row)}
      aria-label={`${row.full_name} — ${row.stage}, ${row.opening_title}: ${STATE_WORD[row.state]}. Open details`}
      className="flex w-full items-start gap-2.5 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-3 text-left transition-colors hover:border-[var(--ui-line-strong)] focus:outline-none focus-visible:border-[var(--accent)]"
    >
      <Icon
        className={cn(
          'mt-0.5 h-4 w-4 shrink-0',
          row.state === 'overdue'
            ? 'text-[var(--ui-danger)]'
            : row.state === 'due_soon'
              ? 'text-[var(--ui-warn)]'
              : 'text-[var(--ui-soft)]',
        )}
        aria-hidden="true"
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] font-medium text-foreground">
          {row.full_name} &mdash; {row.stage}
        </span>
        <span className="block truncate text-[11.5px] text-muted-foreground">
          {row.opening_title} &middot; {STATE_WORD[row.state]}
          {detailed ? ` (${dueWords(row)})` : ''}
          {row.owner_name ? ` · ${row.owner_name}` : ' · no owner'}
          {detailed && row.open_exceptions > 0
            ? ` · ${row.open_exceptions} open exception${row.open_exceptions === 1 ? '' : 's'}`
            : ''}
        </span>
      </span>
    </button>
  );
}
