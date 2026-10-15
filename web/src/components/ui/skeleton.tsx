import * as React from 'react';

import { cn } from '@/lib/utils';

function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('animate-pulse rounded-[10px] bg-[var(--ui-inset-strong)]', className)}
      {...props}
    />
  );
}

export { Skeleton };
