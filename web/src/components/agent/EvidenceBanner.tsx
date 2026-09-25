// EvidenceBanner — the line that stops an unsourced answer reading as sourced.
//
// `evidence_used` is computed by the SERVER (any successful tool result
// carried >=1 citation), never inferred here from whether the reply happens
// to contain a `[S1]` or the turn happens to carry citations — a model that
// forgets to write a marker must not make this banner say "answered from your
// records" when it did, nor should one that writes a confident-sounding
// unsourced claim get to look cited. This is the control for that, and it
// does not depend on the model behaving.

import { CheckCircle2, Info } from '@/design/components/icons';
import { cn } from '@/lib/utils';

interface EvidenceBannerProps {
  evidenceUsed: boolean;
  className?: string;
}

export default function EvidenceBanner({
  evidenceUsed,
  className,
}: EvidenceBannerProps): JSX.Element {
  const Icon = evidenceUsed ? CheckCircle2 : Info;
  return (
    <p className={cn('flex items-start gap-1.5 text-xs opacity-60', className)}>
      <Icon className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
      <span>
        {evidenceUsed
          ? 'Answered from your records. Claims marked [S…] link to the source.'
          : "No records were read for this answer — this is the assistant's own reading."}
      </span>
    </p>
  );
}
