import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Plus } from 'lucide-react'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '../../lib/cn'
import { Reveal } from '../../components/Reveal'
import { Card, SectionHeading } from '../../components/primitives'
import { PRIVACY, FAQS, mailto } from '../../data/landing'

/** What the product does about consent and oversight. Deliberately stops short
 *  of compliance or residency claims: it states behaviour, and points an
 *  evaluating institution to us for the written detail. */
export function Privacy() {
  const { t } = useTranslation()
  return (
    <section id="privacy" aria-labelledby="privacy-title" className="mx-auto max-w-[1200px] scroll-mt-20 px-4 py-12 sm:px-6 sm:py-16">
      <SectionHeading
        id="privacy-title"
        eyebrow={t('lp.priv.eyebrow')}
        title={t('lp.priv.title')}
        lede={t('lp.priv.lede')}
      />
      <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {PRIVACY.map((c) => {
          const Icon = c.icon
          return (
            <li key={c.titleKey}>
              <Card className="h-full p-6">
                <Icon size={22} className="mb-4 text-[var(--ui-info)]" aria-hidden="true" />
                <h3 className="mb-1.5 text-base font-bold text-[var(--lp-text)]">{t(c.titleKey)}</h3>
                <p className="text-sm leading-relaxed text-[var(--lp-text-muted)]">{t(c.descKey)}</p>
              </Card>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

export function FAQ() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(-1)
  return (
    <section id="faq" aria-labelledby="faq-title" className="mx-auto max-w-[780px] scroll-mt-20 px-4 py-20 sm:px-6 sm:py-24">
      <SectionHeading id="faq-title" eyebrow={t('lp.faq.eyebrow')} title={t('lp.faq.title')} />
      <div className="flex flex-col gap-3">
        {FAQS.map(([q, a], i) => {
          const isOpen = open === i
          return (
            <Card key={q} className={cn('overflow-hidden transition-colors', isOpen && 'border-[var(--lp-line-strong)]')}>
              <h3>
                <button
                  type="button"
                  aria-expanded={isOpen}
                  aria-controls={`faq-panel-${i}`}
                  id={`faq-button-${i}`}
                  onClick={() => setOpen(isOpen ? -1 : i)}
                  className="flex min-h-[56px] w-full items-center justify-between gap-4 px-5 py-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--lp-accent)] sm:px-6"
                >
                  <span className="font-sans text-base font-semibold text-[var(--lp-text)]">{t(q)}</span>
                  <Plus
                    size={20}
                    aria-hidden="true"
                    className={cn('flex-none text-[var(--ui-info)] transition-transform motion-reduce:transition-none', isOpen && 'rotate-45')}
                  />
                </button>
              </h3>
              <div id={`faq-panel-${i}`} role="region" aria-labelledby={`faq-button-${i}`} hidden={!isOpen}>
                <p className="px-5 pb-5 text-[15px] leading-relaxed text-[var(--lp-text-muted)] sm:px-6">{t(a)}</p>
              </div>
            </Card>
          )
        })}
      </div>
    </section>
  )
}

/** The tagline band: the page's one brand-surface card, with a white call to
 *  action so it reads against the blue. The hero above stays on the canvas. */
export function FinalCTA() {
  const { t } = useTranslation()
  return (
    <section id="cta" aria-labelledby="cta-title" className="mx-auto max-w-[1200px] px-4 pb-20 sm:px-6 sm:pb-24">
      <Reveal>
        <div className="rounded-card bg-[image:var(--ui-grad-brand)] px-6 py-16 text-center sm:px-12 sm:py-20">
          <h2 id="cta-title" className="mx-auto max-w-[640px] text-[clamp(30px,5vw,48px)] font-extrabold leading-[1.08] tracking-[-0.03em] text-[var(--ui-brand-ink)]">
            {t('lp.cta.title')}
          </h2>
          <p className="mx-auto mt-4 max-w-[480px] text-base leading-relaxed text-white/80">
            {t('lp.cta.lede')}
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <Link to="/register" className={cn(buttonVariants({ size: 'lg' }), 'h-12 bg-[var(--ui-brand-ink)] px-8 text-base text-[#0b1f4d] hover:bg-white/90')}>
              {t('lp.hero.candidateSignUp')}
            </Link>
            <a
              href={mailto('AntHire demo request')}
              className={cn(buttonVariants({ size: 'lg' }), 'h-12 border border-white/40 bg-transparent px-8 text-base text-[var(--ui-brand-ink)] hover:bg-white/10')}
            >
              {t('lp.hero.demo')}
            </a>
          </div>
        </div>
      </Reveal>
    </section>
  )
}
