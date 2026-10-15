// GenerationError - the inline failure notice every AI generator shows.
//
// Shows the server's own sentence where the person is looking (not a toast that
// disappears), with "Try again" when retrying can help. For the daily limit it
// shows the sentence alone - there is nothing to retry until tomorrow.
//
// English-only by design (CLAUDE.md - staff consoles are not translated).

import { AlertTriangle, RefreshCw } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { GenerationFailure } from '@/lib/generationError';

interface Props {
  failure: GenerationFailure;
  onRetry: () => void;
  retryDisabled?: boolean;
  title?: string;
  className?: string;
}

export function GenerationError({
  failure,
  onRetry,
  retryDisabled,
  title,
  className,
}: Props): JSX.Element {
  return (
    <div
      role="alert"
      data-testid="generation-error"
      className={cn(
        'flex flex-wrap items-start justify-between gap-3 rounded-[12px] bg-[var(--ui-danger-wash)] p-3.5',
        className,
      )}
    >
      <div className="flex min-w-0 items-start gap-2">
        <AlertTriangle
          size={16}
          className="mt-0.5 flex-none text-[var(--ui-danger)]"
          aria-hidden="true"
        />
        <div className="min-w-0">
          {title ? <p className="text-[13.5px] font-semibold text-foreground">{title}</p> : null}
          <p className="mt-0.5 break-words text-[13px] leading-relaxed text-foreground">
            {failure.message}
          </p>
        </div>
      </div>
      {failure.canRetry ? (
        <Button type="button" variant="outline" onClick={onRetry} disabled={retryDisabled}>
          <RefreshCw aria-hidden="true" />
          Try again
        </Button>
      ) : null}
    </div>
  );
}

export default GenerationError;
