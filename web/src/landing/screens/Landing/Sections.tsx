import { useTranslation } from 'react-i18next'
import { PROOF } from '../../data/landing'

/** A short band of facts about the product today. No customer numbers: there
 *  are none we can stand behind, so these are things the product does. */
export function ProofBand() {
  const { t } = useTranslation()
  return (
    <section aria-label={t('lp.proof.aria')} className="mx-auto max-w-[1200px] px-4 sm:px-6">
      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-card border border-[var(--lp-line)] bg-[var(--lp-line)] shadow-card md:grid-cols-4">
        {PROOF.map((p) => (
          <div key={p.labelKey} className="bg-[var(--lp-surface)] px-5 py-7 text-center sm:px-6">
            <dt className="font-display text-[clamp(28px,4vw,40px)] font-extrabold leading-none tracking-[-0.03em] text-[var(--lp-text)]">{p.value}</dt>
            <dd className="mx-auto mt-2 max-w-[200px] text-sm leading-snug text-[var(--lp-text-muted)]">{t(p.labelKey)}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
