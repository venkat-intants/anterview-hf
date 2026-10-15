import type { Variants } from 'framer-motion'

export type RevealKind = 'fade' | 'up'

/**
 * Scroll-in reveal: a short fade with a small upward settle. Played once and
 * kept small on purpose — sections should arrive, not perform. Reveal skips it
 * entirely under prefers-reduced-motion.
 */
export const reveal: Variants = {
  hidden: { opacity: 0, y: 14 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.45, ease: [0.2, 0.7, 0.2, 1] },
  },
}
