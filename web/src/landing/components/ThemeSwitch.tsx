import { Moon, Sun } from 'lucide-react'
import { cn } from '../lib/cn'
import type { LandingTheme } from '../lib/useLandingTheme'

/**
 * Two-position light/dark switch for the landing page. A segmented control
 * rather than a single toggling icon: the current theme is visible without the
 * reader having to work out whether the icon shows the state or the action.
 */
export function ThemeSwitch({
  theme,
  onChange,
  className,
}: {
  theme: LandingTheme
  onChange: (next: LandingTheme) => void
  className?: string
}) {
  const options: { id: LandingTheme; label: string; Icon: typeof Sun }[] = [
    { id: 'light', label: 'Light', Icon: Sun },
    { id: 'dark', label: 'Dark', Icon: Moon },
  ]

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className={cn(
        'flex items-center gap-0.5 rounded-pill border p-[3px]',
        className,
      )}
      style={{ borderColor: 'var(--lp-line)', background: 'var(--lp-chip)' }}
    >
      {options.map(({ id, label, Icon }) => {
        const active = theme === id
        return (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={label}
            title={`${label} theme`}
            data-testid={`landing-theme-${id}`}
            onClick={() => onChange(id)}
            className="inline-flex h-[26px] w-[30px] items-center justify-center rounded-pill transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--lp-accent)]"
            style={
              active
                ? { background: 'var(--lp-btn)', color: 'var(--lp-btn-text)' }
                : { color: 'var(--lp-text-muted)' }
            }
          >
            <Icon size={14} aria-hidden="true" />
          </button>
        )
      })}
    </div>
  )
}
