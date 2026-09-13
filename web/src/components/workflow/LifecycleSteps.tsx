// LifecycleSteps — Draft → Preview → Validate → Publish, made visible (D5).
//
// Publishing sends real invitations to real people, so the order matters and
// should be on screen rather than implied: look at it as a candidate will, fix
// what blocks it, then publish.

import { cn } from '@/lib/utils';
import { stepStates, type LifecycleInput, type StepState } from '@/lib/workflowLifecycle';

const TONE: Record<StepState, string> = {
  done: 'border-[var(--ui-ok)]/40 text-[var(--ui-ok)]',
  current: 'border-[var(--accent)] text-foreground',
  blocked: 'border-[var(--ui-warn)]/50 text-[var(--ui-warn)]',
  todo: 'border-border text-[var(--ui-faint)]',
};

export default function LifecycleSteps({
  onPreview,
  onPublish,
  ...input
}: LifecycleInput & { onPreview: () => void; onPublish: () => void }): JSX.Element {
  const s = stepStates(input);
  const issueText = input.publishable
    ? 'Ready'
    : `${input.issues} issue${input.issues === 1 ? '' : 's'} to fix`;
  return (
    <ol aria-label="Workflow lifecycle" className="mb-4 flex flex-wrap items-center gap-2 text-[12px]">
      <li className={cn('rounded-pill border px-3 py-1', TONE[s.draft])} data-state={s.draft}>
        1. Draft
      </li>
      <li>
        <button
          type="button"
          onClick={onPreview}
          className={cn('rounded-pill border px-3 py-1 hover:border-[var(--accent)]', TONE[s.preview])}
          data-state={s.preview}
        >
          2. Preview as a candidate
        </button>
      </li>
      <li className={cn('rounded-pill border px-3 py-1', TONE[s.validate])} data-state={s.validate}>
        3. Validate · {issueText}
      </li>
      <li>
        <button
          type="button"
          onClick={onPublish}
          disabled={!input.publishable}
          className={cn(
            'rounded-pill border px-3 py-1 disabled:cursor-not-allowed disabled:opacity-60',
            TONE[s.publish],
          )}
          data-state={s.publish}
        >
          4. Publish
        </button>
      </li>
    </ol>
  );
}
