// What the candidate SEES while their interviewer is (not) arriving: never a
// silent wait. waiting -> "connecting", retrying -> says so with the attempt,
// unavailable -> a clear message and a way to try again.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import type { InterviewerStatus } from '../hooks/useInterviewerWatchdog';

const retryInterviewer = vi.fn(() => Promise.resolve());
let interviewerStatus: InterviewerStatus = 'waiting';
let redispatchAttempt = 0;

vi.mock('../hooks/useLiveKitInterview', () => ({
  useLiveKitInterview: () => ({
    status: 'connected',
    error: null,
    isMicEnabled: true,
    isCameraEnabled: false,
    videoRef: { current: null },
    localVideoRef: { current: null },
    connect: vi.fn(() => Promise.resolve()),
    disconnect: vi.fn(() => Promise.resolve()),
    toggleMic: vi.fn(),
    toggleCamera: vi.fn(),
    interviewerStatus,
    redispatchAttempt,
    maxRedispatch: 2,
    retryInterviewer,
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
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
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
  interviewerStatus = 'waiting';
  redispatchAttempt = 0;
});

describe('LiveKitInterview - waiting for the interviewer', () => {
  it('says it is connecting while the first dispatch is in flight', () => {
    renderRoom();
    expect(screen.getByText('Connecting you to your interviewer…')).toBeInTheDocument();
  });

  it('says so when it is asking for another interviewer', () => {
    interviewerStatus = 'retrying';
    redispatchAttempt = 1;
    renderRoom();
    expect(screen.getByText('Still connecting your interviewer…')).toBeInTheDocument();
    expect(screen.getByText(/attempt 1 of 2/i)).toBeInTheDocument();
  });

  it('gives a clear message and a retry when it has given up', async () => {
    interviewerStatus = 'unavailable';
    redispatchAttempt = 2;
    const user = userEvent.setup();
    renderRoom();

    expect(screen.getByText('We could not reach your interviewer.')).toBeInTheDocument();
    expect(screen.queryByText('Connecting you to your interviewer…')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(retryInterviewer).toHaveBeenCalledTimes(1);
  });
});
