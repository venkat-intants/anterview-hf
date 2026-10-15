// JourneyRail — a multi-stage setup flow where every stage's state is a FACT the
// server holds (a published version, an attached exam), not a guess.
//
//   lg+      a ~300px rail of stages on the left, the active stage's panel on the right
//   < lg     an accordion: the active stage is open and the panel sits inside it
//
// The panel (`children`) is rendered exactly once, in whichever place the
// viewport calls for, so nothing in it mounts twice.

import { useCallback, useId, useSyncExternalStore, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { Check, Clock, Lock } from './icons';

export type StageState = 'todo' | 'active' | 'blocked' | 'awaiting' | 'done' | 'optional';
export type SubstepState = 'todo' | 'active' | 'done' | 'blocked';

export interface JourneySubstep {
  key: string;
  label: ReactNode;
  state: SubstepState;
}

export interface JourneyStage {
  key: string;
  title: ReactNode;
  state: StageState;
  /** The server fact behind the state: "JD v2 published", "2 of 3 rounds have an exam". */
  fact?: ReactNode;
  /** Why a `blocked` stage cannot start. Shown as text under the stage. */
  reason?: ReactNode;
  /** Who has to act (for `awaiting`) or who did (for `done`). */
  actor?: string;
  /** A pre-formatted date for the actor line. */
  at?: string;
  substeps?: JourneySubstep[];
}

interface JourneyRailProps {
  stages: JourneyStage[];
  /** The key of the open stage; its panel is `children`. */
  activeKey: string;
  onSelect: (key: string) => void;
  /** Names the rail for assistive tech. */
  label: string;
  /**
   * The word for each state, spoken and shown. Required, so the component never
   * hard-codes one: pass translated text.
   */
  stateLabels: Record<StageState, string>;
  className?: string;
  /** The active stage's panel. */
  children: ReactNode;
  'data-testid'?: string;
}

const DESKTOP = '(min-width: 1024px)';

// Reads the `lg` breakpoint without a resize listener per instance. Where
// matchMedia is missing (jsdom) the desktop layout is used.
function useIsDesktop(): boolean {
  const subscribe = useCallback((notify: () => void) => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => undefined;
    const mq = window.matchMedia(DESKTOP);
    mq.addEventListener('change', notify);
    return () => mq.removeEventListener('change', notify);
  }, []);
  return useSyncExternalStore(
    subscribe,
    () => (typeof window.matchMedia === 'function' ? window.matchMedia(DESKTOP).matches : true),
    () => true,
  );
}

function Marker({ state, index }: { state: StageState; index: number }): JSX.Element {
  const base = 'flex h-7 w-7 flex-none items-center justify-center rounded-full text-[12.5px] font-bold';
  if (state === 'done') {
    return (
      <span className={cn(base, 'bg-primary text-primary-foreground')}>
        <Check size={14} aria-hidden="true" />
      </span>
    );
  }
  if (state === 'blocked') {
    return (
      <span className={cn(base, 'border border-[var(--ui-line-strong)] text-muted-foreground')}>
        <Lock size={13} aria-hidden="true" />
      </span>
    );
  }
  if (state === 'awaiting') {
    return (
      <span className={cn(base, 'bg-[var(--ui-warn-wash)] text-[var(--ui-warn)]')}>
        <Clock size={14} aria-hidden="true" />
      </span>
    );
  }
  if (state === 'optional') {
    return (
      <span className={cn(base, 'border border-dashed border-[var(--ui-line-strong)]')}>
        <span className="h-1.5 w-1.5 rounded-full bg-[var(--ui-faint)]" />
      </span>
    );
  }
  if (state === 'active') {
    return (
      <span
        className={cn(base, 'bg-[var(--ui-highlight)] text-[var(--ui-highlight-ink)] ring-2 ring-[var(--accent)]')}
      >
        {index + 1}
      </span>
    );
  }
  return (
    <span className={cn(base, 'border border-[var(--ui-line-strong)] text-muted-foreground')}>{index + 1}</span>
  );
}

function SubstepMark({ state }: { state: SubstepState }): JSX.Element {
  if (state === 'done') return <Check size={13} aria-hidden="true" className="flex-none text-[var(--ui-ok)]" />;
  if (state === 'blocked') return <Lock size={12} aria-hidden="true" className="flex-none text-muted-foreground" />;
  if (state === 'active') {
    return <span aria-hidden="true" className="h-2 w-2 flex-none rounded-full bg-[var(--accent)]" />;
  }
  return (
    <span aria-hidden="true" className="h-2 w-2 flex-none rounded-full border border-[var(--ui-line-strong)]" />
  );
}

/**
 * The ordered stages of a setup flow with the active stage's panel beside (lg+)
 * or inside (below lg) it. Use it where the work is "first this, then this" and
 * each stage's state comes from real data; do not use it for a plain tab set.
 *
 * Each stage is a button in an <ol>, in tab order = stage order. The active one
 * carries `aria-current="step"` and `aria-expanded="true"`; its state is shown
 * as an icon AND the word from `stateLabels`, never colour alone. A blocked
 * stage prints its `reason`. Selecting a stage calls `onSelect`; the parent
 * owns `activeKey`.
 */
export function JourneyRail({
  stages, activeKey, onSelect, label, stateLabels, className, children, 'data-testid': testId,
}: JourneyRailProps): JSX.Element {
  const base = useId();
  const desktop = useIsDesktop();
  const panelId = (key: string): string => `${base}-panel-${key}`;
  const buttonId = (key: string): string => `${base}-stage-${key}`;

  const items = stages.map((stage, i) => {
    const open = stage.key === activeKey;
    const actorLine = [stage.actor, stage.at].filter(Boolean).join(' · ');
    return (
      <li key={stage.key} className={cn(!desktop && 'flex flex-col gap-2')}>
        <button
          type="button"
          id={buttonId(stage.key)}
          aria-expanded={open}
          // Only the active stage's panel is in the DOM; pointing at the others
          // would reference ids that do not exist.
          aria-controls={open ? panelId(stage.key) : undefined}
          aria-current={open ? 'step' : undefined}
          onClick={() => onSelect(stage.key)}
          className={cn(
            'flex min-h-[56px] w-full items-start gap-3 rounded-[12px] px-3 py-3 text-left transition-colors duration-[120ms] motion-reduce:transition-none',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--accent)]',
            // Below lg each stage is its own card and the open stage's panel
            // follows it as a sibling, so a panel's own card never sits inside
            // another card.
            !desktop && 'rounded-card border border-border bg-card',
            open ? 'bg-[var(--ui-highlight-wash)]' : 'hover:bg-[var(--ui-inset-soft)]',
          )}
        >
          <Marker state={stage.state} index={i} />
          <span className="min-w-0 flex-1">
            <span className="block break-words text-[14px] font-semibold text-foreground">{stage.title}</span>
            <span className="mt-0.5 block text-[12.5px] text-muted-foreground">
              <span className="font-semibold text-foreground">{stateLabels[stage.state]}</span>
              {stage.fact ? <> · {stage.fact}</> : null}
            </span>
            {actorLine ? <span className="mt-0.5 block text-[12.5px] text-muted-foreground">{actorLine}</span> : null}
            {stage.state === 'blocked' && stage.reason ? (
              <span className="mt-1 block text-[12.5px] text-muted-foreground">{stage.reason}</span>
            ) : null}
          </span>
        </button>
        {stage.substeps && stage.substeps.length > 0 ? (
          <ul className={cn('mb-2 ml-[22px] mt-1 flex flex-col gap-1 border-l border-border pl-5', !desktop && 'mb-0 mt-0')}>
            {stage.substeps.map((sub) => (
              <li key={sub.key} className="flex min-h-[24px] items-center gap-2 text-[12.5px] text-muted-foreground">
                <SubstepMark state={sub.state} />
                <span className={cn(sub.state === 'done' && 'text-foreground')}>{sub.label}</span>
                <span className="sr-only">({stateLabels[sub.state]})</span>
              </li>
            ))}
          </ul>
        ) : null}
        {!desktop && open ? (
          <div id={panelId(stage.key)} role="region" aria-labelledby={buttonId(stage.key)} className="min-w-0 pb-1">
            {children}
          </div>
        ) : null}
      </li>
    );
  });

  return (
    <div
      data-testid={testId}
      className={cn('lg:grid lg:grid-cols-[300px_minmax(0,1fr)] lg:items-start lg:gap-6', className)}
    >
      <nav aria-label={label}>
        <ol className={cn('flex flex-col', desktop ? 'gap-1' : 'gap-3')}>{items}</ol>
      </nav>
      {desktop ? (
        <div
          id={panelId(activeKey)}
          role="region"
          aria-labelledby={buttonId(activeKey)}
          className="min-w-0"
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}
