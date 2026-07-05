import { type PropsWithChildren, type CSSProperties } from 'react'
import { cn } from '../lib/cn'

/** Dark tinted card. */
export function GlassCard({ children, className, style }: PropsWithChildren<{ className?: string; style?: CSSProperties }>) {
  return (
    <div className={cn('rounded-card border border-white/[0.08] bg-obsidian', className)} style={style}>
      {children}
    </div>
  )
}

/** Pill (nav link / chip / badge). */
export function Pill({ children, className }: PropsWithChildren<{ className?: string }>) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-pill border border-white/10 bg-charcoal/70 px-3 py-1 text-xs text-mist', className)}>
      {children}
    </span>
  )
}

/** KPI / stat tile. */
export function StatCard({ icon, value, label, className }: { icon?: React.ReactNode; value: string; label: string; className?: string }) {
  return (
    <GlassCard className={cn('p-5 transition-transform duration-300 hover:-translate-y-1', className)}>
      {icon && <div className="mb-3 text-2xl text-white/90">{icon}</div>}
      <div className="text-[28px] font-semibold tracking-[-1px]">{value}</div>
      <div className="mt-0.5 text-[12.5px] text-ash">{label}</div>
    </GlassCard>
  )
}

/** Animated SVG score ring. `pct` 0–100. */
export function ScoreRing({ pct, size = 128, label = 'OVERALL', value }: { pct: number; size?: number; label?: string; value?: number }) {
  const r = size / 2 - 11
  const circ = 2 * Math.PI * r
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={11} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="url(#sr)" strokeWidth={11} strokeLinecap="round"
          strokeDasharray={circ} strokeDashoffset={circ * (1 - pct / 100)}
          style={{ transition: 'stroke-dashoffset 1.6s cubic-bezier(.2,.7,.2,1)' }} />
        <defs>
          <linearGradient id="sr" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#0088ff" />
            <stop offset="100%" stopColor="#a887dc" />
          </linearGradient>
        </defs>
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <div className="text-[34px] font-semibold tracking-[-1.5px] text-white">{value ?? pct}</div>
        <div className="text-[10px] tracking-[1px] text-fog">{label}</div>
      </div>
    </div>
  )
}

/** Looping audio waveform bars. */
export function WaveBars({ count = 9, colors = ['#0088ff', '#60a5fa'], className }: { count?: number; colors?: string[]; className?: string }) {
  return (
    <div className={cn('flex items-end gap-[3px]', className)}>
      {Array.from({ length: count }).map((_, i) => (
        <span key={i} className="w-[3px] rounded-full animate-wave"
          style={{ height: `${40 + Math.abs(Math.sin(i * 0.9)) * 60}%`, background: colors[i % colors.length], animationDelay: `${(i % 5) * 0.1}s` }} />
      ))}
    </div>
  )
}

/** Auto-scrolling, duplicated marquee row. */
export function Marquee({ children, className }: PropsWithChildren<{ className?: string }>) {
  return (
    <div className={cn('at-marquee-mask relative overflow-hidden', className)}>
      <div className="flex w-max gap-12 animate-marquee">
        {children}
        {children}
      </div>
    </div>
  )
}
