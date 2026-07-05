import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
import { revealVariants, staggerParent, staggerChild, type RevealDir } from '../lib/motion';

interface RevealProps {
  children: ReactNode;
  /** direction the element travels in from */
  dir?: RevealDir;
  className?: string;
  /** delay in seconds */
  delay?: number;
  /** render once it enters, stay revealed */
  once?: boolean;
}

/** Scroll-reveal wrapper using framer-motion whileInView. */
export function Reveal({ children, dir = 'fade', className, delay = 0, once = true }: RevealProps): JSX.Element {
  return (
    <motion.div
      className={className}
      custom={dir}
      variants={revealVariants}
      initial="hidden"
      whileInView="show"
      viewport={{ once, amount: 0.25 }}
      transition={{ delay }}
    >
      {children}
    </motion.div>
  );
}

interface StaggerProps {
  children: ReactNode;
  className?: string;
}

/** Parent that staggers direct <Stagger.Item> children. */
export function Stagger({ children, className }: StaggerProps): JSX.Element {
  return (
    <motion.div
      className={className}
      variants={staggerParent}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, amount: 0.2 }}
    >
      {children}
    </motion.div>
  );
}

export function StaggerItem({ children, className }: StaggerProps): JSX.Element {
  return (
    <motion.div className={className} variants={staggerChild}>
      {children}
    </motion.div>
  );
}
