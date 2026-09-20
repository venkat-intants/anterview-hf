// LockBanner — PH4-D1. Shown on a round whose content is fixed. Two ways
// forward, never a dead end: Unpublish (only while the round is actually
// published) and Duplicate round (always, while locked).

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LockBanner } from '../components/bank/LockBanner';
import { PUBLISHED_REASON, TAKEN_REASON } from '../lib/examLocks';

describe('LockBanner', () => {
  it('shows the published reason verbatim, with both Unpublish and Duplicate round', async () => {
    const user = userEvent.setup();
    const onUnpublish = vi.fn();
    const onDuplicate = vi.fn();
    render(
      <LockBanner
        reason={PUBLISHED_REASON}
        canUnpublish
        onUnpublish={onUnpublish}
        onDuplicate={onDuplicate}
      />,
    );

    expect(screen.getByText(PUBLISHED_REASON)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Unpublish' }));
    expect(onUnpublish).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: /Duplicate round/ }));
    expect(onDuplicate).toHaveBeenCalledTimes(1);
  });

  it('hides Unpublish when the round is locked for a reason other than being published', () => {
    render(
      <LockBanner reason={TAKEN_REASON} canUnpublish={false} onDuplicate={vi.fn()} />,
    );

    expect(screen.getByText(TAKEN_REASON)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Unpublish' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Duplicate round/ })).toBeInTheDocument();
  });

  it('disables the buttons while the action is in flight', () => {
    render(
      <LockBanner
        reason={PUBLISHED_REASON}
        canUnpublish
        onUnpublish={vi.fn()}
        unpublishing
        onDuplicate={vi.fn()}
        duplicating
      />,
    );
    expect(screen.getByRole('button', { name: /Unpublish/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Duplicate round/ })).toBeDisabled();
  });
});
