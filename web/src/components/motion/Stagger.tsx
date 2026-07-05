// Stagger / StaggerItem — choreograph a list so children cascade in.
// Wrap a group in <Stagger> and each direct child in <StaggerItem>.

import { motion } from 'framer-motion';
import type { ReactNode } from 'react';
import { staggerContainer, staggerItem } from './variants';

interface StaggerProps {
  children: ReactNode;
  className?: string;
  /** Seconds between each child (default 0.07). */
  stagger?: number;
  /** Delay before the first child (default 0). */
  delay?: number;
  /** Animate on scroll-into-view instead of on mount (default true). */
  onView?: boolean;
}

export function Stagger({
  children,
  className,
  stagger = 0.07,
  delay = 0,
  onView = true,
}: StaggerProps) {
  const viewProps = onView
    ? ({ whileInView: 'show', viewport: { once: true, margin: '0px 0px -8% 0px' } } as const)
    : ({ animate: 'show' } as const);

  return (
    <motion.div
      className={className}
      variants={staggerContainer(stagger, delay)}
      initial="hidden"
      {...viewProps}
    >
      {children}
    </motion.div>
  );
}

interface StaggerItemProps {
  children: ReactNode;
  className?: string;
}

export function StaggerItem({ children, className }: StaggerItemProps) {
  return (
    <motion.div className={className} variants={staggerItem}>
      {children}
    </motion.div>
  );
}
