// LoadingSkeleton: a candidate content area while its data loads (UI
// conventions, "Loading, empty and error states", D3).
//
// Grey blocks shaped like the cards that replace them, instead of a spinner
// and a line of text. The label is still said once: it is the region's
// accessible status text (visually hidden), already translated by the caller,
// so a screen reader hears "Loading your offer" rather than nothing.

import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

interface LoadingSkeletonProps {
  /** What is loading, already translated ("Loading your task…"). */
  label: string;
  /** How many card-shaped blocks follow the first, taller one. */
  rows?: number;
  className?: string;
  'data-testid'?: string;
}

export function LoadingSkeleton({
  label,
  rows = 2,
  className,
  'data-testid': testId,
}: LoadingSkeletonProps): JSX.Element {
  return (
    <div
      role="status"
      aria-busy="true"
      data-testid={testId}
      className={cn('flex w-full flex-col gap-4', className)}
    >
      <span className="sr-only">{label}</span>
      <Skeleton aria-hidden="true" className="h-36 w-full rounded-[16px]" />
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} aria-hidden="true" className="h-20 w-full rounded-[16px]" />
      ))}
    </div>
  );
}
