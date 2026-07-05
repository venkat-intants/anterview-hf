import type { Variants, Transition } from 'framer-motion';

export type RevealDir = 'fade' | 'left' | 'right' | 'zoom';

const EASE: Transition['ease'] = [0.2, 0.7, 0.2, 1];

/** whileInView reveal — pass the direction via the `custom` prop. */
export const revealVariants: Variants = {
  hidden: (dir: RevealDir = 'fade') => ({
    opacity: 0,
    y: dir === 'fade' ? 46 : 0,
    x: dir === 'left' ? -90 : dir === 'right' ? 90 : 0,
    scale: dir === 'zoom' ? 0.88 : 1,
  }),
  show: {
    opacity: 1,
    x: 0,
    y: 0,
    scale: 1,
    transition: { duration: 0.6, ease: EASE },
  },
};

/** Parent that staggers its children's reveal. */
export const staggerParent: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.08, delayChildren: 0.05 } },
};

/** Child used inside a staggerParent. */
export const staggerChild: Variants = {
  hidden: { opacity: 0, y: 28 },
  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: EASE } },
};

export const springSoft: Transition = { type: 'spring', stiffness: 260, damping: 26 };
