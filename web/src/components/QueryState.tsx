// QueryState — loading, failed, empty and loaded, for any list or panel fed by
// one query (audit X3, consistency D1).
//
// The failure it exists to prevent: a query with no error branch renders its
// empty state when the load FAILS, so HR is told "No exams yet" or "No attempts
// yet" while the exams are there and the server is down. Every list query must
// tell the four apart:
//
//   loading -> skeletons (`loading`, or two skeleton rows by default)
//   failed  -> QueryErrorPanel: what could not load, why when we know, and
//              "Try again", which refetches
//   empty   -> `empty` (an EmptyState with the next step)
//   loaded  -> children(data)
//
// It renders a fragment, so it can sit inside a grid or a table body next to
// siblings it does not own (the exams grid keeps its "Create new exam" tile).
//
// A background refetch that fails while data is already on screen keeps the
// data: the error panel is only for "there is nothing to show because the load
// failed". Use it for queries that are enabled; a disabled query has no data and
// no error, and would sit in the loading state.

import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { AlertTriangle } from '@/design/components/icons';
import { errorMessage } from '@/lib/errorMessage';
import { cn } from '@/lib/utils';

/** The slice of a react-query result QueryState reads. UseQueryResult fits it. */
export interface QueryLike<T> {
  data: T | undefined;
  isError: boolean;
  error: unknown;
  isFetching: boolean;
  refetch: () => unknown;
}

interface QueryErrorPanelProps {
  /** What could not load, in the house style: "Could not load exams." */
  title: string;
  /** The error, used to say why when that is known (offline, no permission). */
  error?: unknown;
  /** Refetch. Omit only when retrying cannot help. */
  onRetry?: () => void;
  /** True while the retry is in flight. */
  retrying?: boolean;
  className?: string;
}

/** A failed load, with the reason when known and a way to try again. */
export function QueryErrorPanel({
  title,
  error,
  onRetry,
  retrying = false,
  className,
}: QueryErrorPanelProps): JSX.Element {
  // errorMessage falls back to the title itself; anything that merely extends
  // the title ("... Try again in a moment.") adds nothing next to the button.
  const reason = error === undefined ? '' : errorMessage(error, title);
  const detail = reason && !reason.startsWith(title) ? reason : '';
  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-[16px] border border-dashed border-[var(--ui-line-strong)] px-6 py-8 text-center',
        className,
      )}
    >
      <AlertTriangle className="h-5 w-5 text-[var(--ui-danger)]" aria-hidden="true" />
      <div className="space-y-1">
        <p className="text-[14px] font-semibold text-foreground">{title}</p>
        {detail ? <p className="text-[13px] text-muted-foreground">{detail}</p> : null}
      </div>
      {onRetry ? (
        <Button
          type="button"
          variant="outline"
          onClick={onRetry}
          disabled={retrying}
          aria-busy={retrying}
        >
          {retrying ? 'Trying again…' : 'Try again'}
        </Button>
      ) : null}
    </div>
  );
}

interface QueryStateProps<T> {
  query: QueryLike<T>;
  /** Copy for a failed load: "Could not load <what>." */
  errorTitle: string;
  /** Shown while loading. Defaults to two skeleton rows. */
  loading?: ReactNode;
  /** True when the loaded data has nothing to show. */
  isEmpty?: (data: T) => boolean;
  /** Shown for loaded-but-empty data. Required with `isEmpty`. */
  empty?: ReactNode;
  /** Extra classes for the error panel (a grid span, a margin). */
  errorClassName?: string;
  children: (data: T) => ReactNode;
}

export function QueryState<T>({
  query,
  errorTitle,
  loading,
  isEmpty,
  empty,
  errorClassName,
  children,
}: QueryStateProps<T>): JSX.Element {
  const { data } = query;
  if (data === undefined) {
    if (query.isError) {
      return (
        <QueryErrorPanel
          title={errorTitle}
          error={query.error}
          onRetry={() => void query.refetch()}
          retrying={query.isFetching}
          className={errorClassName}
        />
      );
    }
    return (
      <>
        {loading ?? (
          <div className="flex flex-col gap-2" aria-busy="true">
            <span className="sr-only">Loading…</span>
            <Skeleton className="h-16 rounded-[16px]" />
            <Skeleton className="h-16 rounded-[16px]" />
          </div>
        )}
      </>
    );
  }
  if (isEmpty?.(data)) return <>{empty ?? null}</>;
  return <>{children(data)}</>;
}
