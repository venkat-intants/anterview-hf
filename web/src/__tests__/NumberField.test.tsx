// HR-H2 — the workflow builder's number fields save once, when the person is
// done, after a range check. They used to PATCH on every keystroke from server
// state, which dropped characters and sent intermediate values.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { NumberField } from '../components/workflow/NumberField';

function field(props: Partial<Parameters<typeof NumberField>[0]> = {}) {
  const onCommit = vi.fn();
  const view = render(
    <>
      <label htmlFor="days">Days to complete</label>
      <NumberField id="days" value={7} min={1} max={365} onCommit={onCommit} {...props} />
    </>,
  );
  return { onCommit, view, input: screen.getByLabelText('Days to complete') };
}

describe('NumberField', () => {
  it('keeps every keystroke and commits once, on blur', async () => {
    const user = userEvent.setup();
    const { onCommit, input } = field();
    await user.clear(input);
    await user.type(input, '145');
    expect(input).toHaveValue(145);
    expect(onCommit).not.toHaveBeenCalled();
    await user.tab();
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenCalledWith(145);
  });

  it('commits on Enter', async () => {
    const user = userEvent.setup();
    const { onCommit, input } = field();
    await user.clear(input);
    await user.type(input, '30{Enter}');
    expect(onCommit).toHaveBeenCalledWith(30);
  });

  it('refuses an empty value when one is required, and a value out of range', async () => {
    const user = userEvent.setup();
    const { onCommit, input } = field();
    await user.clear(input);
    await user.tab();
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a whole number from 1 to 365.');
    await user.type(input, '400');
    await user.tab();
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a whole number from 1 to 365.');
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('commits null for an empty optional field', async () => {
    const user = userEvent.setup();
    const { onCommit, input } = field({ allowEmpty: true });
    await user.clear(input);
    await user.tab();
    expect(onCommit).toHaveBeenCalledWith(null);
  });

  it('puts the saved value back on Escape', async () => {
    const user = userEvent.setup();
    const { onCommit, input } = field();
    await user.clear(input);
    await user.type(input, '99{Escape}');
    expect(input).toHaveValue(7);
    await user.tab();
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('does not commit a value that did not change', async () => {
    const user = userEvent.setup();
    const { onCommit, input } = field();
    await user.click(input);
    await user.tab();
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('follows a new saved value while nobody is typing', () => {
    const { view, input } = field();
    view.rerender(
      <>
        <label htmlFor="days">Days to complete</label>
        <NumberField id="days" value={14} min={1} max={365} onCommit={vi.fn()} />
      </>,
    );
    expect(input).toHaveValue(14);
  });

  it('shows the saved value again after a save fails', async () => {
    // The parent's saved value stays 7 because the server refused 99; the
    // builder bumps resetKey from the mutation's onError.
    const user = userEvent.setup();
    const onCommit = vi.fn();
    const ui = (resetKey: number) => (
      <>
        <label htmlFor="days">Days to complete</label>
        <NumberField
          id="days"
          value={7}
          min={1}
          max={365}
          onCommit={onCommit}
          resetKey={resetKey}
        />
      </>
    );
    const view = render(ui(0));
    const input = screen.getByLabelText('Days to complete');
    await user.clear(input);
    await user.type(input, '99');
    await user.tab();
    expect(onCommit).toHaveBeenCalledWith(99);
    expect(input).toHaveValue(99);
    view.rerender(ui(1));
    expect(input).toHaveValue(7);
  });

  it('does not wipe a new edit when a reset arrives mid-typing', async () => {
    const user = userEvent.setup();
    const ui = (resetKey: number) => (
      <>
        <label htmlFor="days">Days to complete</label>
        <NumberField id="days" value={7} min={1} max={365} onCommit={vi.fn()} resetKey={resetKey} />
      </>
    );
    const view = render(ui(0));
    const input = screen.getByLabelText('Days to complete');
    await user.clear(input);
    await user.type(input, '42');
    view.rerender(ui(1));
    expect(input).toHaveValue(42);
  });
});
