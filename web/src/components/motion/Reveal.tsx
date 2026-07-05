// Reveal — scroll-triggered entrance. Children fade + rise into view once,
// when they scroll into the viewport. The workhorse of the redesign: wrap any
// section/card in <Reveal> to give the page a choreographed feel.

import { motion } from 'framer-motion';
import type { ReactNode } from 'react';
import { EASE_OUT } from './variants';

interface RevealProps {
  children: ReactNode;
  /** Stagger offset (s) — handy when mapping a list of <Reveal>s by index. */
  delay?: number;
  /** Travel distance in px before settling (default 18). */
  y?: number;
  className?: string;
  /** Re-run the animation every time it enters view (default false = once). */
  repeat?: boolean;
  as?: 'div' | 'section' | 'li' | 'article';
}

export default function Reveal({
  children,
  delay = 0,
  y = 18,
  className,
  repeat = false,
  as = 'div',
}: RevealProps) {
  const MotionTag = motion[as];
  return (
    <MotionTag
      className={className}
      initial={{ opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: !repeat, margin: '0px 0px -10% 0px' }}
      transition={{ duration: 0.55, ease: EASE_OUT, delay }}
    >
      {children}
    </MotionTag>
  );
}
