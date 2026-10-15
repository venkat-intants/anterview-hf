// Card — the one container with a fixed anatomy.
//
//   eyebrow
//   [icon] title                         actions     header
//   description
//   ─────────────────────────────────────────────    hairline only when `divided`
//   body (children)
//   ─────────────────────────────────────────────
//   footer
//
// Plain by default: a hairline and no shadow. A restructured page picks ONE
// `raised` or `feature` region and everything else stays plain. GlassCard and
// SectionCard in primitives.tsx / page.tsx are separate, unchanged wrappers.

import { useId, type ElementType, type HTMLAttributes, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { ChevronRight, type LucideIcon } from './icons';

export type CardEmphasis = 'plain' | 'raised' | 'feature';
export type CardDensity = 'compact' | 'default' | 'roomy';

const PAD: Record<CardDensity, string> = {
  compact: 'p-4',
  default: 'p-5 sm:p-6',
  roomy: 'p-6 sm:p-8',
};

const TITLE: Record<CardDensity, string> = {
  compact: 'text-[15px] font-semibold',
  default: 'text-[17px] font-bold tracking-[-0.2px]',
  roomy: 'text-[20px] font-bold',
};

// Header-to-body spacing. `divided` adds the hairline at the same distance.
const HEADER_GAP: Record<CardDensity, string> = {
  compact: 'mb-3',
  default: 'mb-4',
  roomy: 'mb-5',
};
const HEADER_RULE_PAD: Record<CardDensity, string> = {
  compact: 'pb-3',
  default: 'pb-4',
  roomy: 'pb-5',
};

const EMPHASIS: Record<CardEmphasis, string> = {
  plain: 'border-border bg-card',
  raised: 'border-border bg-card shadow-card',
  feature: 'border-[rgba(var(--accent-rgb),0.2)] bg-[image:var(--ui-grad-feature)]',
};

// Hover and press only ever appear on a card that goes somewhere.
const INTERACTIVE =
  'group transition-[border-color,box-shadow,transform] duration-200 ease-out hover:border-[var(--ui-line-strong)] hover:shadow-lift active:scale-[0.99] motion-reduce:transition-none motion-reduce:transform-none';

const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background';

// A stretched title link paints its ring on the overlay so the whole card, not
// the title text, shows focus.
const STRETCHED_RING =
  'after:absolute after:inset-0 after:rounded-card focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-[var(--accent)] focus-visible:after:ring-offset-2 focus-visible:after:ring-offset-background';

export interface CardProps
  extends Omit<HTMLAttributes<HTMLElement>, 'title' | 'children' | 'className'> {
  /** `plain` hairline (default) · `raised` shadow · `feature` accent wash, at most one per page. */
  emphasis?: CardEmphasis;
  density?: CardDensity;
  /** Small caps label above the title. */
  eyebrow?: ReactNode;
  title?: ReactNode;
  description?: ReactNode;
  /** Only for cards that are navigational objects; not a decoration for every title. */
  icon?: LucideIcon;
  /** Header-right controls. */
  actions?: ReactNode;
  /** Meta on the left, link or secondary action on the right; always under a hairline. */
  footer?: ReactNode;
  /** Removes body padding so a table or list runs edge to edge. */
  flush?: boolean;
  /** Hairline between header and body. */
  divided?: boolean;
  /** Makes the card a link. See the JSDoc on `Card` for the two shapes. */
  to?: string;
  /** Element for a non-link card. Defaults to `div`. */
  as?: 'div' | 'section' | 'article' | 'aside' | 'li';
  /** Heading level of the title. Defaults to `h2`. */
  titleAs?: 'h2' | 'h3';
  className?: string;
  children?: ReactNode;
  'data-testid'?: string;
}

/**
 * The container for a discrete object (a job, an offer, a tool) or a page's
 * primary region. Do not nest a Card in a Card: inside one, use `Well`,
 * `Section` or `RowList`.
 *
 * With `to` and no `actions` the whole card is one `<a>` (raised, with a
 * trailing chevron), so its body must hold no controls of its own. With `to`
 * and `actions` the title is the link, stretched over the card; actions and any
 * control in the body must be `relative z-10` to stay clickable. With no `to`
 * there is no hover state at all. A section card names itself from its title.
 */
export function Card({
  emphasis = 'plain',
  density = 'default',
  eyebrow,
  title,
  description,
  icon: Icon,
  actions,
  footer,
  flush = false,
  divided = false,
  to,
  as = 'div',
  titleAs = 'h2',
  className,
  children,
  ...rest
}: CardProps): JSX.Element {
  const titleId = useId();
  const wholeLink = Boolean(to) && !actions;
  const stretched = Boolean(to) && Boolean(actions);
  const interactive = wholeLink || stretched;
  // A clickable card is always at least raised; feature stays feature.
  const level: CardEmphasis = interactive && emphasis === 'plain' ? 'raised' : emphasis;

  const rootClass = cn(
    'relative rounded-card border text-card-foreground',
    EMPHASIS[level],
    flush ? 'overflow-hidden' : PAD[density],
    interactive && INTERACTIVE,
    wholeLink && cn('block', FOCUS_RING),
    className,
  );

  const hasHeader = Boolean(eyebrow || title || description || Icon || actions);
  const Heading = titleAs;

  const titleNode =
    title === undefined || title === null ? null : (
      <Heading id={titleId} className={cn('flex items-center gap-2 text-foreground', TITLE[density])}>
        {Icon ? <Icon size={20} className="flex-none text-[var(--ui-info)]" aria-hidden="true" /> : null}
        {/* Wraps instead of truncating: a Hindi or Telugu title is often longer
            than its English one and must not clip on a narrow screen. */}
        <span className="min-w-0 break-words">
          {stretched && to ? (
            <Link to={to} className={cn('rounded-[6px]', STRETCHED_RING)}>
              {title}
            </Link>
          ) : (
            title
          )}
        </span>
      </Heading>
    );

  const header = hasHeader ? (
    <div
      className={cn(
        'flex items-start justify-between gap-4',
        flush ? PAD[density] : HEADER_GAP[density],
        divided && 'border-b border-border',
        divided && !flush && HEADER_RULE_PAD[density],
      )}
    >
      <div className="min-w-0">
        {eyebrow ? (
          <p className="mb-1 text-[12px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            {eyebrow}
          </p>
        ) : null}
        {titleNode}
        {description ? (
          <p className="mt-1 text-[13.5px] leading-relaxed text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="relative z-10 flex flex-none items-center gap-2">{actions}</div> : null}
      {wholeLink ? (
        <ChevronRight
          size={16}
          aria-hidden="true"
          className="mt-0.5 flex-none text-muted-foreground transition-transform duration-200 group-hover:translate-x-0.5 motion-reduce:transform-none motion-reduce:transition-none"
        />
      ) : null}
    </div>
  ) : null;

  const footerNode = footer ? (
    <div
      className={cn(
        'flex items-center justify-between gap-3 border-t border-border',
        flush ? PAD[density] : 'mt-4 pt-4',
      )}
    >
      {footer}
    </div>
  ) : null;

  const content = (
    <>
      {header}
      {children}
      {footerNode}
    </>
  );

  if (wholeLink && to) {
    return (
      <Link to={to} className={rootClass} {...rest}>
        {content}
      </Link>
    );
  }

  const Tag: ElementType = as;
  // A section needs a name to be a landmark; the title is that name unless the
  // caller supplied its own.
  const labelled =
    as === 'section' && title !== undefined && title !== null && !rest['aria-label'] && !rest['aria-labelledby']
      ? titleId
      : undefined;
  return (
    <Tag className={rootClass} aria-labelledby={labelled} {...rest}>
      {content}
    </Tag>
  );
}
