import { Link } from 'react-router-dom'
import { BrandMark } from '../../components/primitives'
import { CONTACT_EMAIL, mailto } from '../../data/landing'

type FooterLink = { label: string; href: string } | { label: string; to: string }

const COLS: { h: string; links: FooterLink[] }[] = [
  {
    h: 'Product',
    links: [
      { label: 'How it works', href: '#how' },
      { label: 'For candidates', href: '#candidates' },
      { label: 'Interviewers', href: '#avatars' },
      { label: 'Languages', href: '#languages' },
    ],
  },
  {
    h: 'For hiring teams',
    links: [
      { label: 'Growing companies', href: '#audience' },
      { label: 'Talent acquisition', href: '#audience' },
      { label: 'Hiring managers', href: '#audience' },
      { label: 'Book a demo', href: mailto('AntHire demo request') },
    ],
  },
  {
    h: 'Account',
    links: [
      { label: 'Log in', to: '/login' },
      { label: 'Sign up', to: '/register' },
      { label: 'Consent and oversight', href: '#privacy' },
      { label: 'FAQ', href: '#faq' },
    ],
  },
]

const linkClass =
  'inline-flex min-h-[32px] items-center rounded text-sm text-[var(--lp-text-muted)] transition-colors hover:text-[var(--lp-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--lp-accent)]'

export function Footer() {
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
              Voice-first AI interviews in English, Hindi and Telugu.
            </p>
            <a href={`mailto:${CONTACT_EMAIL}`} className={linkClass}>{CONTACT_EMAIL}</a>
          </div>
          {COLS.map((c) => (
            <nav key={c.h} aria-label={c.h}>
              <h2 className="mb-3 font-sans text-sm font-semibold text-[var(--lp-text)]">{c.h}</h2>
              <ul className="flex flex-col gap-1">
                {c.links.map((l) => (
                  <li key={l.label}>
                    {'to' in l ? (
                      <Link to={l.to} className={linkClass}>{l.label}</Link>
                    ) : (
                      <a href={l.href} className={linkClass}>{l.label}</a>
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
