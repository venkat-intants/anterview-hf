// NumberField — a number setting that saves once, when the person is done.
//
// HR-H2. The workflow builder's number fields used to PATCH on every
// keystroke, with the input's value read straight back from the server cache.
// React snapped the field back to the old value until each response landed, so
// fast typing dropped characters; responses could land out of order; and every
// intermediate value reached the server ("1" on the way to "15", "150" in a
// 0-100 field), earning a 422 or, worse, being saved. Some fields could not
// even be cleared to retype.
//
// This keeps a local draft while the field is being edited and commits it on
// blur or Enter, after checking the range here. Escape puts the saved value
// back. A value the server changes underneath (another tab, a refetch) shows
// up as long as the person is not mid-edit.
//
// A save the server refuses leaves the saved value as it was, so nothing
// above would notice and the field would keep showing the refused number as
// if it had been kept. The caller bumps `resetKey` when a save fails, and the
// field then shows the saved value again (unless the person is already typing
// a new one).

import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

export interface NumberFieldProps {
  id: string;
  /** The saved value. Null shows an empty field. */
  value: number | null;
  /** Called once per edit, with a value that passed the checks below. */
  onCommit: (next: number | null) => void;
  min: number;
  max: number;
  /** When true an empty field commits null; otherwise empty is refused. */
  allowEmpty?: boolean;
  /** Values must be a multiple of this (1 = whole numbers). Default 1. */
  step?: number;
  /** Allow decimals (step is then only the browser spinner's increment). */
  decimal?: boolean;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  /** Overrides the default "Enter a whole number from min to max." */
  rangeMessage?: string;
  /**
   * Change this when a save fails. The field then drops its draft and shows
   * the saved value, so a refused number is not left on screen as if kept.
   */
  resetKey?: number;
}

/** Why `raw` cannot be saved, or null when it can. */
function numberFieldError(
  raw: string,
  {
    min,
    max,
    allowEmpty = false,
    step = 1,
    decimal = false,
    rangeMessage,
  }: Pick<NumberFieldProps, 'min' | 'max' | 'allowEmpty' | 'step' | 'decimal' | 'rangeMessage'>,
): string | null {
  const text = raw.trim();
  const range =
    rangeMessage ??
    (decimal
      ? `Enter a number from ${min} to ${max}.`
      : `Enter a whole number from ${min} to ${max}.`);
  if (text === '') return allowEmpty ? null : range;
  const n = Number(text);
  if (!Number.isFinite(n) || n < min || n > max) return range;
  if (!decimal && (!Number.isInteger(n) || (step > 1 && n % step !== 0))) return range;
  return null;
}

function show(value: number | null): string {
  return value === null ? '' : String(value);
}

export function NumberField({
  id,
  value,
  onCommit,
  min,
  max,
  allowEmpty = false,
  step = 1,
  decimal = false,
  disabled,
  placeholder,
  className,
  rangeMessage,
  resetKey,
}: NumberFieldProps): JSX.Element {
  const [draft, setDraft] = useState(show(value));
  const [error, setError] = useState<string | null>(null);
  const editing = useRef(false);

  // Follow the saved value while nobody is typing in this field, and come
  // back to it after a failed save (resetKey changes).
  useEffect(() => {
    if (!editing.current) {
      setDraft(show(value));
      setError(null);
    }
  }, [value, resetKey]);

  function commit(): void {
    const problem = numberFieldError(draft, { min, max, allowEmpty, step, decimal, rangeMessage });
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    editing.current = false;
    const text = draft.trim();
    const next = text === '' ? null : Number(text);
    if (next !== value) onCommit(next);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>): void {
    if (e.key === 'Enter') {
      e.preventDefault();
      commit();
    } else if (e.key === 'Escape') {
      editing.current = false;
      setDraft(show(value));
      setError(null);
    }
  }

  const errorId = `${id}-error`;
  return (
    <div className="flex flex-col gap-1">
      <Input
        id={id}
        type="number"
        inputMode={decimal ? 'decimal' : 'numeric'}
        min={min}
        max={max}
        step={decimal ? 'any' : step}
        disabled={disabled}
        placeholder={placeholder}
        value={draft}
        onChange={(e) => {
          editing.current = true;
          setDraft(e.target.value);
          if (error) setError(null);
        }}
        onBlur={commit}
        onKeyDown={onKeyDown}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        className={cn(error && 'border-[var(--ui-danger)]', className)}
      />
      {error ? (
        <p id={errorId} role="alert" className="text-[12.5px] text-[var(--ui-danger)]">
          {error}
        </p>
      ) : null}
    </div>
  );
}
