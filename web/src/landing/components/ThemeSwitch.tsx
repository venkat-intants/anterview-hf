import { Moon, Sun } from 'lucide-react'
import { useTranslation } from 'react-i18next'
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
  const { t } = useTranslation()
  const options: { id: LandingTheme; label: string; title: string; Icon: typeof Sun }[] = [
    { id: 'light', label: t('lp.theme.light'), title: t('lp.theme.lightTitle'), Icon: Sun },
    { id: 'dark', label: t('lp.theme.dark'), title: t('lp.theme.darkTitle'), Icon: Moon },
  ]

  return (
    <div
      role="radiogroup"
      aria-label={t('lp.theme.group')}
      className={cn('flex items-center gap-0.5 rounded-pill border border-[var(--lp-line)] bg-[var(--lp-surface-alt)] p-[3px]', className)}
    >
      {options.map(({ id, label, title, Icon }) => {
        const active = theme === id
        return (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={label}
            title={title}
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
