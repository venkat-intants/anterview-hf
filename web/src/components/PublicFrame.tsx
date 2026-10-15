// PublicFrame — the one frame every no-login, emailed-link page renders in
// (apply, resume, careers, exam, offer, task, interview invite).
//
// These pages are a stranger's first impression of the company that invited
// them, usually on a phone and often under stress, and they have no app shell.
// So: a slim header (brand mark, the company's name where the page has it,
// colour mode and language), one centred readable column, a quiet footer.

import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import ModeSwitch from '@/components/ModeSwitch';
import { AlertCircle, CheckCircle2 } from '@/design/components/icons';
import { StepContext } from '@/design/components/next-step';

interface PublicFrameProps {
  children: ReactNode;
  /** The inviting company, when the page knows it. */
  company?: string | null;
  /** Column width: `narrow` for a single card, `wide` for forms and listings. */
  width?: 'narrow' | 'default' | 'wide';
  /** Hide the language switcher (a timed exam keeps its header quiet). */
  hideLanguage?: boolean;
  /** Show the "this link is private to you, do not share it" footer. Only for
   *  pages reached from an emailed personal link. The careers page and the
   *  open application form are meant to be circulated, so there it would be
   *  false — and would tell applicants not to share a job. */
  privateLink?: boolean;
  /** Extra header content, right-aligned before the switches. */
  headerExtra?: ReactNode;
  /** The step this page is (translated), e.g. "Assessment". Shown as a context
   *  line under the header. Never "step 3 of 5": a token page does not know
   *  how many steps the hiring process has. */
  step?: ReactNode;
  /** The role, only where the page's own payload carries it and the page does
   *  not already print it as its heading. The company is in the header. */
  role?: ReactNode;
  className?: string;
}

const WIDTHS = {
  narrow: 'max-w-[560px]',
  default: 'max-w-[720px]',
  wide: 'max-w-[960px]',
} as const;

export function BrandMark({ className }: { className?: string }): JSX.Element {
  // The mark is the fixed brand surface in both modes: it is the brand, not a
  // surface that follows the mode.
  return (
    <span
      aria-hidden="true"
      className={cn(
        'flex h-8 w-8 flex-none items-center justify-center rounded-[9px] bg-[image:var(--ui-grad-brand)] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.16)]',
        className,
      )}
    >
      <span className="h-2.5 w-2.5 rounded-full bg-[var(--ui-brand-ink)]" />
    </span>
  );
}

export function PublicFrame({
  children,
  company,
  width = 'default',
  hideLanguage = false,
  privateLink = false,
  headerExtra,
  step,
  role,
  className,
}: PublicFrameProps): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-screen flex-col bg-background font-sans text-foreground">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex h-14 w-full max-w-[960px] items-center justify-between gap-3 px-4">
          <div className="flex min-w-0 items-center gap-2.5">
            <BrandMark />
            <span className="font-display text-[16px] font-bold tracking-[-0.3px]">AntHire</span>
            {company ? (
              <>
                <span
                  aria-hidden="true"
                  className="h-4 w-px flex-none bg-[var(--ui-line-strong)]"
                />
                <span className="truncate text-[14px] text-muted-foreground">{company}</span>
              </>
            ) : null}
          </div>
          <div className="flex flex-none items-center gap-2">
            {headerExtra}
            {hideLanguage ? null : <LanguageSwitcher />}
            <ModeSwitch />
          </div>
        </div>
      </header>
      <main className={cn('mx-auto w-full flex-1 px-4 py-8 sm:py-10', WIDTHS[width], className)}>
        {step ? <StepContext step={step} role={role} className="mb-4" /> : null}
        {children}
      </main>
      {privateLink ? (
        <footer className="px-4 pb-8 pt-2 text-center text-[12.5px] text-muted-foreground">
          {t('publicFrame.footer')}
        </footer>
      ) : null}
    </div>
  );
}

interface PublicNoticeProps {
  title: ReactNode;
  description?: ReactNode;
  /** `ok` for a completed state, `warn` for a link that cannot be used. */
  tone?: 'ok' | 'warn' | 'danger' | 'info';
  /** What to do next — a button or link. */
  action?: ReactNode;
  /** A line of reassurance under the description. */
  hint?: ReactNode;
  icon?: ReactNode;
  className?: string;
}

/** A terminal state on a public page: finished, or a link that cannot be used.
 *  Says what happened and what to do, rather than printing a bare error. */
export function PublicNotice({
  title,
  description,
  tone = 'warn',
  action,
  hint,
  icon,
  className,
}: PublicNoticeProps): JSX.Element {
  const toneClass = {
    ok: 'bg-[var(--ui-ok-wash)] text-[var(--ui-ok)]',
    warn: 'bg-[var(--ui-warn-wash)] text-[var(--ui-warn)]',
    danger: 'bg-[var(--ui-danger-wash)] text-[var(--ui-danger)]',
    info: 'bg-[var(--ui-info-wash)] text-[var(--ui-info)]',
  }[tone];
  return (
    <div
      className={cn(
        'flex flex-col items-center rounded-card border border-border bg-card px-6 py-10 text-center shadow-card',
        className,
      )}
    >
      <span
        className={cn('mb-4 flex h-12 w-12 items-center justify-center rounded-full', toneClass)}
      >
        {icon ??
          (tone === 'ok' ? (
            <CheckCircle2 size={22} aria-hidden="true" />
          ) : (
            <AlertCircle size={22} aria-hidden="true" />
          ))}
      </span>
      <h1 className="text-[22px] font-bold leading-tight tracking-[-0.4px] text-foreground">
        {title}
      </h1>
      {description ? (
        <p className="mt-2 max-w-[440px] text-[15px] leading-relaxed text-muted-foreground">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-5">{action}</div> : null}
      {hint ? (
        <p className="mt-4 max-w-[440px] text-[13px] leading-relaxed text-[var(--ui-soft)]">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
