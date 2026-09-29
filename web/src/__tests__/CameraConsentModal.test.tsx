// CameraConsentModal — the exam's dedicated camera-proctoring consent gate
// (camera-proctoring contract §3). Mirrors ConsentModal.test.tsx's style.
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CameraConsentModal from '../pages/exam/CameraConsentModal';

function renderModal(overrides: Partial<React.ComponentProps<typeof CameraConsentModal>> = {}) {
  const defaults = { onAgree: vi.fn(), onDecline: vi.fn() };
  const props = { ...defaults, ...overrides };
  return { ...render(<CameraConsentModal {...props} />), props };
}

describe('CameraConsentModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('says the camera is on, what is detected, and that no video/image is recorded', () => {
    renderModal();
    expect(screen.getByText(/camera monitoring for this exam/i)).toBeInTheDocument();
    expect(screen.getByText(/whether you are visible/i)).toBeInTheDocument();
    expect(screen.getByText(/more than one person/i)).toBeInTheDocument();
    // The load-bearing sentence — no image ever leaves the browser.
    expect(
      screen.getByText(/no video or image is ever recorded or sent anywhere/i),
    ).toBeInTheDocument();
  });

  it('has role="dialog" and aria-modal="true"', () => {
    renderModal();
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
  });

  it('has aria-labelledby pointing to the heading', () => {
    renderModal();
    const dialog = screen.getByRole('dialog');
    const labelId = dialog.getAttribute('aria-labelledby');
    expect(labelId).toBeTruthy();
    expect(document.getElementById(labelId!)?.textContent).toMatch(/camera monitoring/i);
  });

  it('moves focus to "Turn on camera monitoring" on mount', () => {
    renderModal();
    expect(screen.getByRole('button', { name: /turn on camera monitoring/i })).toHaveFocus();
  });

  it('calls onAgree when "Turn on camera monitoring" is clicked', async () => {
    const user = userEvent.setup();
    const { props } = renderModal();
    await user.click(screen.getByRole('button', { name: /turn on camera monitoring/i }));
    expect(props.onAgree).toHaveBeenCalledOnce();
  });

  it('calls onDecline when "Decline" is clicked', async () => {
    const user = userEvent.setup();
    const { props } = renderModal();
    await user.click(screen.getByRole('button', { name: /decline/i }));
    expect(props.onDecline).toHaveBeenCalledOnce();
  });

  it('calls onDecline when Escape is pressed', async () => {
    const user = userEvent.setup();
    const { props } = renderModal();
    await user.keyboard('{Escape}');
    expect(props.onDecline).toHaveBeenCalledOnce();
  });
});
