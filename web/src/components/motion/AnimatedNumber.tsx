// AnimatedNumber — counts up to a target value when scrolled into view.
// Perfect for stat tiles / KPIs on dashboards. Respects reduced-motion (it
// snaps to the final value via Framer's MotionConfig when the user opts out).

import { useEffect, useRef } from 'react';
import {
  animate,
  useInView,
  useMotionValue,
  useReducedMotion,
  useTransform,
  motion,
} from 'framer-motion';

interface AnimatedNumberProps {
  value: number;
  /** Decimal places to render (default 0). */
  decimals?: number;
  /** Count-up duration in seconds (default 1.2). */
  duration?: number;
  prefix?: string;
  suffix?: string;
  className?: string;
}

export default function AnimatedNumber({
  value,
  decimals = 0,
  duration = 1.2,
  prefix = '',
  suffix = '',
  className,
}: AnimatedNumberProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: '0px 0px -10% 0px' });
  const reduce = useReducedMotion();
  const mv = useMotionValue(0);
  const rounded = useTransform(mv, (v) =>
    `${prefix}${v.toFixed(decimals)}${suffix}`,
  );

  useEffect(() => {
    if (!inView) return;
    if (reduce) {
      mv.set(value);
      return;
    }
    const controls = animate(mv, value, { duration, ease: [0.22, 1, 0.36, 1] });
    return () => controls.stop();
  }, [inView, value, duration, reduce, mv]);

  return (
    <span ref={ref} className={className}>
      <motion.span>{rounded}</motion.span>
    </span>
  );
}
