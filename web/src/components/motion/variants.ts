// Shared Framer Motion variants for the futuristic redesign.
// Centralised so every page animates with the same rhythm and easing.

import type { Variants, Transition } from 'framer-motion';

// Signature easing — a soft "ease-out-expo" that feels expensive.
export const EASE_OUT = [0.22, 1, 0.36, 1] as const;

export const springSoft: Transition = { type: 'spring', stiffness: 320, damping: 30 };
export const springSnappy: Transition = { type: 'spring', stiffness: 480, damping: 26 };

export const fadeInUp: Variants = {
  hidden: { opacity: 0, y: 18 },
  show: { opacity: 1, y: 0, transition: { duration: 0.55, ease: EASE_OUT } },
};

export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.5, ease: EASE_OUT } },
};

export const scaleIn: Variants = {
  hidden: { opacity: 0, scale: 0.96 },
  show: { opacity: 1, scale: 1, transition: { duration: 0.4, ease: EASE_OUT } },
};

// Container that staggers its children (use with StaggerItem).
export const staggerContainer = (stagger = 0.07, delay = 0): Variants => ({
  hidden: {},
  show: {
    transition: { staggerChildren: stagger, delayChildren: delay },
  },
});

export const staggerItem: Variants = fadeInUp;
