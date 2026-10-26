// The REAL useLiveKitInterview hook with only livekit-client and the HTTP layer
// faked. Review blocker B1: "Try again" used to run disconnect() -> status
// 'disconnected' -> LiveKitInterview's "interview is over" effect navigated to
// /complete, stranding the candidate on a page that polls for a scorecard that
// never exists. A test that mocks the hook cannot see that, so this one does not.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ApiError } from '../api/client';

vi.mock('livekit-client', () => {
  const ConnectionState = {
    Connected: 'connected',
    Connecting: 'connecting',
    Reconnecting: 'reconnecting',
    Disconnected: 'disconnected',
  };
  const RoomEvent = {
    ConnectionStateChanged: 'cs',
    Disconnected: 'dc',
    TrackSubscribed: 'ts',
    TrackUnsubscribed: 'tu',
    ParticipantConnected: 'pc',
  };
  const Track = {
    Kind: { Video: 'video', Audio: 'audio' },
    Source: { Camera: 'camera' },
  };
  class Room {
    state = 'disconnected';
    remoteParticipants = new Map<string, unknown>();
    private listeners = new Map<string, ((...a: unknown[]) => void)[]>();
    localParticipant = {
      setMicrophoneEnabled: () => Promise.resolve(),
      setCameraEnabled: () => Promise.resolve(),
      getTrackPublication: () => undefined,
    };
    on(event: string, fn: (...a: unknown[]) => void) {
      this.listeners.set(event, [...(this.listeners.get(event) ?? []), fn]);
      return this;
    }
    removeAllListeners() {
      this.listeners.clear();
      return this;
    }
    private emit(event: string, ...args: unknown[]) {
      (this.listeners.get(event) ?? []).forEach((fn) => fn(...args));
    }
    connect() {
      this.state = 'connected';
      this.emit('cs', 'connected');
      return Promise.resolve();
    }
    disconnect() {
      this.state = 'disconnected';
      this.emit('cs', 'disconnected');
      this.emit('dc');
      return Promise.resolve();
    }
  }
  return { Room, RoomEvent, ConnectionState, Track };
});

const getRoomToken = vi.fn(() =>
  Promise.resolve({ url: 'wss://x.invalid', token: 't', room_name: 'sess-1' }),
);
const redispatchInterviewer = vi.fn((): Promise<unknown> => Promise.resolve({}));
vi.mock('../api/rooms', () => ({
  getRoomToken: () => getRoomToken(),
  redispatchInterviewer: () => redispatchInterviewer(),
}));
vi.mock('../lib/toast', () => ({ toast: { error: vi.fn(), warning: vi.fn() } }));
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

async function renderRoom() {
  render(
    <MemoryRouter>
      <LiveKitInterview sessionId="sess-1" />
    </MemoryRouter>,
  );
  // Let connect() (token -> room.connect) settle.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  redispatchInterviewer.mockImplementation(() => Promise.resolve({}));
});
afterEach(() => {
  vi.useRealTimers();
});

describe('LiveKitInterview - retrying when no interviewer ever joins (real hook)', () => {
  it('Try again rejoins with a fresh token and never navigates away', async () => {
    await renderRoom();
    expect(getRoomToken).toHaveBeenCalledTimes(1);

    // 20 s + 20 s + 20 s with nobody joining: two re-dispatches, then give up.
    // One act() per timeout: each re-dispatch re-renders and arms the next timer.
    for (let i = 0; i < 3; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20_001);
      });
    }
    expect(redispatchInterviewer).toHaveBeenCalledTimes(2);
    expect(screen.getByText('We could not reach your interviewer.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    // A fresh join (new token => new slot reservation) ...
    expect(getRoomToken).toHaveBeenCalledTimes(2);
    // ... and the candidate stays on the interview page.
    expect(navigate).not.toHaveBeenCalled();
    // The retry starts over rather than showing the stale failure.
    expect(screen.queryByText('We could not reach your interviewer.')).toBeNull();
  });

  it('says so when the server refuses further re-dispatches (429)', async () => {
    redispatchInterviewer.mockImplementation(() =>
      Promise.reject(new ApiError('Too many requests', 429)),
    );
    await renderRoom();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(21_000);
    });

    // One attempt, not the whole countdown: the server said it will not try again.
    expect(redispatchInterviewer).toHaveBeenCalledTimes(1);
    expect(
      screen.getByText(/We tried several times and could not start your interviewer/),
    ).toBeInTheDocument();
  });
});
