// ErrorBoundary — React class error boundary wrapping the routed app.
// Renders a friendly fallback with a reload action and fires a toast.
// Catches unhandled render errors that React's concurrent mode surfaces.
// NOTE: Class component is required — React does not support hook-based
// error boundaries. We keep it as small as possible.

import { Component } from 'react';
import type { ReactNode, ErrorInfo } from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';
import i18n from '@/lib/i18n';
import { Button } from '@/components/ui/button';
import { toast } from '@/lib/toast';

/** Arguments handed to a custom `fallback` renderer. */
export interface ErrorFallbackProps {
  /** Message off the caught error; null when the thrown value was not an Error. */
  errorMessage: string | null;
  /**
   * Clear the boundary and re-render its children. React has already unmounted
   * the crashed subtree, so this is a genuine fresh mount — which is what makes
   * a SCOPED boundary recoverable rather than merely informative.
   */
  reset: () => void;
}

interface Props {
  children: ReactNode;
  /**
   * Optional replacement for the default full-screen "reload the page" card.
   *
   * Exists for boundaries scoped to one subtree, where destroying the whole
   * app is the wrong remedy — the live interview keeps its LiveKit room and
   * server-side session across a client render crash, so its fallback offers a
   * rejoin instead. Omitted ⇒ app-root behaviour is byte-for-byte unchanged.
   */
  fallback?: (props: ErrorFallbackProps) => ReactNode;
}

interface State {
  hasError: boolean;
  errorMessage: string | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, errorMessage: null };
  }

  static getDerivedStateFromError(error: unknown): State {
    const msg = error instanceof Error ? error.message : 'An unexpected error occurred.';
    return { hasError: true, errorMessage: msg };
  }

  override componentDidCatch(error: unknown, _info: ErrorInfo): void {
    const msg = error instanceof Error ? error.message : 'Unknown render error';
    // Non-blocking toast so the fallback UI renders first
    setTimeout(() => {
      toast.error(`App error: ${msg}`);
    }, 0);
    // In production you would send to Sentry here:
    // Sentry.captureException(error, { extra: info });
    // In dev, errors are already shown in the overlay; no extra logging needed.
  }

  handleReload = () => {
    window.location.reload();
  };

  handleReset = () => {
    this.setState({ hasError: false, errorMessage: null });
  };

  override render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback({
          errorMessage: this.state.errorMessage,
          reset: this.handleReset,
        });
      }
      return (
        <main
          className="min-h-screen flex items-center justify-center bg-background px-4"
          role="alert"
          aria-live="assertive"
        >
          <div className="max-w-md w-full text-center space-y-8">
            <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-xl bg-destructive/10 ring-1 ring-inset ring-destructive/20">
              <AlertTriangle className="h-8 w-8 text-destructive" aria-hidden="true" />
            </div>
            <div className="space-y-3">
              <h1 className="text-heading font-semibold text-foreground">
                {i18n.t('error.errorBoundaryTitle')}
              </h1>
              <p className="text-body-sm text-muted-foreground">
                {i18n.t('error.errorBoundaryDesc')}
              </p>
              {import.meta.env.DEV && this.state.errorMessage && (
                <p className="mt-2 rounded-xl border border-border bg-muted/40 px-4 py-2 text-left font-mono text-caption text-muted-foreground break-all">
                  {this.state.errorMessage}
                </p>
              )}
            </div>
            <Button onClick={this.handleReload} className="gap-2">
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              {i18n.t('error.reload')}
            </Button>
          </div>
        </main>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
