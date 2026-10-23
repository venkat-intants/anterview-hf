// The shared form controls (docs/UI-CONVENTIONS.md, "Forms"): Input, Textarea,
// SelectControl and the FormField wrapper. The rule they exist for is ONE
// control height, so that is asserted directly, alongside the id wiring a form
// would otherwise do by hand.

import { createRef } from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { SelectControl } from '@/components/ui/select';
import { FormField } from '@/components/ui/form-field';
import { CONTROL_HEIGHT } from '@/components/ui/control-styles';
import { buttonVariants } from '@/components/ui/button';
import { Field, SelectField } from '@/design/components/primitives';

describe('one control height', () => {
  it('is 40px by default and 36px compact, the same as Button', () => {
    expect(CONTROL_HEIGHT).toEqual({ md: 'h-10', sm: 'h-9' });
    expect(buttonVariants()).toContain(CONTROL_HEIGHT.md);
    expect(buttonVariants({ size: 'sm' })).toContain(CONTROL_HEIGHT.sm);
  });

  it('Input uses it, md unless asked for sm', () => {
    render(
      <>
        <Input aria-label="Default" />
        <Input aria-label="Compact" size="sm" />
      </>,
    );
    const md = screen.getByRole('textbox', { name: 'Default' });
    const sm = screen.getByRole('textbox', { name: 'Compact' });
    expect(md).toHaveClass('h-10', 'rounded-[10px]');
    expect(md).not.toHaveAttribute('data-size');
    expect(sm).toHaveClass('h-9');
    expect(sm).not.toHaveClass('h-10');
    expect(sm).toHaveAttribute('data-size', 'sm');
  });

  it('Field draws its box at the same height', () => {
    render(<Field label="Email" />);
    const box = screen.getByRole('textbox', { name: 'Email' }).parentElement;
    expect(box).toHaveClass('h-10');
  });
});

describe('Input', () => {
  it('forwards ref, type and value handling', async () => {
    const ref = createRef<HTMLInputElement>();
    render(<Input ref={ref} type="email" aria-label="Email" />);
    const el = screen.getByRole('textbox', { name: 'Email' });
    expect(ref.current).toBe(el);
    expect(el).toHaveAttribute('type', 'email');
    await userEvent.type(el, 'a@b.co');
    expect(el).toHaveValue('a@b.co');
  });

  it('marks itself invalid from the prop or from aria-invalid', () => {
    render(
      <>
        <Input aria-label="A" invalid />
        <Input aria-label="B" aria-invalid="true" />
        <Input aria-label="C" />
      </>,
    );
    expect(screen.getByRole('textbox', { name: 'A' })).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('textbox', { name: 'B' })).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('textbox', { name: 'C' })).not.toHaveAttribute('aria-invalid');
  });

  it('lets a call site add classes', () => {
    render(<Input aria-label="X" className="w-24" />);
    expect(screen.getByRole('textbox', { name: 'X' })).toHaveClass('w-24', 'h-10');
  });
});

describe('Textarea', () => {
  it('starts at three lines, grows vertically only, and shares the surface', () => {
    const ref = createRef<HTMLTextAreaElement>();
    render(<Textarea ref={ref} aria-label="Notes" invalid />);
    const el = screen.getByRole('textbox', { name: 'Notes' });
    expect(ref.current).toBe(el);
    expect(el.tagName).toBe('TEXTAREA');
    expect(el).toHaveAttribute('rows', '3');
    expect(el).toHaveClass('resize-y', 'rounded-[10px]', 'border-input');
    expect(el).toHaveAttribute('aria-invalid', 'true');
  });

  it('takes more rows when asked', () => {
    render(<Textarea aria-label="Long" rows={6} />);
    expect(screen.getByRole('textbox', { name: 'Long' })).toHaveAttribute('rows', '6');
  });
});

describe('FormField', () => {
  it('labels the control and links hint and error to it', () => {
    render(
      <FormField label="Opening name" hint="Shown to candidates" error="Enter a name.">
        {(p) => <Input {...p} />}
      </FormField>,
    );
    const input = screen.getByRole('textbox', { name: 'Opening name' });
    expect(input).toHaveAttribute('aria-invalid', 'true');
    const described = input.getAttribute('aria-describedby') ?? '';
    const ids = described.split(' ');
    expect(ids).toHaveLength(2);
    expect(document.getElementById(ids[0])).toHaveTextContent('Enter a name.');
    expect(document.getElementById(ids[1])).toHaveTextContent('Shown to candidates');
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a name.');
  });

  it('keeps a caller id and caller describedBy, and is quiet with no error', () => {
    render(
      <FormField label="Notes" id="notes" describedBy="extra">
        {(p) => <Textarea {...p} />}
      </FormField>,
    );
    const el = screen.getByRole('textbox', { name: 'Notes' });
    expect(el).toHaveAttribute('id', 'notes');
    expect(el).toHaveAttribute('aria-describedby', 'extra');
    expect(el).not.toHaveAttribute('aria-invalid');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('works around a SelectControl, and SelectField is built on it', () => {
    render(
      <>
        <FormField label="Stage" error="Pick a stage.">
          {(p) => (
            <SelectControl {...p}>
              <option value="">Choose</option>
            </SelectControl>
          )}
        </FormField>
        <SelectField label="Mode" hint="How it runs">
          <option value="a">A</option>
        </SelectField>
      </>,
    );
    expect(screen.getByRole('combobox', { name: 'Stage' })).toHaveAttribute('aria-invalid', 'true');
    const mode = screen.getByRole('combobox', { name: 'Mode' });
    expect(document.getElementById(mode.getAttribute('aria-describedby') ?? '')).toHaveTextContent(
      'How it runs',
    );
  });
});
