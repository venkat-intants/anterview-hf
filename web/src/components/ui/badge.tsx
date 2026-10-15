import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground',
        secondary: 'border-transparent bg-secondary text-secondary-foreground',
        destructive: 'border-transparent bg-[var(--ui-danger-wash)] text-[var(--ui-danger)]',
        outline: 'border-border text-foreground',
        // Soft tinted status pills on the per-mode status tokens, so the text
        // holds contrast on white as well as on ink — used by the scorecard
        // score bands, HR pipeline stages, exam pass/fail, etc.
        success: 'border-transparent bg-[var(--ui-ok-wash)] text-[var(--ui-ok)]',
        accent: 'border-transparent bg-[var(--ui-info-wash)] text-[var(--ui-info)]',
        warning: 'border-transparent bg-[var(--ui-warn-wash)] text-[var(--ui-warn)]',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
