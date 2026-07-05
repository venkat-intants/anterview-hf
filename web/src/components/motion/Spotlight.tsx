// Spotlight — a pointer-following radial glow that lights up a surface on hover.
// Wrap any card/panel; the glow tracks the cursor over the element. Pure CSS
// radial-gradient driven by two motion values, so it's cheap.

import { useRef } from 'react';
import { motion, useMotionTemplate, useMotionValue } from 'framer-motion';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface SpotlightProps {
  children: ReactNode;
  className?: string;
  /** Glow color (CSS color). Defaults to the focus-ring blue. */
  color?: string;
  /** Glow radius in px (default 320). */
  size?: number;
}

export default function Spotlight({
  children,
  className,
  color = 'hsl(var(--ring) / 0.18)',
  size = 320,
}: SpotlightProps) {
  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  const ref = useRef<HTMLDivElement>(null);
  const background = useMotionTemplate`radial-gradient(${size}px circle at ${mx}px ${my}px, ${color}, transparent 65%)`;

  function onMove(e: React.MouseEvent<HTMLDivElement>) {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    mx.set(e.clientX - rect.left);
    my.set(e.clientY - rect.top);
  }

  return (
    <div
      ref={ref}
      onMouseMove={onMove}
      className={cn('group relative overflow-hidden', className)}
    >
      <motion.div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100"
        style={{ background }}
      />
      {children}
    </div>
  );
}
