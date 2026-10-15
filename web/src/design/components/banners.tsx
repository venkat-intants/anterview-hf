// Brand banner system — gives the app a consistent commercial "character":
//   • PromoBanner — a gradient hero/announcement strip with an optional CTA,
//     decorative icon, badge and one-click dismiss (persisted per id).
//   • TrustStrip  — a row of USP chips (voice-first, DPDP-compliant, …).
//   • Badge       — a small uppercase accent tag (NEW · AI · LIVE · …).
// Pure presentational; reused across the candidate and HR surfaces.

import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { X, type LucideIcon } from './icons';

// PromoBanner is a FIXED-colour card in both modes — a tinted fill carrying its
// own ink, the same device as the auth brand panel — so its interior contrast is
// fixed and it is deliberately NOT wired to the mode tokens.
// TrustStrip sits on the page canvas, so it IS.
export type BannerTone = 'aurora' | 'electric' | 'forest' | 'amber';

// `bg` is the fill, `ink` the text on it, `chip` the icon tile. Each pair is
// chosen together: ink on fill is at least 7:1 for all four.
// `cta` / `ctaInk` are the button on the card. They are separate from `ink`
// because the brand tone is a dark fill with white text, where a button made
// from the ink colour would be white-on-white.
interface Tone {
  bg: string;
  ink: string;
  chip: string;
  cta: string;
  ctaInk: string;
}

const TONE: Record<BannerTone, Tone> = {
  // The default: the fixed brand surface (see --ui-grad-brand in index.css).
  aurora: {
    bg: 'linear-gradient(140deg,#0b1f4d 0%,#1447c4 62%,#1f62d6 100%)',
    ink: '#ffffff',
    chip: 'rgba(255,255,255,0.14)',
    cta: '#ffffff',
    ctaInk: '#0b1f4d',
  },
  electric: { bg: '#e3ecff', ink: '#153a94', chip: 'rgba(21,58,148,0.1)', cta: '#153a94', ctaInk: '#ffffff' },
  forest: { bg: '#cdf2d6', ink: '#06301a', chip: 'rgba(6,48,26,0.1)', cta: '#06301a', ctaInk: '#ffffff' },
  amber: { bg: '#ffe2b8', ink: '#3a2203', chip: 'rgba(58,34,3,0.1)', cta: '#3a2203', ctaInk: '#ffffff' },
};

/* ───────────────────────────── Badge ───────────────────────────── */

// On the per-mode status tokens: the old pastel hexes were chosen against a
// black card and were unreadable on a white one.
const BADGE_TONE: Record<string, string> = {
  electric: 'border-transparent bg-[var(--ui-info-wash)] text-[var(--ui-info)]',
  forest: 'border-transparent bg-[var(--ui-ok-wash)] text-[var(--ui-ok)]',
  amber: 'border-transparent bg-[var(--ui-warn-wash)] text-[var(--ui-warn)]',
  lavender: 'border-transparent bg-[var(--ui-lavender-wash)] text-[var(--ui-lavender)]',
};

export function Badge({
  children,
  tone = 'electric',
  className,
}: {
  children: ReactNode;
  tone?: 'electric' | 'forest' | 'amber' | 'lavender';
  className?: string;
}): JSX.Element {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[12px] font-bold tracking-[0.01em]',
        BADGE_TONE[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/* ─────────────────────────── TrustStrip ────────────────────────── */

export interface TrustChip {
  icon: LucideIcon;
  label: string;
}

export function TrustStrip({
  items,
  className,
}: {
  items: TrustChip[];
  className?: string;
}): JSX.Element {
  return (
    <div className={cn('flex flex-wrap items-center gap-2', className)}>
      {items.map(({ icon: Icon, label }) => (
        <span
          key={label}
          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-[var(--ui-inset-soft)] px-3 py-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:border-[var(--ui-line-strong)] hover:text-foreground"
        >
          <Icon size={13} className="text-[var(--ui-info)]" aria-hidden="true" />
          {label}
        </span>
      ))}
    </div>
  );
}

/* ────────────────────────── PromoBanner ────────────────────────── */

export interface PromoBannerProps {
  title: string;
  subtitle?: string;
  eyebrow?: string;
  badge?: string;
  icon?: LucideIcon;
  tone?: BannerTone;
  cta?: { label: string; to?: string; onClick?: () => void };
  /** When set, a dismiss button appears and the choice persists in localStorage. */
  dismissId?: string;
  className?: string;
}

export function PromoBanner({
  title,
  subtitle,
  eyebrow,
  badge,
  icon: Icon,
  tone = 'aurora',
  cta,
  dismissId,
  className,
}: PromoBannerProps): JSX.Element | null {
  const storageKey = dismissId ? `anterview:banner:${dismissId}` : null;
  const [dismissed, setDismissed] = useState<boolean>(() => {
    if (!storageKey || typeof window === 'undefined') return false;
    try {
      return window.localStorage.getItem(storageKey) === '1';
    } catch {
      return false;
    }
  });

  if (dismissed) return null;

  const t = TONE[tone];

  const dismiss = (): void => {
    if (storageKey) {
      try {
        window.localStorage.setItem(storageKey, '1');
      } catch {
        /* ignore quota / privacy-mode errors */
      }
    }
    setDismissed(true);
  };

  // The CTA takes its colours from the tone, like the rest of the banner: the
  // card is a fixed fill in both modes, so the mode's --primary is not a safe
  // pairing for it. The link form
  // is a styled <Link>, not a <button> inside an <a>: nesting the two is
  // invalid HTML and gave keyboard users two tab stops for one action.
  const ctaClass =
    'inline-flex items-center justify-center gap-2 rounded-full px-5 py-2.5 text-[14px] font-semibold transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-current focus-visible:ring-offset-2';
  const ctaStyle = { background: t.cta, color: t.ctaInk };
  const ctaNode = cta ? (
    cta.to ? (
      <Link to={cta.to} className={ctaClass} style={ctaStyle}>
        {cta.label}
      </Link>
    ) : (
      <button type="button" className={ctaClass} style={ctaStyle} onClick={cta.onClick}>
        {cta.label}
      </button>
    )
  ) : null;

  return (
    <div
      className={cn(
        'relative overflow-hidden rounded-card',
        className,
      )}
      style={{ background: t.bg, color: t.ink }}
    >

      {dismissId && (
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss"
          className="absolute right-3 top-3 z-10 inline-flex h-8 w-8 items-center justify-center rounded-full opacity-60 transition-[opacity,background-color] hover:bg-black/10 hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-current"
        >
          <X size={15} aria-hidden="true" />
        </button>
      )}

      <div className="relative flex flex-col gap-5 p-6 md:flex-row md:items-center md:justify-between md:p-7">
        <div className="max-w-[660px]">
          {(badge || eyebrow) && (
            <div className="mb-3 flex flex-wrap items-center gap-2.5">
              {badge && (
                <span
                  className="inline-flex items-center rounded-full px-2 py-0.5 text-[10.5px] font-bold uppercase tracking-[0.08em]"
                  style={{ background: t.cta, color: t.ctaInk }}
                >
                  {badge}
                </span>
              )}
              {eyebrow && (
                <span className="text-[11.5px] font-semibold uppercase tracking-[0.08em] opacity-70">
                  {eyebrow}
                </span>
              )}
            </div>
          )}
          <h2
            className="font-bold tracking-[-0.5px]"
            style={{ fontSize: 'clamp(20px, 2.4vw, 27px)', lineHeight: 1.15 }}
          >
            {title}
          </h2>
          {subtitle && (
            <p className="mt-2 max-w-[580px] text-[14.5px] leading-relaxed opacity-80">
              {subtitle}
            </p>
          )}
          {ctaNode && <div className="mt-4">{ctaNode}</div>}
        </div>

        {Icon && (
          <div
            className="hidden h-[84px] w-[84px] flex-none items-center justify-center rounded-[22px] md:flex"
            style={{ background: t.chip }}
            aria-hidden="true"
          >
            <Icon size={36} />
          </div>
        )}
      </div>
    </div>
  );
}
