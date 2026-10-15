import { Reveal } from '../../components/Reveal'
import { SectionHeading } from '../../components/primitives'
import { ShotFrame } from '../../components/ShotFrame'
import { cn } from '../../lib/cn'
import { SHOTS, WALKTHROUGH } from '../../data/landing'

/** "See it work": three real screens of the product, one per step. The text and
 *  the picture swap sides on desktop and stack on a phone. Every screenshot is
 *  of the app in demo mode with invented data. */
export function Walkthrough() {
  return (
    <section id="how" aria-labelledby="how-title" className="mx-auto max-w-[1200px] scroll-mt-20 px-4 py-20 sm:px-6 sm:py-24">
      <SectionHeading
        id="how-title"
        eyebrow="See it work"
        title="From role to scorecard in three steps."
        lede="These are the real screens, shown in demo mode with invented data."
      />
      <ol className="flex flex-col gap-14 sm:gap-20">
        {WALKTHROUGH.map((step, i) => {
          const shot = SHOTS[step.shot]
          const flip = i % 2 === 1
          return (
            <li key={step.title}>
              <Reveal>
                <div className={cn('grid grid-cols-1 items-center gap-8 lg:gap-14', flip ? 'lg:grid-cols-[1.35fr_0.65fr]' : 'lg:grid-cols-[0.65fr_1.35fr]')}>
                  <div className={cn(flip && 'lg:order-2')}>
                    <span className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-[var(--ui-info-wash)] font-display text-sm font-bold text-[var(--ui-info)]">
                      {i + 1}
                    </span>
                    <h3 className="mt-4 text-[clamp(22px,3vw,30px)] font-bold leading-tight tracking-[-0.02em] text-[var(--lp-text)]">{step.title}</h3>
                    <p className="mt-3 max-w-[460px] text-base leading-relaxed text-[var(--lp-text-muted)]">{step.desc}</p>
                  </div>
                  <div className={cn('flex justify-center rounded-[24px] bg-[var(--lp-surface-alt)] p-2.5 sm:p-8', flip && 'lg:order-1')}>
                    <ShotFrame shot={shot} className="w-full" style={{ maxWidth: step.maxWidth }} />
                  </div>
                </div>
              </Reveal>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
