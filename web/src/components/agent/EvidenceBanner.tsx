// EvidenceBanner — the line that stops an unsourced answer reading as sourced.
//
// `citationState` is computed by the SERVER (see `CitationState` in
// api/agent.ts) — never inferred here from whether the reply happens to
// contain a `[S1]` or the turn happens to carry citations. Three states, not
// two, because "records were read" and "a claim is tied to one of them" are
// different facts:
//
//   'sourced'      — records were read AND at least one claim is tied to one.
//   'unattributed' — records were read but NOTHING ties a claim to any of
//                    them (PH5-E1 criteria 4/5's gap: a model that writes no
//                    markers at all must not get to look exactly like
//                    'sourced' text — a "sourced" model earning that state by
//                    inventing refs would be strictly worse, and the server
//                    guards against that by counting only markers that
//                    survived validation).
//   'unread'       — no record was read at all.

import { AlertTriangle, CheckCircle2, Info } from '@/design/components/icons';
import type { CitationState } from '@/api/agent';
import { cn } from '@/lib/utils';

interface EvidenceBannerProps {
  citationState: CitationState;
  className?: string;
}

const COPY: Record<CitationState, string> = {
  sourced: 'Answered from your records. Claims marked [S…] link to the source.',
  unattributed:
    'Records were read for this answer, but no claim in it is tied to a ' +
    "specific one — read it as the assistant's own summary, not a sourced fact.",
  unread: "No records were read for this answer — this is the assistant's own reading.",
};

const ICON: Record<CitationState, typeof CheckCircle2> = {
  sourced: CheckCircle2,
  unattributed: AlertTriangle,
  unread: Info,
};

export default function EvidenceBanner({
  citationState,
  className,
}: EvidenceBannerProps): JSX.Element {
  const Icon = ICON[citationState];
  return (
    <p className={cn('flex items-start gap-1.5 text-xs opacity-60', className)}>
      <Icon className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
      <span>{COPY[citationState]}</span>
    </p>
  );
}
