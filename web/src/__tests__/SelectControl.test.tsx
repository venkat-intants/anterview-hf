// The dropdown system, in two halves.
//
//  1. SelectControl (ui/select-control.tsx): a thin
//     wrapper that must STAY a native <select> — tests, mobile pickers and screen
//     readers all depend on that — while adding size / invalid / leadingIcon.
//  2. The CSS guard: index.css carries the design (a base-layer rule for every
//     select and an @supports (appearance: base-select) block for the open list).
//     jsdom cannot render CSS, so the contract is asserted on the source: the rules
//     exist, they sit where they must, and they hold no hard-coded colour (a hex
//     would not follow light/dark/the accent picker).

import { createRef } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { readFileSync } from 'node:fs';
import { Search } from 'lucide-react';
import { SelectControl, NativeSelect } from '@/components/ui/select-control';
import { SelectField } from '@/design/components/primitives';

describe('SelectControl', () => {
  it('renders a native select with its options', () => {
    render(
      <SelectControl aria-label="Status" defaultValue="b">
        <option value="a">A</option>
        <option value="b">B</option>
      </SelectControl>,
    );
    const el = screen.getByRole('combobox', { name: 'Status' });
    expect(el.tagName).toBe('SELECT');
    expect(screen.getAllByRole('option')).toHaveLength(2);
    expect(el).toHaveValue('b');
  });

  it('is the same component as NativeSelect', () => {
    expect(NativeSelect).toBe(SelectControl);
  });

  it('passes name, className, id and data attributes straight through', () => {
    render(
      <SelectControl aria-label="Role" name="role" id="role-id" className="extra" data-testid="sel">
        <option value="x">X</option>
      </SelectControl>,
    );
    const el = screen.getByTestId('sel');
    expect(el).toHaveAttribute('name', 'role');
    expect(el).toHaveAttribute('id', 'role-id');
    expect(el).toHaveClass('extra');
    expect(el).toHaveClass('w-full');
  });

  it('forwards its ref to the select element', () => {
    const ref = createRef<HTMLSelectElement>();
    render(
      <SelectControl ref={ref} aria-label="R">
        <option>one</option>
      </SelectControl>,
    );
    expect(ref.current).toBeInstanceOf(HTMLSelectElement);
  });

  it('marks invalid via aria-invalid, and honours an explicit aria-invalid', () => {
    const { rerender } = render(
      <SelectControl aria-label="S" invalid>
        <option>one</option>
      </SelectControl>,
    );
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-invalid', 'true');
    rerender(
      <SelectControl aria-label="S">
        <option>one</option>
      </SelectControl>,
    );
    expect(screen.getByRole('combobox')).not.toHaveAttribute('aria-invalid');
    rerender(
      <SelectControl aria-label="S" aria-invalid="true">
        <option>one</option>
      </SelectControl>,
    );
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-invalid', 'true');
  });

  it('maps size="sm" to data-size (the compact 36px design) and md to nothing', () => {
    const { rerender } = render(
      <SelectControl aria-label="S" size="sm">
        <option>one</option>
      </SelectControl>,
    );
    expect(screen.getByRole('combobox')).toHaveAttribute('data-size', 'sm');
    rerender(
      <SelectControl aria-label="S">
        <option>one</option>
      </SelectControl>,
    );
    expect(screen.getByRole('combobox')).not.toHaveAttribute('data-size');
  });

  it('works with userEvent.selectOptions and fires onChange', async () => {
    const onChange = vi.fn();
    render(
      <SelectControl aria-label="Status" defaultValue="a" onChange={onChange}>
        <option value="a">A</option>
        <option value="b">B</option>
      </SelectControl>,
    );
    await userEvent.selectOptions(screen.getByRole('combobox'), 'b');
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('combobox')).toHaveValue('b');
  });

  it('can be disabled, and a disabled select ignores selectOptions', async () => {
    const onChange = vi.fn();
    render(
      <SelectControl aria-label="Status" disabled onChange={onChange}>
        <option value="a">A</option>
        <option value="b">B</option>
      </SelectControl>,
    );
    const el = screen.getByRole('combobox');
    expect(el).toBeDisabled();
    await userEvent.selectOptions(el, 'b');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('draws a leading icon outside the accessibility tree and indents the text', () => {
    const { container } = render(
      <SelectControl aria-label="Search by" leadingIcon={<Search data-testid="icon" />}>
        <option>one</option>
      </SelectControl>,
    );
    const icon = screen.getByTestId('icon');
    expect(icon.closest('[aria-hidden="true"]')).not.toBeNull();
    expect(screen.getByRole('combobox')).toHaveClass('ps-9');
    expect(container.querySelectorAll('select')).toHaveLength(1);
  });

  it('adds no wrapper element when there is no leading icon', () => {
    const { container } = render(
      <SelectControl aria-label="S">
        <option>one</option>
      </SelectControl>,
    );
    expect(container.firstElementChild?.tagName).toBe('SELECT');
  });
});

describe('SelectField', () => {
  it('labels the select and wires the error to it', () => {
    render(
      <SelectField label="Currency" error="Pick one">
        <option value="">Choose</option>
        <option value="INR">INR</option>
      </SelectField>,
    );
    const el = screen.getByLabelText('Currency');
    expect(el.tagName).toBe('SELECT');
    expect(el).toHaveAttribute('aria-invalid', 'true');
    expect(el).toHaveAccessibleDescription('Pick one');
    expect(screen.getByRole('alert')).toHaveTextContent('Pick one');
  });
});

// ── CSS guard ────────────────────────────────────────────────────────────────

const rawCss = readFileSync('src/index.css', 'utf8');
// Comments are stripped so prose that mentions a selector cannot satisfy (or
// fail) an assertion about the rules themselves.
const css = rawCss.replace(/\/\*[\s\S]*?\*\//g, '');

/** The text of the first `@supports (appearance: base-select) { … }` block. */
function supportsBlock(): string {
  const start = css.indexOf('@supports (appearance: base-select)');
  if (start === -1) return '';
  const open = css.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === '{') depth += 1;
    else if (css[i] === '}') {
      depth -= 1;
      if (depth === 0) return css.slice(open + 1, i);
    }
  }
  return '';
}

/** Ignores `%23`-style escapes in url() data URIs too: a colour is a hex literal
 *  or an rgb()/hsl() with numbers baked in OUTSIDE a token reference. */
const HEX = /#[0-9a-fA-F]{3,8}\b/;

describe('index.css dropdown design', () => {
  it('has a low-specificity base rule that gives every select the control design', () => {
    expect(css).toMatch(/select:where\(:not\(\[multiple\]\):not\(\[size\]\)\)\s*\{[^}]*appearance:\s*none/);
    const rule = /select:where\(:not\(\[multiple\]\):not\(\[size\]\)\)\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';
    expect(rule).toContain('height: 2.5rem');
    expect(rule).toContain('border-radius: 10px');
    expect(rule).toContain('text-overflow: ellipsis');
    // The chevron is built from currentColor, so it follows the mode.
    expect(rule).toContain('currentColor');
  });

  it('keeps the text clear of the chevron regardless of a call site px-*', () => {
    expect(css).toMatch(/\bselect:not\(\[multiple\]\):not\(\[size\]\)\s*\{\s*padding-inline-end:\s*2\.25rem/);
  });

  it('excludes select[multiple] and select[size] from the dropdown design', () => {
    const selectors = css.match(/(^|\n)\s*select:where\([^{]*\{/g) ?? [];
    expect(selectors.length).toBeGreaterThan(5);
    for (const sel of selectors) {
      expect(sel).toContain(':not([multiple]):not([size])');
    }
  });

  it('defines focus, disabled, invalid and compact states', () => {
    expect(css).toContain('[aria-invalid=\'true\']');
    expect(css).toMatch(/select:where\(:not\(\[multiple\]\):not\(\[size\]\)\):focus-visible/);
    expect(css).toMatch(/\):disabled\)/);
    expect(css).toContain("[data-size='sm']");
  });

  it('progressively enhances the open list inside @supports (appearance: base-select)', () => {
    const block = supportsBlock();
    expect(block).not.toBe('');
    expect(block).toContain('appearance: base-select');
    expect(block).toContain('::picker(select)');
    expect(block).toContain('::picker-icon');
    expect(block).toContain('::checkmark');
    expect(block).toContain('var(--ui-shadow-card)');
    expect(block).toContain('border-radius: 12px');
    expect(block).toContain('padding: 4px');
    expect(block).toContain('max-block-size');
    expect(block).toContain('prefers-reduced-motion');
    expect(block).toContain('optgroup');
  });

  it('keeps @supports outside @layer (Tailwind hoists an @layer out of an @supports and drops the condition)', () => {
    const block = supportsBlock();
    expect(block).not.toMatch(/@layer/);
    const at = css.indexOf('@supports (appearance: base-select)');
    // Not nested in any other block: everything before it balances out.
    const before = css.slice(0, at);
    expect(before.split('{').length - before.split('}').length).toBe(0);
  });

  it('documents the Firefox/Safari fallback next to the @supports block', () => {
    expect(rawCss).toMatch(/Firefox and Safari[\s\S]{0,200}intended fallback/);
  });

  it('holds no hard-coded hex colour in the dropdown rules', () => {
    const start = css.indexOf('select:where(:not([multiple])');
    const end = css.indexOf('[data-radix-popper-content-wrapper]');
    const dropdownCss = css.slice(start, end === -1 ? undefined : end);
    expect(dropdownCss.length).toBeGreaterThan(1000);
    expect(dropdownCss).not.toMatch(HEX);
    // %23 is a URL-encoded '#': an escaped hex inside a data-URI is still a hex.
    expect(dropdownCss).not.toMatch(/%23[0-9a-fA-F]{3,8}/);
  });
});
