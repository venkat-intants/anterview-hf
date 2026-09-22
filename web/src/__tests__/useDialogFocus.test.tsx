// useDialogFocus — the shared modal-dialog accessibility hook (code review
// follow-up). A dialog that is only ever MOUNTED while open (every dialog in
// this codebase) gets: focus moved inside on mount, Tab trapped within it,
// Escape closing it, and focus restored to the trigger on unmount.

import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useDialogFocus } from '../hooks/useDialogFocus';

function TestDialog({ onClose }: { onClose: () => void }) {
  const panelRef = useDialogFocus<HTMLDivElement>(onClose);
  return (
    <div ref={panelRef} role="dialog" aria-modal="true" tabIndex={-1} data-testid="panel">
      <button>First</button>
      <button>Last</button>
    </div>
  );
}

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button onClick={() => setOpen(true)}>Open dialog</button>
      {open ? <TestDialog onClose={() => setOpen(false)} /> : null}
    </div>
  );
}

describe('useDialogFocus', () => {
  it('moves focus to the first focusable element inside the dialog on open', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole('button', { name: 'Open dialog' }));

    await waitFor(() => expect(screen.getByRole('button', { name: 'First' })).toHaveFocus());
  });

  it('closes on Escape and returns focus to the trigger', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const trigger = screen.getByRole('button', { name: 'Open dialog' });
    await user.click(trigger);
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('traps Tab inside the dialog — wraps last back to first', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', { name: 'Open dialog' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'First' })).toHaveFocus());

    await user.tab(); // First -> Last
    expect(screen.getByRole('button', { name: 'Last' })).toHaveFocus();

    await user.tab(); // Last -> wraps to First, never escaping to the trigger
    expect(screen.getByRole('button', { name: 'First' })).toHaveFocus();
  });

  it('traps Shift+Tab inside the dialog — wraps first back to last', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', { name: 'Open dialog' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'First' })).toHaveFocus());

    await user.tab({ shift: true }); // First -> wraps to Last

    expect(screen.getByRole('button', { name: 'Last' })).toHaveFocus();
  });
});
