// Flow blocks: the single "do this next" strip and the small pieces that sit
// around it (a labelled turn, an action line, the step a public page is on, what
// happens after it). None of them hard-codes a word: every visible string is a
// prop, so candidate pages can pass translated text.

import { useId, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import {
  AlertCircle, AlertTriangle, ArrowRight, Check, CheckCircle2, Clock, Info, type LucideIcon,
} from './icons';
import { StatusTag } from './primitives';

/* ─────────────────────────────  NextStep  ───────────────────────────── */

export type NextStepTone = 'default' | 'info' | 'ok' | 'warn' | 'danger';

const TONE_SURFACE: Record<NextStepTone, string> = {
  default: 'border-border bg-card shadow-card',
  info: 'border-[rgba(var(--accent-rgb),0.2)] bg-[var(--ui-info-wash)]',
  ok: 'border-border bg-[var(--ui-ok-wash)]',
  warn: 'border-border bg-[var(--ui-warn-wash)]',
  danger: 'border-border bg-[var(--ui-danger-wash)]',
};

const TONE_ICON: Record<NextStepTone, { icon: LucideIcon | null; color: string }> = {
  default: { icon: null, color: 'text-[var(--ui-info)]' },
  info: { icon: Info, color: 'text-[var(--ui-info)]' },
  ok: { icon: CheckCircle2, color: 'text-[var(--ui-ok)]' },
  warn: { icon: AlertTriangle, color: 'text-[var(--ui-warn)]' },
  danger: { icon: AlertCircle, color: 'text-[var(--ui-danger)]' },
};

interface NextStepProps {
  /** Small caps label above the title, e.g. "Your next step". */
  eyebrow?: ReactNode;
  title: ReactNode;
  /** One muted line of context under the title. */
  context?: ReactNode;
  /** A pre-formatted date or deadline line. */
  dateLine?: ReactNode;
  /**
   * The one button or link for this step. Leave it out for a step that is
   * waiting on someone else; the strip then reads as a plain statement.
   */
  action?: ReactNode;
  /** How many other things are waiting. Shown only when > 0 and `moreLabel` is given. */
  moreCount?: number;
  /** Where "more" goes. Without it the count is plain text. */
  moreTo?: string;
  /** The translated "3 more waiting" text. The component never builds it. */
  moreLabel?: ReactNode;
  tone?: NextStepTone;
  /** Overrides the tone's default icon. */
  icon?: LucideIcon;
  /** Heading level of the title. Defaults to `h2`. */
  titleAs?: 'h2' | 'h3';
  className?: string;
  'data-testid'?: string;
}

/**
 * The one status or next step for a page; at most one per page, placed under
 * the PageHeader. It carries exactly one action, so the page has one primary.
 * The action area is full width and 48px tall below `sm`, and content-width at
 * `sm` and up; pass a Button or a Link as a single child element.
 */
export function NextStep({
  eyebrow, title, context, dateLine, action, moreCount, moreTo, moreLabel,
  tone = 'default', icon, titleAs: Heading = 'h2', className, 'data-testid': testId,
}: NextStepProps): JSX.Element {
  const id = useId();
  const preset = TONE_ICON[tone];
  const Icon = icon ?? preset.icon;
  const showMore = typeof moreCount === 'number' && moreCount > 0 && Boolean(moreLabel);
  return (
    <section
      aria-labelledby={id}
      data-testid={testId}
      className={cn(
        'flex flex-col gap-4 rounded-card border p-4 sm:flex-row sm:items-center sm:justify-between sm:gap-6 sm:p-5',
        TONE_SURFACE[tone],
        className,
      )}
    >
      <div className="flex min-w-0 items-start gap-3">
        {Icon ? <Icon size={20} aria-hidden="true" className={cn('mt-0.5 flex-none', preset.color)} /> : null}
        <div className="min-w-0">
          {eyebrow ? (
            <p className="mb-1 text-[12px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
              {eyebrow}
            </p>
          ) : null}
          <Heading id={id} className="break-words text-[17px] font-bold tracking-[-0.2px] text-foreground">
            {title}
          </Heading>
          {context ? (
            <p className="mt-1 text-[13.5px] leading-relaxed text-muted-foreground">{context}</p>
          ) : null}
          {dateLine ? <p className="mt-1 text-[13px] text-muted-foreground">{dateLine}</p> : null}
          {showMore ? (
            moreTo ? (
              <Link
                to={moreTo}
                className="mt-1 inline-flex min-h-[40px] items-center rounded-[6px] text-[13px] font-medium text-foreground underline underline-offset-2 hover:text-[var(--ui-info)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                {moreLabel}
              </Link>
            ) : (
              <p className="mt-1 text-[13px] text-muted-foreground">{moreLabel}</p>
            )
          ) : null}
        </div>
      </div>
      {action ? (
        <div className="w-full sm:w-auto sm:flex-none [&>*]:min-h-[48px] [&>*]:w-full sm:[&>*]:min-h-[40px] sm:[&>*]:w-auto">
          {action}
        </div>
      ) : null}
    </section>
  );
}

/* ─────────────────────────────  ActionRow  ───────────────────────────── */

interface ActionRowProps {
  icon: LucideIcon;
  title: ReactNode;
  dateLine?: ReactNode;
  /** The row's one button or link. */
  action: ReactNode;
  /** A failure from that action, announced to assistive tech. */
  error?: ReactNode;
  className?: string;
  'data-testid'?: string;
}

/**
 * One thing the person can do right now, with its deadline and its button:
 * "Pick an interview time — by 14 Oct — [Choose]". Borderless (an inset fill)
 * so it can sit inside a card; the action is full width and 48px on a phone.
 */
export function ActionRow({
  icon: Icon, title, dateLine, action, error, className, 'data-testid': testId,
}: ActionRowProps): JSX.Element {
  return (
    <div data-testid={testId} className={cn('rounded-[12px] bg-[var(--ui-inset-soft)] p-3.5', className)}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-10 w-10 flex-none items-center justify-center rounded-[8px] bg-[var(--ui-highlight-wash)] text-foreground">
            <Icon size={20} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="break-words text-[14px] font-semibold text-foreground">{title}</p>
            {dateLine ? <p className="mt-0.5 text-[12.5px] text-muted-foreground">{dateLine}</p> : null}
          </div>
        </div>
        <div className="w-full sm:w-auto sm:flex-none [&>*]:min-h-[48px] [&>*]:w-full sm:[&>*]:min-h-[40px] sm:[&>*]:w-auto">
          {action}
        </div>
      </div>
      {error ? (
        <p role="alert" className="mt-2 text-[12.5px] font-medium text-[var(--ui-danger)]">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/* ─────────────────────────────  TurnBadge  ───────────────────────────── */

export type Turn = 'you' | 'them' | 'closed';

const TURN: Record<Turn, { icon: LucideIcon; tone: 'electric' | 'neutral' }> = {
  you: { icon: ArrowRight, tone: 'electric' },
  them: { icon: Clock, tone: 'neutral' },
  closed: { icon: Check, tone: 'neutral' },
};

interface TurnBadgeProps {
  turn: Turn;
  /** The translated words: "Your move", "With the hiring team", "Closed". Required: the badge never speaks for itself. */
  label: string;
  className?: string;
}

/**
 * Whose move it is on an application. State is an icon AND the label, never
 * colour alone. Only `you` is emphasised; the rest stay quiet.
 */
export function TurnBadge({ turn, label, className }: TurnBadgeProps): JSX.Element {
  const { icon: Icon, tone } = TURN[turn];
  return (
    <StatusTag tone={tone} className={className}>
      <span data-turn={turn} className="inline-flex items-center gap-1.5">
        <Icon size={14} aria-hidden="true" />
        {label}
      </span>
    </StatusTag>
  );
}

/* ─────────────────────────────  StepContext  ───────────────────────────── */

interface StepContextProps {
  /** Where the person is: "Step 2 of 4". */
  step: ReactNode;
  role?: ReactNode;
  company?: ReactNode;
  className?: string;
}

/** The line at the head of a public page: step, then the role and company it is about. */
export function StepContext({ step, role, company, className }: StepContextProps): JSX.Element {
  const parts: { key: string; node: ReactNode }[] = [{ key: 'step', node: step }];
  if (role) parts.push({ key: 'role', node: role });
  if (company) parts.push({ key: 'company', node: company });
  return (
    <p className={cn('flex flex-wrap items-center gap-x-2 text-[13px] text-muted-foreground', className)}>
      {parts.map((p, i) => (
        <span key={p.key} className="inline-flex items-center gap-2">
          {i > 0 ? <span aria-hidden="true">·</span> : null}
          <span className={cn(i === 0 && 'font-semibold text-foreground')}>{p.node}</span>
        </span>
      ))}
    </p>
  );
}

/* ─────────────────────────────  WhatNext  ───────────────────────────── */

interface WhatNextProps {
  title: ReactNode;
  /** Heading level of the title. Defaults to `h2`. */
  titleAs?: 'h2' | 'h3';
  className?: string;
  children: ReactNode;
}

/** What happens after a finish screen. Honest text only: no promise the product does not keep. */
export function WhatNext({ title, titleAs: Heading = 'h2', className, children }: WhatNextProps): JSX.Element {
  const id = useId();
  return (
    <section aria-labelledby={id} className={cn('rounded-[12px] bg-[var(--ui-inset-soft)] p-4', className)}>
      <Heading id={id} className="text-[15px] font-semibold text-foreground">
        {title}
      </Heading>
      <div className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">{children}</div>
    </section>
  );
}
