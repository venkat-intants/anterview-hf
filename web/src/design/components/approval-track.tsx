// ApprovalTrack — who has approved, who must act now, and what was sent back.
// Every step comes from the record's own approval history; nothing is inferred.

import { useId, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { AlertTriangle, Check } from './icons';

export type ApprovalStepState = 'done' | 'current' | 'todo' | 'returned';

export interface ApprovalStep {
  key: string;
  label: ReactNode;
  state: ApprovalStepState;
  /** Who acted (done) or who must act now (current). */
  actor?: string;
  /** A pre-formatted date. */
  at?: string;
  /** Why it was sent back; shown inline on a `returned` step. */
  note?: ReactNode;
}

interface ApprovalTrackProps {
  steps: ApprovalStep[];
  /** Names the track for assistive tech, e.g. "Approval progress". */
  label: string;
  /** The word for each state, shown and spoken. Pass translated text. */
  stateLabels: Record<ApprovalStepState, string>;
  /** The one action this state calls for (submit, approve, withdraw). */
  primary?: ReactNode;
  /** A quieter alternative beside it. */
  secondary?: ReactNode;
  className?: string;
  'data-testid'?: string;
}

const RULE: Record<ApprovalStepState, string> = {
  done: 'sm:border-primary',
  current: 'sm:border-[var(--accent)]',
  returned: 'sm:border-[var(--ui-warn)]',
  todo: 'sm:border-border',
};

function Mark({ state, index }: { state: ApprovalStepState; index: number }): JSX.Element {
  const base =
    'flex h-7 w-7 flex-none items-center justify-center rounded-full text-[12.5px] font-bold';
  if (state === 'done') {
    return (
      <span className={cn(base, 'bg-primary text-primary-foreground')}>
        <Check size={14} aria-hidden="true" />
      </span>
    );
  }
  if (state === 'returned') {
    return (
      <span className={cn(base, 'bg-[var(--ui-warn-wash)] text-[var(--ui-warn)]')}>
        <AlertTriangle size={14} aria-hidden="true" />
      </span>
    );
  }
  if (state === 'current') {
    return (
      <span
        className={cn(
          base,
          'bg-[var(--ui-highlight)] text-[var(--ui-highlight-ink)] ring-2 ring-[var(--accent)]',
        )}
      >
        {index + 1}
      </span>
    );
  }
  return (
    <span className={cn(base, 'border border-[var(--ui-line-strong)] text-muted-foreground')}>
      {index + 1}
    </span>
  );
}

/**
 * The approval path of a record (an opening, a workflow, an offer): a row of
 * steps at `sm` and up, a vertical stack below. Completed steps show who and
 * when; the current step names who must act; a returned step shows its note
 * inline, linked with `aria-describedby`. State is an icon AND a word from
 * `stateLabels`. `primary` / `secondary` carry the one action the state calls
 * for, so the page needs no second Approval card.
 */
export function ApprovalTrack({
  steps,
  label,
  stateLabels,
  primary,
  secondary,
  className,
  'data-testid': testId,
}: ApprovalTrackProps): JSX.Element {
  const base = useId();
  return (
    <div
      data-testid={testId}
      className={cn('rounded-card border border-border bg-card p-4 sm:p-5', className)}
    >
      <ol aria-label={label} className="flex flex-col gap-4 sm:flex-row sm:gap-4">
        {steps.map((step, i) => {
          const noteId = `${base}-note-${step.key}`;
          const hasNote = step.state === 'returned' && Boolean(step.note);
          const who = [step.actor, step.at].filter(Boolean).join(' · ');
          return (
            <li
              key={step.key}
              aria-current={step.state === 'current' ? 'step' : undefined}
              aria-describedby={hasNote ? noteId : undefined}
              className={cn(
                'flex min-w-0 gap-3 sm:flex-col sm:border-t-2 sm:pt-3',
                // A sent-back step carries the reviewer's note; give it two
                // columns so the note reads as a sentence, not a ribbon.
                hasNote ? 'sm:flex-[2_1_0%]' : 'sm:flex-1',
                RULE[step.state],
              )}
            >
              <Mark state={step.state} index={i} />
              <div className="min-w-0">
                <p className="break-words text-[14px] font-semibold text-foreground">
                  {step.label}
                </p>
                <p className="text-[12.5px] font-medium text-muted-foreground">
                  {stateLabels[step.state]}
                </p>
                {who ? (
                  <p className="mt-0.5 break-words text-[12.5px] text-muted-foreground">{who}</p>
                ) : null}
                {hasNote ? (
                  <p id={noteId} className="mt-1 break-words text-[12.5px] text-foreground">
                    {step.note}
                  </p>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
      {primary || secondary ? (
        <div
          className={cn(
            'flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-end [&>*]:min-h-[48px] [&>*]:w-full sm:[&>*]:min-h-[40px] sm:[&>*]:w-auto',
            // One lone action: no divider, tighter gap. Two actions keep the
            // ruled footer so the pair reads as the decision.
            primary && secondary ? 'mt-4 border-t border-border pt-4' : 'mt-3',
          )}
        >
          {secondary}
          {primary}
        </div>
      ) : null}
    </div>
  );
}
