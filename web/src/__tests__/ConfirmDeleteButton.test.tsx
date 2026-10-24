// Audit X5 / consistency B1: the inline confirm said "Delete" for actions that
// were not deletes, so cancelling an interview session showed "Sure? [Delete]
// [Cancel]", where "Delete" carried out the cancel and "Cancel" backed out. It
// also left focus nowhere after backing out and ignored Escape.

import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfirmDeleteButton } from '../components/ConfirmDeleteButton';
import { Archive } from '../design/components/icons';

describe('ConfirmDeleteButton wording', () => {
  it('repeats the action on the confirm button instead of saying Delete', async () => {
    const user = userEvent.setup();
    render(<ConfirmDeleteButton label="Withdraw scorecard" onConfirm={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'Withdraw scorecard' }));

    const group = screen.getByRole('group', { name: 'Confirm: Withdraw scorecard' });
    expect(group).toHaveTextContent('Sure?');
    expect(screen.getByRole('button', { name: 'Withdraw scorecard' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });

  it('says Keep, not Cancel, to back out of a cancel', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<ConfirmDeleteButton label="Cancel session" onConfirm={onConfirm} />);

    await user.click(screen.getByRole('button', { name: 'Cancel session' }));

    expect(screen.getByRole('button', { name: 'Keep' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel session' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('uses the explicit confirm text for an icon-only trigger', async () => {
    const user = userEvent.setup();
    render(<ConfirmDeleteButton title="Remove Asha" confirmText="Remove" onConfirm={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'Remove Asha' }));

    expect(screen.getByRole('button', { name: 'Remove' })).toBeInTheDocument();
  });

  it('draws the icon the caller passes instead of the trash can', () => {
    const { container } = render(
      <ConfirmDeleteButton label="Archive" icon={Archive} onConfirm={vi.fn()} />,
    );
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('class')).toContain('lucide-archive');
    expect(container.querySelector('.lucide-trash-2, .lucide-trash2')).toBeNull();
  });

  it('can draw no icon at all', () => {
    const { container } = render(
      <ConfirmDeleteButton label="Revoke" icon={null} onConfirm={vi.fn()} />,
    );
    expect(container.querySelector('svg')).toBeNull();
  });
});

describe('ConfirmDeleteButton keyboard and focus', () => {
  it('moves focus to the safe button when armed', async () => {
    const user = userEvent.setup();
    render(<ConfirmDeleteButton label="Delete company" onConfirm={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'Delete company' }));

    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
  });

  it('returns focus to the trigger after backing out', async () => {
    const user = userEvent.setup();
    render(<ConfirmDeleteButton label="Delete company" onConfirm={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'Delete company' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.getByRole('button', { name: 'Delete company' })).toHaveFocus();
    expect(screen.queryByRole('group')).not.toBeInTheDocument();
  });

  it('backs out on Escape without confirming', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<ConfirmDeleteButton label="Cancel loop" onConfirm={onConfirm} />);

    await user.click(screen.getByRole('button', { name: 'Cancel loop' }));
    await user.keyboard('{Escape}');

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.queryByRole('group')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel loop' })).toHaveFocus();
  });

  it('stays armed and busy while the action runs, then returns focus to the trigger', async () => {
    const user = userEvent.setup();

    function Harness() {
      const [pending, setPending] = useState(false);
      return (
        <>
          <ConfirmDeleteButton
            label="Withdraw offer"
            pending={pending}
            onConfirm={() => setPending(true)}
          />
          <button type="button" onClick={() => setPending(false)}>
            finish
          </button>
        </>
      );
    }
    render(<Harness />);

    await user.click(screen.getByRole('button', { name: 'Withdraw offer' }));
    await user.click(screen.getByRole('button', { name: 'Withdraw offer' }));

    // Busy, and still named for the action rather than for its ellipsis.
    const busy = screen.getByRole('button', { name: 'Withdraw offer' });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute('aria-busy', 'true');
    expect(busy).toHaveTextContent('…');
    expect(screen.queryByRole('button', { name: '…' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'finish' }));

    expect(screen.queryByRole('group')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Withdraw offer' })).toHaveFocus();
  });

  it('stays armed after a confirm when pending never becomes true, and can still back out', async () => {
    // Pins the documented contract: the control disarms on pending's change
    // from true to false, so a constant false never disarms it by itself.
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<ConfirmDeleteButton label="Withdraw offer" pending={false} onConfirm={onConfirm} />);

    await user.click(screen.getByRole('button', { name: 'Withdraw offer' }));
    await user.click(screen.getByRole('button', { name: 'Withdraw offer' }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('group', { name: 'Confirm: Withdraw offer' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('group')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Withdraw offer' })).toHaveFocus();
  });

  it('disarms straight away when the caller tracks no pending state', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<ConfirmDeleteButton label="Delete" onConfirm={onConfirm} />);

    await user.click(screen.getByRole('button', { name: 'Delete' }));
    await user.click(screen.getByRole('button', { name: 'Delete' }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('group')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete' })).toHaveFocus();
  });
});

// Re-audit (interviews): "Mark completed" and "Resend schedule" were drawn as
// red danger buttons because the control had one look. A non-destructive
// confirm now asks for tone="neutral"; destructive stays the default.
describe('ConfirmDeleteButton tone', () => {
  it('is destructive by default: a danger trigger and a red confirm', async () => {
    const user = userEvent.setup();
    render(<ConfirmDeleteButton label="Delete window" onConfirm={vi.fn()} />);

    const trigger = screen.getByRole('button', { name: 'Delete window' });
    expect(trigger.className).toContain('text-[var(--ui-danger)]');
    await user.click(trigger);
    expect(screen.getByRole('button', { name: 'Delete window' }).className).toContain(
      'bg-destructive-solid',
    );
  });

  it('draws a neutral outline trigger and a primary confirm for tone="neutral"', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<ConfirmDeleteButton tone="neutral" label="Mark completed" onConfirm={onConfirm} />);

    const trigger = screen.getByRole('button', { name: 'Mark completed' });
    expect(trigger.className).not.toMatch(/danger|destructive/);
    expect(trigger.className).toContain('border-input');
    await user.click(trigger);

    const confirm = screen.getByRole('button', { name: 'Mark completed' });
    expect(confirm.className).not.toMatch(/danger|destructive/);
    expect(confirm.className).toContain('bg-primary');
    // The behaviour is the same: Escape backs out to the trigger, a confirm fires.
    await user.keyboard('{Escape}');
    expect(screen.getByRole('button', { name: 'Mark completed' })).toHaveFocus();
    await user.click(screen.getByRole('button', { name: 'Mark completed' }));
    await user.click(screen.getByRole('button', { name: 'Mark completed' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('keeps an icon-only neutral trigger free of the danger hover', () => {
    render(
      <ConfirmDeleteButton tone="neutral" title="Resend to Asha" confirmText="Resend" onConfirm={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: 'Resend to Asha' }).className).not.toMatch(/danger/);
  });
});
