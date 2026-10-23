import {
  forwardRef, memo, useEffect, useId, useRef, useState,
  type AriaRole, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes,
} from 'react';
import { animate, motion, useInView, useMotionValue } from 'framer-motion';
import { cn } from '../lib/cn';
import { useAccentColor } from '@/lib/useAccentColor';
import { SelectControl } from '@/components/ui/select-control';
import { FormField } from '@/components/ui/form-field';
import { CONTROL_HEIGHT } from '@/components/ui/control-styles';

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
  /** Passed to the root element, so a card can be found in tests. */
  'data-testid'?: string;
  className?: string;
  /** subtle hover lift */
  hover?: boolean;
  /** electric-tinted “featured” surface */
  feature?: boolean;
  /** Landmark/grouping semantics, so a card can BE the region or group rather
   *  than needing a wrapper element to carry the role. */
  role?: AriaRole;
  'aria-label'?: string;
  'aria-labelledby'?: string;
}

export const GlassCard = forwardRef<HTMLDivElement, GlassCardProps>(
  (
    {
      children, className, hover = false, feature = false, 'data-testid': testId,
      role, 'aria-label': ariaLabel, 'aria-labelledby': ariaLabelledBy,
    },
    ref,
  ) => (
    <div
      ref={ref}
      data-testid={testId}
      role={role}
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
      className={cn(
        'rounded-card border p-6 shadow-card',
        feature
          ? 'border-[rgba(var(--accent-rgb),0.2)] bg-[image:var(--ui-grad-feature)]'
          : 'border-border bg-card',
        hover &&
          'transition-[transform,box-shadow,border-color] duration-300 will-change-transform hover:-translate-y-0.5 hover:border-[var(--ui-line-strong)] hover:shadow-card-hover',
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
    trend === 'up' ? 'text-[var(--ui-ok)]' : trend === 'down' ? 'text-[var(--ui-danger)]' : 'text-muted-foreground';
  return (
    <GlassCard feature={feature} className={cn('p-5', className)}>
      <div className="mb-2 text-[13px] font-medium text-muted-foreground">{label}</div>
      <div className="font-display text-[30px] font-bold tracking-[-1px] text-foreground tabular-nums">
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
  // `stopColor=` is a presentation attribute and cannot resolve a CSS var;
  // `style` can, which is what lets the band colour follow the mode.
  const color = clamped >= 85 ? 'var(--ui-ok)' : clamped >= 70 ? accent : clamped >= 55 ? 'var(--ui-warn)' : 'var(--ui-danger)';
  return (
    <div className={cn('relative inline-flex items-center justify-center', className)} style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={accent} />
            <stop offset="100%" style={{ stopColor: color }} />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} style={{ stroke: 'var(--ui-inset-strong)' }} strokeWidth={stroke} fill="none" />
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
        <span className="font-display text-[28px] font-bold tracking-[-1px] text-foreground">{clamped}</span>
        <span className="text-[12px] font-medium text-muted-foreground">{label}</span>
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
          <span key={i} className="whitespace-nowrap text-[14px] text-muted-foreground">{it}</span>
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

export function Avatar({ initials, gradient = 'var(--ui-grad-avatar)', size = 36, className }: AvatarProps): JSX.Element {
  return (
    <span
      // White initials, so every gradient passed in must be dark enough to
      // carry them (4.5:1). The default and design/data/shared.ts GRADIENTS are.
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
  neutral: 'bg-[var(--ui-inset-strong)] text-[var(--ui-soft)]',
  electric: 'bg-[var(--ui-info-wash)] text-[var(--ui-info)]',
  lavender: 'bg-[var(--ui-lavender-wash)] text-[var(--ui-lavender)]',
  amber: 'bg-[var(--ui-warn-wash)] text-[var(--ui-warn)]',
  forest: 'bg-[var(--ui-ok-wash)] text-[var(--ui-ok)]',
  ember: 'bg-[var(--ui-danger-wash)] text-[var(--ui-danger)]',
  // Pink has no dedicated status token; the platform-role pink is the
  // theme-aware pink (darkened in light mode so the chip text holds contrast).
  pink: 'bg-[rgba(221,85,231,0.16)] text-[var(--ui-role-platform)]',
};

interface StatusTagProps {
  children: ReactNode;
  tone?: TagTone;
  dot?: boolean;
  className?: string;
}

export function StatusTag({ children, tone = 'neutral', dot = false, className }: StatusTagProps): JSX.Element {
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-pill px-2.5 py-1 text-[12px] font-semibold', TAG_TONES[tone], className)}>
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
  /** Validation message. Sets aria-invalid, links the message to the input and
   *  announces it, so a page does not wire that up by hand. */
  error?: string;
}

/** A labelled text input with an optional leading icon, built on FormField.
 *  The box is the shared control height (40px), so it lines up with Input,
 *  SelectControl and Button. */
export const Field = forwardRef<HTMLInputElement, FieldProps>(
  ({ label, icon, hint, error, id, className, 'aria-describedby': describedBy, ...rest }, ref) => (
    <FormField label={label} hint={hint} error={error} id={id} describedBy={describedBy}>
      {(control) => (
        <div
          className={cn(
            'flex items-center gap-2.5 rounded-[10px] border bg-card px-3 transition-[border-color,box-shadow] focus-within:shadow-[0_0_0_3px_rgba(var(--accent-rgb),0.16)]',
            CONTROL_HEIGHT.md,
            error
              ? 'border-[var(--ui-danger)]'
              : 'border-input hover:border-[var(--ui-line-strong)] focus-within:border-[var(--accent)]',
          )}
        >
          {icon ? <span className="text-[var(--ui-faint)]">{icon}</span> : null}
          <input
            ref={ref}
            {...control}
            className={cn('w-full min-w-0 bg-transparent text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus:outline-none', className)}
            {...rest}
          />
        </div>
      )}
    </FormField>
  ),
);
Field.displayName = 'Field';

/* ─────────────────────────  SelectField (labeled dropdown)  ───────────────────────── */

interface SelectFieldProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  label: string;
  hint?: string;
  /** Validation message — same wiring as <Field error>. */
  error?: string;
  size?: 'md' | 'sm';
  leadingIcon?: ReactNode;
}

/** The select counterpart of <Field>: FormField around the native dropdown
 *  (styled by the base layer in index.css, so it matches Field's height and
 *  focus ring). Children are the <option>s. */
export const SelectField = forwardRef<HTMLSelectElement, SelectFieldProps>(
  ({ label, hint, error, id, size, leadingIcon, children, 'aria-describedby': describedBy, ...rest }, ref) => (
    <FormField label={label} hint={hint} error={error} id={id} describedBy={describedBy}>
      {(control) => (
        <SelectControl
          ref={ref}
          {...control}
          size={size}
          invalid={Boolean(error)}
          leadingIcon={leadingIcon}
          {...rest}
        >
          {children}
        </SelectControl>
      )}
    </FormField>
  ),
);
SelectField.displayName = 'SelectField';

/* ─────────────────────────  ToggleSwitch  ───────────────────────── */

interface ToggleSwitchProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  /** A REAL disabled state. Callers used to wrap this in `pointer-events-none`,
   *  which stops the mouse and nothing else: Tab + Space still flipped the
   *  switch. Optional and false by default, so every existing caller is
   *  unchanged until it opts in. */
  disabled?: boolean;
}

export function ToggleSwitch({
  checked,
  onChange,
  label,
  disabled = false,
}: ToggleSwitchProps): JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => {
        // Redundant with the native `disabled` above, which stops the click
        // ever being dispatched — that attribute is the real control, and it is
        // what the tests exercise. Kept only as defence against a programmatic
        // call; removing it alone changes no observable behaviour.
        if (!disabled) onChange(!checked);
      }}
      className={cn(
        'relative h-6 w-11 flex-none rounded-pill border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        'disabled:cursor-not-allowed disabled:opacity-50',
        // Off: a recessed track with a hairline, so it reads on white cards as
        // well as on black. On: the signal accent.
        checked
          ? 'border-[var(--accent)] bg-[var(--accent)]'
          : 'border-[var(--ui-line-strong)] bg-[var(--ui-inset-strong)]',
      )}
    >
      {/* The 1px border leaves a 42x22 inner box; the 20px thumb sits 1px in
          from each edge (top-px, left-px / left-[21px]). */}
      <span
        data-testid="toggle-thumb"
        className={cn(
          'absolute top-px h-5 w-5 rounded-full transition-all',
          // On: the thumb takes the primary's own foreground, because the
          // track is the accent and the primary is that same blue.
          checked ? 'left-[21px] bg-primary-foreground' : 'left-px bg-muted-foreground',
        )}
      />
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
    <div className={cn('inline-flex items-center gap-1 rounded-pill bg-[var(--ui-inset-strong)] p-1', className)} role="tablist">
      {tabs.map((t) => {
        const on = t.key === active;
        return (
          <button
            key={t.key}
            role="tab"
            aria-selected={on}
            onClick={() => onChange(t.key)}
            className={cn(
              'rounded-pill px-3.5 py-1.5 text-[13px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
              on ? 'bg-card text-foreground shadow-card' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
