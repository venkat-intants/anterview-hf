import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { BrandMark } from '../../components/primitives'
import { CONTACT_EMAIL, mailto } from '../../data/landing'

type FooterLink = { label: string; href: string } | { label: string; to: string }

// Labels and headings are i18n keys (audit CA-M16).

const COLS: { h: string; links: FooterLink[] }[] = [
  {
    h: 'lp.footer.product',
    links: [
      { label: 'lp.nav.how', href: '#how' },
      { label: 'lp.nav.candidates', href: '#candidates' },
      { label: 'lp.footer.interviewers', href: '#avatars' },
      { label: 'lp.nav.languages', href: '#languages' },
    ],
  },
  {
    h: 'lp.nav.teams',
    links: [
      { label: 'lp.footer.growing', href: '#audience' },
      { label: 'lp.footer.talent', href: '#audience' },
      { label: 'lp.footer.managers', href: '#audience' },
      { label: 'lp.hero.demo', href: mailto('AntHire demo request') },
    ],
  },
  {
    h: 'lp.footer.account',
    links: [
      { label: 'lp.nav.signIn', to: '/login' },
      { label: 'lp.nav.signUp', to: '/register' },
      { label: 'lp.footer.consent', href: '#privacy' },
      { label: 'lp.nav.faq', href: '#faq' },
    ],
  },
]

const linkClass =
  'inline-flex min-h-[32px] items-center rounded text-sm text-[var(--lp-text-muted)] transition-colors hover:text-[var(--lp-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--lp-accent)]'

export function Footer() {
  const { t } = useTranslation()
  return (
    <footer className="border-t border-[var(--lp-line)] bg-[var(--lp-surface-alt)]">
      <div className="mx-auto max-w-[1200px] px-4 pb-10 pt-14 sm:px-6">
        <div className="mb-12 grid grid-cols-2 gap-10 md:grid-cols-[1.4fr_1fr_1fr_1fr]">
          <div className="col-span-2 md:col-span-1">
            <div className="mb-4 flex items-center gap-2.5">
              <BrandMark />
              <span className="font-display text-[17px] font-bold tracking-[-0.4px] text-[var(--lp-text)]">AntHire</span>
            </div>
            <p className="mb-4 max-w-[280px] text-sm leading-relaxed text-[var(--lp-text-muted)]">
              {t('lp.footer.tagline')}
            </p>
            <a href={`mailto:${CONTACT_EMAIL}`} className={linkClass}>{CONTACT_EMAIL}</a>
          </div>
          {COLS.map((c) => (
            <nav key={c.h} aria-label={t(c.h)}>
              <h2 className="mb-3 font-sans text-sm font-semibold text-[var(--lp-text)]">{t(c.h)}</h2>
              <ul className="flex flex-col gap-1">
                {c.links.map((l) => (
                  <li key={l.label}>
                    {'to' in l ? (
                      <Link to={l.to} className={linkClass}>{t(l.label)}</Link>
                    ) : (
                      <a href={l.href} className={linkClass}>{t(l.label)}</a>
                    )}
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--lp-line)] pt-6 text-sm text-[var(--lp-text-muted)]">
          <span>© 2026 Intants Private Limited</span>
          <span>AntHire</span>
        </div>
      </div>
    </footer>
  )
}
