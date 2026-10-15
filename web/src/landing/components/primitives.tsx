import { type PropsWithChildren, type ReactNode } from 'react'
import { cn } from '../lib/cn'

/** The brand mark: the deep-blue brand surface with a white dot, as in the app
 *  sidebar. It is the brand rather than a surface, so it does not follow the
 *  colour mode. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex h-9 w-9 flex-none items-center justify-center rounded-[11px] bg-[image:var(--ui-grad-brand)] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.16)]',
        className,
      )}
    >
      <span className="h-3 w-3 rounded-full bg-[var(--ui-brand-ink)]" aria-hidden="true" />
    </span>
  )
}

/** Resting white card on the paper canvas. */
export function Card({ children, className }: PropsWithChildren<{ className?: string }>) {
  return (
    <div className={cn('rounded-card border border-[var(--lp-line)] bg-[var(--lp-surface)] shadow-card', className)}>
      {children}
    </div>
  )
}

/** Small chip (eyebrows, language tags). */
export function Chip({ children, className }: PropsWithChildren<{ className?: string }>) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-pill border border-[var(--lp-line)] bg-[var(--lp-surface)] px-3 py-1 text-xs font-medium text-[var(--lp-text-soft)]',
        className,
      )}
    >
      {children}
    </span>
  )
}

/** Section heading block: a small violet eyebrow, a display title, an optional lede. */
export function SectionHeading({
  eyebrow,
  title,
  lede,
  align = 'center',
  id,
}: {
  eyebrow: string
  title: ReactNode
  lede?: ReactNode
  align?: 'center' | 'left'
  id?: string
}) {
  return (
    <div className={cn('mb-10 max-w-[680px]', align === 'center' && 'mx-auto text-center')}>
      <p className="mb-3 text-sm font-semibold text-[var(--ui-info)]">{eyebrow}</p>
      <h2 id={id} className="text-[clamp(28px,4vw,40px)] font-bold leading-[1.12] tracking-[-0.02em] text-[var(--lp-text)]">
        {title}
      </h2>
      {lede && <p className="mt-4 text-base leading-relaxed text-[var(--lp-text-muted)]">{lede}</p>}
    </div>
  )
}

/** Animated SVG score ring. `pct` 0–100. */
export function ScoreRing({ pct, size = 112, label = 'SAMPLE', value }: { pct: number; size?: number; label?: string; value?: number }) {
  const stroke = 10
  const r = size / 2 - stroke
  const circ = 2 * Math.PI * r
  return (
    <div className="relative flex-none" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ transform: 'rotate(-90deg)' }} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--lp-inset)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--lp-accent)"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={circ * (1 - pct / 100)}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <div className="font-display text-[30px] font-bold leading-none tracking-[-1px] text-[var(--lp-text)]">{value ?? pct}</div>
        <div className="mt-1 text-xs font-medium text-[var(--lp-text-faint)]">{label}</div>
      </div>
    </div>
  )
}

/** Audio waveform bars; they hold still under prefers-reduced-motion. */
export function WaveBars({ count = 9, className }: { count?: number; className?: string }) {
  return (
    <div className={cn('flex h-5 items-end gap-[3px]', className)} aria-hidden="true">
      {Array.from({ length: count }).map((_, i) => (
        <span
          key={i}
          className="h-full w-[3px] rounded-full bg-[var(--lp-accent)] animate-wave"
          style={{ height: `${40 + Math.abs(Math.sin(i * 0.9)) * 60}%`, animationDelay: `${(i % 5) * 0.1}s` }}
        />
      ))}
    </div>
  )
}
