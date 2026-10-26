// Audit CA-H5: one tap on "End" used to end a real hiring interview, with no
// way back and no scorecard. The first press now only arms a confirmation.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const disconnect = vi.fn();
vi.mock('../hooks/useLiveKitInterview', () => ({
  useLiveKitInterview: () => ({
    status: 'connected',
    error: null,
    isMicEnabled: true,
    isCameraEnabled: false,
    videoRef: { current: null },
    localVideoRef: { current: null },
    connect: vi.fn(() => Promise.resolve()),
    disconnect,
    toggleMic: vi.fn(),
    toggleCamera: vi.fn(),
    interviewerStatus: 'joined',
    redispatchAttempt: 0,
    maxRedispatch: 2,
    retryInterviewer: vi.fn(() => Promise.resolve()),
  }),
}));
vi.mock('../features/proctoring/useProctoring', () => ({
  useProctoring: () => ({ ready: false, activeWarning: null, calibrating: false }),
}));
vi.mock('../api/integrity', () => ({ postIntegrityEvents: vi.fn() }));
vi.mock('../features/interview/useFullscreen', () => ({
  useFullscreen: () => ({ isFullscreen: true, supported: false }),
  requestFullscreen: vi.fn(),
  exitFullscreen: vi.fn(() => Promise.resolve()),
}));

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

import LiveKitInterview from '../features/interview/LiveKitInterview';

function renderRoom() {
  return render(
    <MemoryRouter>
      <LiveKitInterview sessionId="sess-1" />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  disconnect.mockResolvedValue(undefined);
});

describe('LiveKitInterview - ending', () => {
  it('does not end the interview on the first press', async () => {
    const user = userEvent.setup();
    renderRoom();
    await user.click(screen.getByRole('button', { name: 'End interview' }));

    expect(disconnect).not.toHaveBeenCalled();
    expect(screen.getByRole('alertdialog')).toHaveTextContent(/no scorecard will be generated/i);
    // The safe choice has focus, so a stray second Enter keeps going.
    expect(screen.getByRole('button', { name: 'Keep going' })).toHaveFocus();
  });

  it('can be backed out of', async () => {
    const user = userEvent.setup();
    renderRoom();
    await user.click(screen.getByRole('button', { name: 'End interview' }));
    await user.click(screen.getByRole('button', { name: 'Keep going' }));

    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(disconnect).not.toHaveBeenCalled();
  });

  it('backs out on Escape', async () => {
    const user = userEvent.setup();
    renderRoom();
    await user.click(screen.getByRole('button', { name: 'End interview' }));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('ends it once confirmed', async () => {
    const user = userEvent.setup();
    renderRoom();
    await user.click(screen.getByRole('button', { name: 'End interview' }));
    const dialog = screen.getByRole('alertdialog');
    await user.click(
      Array.from(dialog.querySelectorAll('button')).find((b) =>
        b.textContent?.includes('End interview'),
      ) as HTMLButtonElement,
    );

    await waitFor(() => expect(disconnect).toHaveBeenCalled());
    expect(navigate).toHaveBeenCalledWith('/interview/sess-1/complete', {
      state: { endedEarly: true },
      replace: true,
    });
  });
});
