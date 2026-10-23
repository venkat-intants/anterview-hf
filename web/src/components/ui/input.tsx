import * as React from 'react';

import { cn } from '@/lib/utils';
import {
  CONTROL_HEIGHT,
  controlSurfaceClass,
  controlTextClass,
  type ControlSize,
} from './control-styles';

/**
 * Input: the one text input. 40px tall (`size="sm"` is 36px), the same height,
 * radius, border and focus ring as SelectControl and Button, so a row of mixed
 * controls lines up. `invalid` sets `aria-invalid`, which draws the danger
 * border. Everything else (ref, type, value/onChange, aria-*) passes through.
 *
 * Checkbox, radio, range and file inputs are not text inputs and stay native.
 */
export interface InputProps extends Omit<React.ComponentProps<'input'>, 'size'> {
  size?: ControlSize;
  invalid?: boolean;
}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, size = 'md', invalid, 'aria-invalid': ariaInvalid, ...props }, ref) => {
    return (
      <input
        type={type}
        data-size={size === 'sm' ? 'sm' : undefined}
        aria-invalid={invalid ? true : ariaInvalid}
        className={cn(
          controlSurfaceClass,
          controlTextClass,
          CONTROL_HEIGHT[size],
          'py-0 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground',
          className,
        )}
        ref={ref}
        {...props}
      />
    );
  },
);
Input.displayName = 'Input';

export { Input };
