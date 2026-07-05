// Brand banner system — gives the app a consistent commercial "character":
//   • PromoBanner — a gradient hero/announcement strip with an optional CTA,
//     decorative icon, badge and one-click dismiss (persisted per id).
//   • TrustStrip  — a row of USP chips (voice-first, DPDP-compliant, …).
//   • Badge       — a small uppercase accent tag (NEW · AI · LIVE · …).
// Pure presentational; reused across the candidate and HR surfaces.

import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { Pill } from './primitives';
import { X, type LucideIcon } from './icons';

export type BannerTone = 'aurora' | 'electric' | 'forest' | 'amber';

const TONE: Record<BannerTone, { bg: string; glow: string; accent: string }> = {
  aurora: {
    bg: 'linear-gradient(120deg,#0a0a1f 0%,#10204e 46%,#3a1a5e 100%)',
    glow: 'rgba(var(--accent-rgb),0.55)',
    accent: '#60a5fa',
  },
  electric: {
    bg: 'linear-gradient(120deg,#00111f 0%,#012a57 100%)',
    glow: 'rgba(var(--accent-rgb),0.45)',
    accent: '#60a5fa',
  },
  forest: {
    bg: 'linear-gradient(120deg,#02140c 0%,#063a20 100%)',
    glow: 'rgba(39,201,63,0.4)',
    accent: '#27c93f',
  },
  amber: {
    bg: 'linear-gradient(120deg,#1c1303 0%,#3d2a06 100%)',
    glow: 'rgba(255,183,100,0.4)',
    accent: '#ffb764',
  },
};

/* ───────────────────────────── Badge ───────────────────────────── */

const BADGE_TONE: Record<string, string> = {
  electric: 'border-[rgba(var(--accent-rgb),0.45)] bg-[rgba(var(--accent-rgb),0.16)] text-[#7cb8ff]',
  forest: 'border-[rgba(39,201,63,0.45)] bg-[rgba(39,201,63,0.16)] text-[#56e06f]',
  amber: 'border-[rgba(255,183,100,0.45)] bg-[rgba(255,183,100,0.16)] text-[#ffc987]',
  lavender: 'border-[rgba(184,85,231,0.45)] bg-[rgba(184,85,231,0.16)] text-[#d4a6f0]',
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
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.12em]',
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
          className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-[12px] font-medium text-[#b8babf] transition-colors hover:border-white/[0.16] hover:text-white"
        >
          <Icon size={13} className="text-[#60a5fa]" aria-hidden="true" />
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

  const ctaNode = cta ? (
    cta.to ? (
      <Link to={cta.to}>
        <Pill className="px-5 py-2.5">{cta.label}</Pill>
      </Link>
    ) : (
      <Pill type="button" className="px-5 py-2.5" onClick={cta.onClick}>
        {cta.label}
      </Pill>
    )
  ) : null;

  return (
    <div
      className={cn(
        'relative overflow-hidden rounded-[24px] border border-white/[0.08]',
        className,
      )}
      style={{ background: t.bg }}
    >
      {/* glow */}
      <div
        className="pointer-events-none absolute -right-20 -top-24 h-72 w-72 rounded-full opacity-50 blur-3xl"
        style={{ background: t.glow }}
        aria-hidden="true"
      />
      {/* faint dotted texture for depth */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.05]"
        style={{
          backgroundImage: 'radial-gradient(circle at 1px 1px, #fff 1px, transparent 0)',
          backgroundSize: '22px 22px',
        }}
        aria-hidden="true"
      />

      {dismissId && (
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss"
          className="absolute right-3 top-3 z-10 inline-flex h-7 w-7 items-center justify-center rounded-full text-white/50 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
        >
          <X size={15} aria-hidden="true" />
        </button>
      )}

      <div className="relative flex flex-col gap-5 p-6 md:flex-row md:items-center md:justify-between md:p-7">
        <div className="max-w-[660px]">
          {(badge || eyebrow) && (
            <div className="mb-3 flex flex-wrap items-center gap-2.5">
              {badge && <Badge tone="electric">{badge}</Badge>}
              {eyebrow && (
                <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-white/55">
                  {eyebrow}
                </span>
              )}
            </div>
          )}
          <h2
            className="font-semibold tracking-[-0.6px] text-white"
            style={{ fontSize: 'clamp(20px, 2.4vw, 27px)', lineHeight: 1.15 }}
          >
            {title}
          </h2>
          {subtitle && (
            <p className="mt-2 max-w-[580px] text-[14px] leading-relaxed text-white/70">
              {subtitle}
            </p>
          )}
          {ctaNode && <div className="mt-4">{ctaNode}</div>}
        </div>

        {Icon && (
          <div
            className="hidden h-[84px] w-[84px] flex-none items-center justify-center rounded-[20px] border border-white/15 bg-white/[0.07] backdrop-blur md:flex"
            style={{ boxShadow: `0 0 60px -12px ${t.glow}` }}
            aria-hidden="true"
          >
            <Icon size={36} style={{ color: t.accent }} />
          </div>
        )}
      </div>
    </div>
  );
}
