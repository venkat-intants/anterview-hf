// Collapsible — one disclosure for every activity / history / timeline section.
//
// Activity is a RECORD of what happened; it is secondary to the thing a person
// came to do. So it sits closed under a one-line summary of its newest entry,
// and a person who wants it opens it once and is remembered.
//
//   Collapsible      a real disclosure: <button aria-expanded aria-controls> in a
//                    heading, and a region. Closed content is not rendered, so a
//                    long list costs nothing until it is opened.
//   ProgressiveList  the first few items, then "Show all n". Used inside a
//                    Collapsible so an opened history is still short.
//
// MOTION. The height animates with a grid-rows transition (no measuring, no
// layout thrash). Under `prefers-reduced-motion: reduce`, and wherever
// `matchMedia` does not exist, open and close are instant.
//
// MEMORY. `persistKey` stores the choice in localStorage. Every access is in a
// try/catch: private mode, a blocked cookie policy or a full quota must leave
// the section working, just forgetful. Include the signed-in user's id in the
// key (see hooks/useScopedPersistKey) so two people on one browser do not share
// a preference.

import {
  Fragment,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { cn } from '@/lib/utils';
import { ChevronRight } from './icons';

const STORAGE_PREFIX = 'anthire:collapsible:';
/** Matches the `duration-200` on the height transition; the content is unmounted after it. */
const ANIMATION_MS = 220;

function readStored(key: string | undefined): boolean | null {
  if (!key) return null;
  try {
    const v = window.localStorage.getItem(STORAGE_PREFIX + key);
    return v === '1' ? true : v === '0' ? false : null;
  } catch {
    return null;
  }
}

function writeStored(key: string | undefined, open: boolean): void {
  if (!key) return;
  try {
    window.localStorage.setItem(STORAGE_PREFIX + key, open ? '1' : '0');
  } catch {
    /* storage unavailable: the choice lasts for this visit only */
  }
}

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return true;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export interface CollapsibleProps {
  title: ReactNode;
  /** How many entries the section holds. Shown beside the title. */
  count?: number;
  /** Singular noun for the composed summary: "event" gives "12 events". */
  noun?: string;
  /** Plural when it is not just "+s": "entry" -> "entries". */
  nounPlural?: string;
  /** The newest entry in a few words: "Vikram Singh moved to AI interview, 2h ago". */
  latest?: ReactNode;
  /** Replaces the composed "n events · latest: …" line when you need other words. */
  summary?: ReactNode;
  defaultOpen?: boolean;
  /** Remembers the open/closed choice in localStorage under this key. */
  persistKey?: string;
  /** Heading level for the title; the button sits inside it. */
  headingLevel?: 'h2' | 'h3' | 'h4';
  /** Controls to the right of the header (a link, a filter). Not inside the toggle. */
  actions?: ReactNode;
  /** `card` is a bordered surface of its own; `bare` sits inside a card or page. */
  variant?: 'card' | 'bare';
  className?: string;
  children: ReactNode;
  'data-testid'?: string;
}

/** "12 events · latest: …" — only the parts that are true. */
function composeSummary(
  count: number | undefined,
  noun: string | undefined,
  nounPlural: string | undefined,
  latest: ReactNode,
): ReactNode {
  const one = noun ?? 'item';
  const counted =
    count === undefined ? null : `${count} ${count === 1 ? one : (nounPlural ?? `${one}s`)}`;
  if (counted && latest) return <>{counted} · latest: {latest}</>;
  return counted ?? (latest ? <>latest: {latest}</> : null);
}

export function Collapsible({
  title,
  count,
  noun,
  nounPlural,
  latest,
  summary,
  defaultOpen = false,
  persistKey,
  headingLevel: Heading = 'h3',
  actions,
  variant = 'bare',
  className,
  children,
  'data-testid': testId,
}: CollapsibleProps): JSX.Element {
  const base = useId();
  const buttonId = `${base}-toggle`;
  const regionId = `${base}-region`;

  const [open, setOpen] = useState<boolean>(() => readStored(persistKey) ?? defaultOpen);
  // `rendered` keeps the content mounted while it animates shut; `expanded` is
  // the visual state the transition follows.
  const [rendered, setRendered] = useState(open);
  const [expanded, setExpanded] = useState(open);
  const first = useRef(true);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return undefined;
    }
    if (prefersReducedMotion()) {
      setRendered(open);
      setExpanded(open);
      return undefined;
    }
    if (open) {
      setRendered(true);
      const raf = requestAnimationFrame(() => requestAnimationFrame(() => setExpanded(true)));
      return () => cancelAnimationFrame(raf);
    }
    setExpanded(false);
    const t = window.setTimeout(() => setRendered(false), ANIMATION_MS);
    return () => window.clearTimeout(t);
  }, [open]);

  const toggle = useCallback(() => {
    setOpen((was) => {
      writeStored(persistKey, !was);
      return !was;
    });
  }, [persistKey]);

  const line = summary ?? composeSummary(count, noun, nounPlural, latest);

  return (
    <section
      data-testid={testId}
      className={cn(
        variant === 'card'
          ? 'rounded-card border border-border bg-card shadow-card'
          : 'border-t border-border first:border-t-0',
        className,
      )}
    >
      <div className={cn('flex items-center gap-2', variant === 'card' ? 'px-4' : 'px-0')}>
        <Heading className="min-w-0 flex-1">
          <button
            type="button"
            id={buttonId}
            aria-expanded={open}
            aria-controls={regionId}
            onClick={toggle}
            className="group flex min-h-10 w-full items-center gap-2.5 rounded-[10px] py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <ChevronRight
              size={16}
              aria-hidden="true"
              className={cn(
                'flex-none text-muted-foreground transition-transform duration-200 motion-reduce:transition-none',
                open && 'rotate-90',
              )}
            />
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2">
                <span className="text-[15px] font-semibold text-foreground">{title}</span>
                {count !== undefined ? (
                  <span className="rounded-full bg-[var(--ui-inset-strong)] px-2 py-0.5 text-[12px] font-semibold tabular-nums text-[var(--ui-soft)]">
                    {count}
                  </span>
                ) : null}
              </span>
              {!open && line ? (
                <span
                  data-testid="collapsible-summary"
                  className="mt-0.5 block truncate text-[12.5px] text-muted-foreground"
                >
                  {line}
                </span>
              ) : null}
            </span>
          </button>
        </Heading>
        {actions ? <div className="flex flex-none items-center gap-2">{actions}</div> : null}
      </div>

      <div
        id={regionId}
        role="region"
        aria-labelledby={buttonId}
        hidden={!rendered}
        className={cn(
          'grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none',
          expanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
        )}
      >
        <div className="min-h-0 overflow-hidden">
          {rendered ? (
            <div className={cn('pb-3 pt-1', variant === 'card' ? 'px-4' : 'pl-[26px]')}>{children}</div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

/* ───────────────────────────  ProgressiveList  ─────────────────────────── */

export interface ProgressiveListProps<T> {
  items: readonly T[];
  /** Must return an <li>. */
  renderItem: (item: T, index: number) => ReactNode;
  getKey: (item: T, index: number) => string;
  /** How many to show before "Show all". Default 5. */
  initial?: number;
  /** Plural noun for the button: "events" gives "Show all 12 events". */
  noun?: string;
  as?: 'ul' | 'ol';
  className?: string;
  'aria-label'?: string;
}

/**
 * The first `initial` items, then one button to reveal the rest. A list that
 * fits is rendered whole with no button.
 */
export function ProgressiveList<T>({
  items,
  renderItem,
  getKey,
  initial = 5,
  noun = 'items',
  as: Tag = 'ul',
  className,
  'aria-label': ariaLabel,
}: ProgressiveListProps<T>): JSX.Element {
  const [all, setAll] = useState(false);
  const hidden = items.length - initial;
  const shown = all || hidden <= 0 ? items : items.slice(0, initial);
  return (
    <>
      <Tag aria-label={ariaLabel} className={className}>
        {shown.map((item, i) => (
          <Fragment key={getKey(item, i)}>{renderItem(item, i)}</Fragment>
        ))}
      </Tag>
      {hidden > 0 ? (
        <button
          type="button"
          aria-expanded={all}
          onClick={() => setAll((v) => !v)}
          className="mt-1 inline-flex min-h-10 items-center rounded-[10px] px-1 text-[13px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          {all ? 'Show fewer' : `Show all ${items.length} ${noun}`}
        </button>
      ) : null}
    </>
  );
}
