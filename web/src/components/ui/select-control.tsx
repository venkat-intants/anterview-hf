import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * SelectControl — the native `<select>` with the AntHire dropdown design.
 *
 * It is deliberately a thin wrapper: the look (40px / 36px, 10px radius, chevron,
 * focus ring, disabled, invalid, and the customizable open list where the browser
 * supports it) comes from the base layer in index.css, which styles EVERY `select`
 * — so a bare `<select>` and this component are visually identical. The component
 * exists to give new code one import with the knobs the base layer cannot express:
 *
 *  - `size`         'md' (40px, default) | 'sm' (36px, compact)
 *  - `invalid`      sets `aria-invalid` (the CSS draws the danger border from it)
 *  - `leadingIcon`  an icon inside the left edge of the control
 *
 * Everything else — ref, name, value/onChange, disabled, children, aria-* — passes
 * straight through, and it stays a native element so `userEvent.selectOptions`,
 * mobile pickers and screen readers all behave as they do for any `<select>`.
 */
export interface SelectControlProps extends Omit<
  React.SelectHTMLAttributes<HTMLSelectElement>,
  'size'
> {
  size?: 'md' | 'sm';
  invalid?: boolean;
  leadingIcon?: React.ReactNode;
  /** Classes for the wrapper that exists only when `leadingIcon` is set. */
  wrapperClassName?: string;
}

const SelectControl = React.forwardRef<HTMLSelectElement, SelectControlProps>(
  (
    {
      size = 'md',
      invalid,
      leadingIcon,
      className,
      wrapperClassName,
      'aria-invalid': ariaInvalid,
      children,
      ...props
    },
    ref,
  ) => {
    const select = (
      <select
        ref={ref}
        data-size={size === 'sm' ? 'sm' : undefined}
        aria-invalid={invalid ? true : ariaInvalid}
        className={cn('w-full', leadingIcon && 'ps-9', className)}
        {...props}
      >
        {children}
      </select>
    );

    if (!leadingIcon) return select;

    return (
      <span className={cn('relative inline-flex w-full items-center', wrapperClassName)}>
        <span
          aria-hidden="true"
          className="pointer-events-none absolute start-3 flex h-4 w-4 items-center justify-center text-[var(--ui-faint)] [&>svg]:h-4 [&>svg]:w-4"
        >
          {leadingIcon}
        </span>
        {select}
      </span>
    );
  },
);
SelectControl.displayName = 'SelectControl';

export { SelectControl, SelectControl as NativeSelect };
