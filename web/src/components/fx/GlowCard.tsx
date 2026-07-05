// GlowCard — the redesign's default surface: a Card that lifts and lights up on
// hover, with an optional cursor-following spotlight. Backward-compatible with
// the existing Card API (forwards className + children), so pages can swap
// <Card> → <GlowCard> incrementally.

import { forwardRef } from 'react';
import type { ReactNode } from 'react';
import { Card } from '@/components/ui/card';
import Spotlight from '@/components/motion/Spotlight';
import { cn } from '@/lib/utils';

interface GlowCardProps {
  children: ReactNode;
  className?: string;
  /** Enable the cursor-following spotlight glow (default true). */
  spotlight?: boolean;
  /** Hover-lift + ring-glow elevation (default true). */
  interactive?: boolean;
}

const GlowCard = forwardRef<HTMLDivElement, GlowCardProps>(
  ({ children, className, spotlight = true, interactive = true }, ref) => {
    const card = (
      <Card
        ref={ref}
        className={cn(
          'relative',
          interactive &&
            'lift hover:border-ring/40 hover:shadow-glow-soft',
          className,
        )}
      >
        {children}
      </Card>
    );

    if (!spotlight) return card;

    return (
      <Spotlight className="rounded-[28px]">
        {card}
      </Spotlight>
    );
  },
);
GlowCard.displayName = 'GlowCard';

export default GlowCard;
