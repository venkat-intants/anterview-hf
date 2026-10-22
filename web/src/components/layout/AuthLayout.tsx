// AuthLayout — split-screen shell shared by the sign-in and account screens.
//
// Left  (lg+): brand panel on the fixed deep-blue `--auth-gradient` card.
//              The gradient is dark in both modes, so its text is the fixed
//              white `--ui-brand-ink` rather than a mode-following token.
// Right (all): the form column (passed as `children`), centred on the canvas.
//
// The brand panel is decorative and hidden below `lg`; on small screens the
// form column opens with a compact brand mark instead.
//
// This file also holds the small pieces every account screen shares, so the
// standalone pages (verify, reset, activate, change password, 404, Google
// callback) read as one family: BrandMark, StatusPanel, CenteredCard,
// FormError, PasswordField and PasswordStrength.

import { forwardRef, useId, useState } from 'react';
import type { InputHTMLAttributes, ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Sparkles, Languages, ShieldCheck } from '@/design/components/icons';
import type { LucideIcon } from '@/design/components/icons';
import ModeSwitch from '@/components/ModeSwitch';
import { cn } from '@/lib/utils';

interface Feature {
  icon: typeof Sparkles;
  titleKey: string;
  descKey: string;
}

const FEATURES: Feature[] = [
  { icon: Sparkles, titleKey: 'auth.feature1Title', descKey: 'auth.feature1Desc' },
  { icon: Languages, titleKey: 'auth.feature2Title', descKey: 'auth.feature2Desc' },
  { icon: ShieldCheck, titleKey: 'auth.feature3Title', descKey: 'auth.feature3Desc' },
];

// ── Brand mark ───────────────────────────────────────────────────────────────
// The standard mark: deep-blue brand surface with a white dot, as in the app
// sidebar. On the blue brand panel (`tone="onBrand"`) it inverts to a white
// square with a blue dot so it does not vanish into its own background. It is
// the brand, not a surface, so it does not follow the mode.
export function BrandMark({
  size = 'md',
  tone = 'default',
}: {
  size?: 'md' | 'lg';
  tone?: 'default' | 'onBrand';
}) {
  const onBrand = tone === 'onBrand';
  return (
    <span
      className={cn(
        'flex flex-none items-center justify-center shadow-[inset_0_0_0_1px_rgba(255,255,255,0.16)]',
        onBrand ? 'bg-[var(--ui-brand-ink)]' : 'bg-[image:var(--ui-grad-brand)]',
        size === 'lg' ? 'h-12 w-12 rounded-[14px]' : 'h-9 w-9 rounded-[11px]',
      )}
      aria-hidden="true"
    >
      <span
        className={cn(
          'rounded-full',
          onBrand ? 'bg-[image:var(--ui-grad-brand)]' : 'bg-[var(--ui-brand-ink)]',
          size === 'lg' ? 'h-4 w-4' : 'h-3 w-3',
        )}
      />
    </span>
  );
}

// ── Brand showcase (desktop only) ────────────────────────────────────────────
function BrandPanel() {
  const { t } = useTranslation();

  return (
    <aside
      className="hidden text-[var(--ui-brand-ink)] lg:flex lg:flex-col lg:justify-between lg:p-12 xl:p-14"
      style={{ background: 'var(--auth-gradient)' }}
    >
      {/* Top — brand lockup */}
      <Link
        to="/"
        className="inline-flex w-fit items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ui-brand-ink)]"
        aria-label="AntHire home"
      >
        <BrandMark tone="onBrand" />
        <span className="font-display text-[19px] font-bold tracking-[-0.4px]">AntHire</span>
      </Link>

      {/* Middle — value proposition + features */}
      <div className="mt-auto max-w-md">
        <h2 className="font-display text-[44px] font-bold leading-[1.05] tracking-[-1.6px] xl:text-[52px]">
          {t('auth.brandTagline')}
        </h2>

        <ul className="mt-10 flex flex-col gap-5">
          {FEATURES.map((f) => {
            const Icon = f.icon;
            return (
              <li key={f.titleKey} className="flex items-start gap-3.5">
                <span className="flex h-10 w-10 flex-none items-center justify-center rounded-full bg-white/15 ring-1 ring-inset ring-white/20">
                  <Icon className="h-[18px] w-[18px]" aria-hidden="true" />
                </span>
                <div>
                  <p className="text-[15px] font-bold">{t(f.titleKey)}</p>
                  <p className="mt-0.5 text-[14px] leading-relaxed text-white/80">{t(f.descKey)}</p>
                </div>
              </li>
            );
          })}
        </ul>
      </div>

      {/* Bottom — trust line */}
      <p className="mt-12 flex items-center gap-2 text-[13px] font-medium text-white/80">
        <ShieldCheck className="h-4 w-4 shrink-0" aria-hidden="true" />
        {t('auth.trustNote')}
      </p>
    </aside>
  );
}

interface AuthLayoutProps {
  children: ReactNode;
}

export default function AuthLayout({ children }: AuthLayoutProps) {
  return (
    // Canvas follows the mode; the brand panel keeps its own fixed gradient.
    <main className="min-h-screen w-full bg-background font-sans text-foreground lg:grid lg:grid-cols-[1.05fr_1fr] xl:grid-cols-[1.15fr_1fr]">
      <BrandPanel />

      {/* Form column — vertically centred, with the mode switch pinned to its
          corner: a visitor who lands straight on /login (an emailed link, say)
          needs the choice here, not only on the marketing page. */}
      <div className="relative flex min-h-screen items-center justify-center px-6 py-16 lg:min-h-0">
        <div className="absolute right-6 top-6 z-10">
          <ModeSwitch />
        </div>
        <div className="w-full max-w-[400px]">
          {/* Compact brand mark; the brand panel carries it from lg up. */}
          <Link
            to="/"
            className="mb-8 flex w-fit items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] lg:hidden"
            aria-label="AntHire home"
          >
            <BrandMark />
            <span className="font-display text-[17px] font-bold tracking-[-0.4px] text-foreground">
              AntHire
            </span>
          </Link>
          {children}
        </div>
      </div>
    </main>
  );
}

// ── Shared pieces for the account screens ────────────────────────────────────

/** Full-page centred card on the canvas, for pages that stand alone (no brand
 *  panel): forced password change, Google callback, 404. */
export function CenteredCard({
  children,
  labelledBy,
  className,
}: {
  children: ReactNode;
  labelledBy?: string;
  className?: string;
}) {
  return (
    <main
      aria-labelledby={labelledBy}
      className="flex min-h-screen items-center justify-center bg-background px-4 py-12 font-sans text-foreground"
    >
      <div className="w-full max-w-md">
        <div className="mb-6 flex justify-center">
          <BrandMark size="lg" />
        </div>
        <div
          className={cn(
            'rounded-card border border-border bg-card p-6 shadow-card sm:p-8',
            className,
          )}
        >
          {children}
        </div>
      </div>
    </main>
  );
}

type StatusTone = 'ok' | 'danger' | 'info';

const TONE_CLASS: Record<StatusTone, string> = {
  ok: 'bg-[var(--ui-ok-wash)] text-[var(--ui-ok)]',
  danger: 'bg-[var(--ui-danger-wash)] text-[var(--ui-danger)]',
  info: 'bg-[var(--ui-info-wash)] text-[var(--ui-info)]',
};

/** Icon tile + title + description + actions: the outcome of a step (sent,
 *  verified, expired, failed). The heading is the page's h1. */
export function StatusPanel({
  icon: Icon,
  tone,
  title,
  titleId,
  children,
  actions,
  spin = false,
}: {
  icon: LucideIcon;
  tone: StatusTone;
  title: ReactNode;
  titleId?: string;
  children?: ReactNode;
  actions?: ReactNode;
  spin?: boolean;
}) {
  return (
    <div className="flex flex-col items-center text-center">
      <span
        className={cn(
          'inline-flex h-14 w-14 items-center justify-center rounded-full',
          TONE_CLASS[tone],
        )}
      >
        <Icon className={cn('h-6 w-6', spin && 'animate-spin')} aria-hidden="true" />
      </span>
      <h1
        id={titleId}
        className="mt-5 text-[24px] font-bold leading-tight tracking-[-0.5px] text-foreground"
      >
        {title}
      </h1>
      {children ? (
        <div className="mt-2 max-w-sm space-y-3 text-[14px] leading-relaxed text-muted-foreground">
          {children}
        </div>
      ) : null}
      {actions ? (
        <div className="mt-6 flex w-full flex-col items-center gap-3">{actions}</div>
      ) : null}
    </div>
  );
}

/** Inline form error. The wrapper always occupies a line so an error
 *  appearing never pushes the submit button down; only the message is the
 *  live region. Pass `id` and point the field's aria-describedby at it. */
export function FormError({ id, children }: { id?: string; children?: ReactNode }) {
  return (
    <div className="min-h-5">
      {children ? (
        <p
          id={id}
          role="alert"
          className="text-[13px] font-medium leading-5 text-[var(--ui-danger)]"
        >
          {children}
        </p>
      ) : null}
    </div>
  );
}

type PasswordFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  label: string;
  icon?: ReactNode;
  hint?: string;
  /** Marks the field invalid (border + aria-invalid); the message itself is
   *  rendered by the page's FormError and linked through aria-describedby. */
  invalid?: boolean;
};

/** A password input with a show/hide toggle. Same look as `Field`; the toggle
 *  is a real button (type="button", so it never submits) after the input in
 *  tab order, and it exposes its state with aria-pressed. Its name is just
 *  "Show"/"Hide" so the field's own label stays the only thing a "password"
 *  label query resolves to. */
export const PasswordField = forwardRef<HTMLInputElement, PasswordFieldProps>(
  (
    { label, icon, hint, invalid, id, className, 'aria-describedby': describedBy, ...rest },
    ref,
  ) => {
    const { t } = useTranslation();
    const autoId = useId();
    const fieldId = id ?? autoId;
    const hintId = `${fieldId}-hint`;
    const [visible, setVisible] = useState(false);
    const describedIds = [describedBy, hint ? hintId : null].filter(Boolean).join(' ') || undefined;
    return (
      <div className="flex flex-col gap-1.5">
        <label htmlFor={fieldId} className="text-[13px] font-semibold text-foreground">
          {label}
        </label>
        <div
          className={cn(
            'flex items-center gap-2.5 rounded-[12px] border bg-card py-1 pl-3.5 pr-1.5 transition-[border-color,box-shadow] focus-within:shadow-[0_0_0_3px_rgba(var(--accent-rgb),0.16)]',
            invalid
              ? 'border-[var(--ui-danger)]'
              : 'border-input hover:border-[var(--ui-line-strong)] focus-within:border-[var(--accent)]',
          )}
        >
          {icon ? <span className="text-[var(--ui-faint)]">{icon}</span> : null}
          <input
            ref={ref}
            id={fieldId}
            type={visible ? 'text' : 'password'}
            aria-invalid={invalid ? true : undefined}
            aria-describedby={describedIds}
            className={cn(
              'min-h-10 w-full min-w-0 bg-transparent text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus:outline-none',
              className,
            )}
            {...rest}
          />
          <button
            type="button"
            aria-pressed={visible}
            onClick={() => {
              setVisible((v) => !v);
            }}
            className="inline-flex min-h-10 min-w-10 flex-none items-center justify-center rounded-[8px] px-2 text-[12.5px] font-semibold text-[var(--ui-info)] transition-colors hover:bg-[var(--ui-inset)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            {visible ? t('auth.hidePassword') : t('auth.showPassword')}
          </button>
        </div>
        {hint ? (
          <span id={hintId} className="text-[12.5px] text-muted-foreground">
            {hint}
          </span>
        ) : null}
      </div>
    );
  },
);
PasswordField.displayName = 'PasswordField';

/** 0–4 password-strength score (presentation only). */
function strengthOf(pw: string): number {
  let s = 0;
  if (pw.length >= 8) s++;
  if (pw.length >= 12) s++;
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) s++;
  if (/\d/.test(pw) && /[^A-Za-z0-9]/.test(pw)) s++;
  return Math.min(4, s);
}

const STRENGTH_COLOR = [
  'var(--ui-danger)',
  'var(--ui-danger)',
  'var(--ui-warn)',
  'var(--ui-info)',
  'var(--ui-ok)',
];
const STRENGTH_LABEL_KEYS = [
  'auth.strength0',
  'auth.strength1',
  'auth.strength2',
  'auth.strength3',
  'auth.strength4',
] as const;

/** Four-segment strength meter. The word carries the meaning; colour only
 *  reinforces it. Renders nothing for an empty password. */
export function PasswordStrength({ password }: { password: string }) {
  const { t } = useTranslation();
  if (password.length === 0) return null;
  const strength = strengthOf(password);
  return (
    <div className="flex items-center gap-3">
      <div className="flex flex-1 gap-1" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <span
            key={i}
            className="h-1.5 flex-1 rounded-full bg-[var(--ui-inset-strong)] transition-colors"
            style={i < strength ? { background: STRENGTH_COLOR[strength] } : undefined}
          />
        ))}
      </div>
      <span
        role="status"
        className="w-16 text-right text-[12px] font-semibold"
        style={{ color: STRENGTH_COLOR[strength] }}
      >
        {t(STRENGTH_LABEL_KEYS[strength])}
      </span>
    </div>
  );
}
