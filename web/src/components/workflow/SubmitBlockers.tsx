// SubmitBlockers — what stops this version going to review, each one linked to
// the round it names. These are the server's own validation errors
// (ValidationReport.errors), shown before a submit is attempted rather than
// discovered through a refused request. The server prefixes a round's errors
// with its title, which is how an error finds its round.

import { AlertTriangle } from '@/design/components/icons';
import { Well } from '@/design/components/rows';

interface Props {
  errors: string[];
  rounds: { id: string; title: string }[];
  onGoToRound: (roundId: string) => void;
}

function blockerRound(
  error: string,
  rounds: { id: string; title: string }[],
): { id: string; title: string } | undefined {
  return rounds.find((r) => error.startsWith(`${r.title}:`));
}

export default function SubmitBlockers({ errors, rounds, onGoToRound }: Props): JSX.Element | null {
  if (errors.length === 0) return null;
  return (
    <Well data-testid="submit-blockers" role="region" aria-label="Blocking issues">
      <p className="flex items-center gap-2 text-[13px] font-semibold text-foreground">
        <AlertTriangle className="h-4 w-4 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />
        {errors.length === 1
          ? '1 thing blocks this from going to review'
          : `${errors.length} things block this from going to review`}
      </p>
      <ul className="mt-2 flex flex-col gap-2">
        {errors.map((error) => {
          const round = blockerRound(error, rounds);
          return (
            <li
              key={error}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] leading-relaxed text-[var(--ui-soft)]"
            >
              <span className="min-w-0 break-words">{error}</span>
              {round ? (
                <button
                  type="button"
                  onClick={() => onGoToRound(round.id)}
                  aria-label={`Go to ${round.title}`}
                  className="inline-flex min-h-[32px] items-center rounded-pill border border-[var(--ui-line-strong)] px-3 text-[12.5px] font-medium text-foreground hover:bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                >
                  Go to {round.title}
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>
    </Well>
  );
}
