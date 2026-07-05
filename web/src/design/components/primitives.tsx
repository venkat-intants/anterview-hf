import {
  forwardRef, memo, useEffect, useId, useRef, useState,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode,
} from 'react';
import { animate, motion, useInView, useMotionValue } from 'framer-motion';
import { cn } from '../lib/cn';
import { useAccentColor } from '@/lib/useAccentColor';

/* ─────────────────────────  AnimatedNumber  ───────────────────────── */

/** Counts the numeric part of a string up from 0 when scrolled into view.
 *  Non-numeric values (e.g. "—") render unchanged. */
export function AnimatedNumber({ value }: { value: string }): JSX.Element {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, amount: 0.6 });
  const mv = useMotionValue(0);
  const match = value.match(/^([^\d]*)([\d.,]+)(.*)$/);
  const [text, setText] = useState(value);
  useEffect(() => {
    const m = value.match(/^([^\d]*)([\d.,]+)(.*)$/);
    if (!m || !inView) return;
    const target = parseFloat(m[2].replace(/,/g, ''));
    const decimals = m[2].includes('.') ? (m[2].split('.')[1]?.length ?? 0) : 0;
    const grouped = m[2].includes(',');
    setText(`${m[1]}0${m[3]}`);
    const controls = animate(mv, target, {
      duration: 1.2,
      ease: [0.2, 0.7, 0.2, 1],
      onUpdate: (v) => {
        const n = decimals ? v.toFixed(decimals) : Math.round(v).toString();
        const shown = grouped ? Number(n).toLocaleString('en-IN') : n;
        setText(`${m[1]}${shown}${m[3]}`);
      },
    });
    return () => controls.stop();
  }, [inView, value, mv]);
  // Always render the ref-bearing span — even for a non-numeric placeholder like
  // "—" shown while data loads. If the ref were dropped on the placeholder pass,
  // useInView would attach its observer to nothing and never fire once the real
  // (numeric) value arrives, leaving the count-up stuck on the placeholder.
  return <span ref={ref}>{match ? text : value}</span>;
}

/* ─────────────────────────  GlassCard  ───────────────────────── */

interface GlassCardProps {
  children: ReactNode;
  className?: string;
  /** subtle hover lift */
  hover?: boolean;
  /** electric-tinted “featured” surface */
  feature?: boolean;
}

export const GlassCard = forwardRef<HTMLDivElement, GlassCardProps>(
  ({ children, className, hover = false, feature = false }, ref) => (
    <div
      ref={ref}
      className={cn(
        'rounded-[24px] border p-6',
        feature
          ? 'border-[rgba(var(--accent-rgb),0.22)] bg-[linear-gradient(160deg,#001b33,#030719)]'
          : 'border-white/[0.08] bg-[#0f0f10]',
        hover &&
          'transition-[transform,box-shadow,border-color] duration-300 will-change-transform hover:-translate-y-1 hover:border-[rgba(var(--accent-rgb),0.45)] hover:shadow-[0_16px_48px_-18px_rgba(var(--accent-rgb),0.45)]',
        className,
      )}
    >
      {children}
    </div>
  ),
);
GlassCard.displayName = 'GlassCard';

/* ─────────────────────────  StatCard  ───────────────────────── */

interface StatCardProps {
  label: string;
  value: string;
  delta?: string;
  trend?: 'up' | 'down' | 'flat';
  feature?: boolean;
  className?: string;
}

export function StatCard({ label, value, delta, trend = 'flat', feature, className }: StatCardProps): JSX.Element {
  const deltaColor =
    trend === 'up' ? 'text-[#27c93f]' : trend === 'down' ? 'text-[#e6714f]' : 'text-[#888b91]';
  return (
    <GlassCard feature={feature} className={cn('p-5', className)}>
      <div className="mb-3 text-[12.5px] text-[#888b91]">{label}</div>
      <div className="text-[30px] font-semibold tracking-[-1.2px] text-white tabular-nums">
        <AnimatedNumber value={value} />
      </div>
      {delta ? <div className={cn('mt-1.5 text-[12px]', deltaColor)}>{delta}</div> : null}
    </GlassCard>
  );
}

/* ─────────────────────────  ScoreRing  ───────────────────────── */

interface ScoreRingProps {
  /** 0–100 */
  score: number;
  size?: number;
  stroke?: number;
  label?: string;
  className?: string;
}

export const ScoreRing = memo(function ScoreRing({
  score, size = 132, stroke = 10, label = 'score', className,
}: ScoreRingProps): JSX.Element {
  const clamped = Math.max(0, Math.min(100, score));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const gradId = useId();
  const accent = useAccentColor();
  const color = clamped >= 85 ? '#27c93f' : clamped >= 70 ? accent : clamped >= 55 ? '#ffb764' : '#e6714f';
  return (
    <div className={cn('relative inline-flex items-center justify-center', className)} style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={accent} />
            <stop offset="100%" stopColor={color} />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} stroke="rgba(255,255,255,0.08)" strokeWidth={stroke} fill="none" />
        <motion.circle
          cx={size / 2} cy={size / 2} r={r} stroke={`url(#${gradId})`} strokeWidth={stroke}
          fill="none" strokeLinecap="round" strokeDasharray={c}
          initial={{ strokeDashoffset: c }}
          whileInView={{ strokeDashoffset: c - (c * clamped) / 100 }}
          viewport={{ once: true }}
          transition={{ duration: 1.4, ease: [0.2, 0.7, 0.2, 1] }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-[28px] font-semibold tracking-[-1px] text-white">{clamped}</span>
        <span className="text-[10px] uppercase tracking-[1px] text-[#70757c]">{label}</span>
      </div>
    </div>
  );
});

/* ─────────────────────────  WaveBars  ───────────────────────── */

interface WaveBarsProps {
  /** drive the animation */
  active?: boolean;
  bars?: number;
  color?: string;
  className?: string;
  height?: number;
}

export function WaveBars({ active = true, bars = 28, color = 'var(--accent)', className, height = 38 }: WaveBarsProps): JSX.Element {
  return (
    <div className={cn('flex items-center gap-[3px]', className)} style={{ height }} aria-hidden="true">
      {Array.from({ length: bars }).map((_, i) => (
        <span
          key={i}
          className={active ? 'av-wave' : undefined}
          style={{
            width: 3, borderRadius: 9999, background: color,
            height: active ? '100%' : '22%',
            transformOrigin: 'center',
            animation: active ? `av-wave-bar ${0.7 + (i % 5) * 0.12}s ease-in-out ${(i % 7) * 0.05}s infinite` : 'none',
            opacity: active ? 1 : 0.4,
          }}
        />
      ))}
    </div>
  );
}

/* ─────────────────────────  Pill (button)  ───────────────────────── */

type PillVariant = 'primary' | 'ghost' | 'outline' | 'accent' | 'danger';

interface PillProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: PillVariant;
  children: ReactNode;
}

const PILL_STYLES: Record<PillVariant, string> = {
  primary: 'bg-white text-black hover:bg-[#eaeaea]',
  ghost: 'bg-white/[0.06] text-white border border-white/10 hover:bg-white/[0.1]',
  outline: 'bg-transparent text-white border border-white/15 hover:border-white/30',
  accent: 'bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa] border border-[rgba(var(--accent-rgb),0.35)] hover:bg-[rgba(var(--accent-rgb),0.2)]',
  danger: 'bg-[rgba(230,113,79,0.14)] text-[#e6714f] border border-[rgba(230,113,79,0.35)] hover:bg-[rgba(230,113,79,0.22)]',
};

export const Pill = forwardRef<HTMLButtonElement, PillProps>(
  ({ variant = 'primary', className, children, ...rest }, ref) => (
    <button
      ref={ref}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-[9999px] px-5 py-2.5 text-[14px] font-semibold',
        'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-black',
        'disabled:cursor-not-allowed disabled:opacity-50',
        PILL_STYLES[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  ),
);
Pill.displayName = 'Pill';

/* ─────────────────────────  Marquee  ───────────────────────── */

interface MarqueeProps {
  items: string[];
  className?: string;
}

export function Marquee({ items, className }: MarqueeProps): JSX.Element {
  const doubled = [...items, ...items];
  return (
    <div className={cn('relative overflow-hidden', className)} aria-hidden="true"
      style={{ maskImage: 'linear-gradient(90deg,transparent,#000 8%,#000 92%,transparent)' }}>
      <div className="av-marquee-track flex w-max gap-10">
        {doubled.map((it, i) => (
          <span key={i} className="whitespace-nowrap text-[14px] text-[#888b91]">{it}</span>
        ))}
      </div>
    </div>
  );
}

/* ─────────────────────────  Avatar  ───────────────────────── */

interface AvatarProps {
  initials: string;
  gradient?: string;
  size?: number;
  className?: string;
}

export function Avatar({ initials, gradient = 'linear-gradient(135deg,var(--accent),#a887dc)', size = 36, className }: AvatarProps): JSX.Element {
  return (
    <span
      className={cn('inline-flex flex-none items-center justify-center rounded-full font-semibold text-white', className)}
      style={{ width: size, height: size, background: gradient, fontSize: size * 0.36 }}
    >
      {initials}
    </span>
  );
}

/* ─────────────────────────  StatusTag  ───────────────────────── */

export type TagTone = 'neutral' | 'electric' | 'lavender' | 'amber' | 'forest' | 'ember' | 'pink';

const TAG_TONES: Record<TagTone, string> = {
  neutral: 'bg-white/[0.08] text-[#b8babf]',
  electric: 'bg-[rgba(var(--accent-rgb),0.16)] text-[#60a5fa]',
  lavender: 'bg-[rgba(168,135,220,0.18)] text-[#c89ce8]',
  amber: 'bg-[rgba(255,183,100,0.16)] text-[#ffb764]',
  forest: 'bg-[rgba(39,201,63,0.16)] text-[#27c93f]',
  ember: 'bg-[rgba(230,113,79,0.16)] text-[#e6714f]',
  pink: 'bg-[rgba(221,85,231,0.16)] text-[#dd55e7]',
};

interface StatusTagProps {
  children: ReactNode;
  tone?: TagTone;
  dot?: boolean;
  className?: string;
}

export function StatusTag({ children, tone = 'neutral', dot = false, className }: StatusTagProps): JSX.Element {
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-pill px-2.5 py-1 text-[11.5px] font-semibold', TAG_TONES[tone], className)}>
      {dot ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : null}
      {children}
    </span>
  );
}

/* ─────────────────────────  Field (labeled input)  ───────────────────────── */

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  icon?: ReactNode;
  hint?: string;
}

export const Field = forwardRef<HTMLInputElement, FieldProps>(
  ({ label, icon, hint, id, className, ...rest }, ref) => {
    const autoId = useId();
    const fieldId = id ?? autoId;
    return (
      <div className="flex flex-col gap-1.5">
        <label htmlFor={fieldId} className="text-[12.5px] font-medium text-[#b8babf]">{label}</label>
        <div className="flex items-center gap-2.5 rounded-[12px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3.5 py-3 focus-within:border-[var(--accent)]">
          {icon ? <span className="text-[#70757c]">{icon}</span> : null}
          <input
            ref={ref}
            id={fieldId}
            className={cn('w-full min-w-0 bg-transparent text-[14px] text-white placeholder:text-[#5a5f66] focus:outline-none', className)}
            {...rest}
          />
        </div>
        {hint ? <span className="text-[11.5px] text-[#70757c]">{hint}</span> : null}
      </div>
    );
  },
);
Field.displayName = 'Field';

/* ─────────────────────────  ToggleSwitch  ───────────────────────── */

interface ToggleSwitchProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
}

export function ToggleSwitch({ checked, onChange, label }: ToggleSwitchProps): JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative h-6 w-11 flex-none rounded-pill transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-black',
        checked ? 'bg-[var(--accent)]' : 'bg-white/15',
      )}
    >
      <span className={cn('absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all', checked ? 'left-[22px]' : 'left-0.5')} />
    </button>
  );
}

/* ─────────────────────────  SegTabs  ───────────────────────── */

interface SegTab {
  key: string;
  label: string;
}

interface SegTabsProps {
  tabs: SegTab[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}

export function SegTabs({ tabs, active, onChange, className }: SegTabsProps): JSX.Element {
  return (
    <div className={cn('inline-flex items-center gap-1 rounded-pill border border-white/[0.08] bg-[rgba(28,29,31,0.6)] p-1', className)} role="tablist">
      {tabs.map((t) => {
        const on = t.key === active;
        return (
          <button
            key={t.key}
            role="tab"
            aria-selected={on}
            onClick={() => onChange(t.key)}
            className={cn(
              'rounded-pill px-3.5 py-1.5 text-[12.5px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
              on ? 'bg-white text-black' : 'text-[#b8babf] hover:text-white',
            )}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
