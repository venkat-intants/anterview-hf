// Tilt — subtle 3D pointer-parallax. The element rotates toward the cursor on
// hover and springs back on leave. Used for hero/feature tiles. Keep the max
// angle small (default 6deg) so it reads as "premium", not gimmicky.

import { useRef } from 'react';
import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface TiltProps {
  children: ReactNode;
  className?: string;
  /** Max rotation in degrees (default 6). */
  max?: number;
}

export default function Tilt({ children, className, max = 6 }: TiltProps) {
  const ref = useRef<HTMLDivElement>(null);
  const px = useMotionValue(0.5);
  const py = useMotionValue(0.5);
  const sx = useSpring(px, { stiffness: 250, damping: 22 });
  const sy = useSpring(py, { stiffness: 250, damping: 22 });
  const rotateX = useTransform(sy, [0, 1], [max, -max]);
  const rotateY = useTransform(sx, [0, 1], [-max, max]);

  function onMove(e: React.MouseEvent<HTMLDivElement>) {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    px.set((e.clientX - rect.left) / rect.width);
    py.set((e.clientY - rect.top) / rect.height);
  }
  function reset() {
    px.set(0.5);
    py.set(0.5);
  }

  return (
    <motion.div
      ref={ref}
      onMouseMove={onMove}
      onMouseLeave={reset}
      style={{ rotateX, rotateY, transformPerspective: 900 }}
      className={cn('[transform-style:preserve-3d]', className)}
    >
      {children}
    </motion.div>
  );
}
