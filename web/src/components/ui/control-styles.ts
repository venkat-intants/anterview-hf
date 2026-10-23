// One look for every form control: Input, Textarea, SelectControl (whose look
// comes from the base layer in index.css) and the Radix SelectTrigger.
//
//   height 40px (`md`, the default) or 36px (`sm`, toolbars and dense rows)
//   radius 10px · 1px --input border, stronger on hover · card fill
//   accent focus ring · disabled and aria-invalid states
//
// A textarea has no fixed height; it starts at three lines and grows.
// See docs/UI-CONVENTIONS.md, "Forms". These are exported so a control that
// cannot be one of the components (a bare <input> inside a composite widget)
// still matches; a call site does not restate the classes by hand.

export type ControlSize = 'md' | 'sm';

/** Tailwind height per control size. 40px matches Button's default height. */
export const CONTROL_HEIGHT: Record<ControlSize, string> = {
  md: 'h-10',
  sm: 'h-9',
};

/** The surface shared by Input and Textarea (and anything styled like them). */
export const controlSurfaceClass =
  'w-full rounded-[10px] border border-input bg-card px-3 text-foreground ' +
  'placeholder:text-[var(--ui-faint)] transition-[border-color,box-shadow,background-color] duration-150 ' +
  'hover:border-[var(--ui-line-strong)] focus-visible:outline-none focus-visible:border-[var(--accent)] ' +
  'focus-visible:shadow-[0_0_0_3px_rgba(var(--accent-rgb),0.2)] ' +
  'disabled:cursor-not-allowed disabled:bg-muted disabled:opacity-55 ' +
  'aria-[invalid=true]:border-[var(--ui-danger)] aria-[invalid=true]:focus-visible:shadow-[0_0_0_3px_var(--ui-danger-wash)]';

/** 16px on phones (iOS zooms into anything smaller on focus), 14px from md up. */
export const controlTextClass = 'text-base md:text-sm';

/** The label above a control. */
export const fieldLabelClass = 'text-[13px] font-semibold text-foreground';

/** Help text under a control. */
export const fieldHintClass = 'text-[12.5px] text-muted-foreground';

/** A validation message under a control. */
export const fieldErrorClass = 'text-[12.5px] font-medium text-[var(--ui-danger)]';
