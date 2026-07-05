// PageTransition — wraps a route's content so every page enters with a
// consistent fade + subtle rise. Drop-in: wrap the top element a page returns.

import { motion } from 'framer-motion';
import type { ReactNode } from 'react';
import { EASE_OUT } from './variants';

interface PageTransitionProps {
  children: ReactNode;
  className?: string;
}

export default function PageTransition({ children, className }: PageTransitionProps) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: EASE_OUT }}
    >
      {children}
    </motion.div>
  );
}
