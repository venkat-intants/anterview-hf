// StatGroup / Stat — a handful of numbers on ONE surface with divided cells,
// placed next to the thing they summarise. Replaces a row of standalone
// StatCards on a restructured page.

import { Children, createContext, isValidElement, useContext, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';

const MissingLabel = createContext('Not available');

// Full class names so Tailwind can see them. Two-up on a phone; the wider
// layouts depend on how many cells there are, up to six.
const GRID: Record<number, string> = {
  1: 'grid-cols-1',
  2: 'grid-cols-2',
  3: 'grid-cols-2 sm:grid-cols-3',
  4: 'grid-cols-2 sm:grid-cols-4',
  5: 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-5',
  6: 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-6',
};

interface StatGroupProps {
  /** Up to six `Stat`s. */
  children: ReactNode;
  /** Names the group for assistive tech. */
  'aria-label'?: string;
  /** Spoken for a cell whose value is missing; the cell itself shows "—". */
  missingLabel?: string;
  className?: string;
  'data-testid'?: string;
}

/**
 * A single surface of divided stat cells. It renders NOTHING when every `Stat`
 * has a null or undefined `value`: a group of dashes is noise, not information.
 * A group with some values shows "—" in the cells that are missing. Place it
 * with the content it summarises (the head of a list or tab), not above
 * unrelated panels.
 */
export function StatGroup({
  children, 'aria-label': ariaLabel, missingLabel = 'Not available', className, 'data-testid': testId,
}: StatGroupProps): JSX.Element | null {
  const cells = Children.toArray(children);
  const allMissing = cells.every(
    (c) => isValidElement<StatProps>(c) && c.type === Stat && (c.props.value === null || c.props.value === undefined),
  );
  if (allMissing) return null;
  const columns = Math.min(Math.max(cells.length, 1), 6);
  return (
    <MissingLabel.Provider value={missingLabel}>
      {/* The grid overhangs the frame by 1px right and bottom, so every cell can
          draw its own right and bottom rule and the frame clips the last ones. */}
      <div
        data-testid={testId}
        className={cn('overflow-hidden rounded-card border border-border bg-card', className)}
      >
        <ul
          aria-label={ariaLabel}
          className={cn(
            'grid -mb-px -mr-px',
            GRID[columns],
            // An odd last cell on a phone takes the whole row instead of leaving a hole.
            '[&>li:last-child:nth-child(odd)]:col-span-2 sm:[&>li:last-child:nth-child(odd)]:col-span-1',
          )}
        >
          {cells}
        </ul>
      </div>
    </MissingLabel.Provider>
  );
}

interface StatProps {
  label: ReactNode;
  /** Null or undefined means "not known"; 0 is a real value and is shown. */
  value: string | number | null | undefined;
  /** Shown small and muted after the value: "/100", "min". */
  unit?: ReactNode;
  /** Makes the cell a link, e.g. to the filtered list it counts. */
  to?: string;
  /** Overrides the group's spoken label for a missing value. */
  missingLabel?: string;
  'data-testid'?: string;
}

const CELL = 'block h-full border-b border-r border-border p-4';

/** One cell of a StatGroup. Numbers are tabular and formatted en-IN. */
export function Stat({ label, value, unit, to, missingLabel, 'data-testid': testId }: StatProps): JSX.Element {
  const groupMissing = useContext(MissingLabel);
  const missing = value === null || value === undefined;
  const shown = typeof value === 'number' ? value.toLocaleString('en-IN') : value;
  const body = (
    <>
      <span className="block text-[13px] font-medium text-muted-foreground">{label}</span>
      <span className="mt-1 block font-display text-[26px] font-bold leading-tight tracking-[-0.6px] text-foreground tabular-nums">
        {missing ? (
          <span role="img" aria-label={missingLabel ?? groupMissing}>
            —
          </span>
        ) : (
          shown
        )}
        {!missing && unit ? (
          <span className="ml-1 text-[0.6em] font-medium tracking-normal text-muted-foreground">{unit}</span>
        ) : null}
      </span>
    </>
  );
  return (
    <li data-testid={testId} className="min-w-0">
      {to ? (
        <Link
          to={to}
          className={cn(
            CELL,
            'transition-colors duration-[120ms] hover:bg-[var(--ui-inset-soft)] motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--accent)]',
          )}
        >
          {body}
        </Link>
      ) : (
        <div className={CELL}>{body}</div>
      )}
    </li>
  );
}
