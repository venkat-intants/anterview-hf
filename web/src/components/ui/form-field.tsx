import * as React from 'react';

import { cn } from '@/lib/utils';
import { fieldErrorClass, fieldHintClass, fieldLabelClass } from './control-styles';

/** The props FormField hands its control so label, hint and error are wired. */
export interface FieldControlProps {
  id: string;
  'aria-describedby'?: string;
  'aria-invalid'?: true;
}

export interface FormFieldProps {
  label: React.ReactNode;
  /** Help text under the control. */
  hint?: React.ReactNode;
  /** Validation message. Marks the control invalid and announces the message. */
  error?: string;
  /** The control's id; generated when omitted. */
  id?: string;
  /** Extra ids for aria-describedby, kept ahead of the hint and error. */
  describedBy?: string;
  className?: string;
  /** Renders the control. Spread the props onto it: `{(p) => <Input {...p} />}`. */
  children: (control: FieldControlProps) => React.ReactNode;
}

/**
 * FormField: a label above one control, with optional hint and error below.
 * It owns the id wiring (htmlFor, aria-describedby, aria-invalid) so a form
 * never does it by hand, and it is the wrapper that the design primitives'
 * `Field` and `SelectField` are built on.
 *
 *   <FormField label="Opening name" error={errors.title}>
 *     {(p) => <Input {...p} value={title} onChange={...} />}
 *   </FormField>
 */
export function FormField({
  label,
  hint,
  error,
  id,
  describedBy,
  className,
  children,
}: FormFieldProps): JSX.Element {
  const autoId = React.useId();
  const fieldId = id ?? autoId;
  const hintId = `${fieldId}-hint`;
  const errorId = `${fieldId}-error`;
  const describedIds =
    [describedBy, error ? errorId : null, hint ? hintId : null].filter(Boolean).join(' ') ||
    undefined;
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <label htmlFor={fieldId} className={fieldLabelClass}>
        {label}
      </label>
      {children({
        id: fieldId,
        'aria-describedby': describedIds,
        'aria-invalid': error ? true : undefined,
      })}
      {error ? (
        <span id={errorId} role="alert" className={fieldErrorClass}>
          {error}
        </span>
      ) : null}
      {hint ? (
        <span id={hintId} className={fieldHintClass}>
          {hint}
        </span>
      ) : null}
    </div>
  );
}
