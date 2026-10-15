/* Shared surface language for every floating list (menus, popovers, the Radix
 * Select list) — and the customizable native select in index.css matches it:
 * card colour, 12px radius, hairline, elevated shadow, 4px padding, 150ms entrance
 * (index.css turns the animation off under prefers-reduced-motion). */
export const menuSurfaceClass =
  'z-50 min-w-[8rem] overflow-hidden rounded-[12px] border border-[var(--ui-line-strong)] bg-popover p-1 text-popover-foreground shadow-elevated outline-none duration-150 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-1 data-[side=left]:slide-in-from-right-1 data-[side=right]:slide-in-from-left-1 data-[side=top]:slide-in-from-bottom-1';

/* A 36px row with an 8px radius; the highlight wash is shared by hover and
 * keyboard focus (Radix moves DOM focus and sets data-highlighted). */
export const menuItemClass =
  'relative flex min-h-9 cursor-default select-none items-center gap-2.5 rounded-[8px] px-2.5 py-1.5 text-[14px] outline-none transition-colors data-[highlighted]:bg-[var(--ui-inset-strong)] data-[highlighted]:text-foreground data-[state=open]:bg-[var(--ui-inset)] data-[disabled]:pointer-events-none data-[disabled]:opacity-45 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-[var(--ui-faint)]';

/* The destructive tone reads the danger token, never a raw red. */
export const menuDestructiveClass =
  'text-[var(--ui-danger)] data-[highlighted]:bg-[var(--ui-danger-wash)] data-[highlighted]:text-[var(--ui-danger)] [&_svg]:text-[var(--ui-danger)]';
