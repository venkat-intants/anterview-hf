// ToggleSwitch — the OFF track must be visible on a light card.
//
// It used to be `bg-white/15`: a white wash that is invisible on white, so
// "Accept applications" rendered as a lone dot with no track. The track and the
// thumb now use theme tokens, and the thumb changes fill with the state so it
// contrasts with whichever track it sits on.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ToggleSwitch } from '../design/components/primitives';

describe('ToggleSwitch', () => {
  it('gives the OFF track a token fill and hairline, never a white wash', () => {
    render(<ToggleSwitch checked={false} onChange={() => {}} label="Accept applications" />);
    const track = screen.getByRole('switch', { name: 'Accept applications' });

    expect(track.getAttribute('aria-checked')).toBe('false');
    expect(track.className).toContain('bg-[var(--ui-inset-strong)]');
    expect(track.className).toContain('border-[var(--ui-line-strong)]');
    expect(track.className).not.toMatch(/bg-white/);
    // Off thumb is the mid-grey, which reads on both the black and white grounds.
    expect(screen.getByTestId('toggle-thumb').className).toContain('bg-muted-foreground');
  });

  it('puts a contrasting thumb on the accent track when ON', () => {
    render(<ToggleSwitch checked onChange={() => {}} label="Accept applications" />);
    const track = screen.getByRole('switch', { name: 'Accept applications' });

    expect(track.getAttribute('aria-checked')).toBe('true');
    expect(track.className).toContain('bg-[var(--accent)]');
    const thumb = screen.getByTestId('toggle-thumb');
    expect(thumb.className).toContain('bg-primary');
    expect(thumb.className).not.toContain('bg-muted-foreground');
  });

  it('reports the next state on click', async () => {
    const onChange = vi.fn();
    render(<ToggleSwitch checked={false} onChange={onChange} label="Accept applications" />);
    await userEvent.setup().click(screen.getByRole('switch'));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
