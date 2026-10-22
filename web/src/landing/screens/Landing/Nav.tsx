import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Menu, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import LanguageSwitcher from '@/components/LanguageSwitcher'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '../../lib/cn'
import { NAV_LINKS } from '../../data/landing'
import { BrandMark } from '../../components/primitives'
import { ThemeSwitch } from '../../components/ThemeSwitch'
import type { LandingTheme } from '../../lib/useLandingTheme'

const linkClass =
  'rounded-pill px-3 py-2 text-sm font-medium text-[var(--lp-text-muted)] transition-colors hover:text-[var(--lp-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--lp-accent)]'

export function Nav({ theme, onThemeChange }: { theme: LandingTheme; onThemeChange: (next: LandingTheme) => void }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const toggleRef = useRef<HTMLButtonElement>(null)

  // Escape closes the menu and returns focus to the button that opened it.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        toggleRef.current?.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <header className="sticky top-0 z-50 border-b border-[var(--lp-line)] bg-[var(--lp-canvas)]">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-[60] focus:rounded-pill focus:bg-[var(--lp-btn)] focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-[var(--lp-btn-text)]"
      >
        {t('lp.nav.skip')}
      </a>
      <nav aria-label={t('lp.nav.mainAria')} className="mx-auto flex h-16 max-w-[1200px] items-center justify-between gap-3 px-4 sm:px-6">
        <a
          href="#top"
          aria-label={t('lp.nav.backToTop')}
          className="flex items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--lp-accent)]"
        >
          <BrandMark />
          <span className="font-display text-[17px] font-bold tracking-[-0.4px] text-[var(--lp-text)]">AntHire</span>
        </a>

        <ul className="hidden items-center gap-1 lg:flex">
          {NAV_LINKS.map((l) => (
            <li key={l.href}>
              <a href={l.href} className={linkClass}>{t(l.labelKey)}</a>
            </li>
          ))}
        </ul>

        <div className="flex items-center gap-2">
          {/* The page is read in EN/HI/TE like every other public page (audit CA-M16). */}
          <LanguageSwitcher />
          <ThemeSwitch theme={theme} onChange={onThemeChange} className="hidden sm:flex" />
          <Link to="/login" className={cn(linkClass, 'hidden h-10 items-center sm:inline-flex')}>{t('lp.nav.signIn')}</Link>
          <Link to="/register" className={buttonVariants({ size: 'default' })}>{t('lp.nav.signUp')}</Link>
          <button
            ref={toggleRef}
            type="button"
            aria-expanded={open}
            aria-controls="landing-mobile-menu"
            aria-label={open ? t('lp.nav.closeMenu') : t('lp.nav.openMenu')}
            onClick={() => setOpen((o) => !o)}
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-[var(--lp-line)] bg-[var(--lp-surface)] text-[var(--lp-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--lp-accent)] lg:hidden"
          >
            {open ? <X size={18} aria-hidden="true" /> : <Menu size={18} aria-hidden="true" />}
          </button>
        </div>
      </nav>

      {open && (
        <div id="landing-mobile-menu" className="border-t border-[var(--lp-line)] bg-[var(--lp-canvas)] px-4 pb-5 pt-2 shadow-elevated sm:px-6 lg:hidden">
          <ul className="flex flex-col">
            {NAV_LINKS.map((l) => (
              <li key={l.href}>
                <a
                  href={l.href}
                  onClick={() => setOpen(false)}
                  className="flex min-h-[44px] items-center rounded-lg px-1 text-base font-medium text-[var(--lp-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--lp-accent)]"
                >
                  {t(l.labelKey)}
                </a>
              </li>
            ))}
            <li className="sm:hidden">
              <Link
                to="/login"
                className="flex min-h-[44px] items-center rounded-lg px-1 text-base font-medium text-[var(--lp-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--lp-accent)]"
              >
                {t('lp.nav.signIn')}
              </Link>
            </li>
          </ul>
          <div className="mt-3 flex items-center justify-between sm:hidden">
            <span className="text-sm text-[var(--lp-text-muted)]">{t('lp.nav.appearance')}</span>
            <ThemeSwitch theme={theme} onChange={onThemeChange} testIdPrefix="landing-menu-theme" />
          </div>
        </div>
      )}
    </header>
  )
}
