// InterviewErrorBoundary — error boundary scoped to the LIVE interview subtree.
//
// The app-root boundary in App.tsx is the outer net, but it is the wrong net
// here: React unmounts everything below the NEAREST boundary, so one malformed
// transcript item, an unexpected avatar state, or a null participant track
// would blank the entire app and hand a candidate in a timed, paid session a
// single "reload the page" button.
//
// Scoping a boundary at the interview panel keeps the blast radius local, and
// — because the LiveKit room and the server-side session both outlive a client
// render crash — the fallback leads with REJOIN, which re-mounts the panel and
// reconnects to the same session. Reload stays as the second option, for the
// case where the crash is in state the remount would rebuild identically.
//
// Copy is assembled from existing i18n keys (EN/HI/TE all present) rather than
// new ones: dedicated strings belong in src/lib/i18n.ts, which this change does
// not own. Swapping them in later is a one-line edit per line of copy.

import { useCallback } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertCircle, RotateCcw } from 'lucide-react';
import ErrorBoundary from '@/components/ErrorBoundary';
import type { ErrorFallbackProps } from '@/components/ErrorBoundary';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface InterviewErrorBoundaryProps {
  children: ReactNode;
}

export default function InterviewErrorBoundary({ children }: InterviewErrorBoundaryProps) {
  const { t } = useTranslation();

  const renderFallback = useCallback(
    ({ errorMessage, reset }: ErrorFallbackProps): ReactNode => (
      <div
        className={cn(
          'fixed inset-0 z-50 flex flex-col items-center justify-center gap-6',
          'bg-black px-6 text-center text-white',
        )}
        role="alert"
        aria-live="assertive"
      >
        <AlertCircle className="h-10 w-10 text-[#e6714f]" aria-hidden="true" />

        <div className="space-y-2">
          <h1 className="text-[20px] font-semibold tracking-[-0.4px]">
            {t('error.errorBoundaryTitle')}
          </h1>
          <p className="mx-auto max-w-sm text-[14px] leading-relaxed text-white/70">
            {t('interview.errorFallback')}
          </p>
        </div>

        {import.meta.env.DEV && errorMessage && (
          <p className="max-w-sm break-all rounded-xl border border-white/10 bg-white/[0.04] px-4 py-2 text-left font-mono text-[12px] text-white/60">
            {errorMessage}
          </p>
        )}

        <div className="flex flex-wrap items-center justify-center gap-3">
          <Button onClick={reset} className="gap-2">
            <RotateCcw className="h-4 w-4" aria-hidden="true" />
            {t('interview.tryAgain')}
          </Button>
          <Button variant="ghost" onClick={() => window.location.reload()}>
            {t('error.reload')}
          </Button>
        </div>
      </div>
    ),
    [t],
  );

  return <ErrorBoundary fallback={renderFallback}>{children}</ErrorBoundary>;
}
