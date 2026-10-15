import { Moon, Sun } from 'lucide-react'
import { cn } from '../lib/cn'
import type { LandingTheme } from '../lib/useLandingTheme'

/**
 * Two-position light/dark switch for the landing page. A segmented control
 * rather than a single toggling icon: the current theme is visible without the
 * reader having to work out whether the icon shows the state or the action.
 *
 * `testIdPrefix` lets the nav render a second copy inside the mobile menu
 * without two elements sharing one data-testid.
 */
export function ThemeSwitch({
  theme,
  onChange,
  className,
  testIdPrefix = 'landing-theme',
}: {
  theme: LandingTheme
  onChange: (next: LandingTheme) => void
  className?: string
  testIdPrefix?: string
}) {
  const options: { id: LandingTheme; label: string; Icon: typeof Sun }[] = [
    { id: 'light', label: 'Light', Icon: Sun },
    { id: 'dark', label: 'Dark', Icon: Moon },
  ]

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className={cn('flex items-center gap-0.5 rounded-pill border border-[var(--lp-line)] bg-[var(--lp-surface-alt)] p-[3px]', className)}
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
            data-testid={`${testIdPrefix}-${id}`}
            onClick={() => onChange(id)}
            className={cn(
              'inline-flex h-8 w-9 items-center justify-center rounded-pill transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--lp-accent)]',
              active ? 'bg-[var(--lp-btn)] text-[var(--lp-btn-text)]' : 'text-[var(--lp-text-muted)] hover:text-[var(--lp-text)]',
            )}
          >
            <Icon size={15} aria-hidden="true" />
          </button>
        )
      })}
    </div>
  )
}
