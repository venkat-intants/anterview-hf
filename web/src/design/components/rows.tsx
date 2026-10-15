// Section, Well and RowList — how content is grouped INSIDE a Card, or on a page
// that already has one, without drawing another box. A restructured page uses
// these in place of cards-in-cards.

import { useId, type HTMLAttributes, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { ChevronRight } from './icons';

/* ─────────────────────────────  Section  ───────────────────────────── */

interface SectionProps {
  title: ReactNode;
  description?: ReactNode;
  /** Header-right controls. */
  actions?: ReactNode;
  /** Heading level. Defaults to `h2`. */
  titleAs?: 'h2' | 'h3';
  className?: string;
  children: ReactNode;
  'data-testid'?: string;
}

/**
 * A titled group with a hairline above it and no box. Use it for a group of
 * content on a page that already has a card, for each field group in a form, and
 * for every block of an aside rail. The hairline is dropped when the section is
 * the first child, so the first group in a card or column does not start with a
 * stray rule.
 */
export function Section({
  title, description, actions, titleAs: Heading = 'h2', className, children, 'data-testid': testId,
}: SectionProps): JSX.Element {
  const id = useId();
  return (
    <section
      data-testid={testId}
      aria-labelledby={id}
      className={cn('border-t border-border pt-5 first:border-t-0 first:pt-0', className)}
    >
      <div className="mb-4 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <Heading id={id} className="text-[15px] font-semibold text-foreground">
            {title}
          </Heading>
          {description ? (
            <p className="mt-1 text-[13.5px] leading-relaxed text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="flex flex-none items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

/* ───────────────────────────────  Well  ─────────────────────────────── */

interface WellProps extends HTMLAttributes<HTMLDivElement> {
  'data-testid'?: string;
}

/**
 * An inset fill with no border, for a note, a callout or a code block inside a
 * card. This is what replaces a bordered box in a bordered box.
 */
export function Well({ className, ...rest }: WellProps): JSX.Element {
  return <div className={cn('rounded-[12px] bg-[var(--ui-inset-soft)] p-3.5', className)} {...rest} />;
}

/* ─────────────────────────────  RowList / Row  ───────────────────────────── */

interface RowListProps {
  className?: string;
  /** Names the list for assistive tech when its heading is not adjacent. */
  'aria-label'?: string;
  'data-testid'?: string;
  children: ReactNode;
}

/**
 * Four or more like records as divided rows: one hairline between rows, no box
 * per row. Put it flush in a Card, or on the page under a Section.
 */
export function RowList({ className, children, ...rest }: RowListProps): JSX.Element {
  return (
    <ul className={cn('divide-y divide-border', className)} {...rest}>
      {children}
    </ul>
  );
}

interface RowProps {
  /** An icon, avatar or status mark at the left. */
  leading?: ReactNode;
  title: ReactNode;
  /** One muted line under the title: who, when, how many. */
  meta?: ReactNode;
  /** A tag or a single control at the right. */
  trailing?: ReactNode;
  /**
   * Makes the whole row a link with a trailing chevron. `trailing` must then be
   * non-interactive (a tag, a number); a row with controls omits `to`.
   */
  to?: string;
  className?: string;
  'data-testid'?: string;
}

const ROW_BODY = 'flex min-h-[56px] items-center gap-3 py-3';

/** One line of a RowList: at least 56px tall, so every row is a comfortable target. */
export function Row({ leading, title, meta, trailing, to, className, 'data-testid': testId }: RowProps): JSX.Element {
  const body = (
    <>
      {leading ? <span className="flex flex-none items-center">{leading}</span> : null}
      <span className="min-w-0 flex-1">
        <span className="block break-words text-[14px] font-medium text-foreground">{title}</span>
        {meta ? <span className="mt-0.5 block text-[12.5px] text-muted-foreground">{meta}</span> : null}
      </span>
      {trailing ? <span className="flex flex-none items-center gap-2">{trailing}</span> : null}
      {to ? <ChevronRight size={16} aria-hidden="true" className="flex-none text-muted-foreground" /> : null}
    </>
  );
  return (
    <li data-testid={testId} className={className}>
      {to ? (
        // The link bleeds 8px past the list edge so its hover wash is not cut
        // flush against the text.
        <Link
          to={to}
          className={cn(
            ROW_BODY,
            '-mx-2 rounded-lg px-2 transition-colors duration-[120ms] hover:bg-[var(--ui-inset-soft)] motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--accent)]',
          )}
        >
          {body}
        </Link>
      ) : (
        <div className={ROW_BODY}>{body}</div>
      )}
    </li>
  );
}
