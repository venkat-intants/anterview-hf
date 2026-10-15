// Page-level building blocks. Every in-app screen is assembled from these so
// that headers, section cards and empty states look and behave the same in the
// candidate app and in every console.
//
//   Page         — the frame: width, left anchor and the 24px rhythm between regions
//   PageBody     — main column plus an optional 320px aside at lg+
//   PageHeader   — title, one-line description, the page's primary actions
//   SectionCard  — a titled card with an optional action in its header
//   EmptyState   — what a section says before it has content, with the next step
//   ProgressMeter— a labelled completion bar
//   Breadcrumbs  — where a detail page sits, and the way back up
//   Stepper      — the stages of a multi-step flow, and which one you are on
//   Toolbar      — one row for a list's search, filters and view controls
//   DataList     — label/value pairs for a record's facts

import { Fragment, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { Check, ChevronRight, type LucideIcon } from './icons';

/* ────────────────────────────────  Page  ──────────────────────────────── */

export type PageWidth = 'form' | 'default' | 'workspace' | 'reading';

// Left-anchored on purpose (no `mx-auto`): the H1 must sit at the same x on
// every page inside the shell. Only `reading` centres, for shell-less pages.
const PAGE_WIDTH: Record<PageWidth, string> = {
  form: 'max-w-[880px]',
  default: 'max-w-[1200px]',
  workspace: 'max-w-none',
  reading: 'mx-auto max-w-[720px]',
};

interface PageProps {
  /** `form` 880px, `default` 1200px, `workspace` full width, `reading` 720px centred. */
  width?: PageWidth;
  className?: string;
  children: ReactNode;
  'data-testid'?: string;
}

/**
 * The frame every in-shell page renders. It owns the maximum width and the 24px
 * rhythm between its regions (header, next step, body) and adds NO padding:
 * AppShell's <main> owns the only gutters, so a page never sets `px-*`, `py-*`,
 * `mx-auto` or `max-w-*` on its wrapper. Use `reading` only for shell-less
 * pages (public, auth), which stay centred.
 */
export function Page({ width = 'default', className, children, 'data-testid': testId }: PageProps): JSX.Element {
  return (
    // The gap owns the space below a PageHeader, so its own `mb-6` is cancelled
    // here; a PageHeader outside a Page keeps the margin it always had.
    <div
      data-testid={testId}
      className={cn('flex w-full min-w-0 flex-col gap-6 [&>header]:mb-0 [&>[data-toolbar]]:mb-0', PAGE_WIDTH[width], className)}
    >
      {children}
    </div>
  );
}

/* ───────────────────────────────  PageBody  ──────────────────────────────── */

interface PageBodyProps {
  /** Context only — facts, secondary links, help. Never the primary action. */
  aside?: ReactNode;
  /** Names the aside landmark for assistive tech. */
  asideLabel?: string;
  className?: string;
  children: ReactNode;
}

/**
 * Main column plus an optional 320px aside at `lg+`. Below `lg` the aside drops
 * under the main column; with no aside it is a single column. Both columns are
 * themselves flex stacks on the 24px rhythm, so several children need no
 * spacing wrapper.
 */
export function PageBody({ aside, asideLabel, className, children }: PageBodyProps): JSX.Element {
  return (
    <div
      className={cn(
        'grid grid-cols-[minmax(0,1fr)] items-start gap-6',
        aside ? 'lg:grid-cols-[minmax(0,1fr)_320px]' : undefined,
        className,
      )}
    >
      <div className="flex min-w-0 flex-col gap-6">{children}</div>
      {aside ? (
        <aside aria-label={asideLabel} className="flex min-w-0 flex-col gap-6">
          {aside}
        </aside>
      ) : null}
    </div>
  );
}

/* ─────────────────────────────  PageHeader  ───────────────────────────── */

interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  /** A small label above the title — the console or section the page sits in. */
  eyebrow?: ReactNode;
  /** Right-aligned controls. Put the primary action last. */
  actions?: ReactNode;
  className?: string;
  /** Applied to the <h1>, so tests and e2e specs can find the page title. */
  titleTestId?: string;
  /** Rendered above the title — pass a <Breadcrumbs>. */
  breadcrumbs?: ReactNode;
  /** Status facts under the description — pills, an approval tag, a date. */
  meta?: ReactNode;
  /** Pills shown beside the title (status, approval) rather than on a row of their own. */
  badges?: ReactNode;
  /**
   * One-row header: at `lg+` the actions sit to the right of the title block
   * and stay top-aligned with the title, and the title block may use the full
   * width. Below `lg` the actions wrap under it, as ever.
   */
  compact?: boolean;
}

/**
 * The page title block. Inside a `Page` its bottom margin is cancelled and the
 * frame's 24px gap takes over; on its own it keeps `mb-6`.
 */
export function PageHeader({
  title, description, eyebrow, actions, className, titleTestId, breadcrumbs, meta, badges, compact,
}: PageHeaderProps): JSX.Element {
  const h1 = (
    <h1
      data-testid={titleTestId}
      className="text-[28px] font-bold leading-[1.15] tracking-[-0.6px] text-foreground sm:text-[32px]"
    >
      {title}
    </h1>
  );
  return (
    <header
      className={cn(
        'mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-4',
        compact && 'lg:flex-nowrap',
        className,
      )}
    >
      <div className={cn('min-w-0 max-w-[720px]', compact && 'lg:max-w-none lg:flex-1')}>
        {breadcrumbs ? <div className="mb-2">{breadcrumbs}</div> : null}
        {eyebrow ? (
          <p className="mb-1.5 text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--ui-info)]">
            {eyebrow}
          </p>
        ) : null}
        {badges ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            {h1}
            <div className="flex flex-wrap items-center gap-2">{badges}</div>
          </div>
        ) : (
          h1
        )}
        {description ? (
          <p className="mt-2 text-[15px] leading-relaxed text-muted-foreground">{description}</p>
        ) : null}
        {meta ? (
          <div className={cn('flex flex-wrap items-center gap-2', compact ? 'mt-2' : 'mt-3')}>{meta}</div>
        ) : null}
      </div>
      {actions ? (
        <div className={cn('flex flex-wrap items-center gap-2', compact && 'lg:flex-none lg:justify-end lg:self-end')}>
          {actions}
        </div>
      ) : null}
    </header>
  );
}

/* ─────────────────────────────  SectionCard  ───────────────────────────── */

interface SectionCardProps {
  title: ReactNode;
  description?: ReactNode;
  icon?: LucideIcon;
  /** Header-right control — typically an Edit or Add button. */
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  /** Applied to the <section>, so a card can be found in tests. */
  'data-testid'?: string;
  /** Labels the region for assistive tech when `title` is not plain text. */
  'aria-label'?: string;
}

export function SectionCard({
  title, description, icon: Icon, action, children, className,
  'data-testid': testId, 'aria-label': ariaLabel,
}: SectionCardProps): JSX.Element {
  return (
    <section
      data-testid={testId}
      aria-label={ariaLabel}
      className={cn('rounded-card border border-border bg-card p-6 shadow-card', className)}
    >
      <div className="mb-4 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-[17px] font-bold tracking-[-0.2px] text-foreground">
            {Icon ? <Icon size={18} className="flex-none text-[var(--ui-info)]" aria-hidden="true" /> : null}
            {/* Wraps instead of truncating: a Hindi or Telugu title is often longer
                than its English one and must not clip on a narrow screen. */}
            <span className="min-w-0 break-words">{title}</span>
          </h2>
          {description ? (
            <p className="mt-1 text-[13.5px] leading-relaxed text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {action ? <div className="flex flex-none items-center gap-2">{action}</div> : null}
      </div>
      {children}
    </section>
  );
}

/* ─────────────────────────────  EmptyState  ───────────────────────────── */

interface EmptyStateProps {
  title: ReactNode;
  description?: ReactNode;
  icon?: LucideIcon;
  /** The next step — a button or link. An empty state without one is a dead end. */
  action?: ReactNode;
  /**
   * `inline` sits inside a card; `page` stands alone with more room; `row` is a
   * single 44px line (muted text plus the action, no icon) for a secondary card.
   */
  size?: 'row' | 'inline' | 'page';
  /**
   * The dashed frame. On by default so existing call sites render as before;
   * pass `false` inside a `Card`, which is already the frame. A `row` is
   * frameless unless you pass `true`.
   */
  bordered?: boolean;
  className?: string;
}

const EMPTY_BASE = 'flex flex-col items-center justify-center';
const EMPTY_FRAME = 'rounded-[16px] border border-dashed border-[var(--ui-line-strong)]';

/** What a section says before it has content. Always offer the next step in `action`. */
export function EmptyState({
  title, description, icon: Icon, action, size = 'inline', bordered, className,
}: EmptyStateProps): JSX.Element {
  // Inline and page keep their dashed frame unless told otherwise; a row is
  // frameless unless asked, since it is a line inside a card, not a block.
  const framed = bordered ?? size !== 'row';
  if (size === 'row') {
    return (
      <div
        className={cn(
          'flex min-h-[44px] flex-wrap items-center justify-center gap-x-3 gap-y-1 text-center text-[13.5px] text-muted-foreground',
          framed && EMPTY_FRAME,
          className,
        )}
      >
        <p>
          {title}
          {description ? <span> {description}</span> : null}
        </p>
        {action ? <div>{action}</div> : null}
      </div>
    );
  }
  return (
    <div
      className={cn(
        EMPTY_BASE,
        framed && EMPTY_FRAME,
        'text-center',
        size === 'page' ? 'px-6 py-16' : 'px-5 py-9',
        className,
      )}
    >
      {Icon ? (
        <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-[var(--ui-highlight-wash)] text-foreground">
          <Icon size={20} aria-hidden="true" />
        </span>
      ) : null}
      <p className="text-[15px] font-semibold text-foreground">{title}</p>
      {description ? (
        <p className="mt-1 max-w-[420px] text-[13.5px] leading-relaxed text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

/* ────────────────────────────  ProgressMeter  ──────────────────────────── */

interface ProgressMeterProps {
  /** 0–100 */
  value: number;
  /** Names what is being measured, for assistive tech. */
  label: string;
  className?: string;
}

export function ProgressMeter({ value, label, className }: ProgressMeterProps): JSX.Element {
  const clamped = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
      className={cn('h-2 w-full overflow-hidden rounded-full bg-[var(--ui-inset-strong)]', className)}
    >
      <div
        className="h-full rounded-full bg-[var(--accent)] transition-[width] duration-500 ease-out"
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

/* ─────────────────────────────  Breadcrumbs  ───────────────────────────── */

export interface Crumb {
  label: ReactNode;
  /** Omit on the last crumb — it is the page you are on, not a link. */
  to?: string;
}

interface BreadcrumbsProps {
  items: Crumb[];
  className?: string;
}

/** Where a detail page sits. Every crumb but the last is a link back up. */
export function Breadcrumbs({ items, className }: BreadcrumbsProps): JSX.Element {
  const { t } = useTranslation();
  return (
    <nav aria-label={t('pageBlocks.breadcrumb')} className={className}>
      <ol className="flex flex-wrap items-center gap-1 text-[13px] text-muted-foreground">
        {items.map((item, i) => {
          const last = i === items.length - 1;
          return (
            <li key={i} className="flex min-w-0 items-center gap-1">
              {item.to && !last ? (
                <Link
                  to={item.to}
                  className="truncate rounded-[6px] px-1 py-0.5 font-medium hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                >
                  {item.label}
                </Link>
              ) : (
                <span
                  aria-current={last ? 'page' : undefined}
                  className={cn('truncate px-1 py-0.5', last && 'font-semibold text-foreground')}
                >
                  {item.label}
                </span>
              )}
              {last ? null : <ChevronRight size={14} aria-hidden="true" className="flex-none opacity-60" />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/* ───────────────────────────────  Stepper  ─────────────────────────────── */

export interface Step {
  key: string;
  label: ReactNode;
  /** A short line under the label, shown from `sm` up. */
  hint?: ReactNode;
}

interface StepperProps {
  steps: Step[];
  /** The key of the step the person is on. Steps before it read as done. */
  current: string;
  /** Names the flow for assistive tech, e.g. "Application progress". */
  label: string;
  /**
   * Replaces the whole below-`sm` line ("Step n of N · current label") with text
   * the caller composes, e.g. "Step 2 of 3 · Round 2 of 4". Pass it already
   * translated. From `sm` up the full stepper is unchanged.
   */
  compactLabel?: string;
  className?: string;
}

/**
 * The stages of a multi-step flow. Presentational: it shows where you are and
 * does not navigate. State is carried by a number or a tick AND by text for
 * assistive tech — never by colour alone. Below `sm` it collapses to
 * "Step n of N" plus the current label, so it never overflows a phone.
 */
export function Stepper({ steps, current, label, compactLabel, className }: StepperProps): JSX.Element {
  const found = steps.findIndex((s) => s.key === current);
  const index = found === -1 ? 0 : found;
  const active = steps[index];
  const { t } = useTranslation();
  return (
    <nav aria-label={label} className={className}>
      <p className="text-[13px] font-medium text-muted-foreground sm:hidden">
        {compactLabel !== undefined ? (
          <span className="font-semibold text-foreground">{compactLabel}</span>
        ) : (
          <>
            {t('pageBlocks.stepOf', { n: index + 1, total: steps.length })}
            {active ? <span className="font-semibold text-foreground"> · {active.label}</span> : null}
          </>
        )}
      </p>
      <ol className="hidden items-start gap-2 sm:flex">
        {steps.map((step, i) => {
          const state = i < index ? 'done' : i === index ? 'current' : 'todo';
          return (
            <Fragment key={step.key}>
              <li aria-current={state === 'current' ? 'step' : undefined} className="flex min-w-0 items-start gap-2.5">
                <span
                  className={cn(
                    'flex h-7 w-7 flex-none items-center justify-center rounded-full text-[12.5px] font-bold',
                    state === 'done' && 'bg-primary text-primary-foreground',
                    state === 'current' && 'bg-[var(--ui-highlight)] text-[var(--ui-highlight-ink)] ring-2 ring-[var(--accent)]',
                    state === 'todo' && 'border border-[var(--ui-line-strong)] text-muted-foreground',
                  )}
                >
                  {state === 'done' ? <Check size={14} aria-hidden="true" /> : i + 1}
                </span>
                <span className="min-w-0 pt-0.5">
                  <span
                    className={cn(
                      'block truncate text-[13.5px]',
                      state === 'todo' ? 'font-medium text-muted-foreground' : 'font-semibold text-foreground',
                    )}
                  >
                    {step.label}
                    <span className="sr-only">
                      {state === 'done'
                        ? ` (${t('pageBlocks.stepDone')})`
                        : state === 'current'
                          ? ` (${t('pageBlocks.stepCurrent')})`
                          : ''}
                    </span>
                  </span>
                  {step.hint ? (
                    <span className="block truncate text-[12px] text-muted-foreground">{step.hint}</span>
                  ) : null}
                </span>
              </li>
              {i < steps.length - 1 ? (
                <li aria-hidden="true" className="mt-3.5 h-px min-w-4 flex-1 bg-[var(--ui-line-strong)]" />
              ) : null}
            </Fragment>
          );
        })}
      </ol>
    </nav>
  );
}

/* ───────────────────────────────  Toolbar  ─────────────────────────────── */

interface ToolbarProps {
  /** Left side: search first, then filters. */
  children: ReactNode;
  /** Right side: result count, sort, view toggles, export. */
  trailing?: ReactNode;
  className?: string;
}

/** One row for a list's controls, so they sit in the same place on every list. */
export function Toolbar({ children, trailing, className }: ToolbarProps): JSX.Element {
  return (
    <div
      data-toolbar=""
      className={cn(
        'mb-4 flex flex-wrap items-center justify-between gap-3 rounded-[12px] border border-border bg-card px-3 py-2.5 shadow-card',
        className,
      )}
    >
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{children}</div>
      {trailing ? <div className="flex flex-none flex-wrap items-center gap-2">{trailing}</div> : null}
    </div>
  );
}

/* ───────────────────────────────  DataList  ────────────────────────────── */

export interface DataItem {
  label: ReactNode;
  /** Falls back to an em dash when empty, so a missing value reads as missing. */
  value?: ReactNode;
}

interface DataListProps {
  items: DataItem[];
  /** Columns from `sm` up. Always one column on a phone. */
  columns?: 1 | 2 | 3;
  className?: string;
}

const DATA_COLS = { 1: '', 2: 'sm:grid-cols-2', 3: 'sm:grid-cols-2 lg:grid-cols-3' } as const;

/** A record's facts as label/value pairs — a real <dl>, not a grid of divs. */
export function DataList({ items, columns = 2, className }: DataListProps): JSX.Element {
  return (
    <dl className={cn('grid gap-x-8 gap-y-4', DATA_COLS[columns], className)}>
      {items.map((item, i) => (
        <div key={i} className="min-w-0">
          <dt className="text-[12.5px] font-medium text-muted-foreground">{item.label}</dt>
          <dd className="mt-0.5 break-words text-[14.5px] text-foreground">
            {item.value === undefined || item.value === null || item.value === '' ? '—' : item.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
