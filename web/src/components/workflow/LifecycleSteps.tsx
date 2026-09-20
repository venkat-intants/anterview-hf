// LifecycleSteps — Draft → Dry run → Review → Approved → Published (PH4-O6),
// made visible.
//
// A version cannot be published at all now without a company super admin
// approving it first (workflows.py publish() 409s otherwise), so the strip is
// mostly a read of where a version stands rather than a set of buttons — only
// the dry-run step is clickable, as a shortcut to running one.

import { cn } from '@/lib/utils';
import { stepStates, type LifecycleInput, type StepState } from '@/lib/workflowLifecycle';

// Said in words as well as colour: the border tone alone would leave a
// screen-reader user, or anyone who cannot tell the tones apart, guessing.
const STATE_WORD: Record<StepState, string> = {
  done: 'done',
  current: 'current step',
  blocked: 'blocked',
  todo: 'not yet',
};

function State({ state }: { state: StepState }): JSX.Element {
  return <span className="sr-only"> ({STATE_WORD[state]})</span>;
}

const TONE: Record<StepState, string> = {
  done: 'border-[var(--ui-ok)]/40 text-[var(--ui-ok)]',
  current: 'border-[var(--accent)] text-foreground',
  blocked: 'border-[var(--ui-warn)]/50 text-[var(--ui-warn)]',
  todo: 'border-border text-[var(--ui-faint)]',
};

export default function LifecycleSteps({
  onRunDryRun,
  ...input
}: LifecycleInput & { onRunDryRun: () => void }): JSX.Element {
  const s = stepStates(input);
  return (
    <ol aria-label="Workflow lifecycle" className="mb-4 flex flex-wrap items-center gap-2 text-[12px]">
      <li
        className={cn('rounded-pill border px-3 py-1', TONE[s.draft])}
        data-state={s.draft}
        aria-current={s.draft === 'current' ? 'step' : undefined}
      >
        1. Draft
        <State state={s.draft} />
      </li>
      <li>
        <button
          type="button"
          onClick={onRunDryRun}
          className={cn('rounded-pill border px-3 py-1 hover:border-[var(--accent)]', TONE[s.dryRun])}
          data-state={s.dryRun}
          aria-current={s.dryRun === 'current' ? 'step' : undefined}
        >
          2. Dry run
          <State state={s.dryRun} />
        </button>
      </li>
      <li
        className={cn('rounded-pill border px-3 py-1', TONE[s.review])}
        data-state={s.review}
        aria-current={s.review === 'current' ? 'step' : undefined}
      >
        3. Review
        <State state={s.review} />
      </li>
      <li
        className={cn('rounded-pill border px-3 py-1', TONE[s.approved])}
        data-state={s.approved}
        aria-current={s.approved === 'current' ? 'step' : undefined}
      >
        4. Approved
        <State state={s.approved} />
      </li>
      <li
        className={cn('rounded-pill border px-3 py-1', TONE[s.publish])}
        data-state={s.publish}
        aria-current={s.publish === 'current' ? 'step' : undefined}
      >
        5. Published
        <State state={s.publish} />
      </li>
    </ol>
  );
}
