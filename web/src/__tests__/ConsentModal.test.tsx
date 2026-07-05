// Tests for ConsentModal component — S3-011
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConsentModal from '../components/ConsentModal';

function renderModal(overrides: Partial<React.ComponentProps<typeof ConsentModal>> = {}) {
  const defaults = {
    onAgree: vi.fn().mockResolvedValue(undefined),
    onDecline: vi.fn(),
    isSubmitting: false,
    error: null,
  };
  const props = { ...defaults, ...overrides };
  return { ...render(<ConsentModal {...props} />), props };
}

describe('ConsentModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // --- Required copy ---

  it('renders the modal heading', () => {
    renderModal();
    expect(
      screen.getByRole('heading', { name: /data.*privacy consent/i }),
    ).toBeInTheDocument();
  });

  it('renders the purpose list items', () => {
    renderModal();
    expect(screen.getByText(/record your voice during the session/i)).toBeInTheDocument();
    expect(screen.getByText(/transcribe your speech.*sarvam stt/i)).toBeInTheDocument();
    expect(screen.getByText(/process the transcript.*google gemini/i)).toBeInTheDocument();
    expect(screen.getByText(/generate a scorecard from your responses/i)).toBeInTheDocument();
  });

  it('renders supported languages', () => {
    renderModal();
    expect(screen.getByText(/english, hindi, telugu/i)).toBeInTheDocument();
  });

  it('renders 90-day retention period', () => {
    renderModal();
    // "90 days" appears in a <strong> tag inside the dd
    expect(screen.getByText(/90 days/i)).toBeInTheDocument();
    expect(screen.getByText(/voice recordings and transcripts are deleted after/i)).toBeInTheDocument();
  });

  it('renders DPDP Act 2023 rights copy with support email', () => {
    renderModal();
    expect(screen.getByText(/dpdp act 2023/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /support@intants\.com/i })).toBeInTheDocument();
  });

  // --- Accessibility ---

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
    const heading = document.getElementById(labelId!);
    expect(heading).not.toBeNull();
    expect(heading?.textContent).toMatch(/data.*privacy consent/i);
  });

  it('moves focus to the "I Agree" button on mount', () => {
    renderModal();
    expect(screen.getByRole('button', { name: /i agree/i })).toHaveFocus();
  });

  // --- Button interactions ---

  it('calls onAgree when "I Agree" is clicked', async () => {
    const user = userEvent.setup();
    const { props } = renderModal();

    await user.click(screen.getByRole('button', { name: /i agree/i }));

    await waitFor(() => {
      expect(props.onAgree).toHaveBeenCalledOnce();
    });
  });

  it('calls onDecline when "Decline" is clicked', async () => {
    const user = userEvent.setup();
    const { props } = renderModal();

    await user.click(screen.getByRole('button', { name: /decline/i }));

    expect(props.onDecline).toHaveBeenCalledOnce();
  });

  // --- Keyboard ---

  it('calls onDecline when Escape is pressed', async () => {
    const user = userEvent.setup();
    const { props } = renderModal();

    // Focus is on "I Agree" button; press Escape
    await user.keyboard('{Escape}');

    expect(props.onDecline).toHaveBeenCalledOnce();
  });

  // --- Submitting state ---

  it('shows a spinner and disables buttons while isSubmitting=true', () => {
    renderModal({ isSubmitting: true });
    expect(screen.getByRole('button', { name: /i agree/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /decline/i })).toBeDisabled();
    // Spinner text visible
    expect(screen.getByText(/saving/i)).toBeInTheDocument();
  });

  it('does not call onAgree when isSubmitting=true and button is clicked', async () => {
    const user = userEvent.setup();
    const onAgree = vi.fn().mockResolvedValue(undefined);
    renderModal({ isSubmitting: true, onAgree });

    // Click the disabled button
    await user.click(screen.getByRole('button', { name: /i agree/i }));

    expect(onAgree).not.toHaveBeenCalled();
  });

  // --- Error display ---

  it('shows error message when error prop is set', () => {
    renderModal({ error: 'Network error. Please try again.' });
    expect(screen.getByRole('alert')).toHaveTextContent(/network error/i);
  });

  it('does not render error alert when error is null', () => {
    renderModal({ error: null });
    // Only one alert should be absent — querySelectorAll to check specifically
    const alerts = screen.queryAllByRole('alert');
    expect(alerts).toHaveLength(0);
  });
});
