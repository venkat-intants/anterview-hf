import * as React from 'react';

import { cn } from '@/lib/utils';
import { controlSurfaceClass, controlTextClass } from './control-styles';

/**
 * Textarea: the multi-line counterpart of Input, with the same border, radius,
 * focus ring and invalid state. It has no fixed height: three lines by default
 * (pass `rows` for more), resizable vertically only so it never breaks the
 * column it sits in.
 */
export interface TextareaProps extends React.ComponentProps<'textarea'> {
  invalid?: boolean;
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, rows = 3, invalid, 'aria-invalid': ariaInvalid, ...props }, ref) => (
    <textarea
      ref={ref}
      rows={rows}
      aria-invalid={invalid ? true : ariaInvalid}
      className={cn(
        controlSurfaceClass,
        controlTextClass,
        'block min-h-[5rem] resize-y py-2 leading-relaxed',
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = 'Textarea';

export { Textarea };
