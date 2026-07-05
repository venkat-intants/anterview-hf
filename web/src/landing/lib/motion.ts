import type { Variants } from 'framer-motion'

export type RevealKind = 'fade' | 'left' | 'right' | 'zoom'

/**
 * Scroll-in reveal variants — fade / slide-left / slide-right / zoom.
 * Use with framer-motion: initial="hidden", whileInView="show", custom={kind}.
 * Bidirectional + smooth; pair with viewport={{ amount: 0.3 }}.
 */
export const reveal: Variants = {
  hidden: (kind: RevealKind = 'fade') => ({
    opacity: 0,
    y: kind === 'fade' ? 46 : 0,
    x: kind === 'left' ? -90 : kind === 'right' ? 90 : 0,
    scale: kind === 'zoom' ? 0.88 : 1,
  }),
  show: {
    opacity: 1,
    x: 0,
    y: 0,
    scale: 1,
    transition: { duration: 0.6, ease: [0.2, 0.7, 0.2, 1] },
  },
}

/** Container that staggers its direct motion children. */
export const stagger: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.08, delayChildren: 0.05 } },
}

/** Cycle reveal kinds so adjacent elements animate differently. */
export const revealKinds: RevealKind[] = ['fade', 'left', 'right', 'zoom']
export const kindFor = (i: number): RevealKind => revealKinds[i % revealKinds.length]
