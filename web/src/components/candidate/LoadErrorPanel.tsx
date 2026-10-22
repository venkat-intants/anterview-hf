// LoadErrorPanel: a candidate page's "this did not load" state, in the
// candidate's language, with a retry.
//
// The staff QueryErrorPanel is English-only by design (staff screens are), so
// candidate pages use this one. It exists for the same reason: a list whose
// load FAILED must not fall through to its empty state and tell someone they
// have no interviews or no resume while the server is down (audit CA-M6,
// CA-M7).

import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { AlertTriangle } from '@/design/components/icons';
import { cn } from '@/lib/utils';

interface LoadErrorPanelProps {
  /** What could not load, already translated. */
  title: string;
  onRetry: () => void;
  retrying?: boolean;
  className?: string;
}

export function LoadErrorPanel({
  title,
  onRetry,
  retrying = false,
  className,
}: LoadErrorPanelProps): JSX.Element {
  const { t } = useTranslation();
  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-[16px] border border-dashed border-[var(--ui-line-strong)] px-6 py-8 text-center',
        className,
      )}
    >
      <AlertTriangle className="h-5 w-5 text-[var(--ui-danger)]" aria-hidden="true" />
      <p className="text-[14px] font-semibold text-foreground">{title}</p>
      <Button
        type="button"
        variant="outline"
        onClick={onRetry}
        disabled={retrying}
        aria-busy={retrying}
      >
        {retrying ? t('error.tryingAgain') : t('error.tryAgain')}
      </Button>
    </div>
  );
}
