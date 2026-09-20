// RoundRouting — PH4-O3. Where a result on this round sends a candidate.
//
// The pass branch is read-only here: it is the chain add/reorder maintain
// (`on_pass_next_round_id`), and letting this panel edit it too would let the
// canvas order and this field disagree about what "next" means. Below the
// threshold and fast-track are the two branches this panel actually sets.
//
// Fast-track is a pair — a score and a destination — enforced together: the
// toggle sets or clears both in one patch, because sending one half leaves
// the round in the "needs both a score and a destination" state the server
// rejects at validate time.

import { ToggleSwitch } from '@/design/components/primitives';
import { AlertTriangle } from '@/design/components/icons';
import { cn } from '@/lib/utils';
import type { Round, RoundBranchPatch } from '@/api/workflows';

interface RoutingTarget {
  id: string;
  title: string;
}

interface Props {
  round: Round;
  /** Every other round in this workflow — the only valid routing targets. */
  otherRounds: RoutingTarget[];
  editable: boolean;
  /** Validation errors that name this round, shown inline. */
  errors: string[];
  onPatch: (fields: RoundBranchPatch) => void;
}

const SELECT =
  'w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none disabled:opacity-60';

export default function RoundRouting({
  round,
  otherRounds,
  editable,
  errors,
  onPatch,
}: Props): JSX.Element {
  const passLabel = round.on_pass_next_round_id
    ? (otherRounds.find((r) => r.id === round.on_pass_next_round_id)?.title ?? 'another round')
    : 'Final decision';
  // A human review has no score to be "below" — it is a person's verdict —
  // so the wording follows the round's own language rather than a threshold
  // that does not apply to it.
  const notPassedLabel = round.kind === 'human_review' ? 'If not passed' : 'If below the threshold';
  // A fast-track needs somewhere to go — with no other round in the workflow
  // there is nothing to offer, so the toggle would otherwise set a score with
  // no destination, exactly the half-set state the server rejects.
  const canFastTrack = round.kind !== 'human_review' && otherRounds.length > 0;
  const hasFastTrack = Boolean(round.on_fast_track_next_round_id);

  return (
    <section
      aria-label="Routing"
      className="flex flex-col gap-3 rounded-[12px] border border-border p-3"
    >
      <h3 className="text-[12px] font-medium text-[var(--ui-soft)]">Routing</h3>

      <p className="text-[12.5px] text-foreground">
        If they pass <span aria-hidden="true">&rarr;</span>{' '}
        <span className="font-medium">{passLabel}</span>
      </p>

      <div>
        <label
          htmlFor={`route-fail-${round.id}`}
          className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]"
        >
          {notPassedLabel}
        </label>
        <select
          id={`route-fail-${round.id}`}
          value={round.on_fail_next_round_id ?? ''}
          disabled={!editable}
          onChange={(e) =>
            onPatch({ on_fail_next_round_id: e.target.value === '' ? null : e.target.value })
          }
          className={SELECT}
        >
          <option value="">Hold for a person</option>
          {otherRounds.map((r) => (
            <option key={r.id} value={r.id}>
              {r.title}
            </option>
          ))}
        </select>
      </div>

      {canFastTrack ? (
        <div className="flex flex-col gap-2 border-t border-border pt-3">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[12px] font-medium text-[var(--ui-soft)]">Fast-track</span>
            <div className={cn(!editable && 'pointer-events-none opacity-50')}>
              <ToggleSwitch
                checked={hasFastTrack}
                label={`Fast-track ${round.title}`}
                onChange={(next) => {
                  if (next) {
                    const suggested = Math.min(100, (round.pass_threshold ?? 50) + 10);
                    onPatch({
                      fast_track_min_percent: round.fast_track_min_percent ?? suggested,
                      on_fast_track_next_round_id: otherRounds[0]?.id ?? null,
                    });
                  } else {
                    onPatch({ fast_track_min_percent: null, on_fast_track_next_round_id: null });
                  }
                }}
              />
            </div>
          </div>
          {hasFastTrack ? (
            <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-foreground">
              <span>score at or above</span>
              <label className="sr-only" htmlFor={`fast-pct-${round.id}`}>
                Fast-track score
              </label>
              <input
                id={`fast-pct-${round.id}`}
                type="number"
                min={1}
                max={100}
                disabled={!editable}
                value={round.fast_track_min_percent ?? ''}
                onChange={(e) =>
                  onPatch({
                    fast_track_min_percent: e.target.value === '' ? null : Number(e.target.value),
                  })
                }
                className="w-20 rounded-[10px] border border-border bg-secondary px-2 py-1.5 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none disabled:opacity-60"
              />
              <span>%</span>
              <span>go to</span>
              <label className="sr-only" htmlFor={`fast-target-${round.id}`}>
                Fast-track destination
              </label>
              <select
                id={`fast-target-${round.id}`}
                value={round.on_fast_track_next_round_id ?? ''}
                disabled={!editable}
                onChange={(e) => onPatch({ on_fast_track_next_round_id: e.target.value })}
                className={cn(SELECT, 'w-auto flex-1')}
              >
                {otherRounds.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.title}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
        </div>
      ) : (
        <p className="border-t border-border pt-3 text-[11.5px] leading-relaxed text-muted-foreground">
          {round.kind === 'human_review'
            ? 'A human review has no score, so it cannot fast-track anyone.'
            : 'Add another round before this one can fast-track anyone.'}
        </p>
      )}

      {errors.length > 0 ? (
        <ul className="flex flex-col gap-1">
          {errors.map((e) => (
            <li key={e} className="flex items-start gap-1.5 text-[11.5px] text-[var(--ui-warn)]">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {e}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
