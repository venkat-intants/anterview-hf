// The device check on StartInterview must only report what it actually tested:
// it opens the microphone, stops every track at once, and names the specific
// reason when something is wrong.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from '../context/AuthContext';
import { ConsentProvider } from '../context/ConsentContext';
import StartInterview from '../pages/StartInterview';

vi.mock('../api/jobs', () => ({ createCustomJob: vi.fn() }));
vi.mock('../api/sessions', () => ({ createSession: vi.fn() }));
vi.mock('../api/avatars', () => ({ getAvatars: vi.fn().mockResolvedValue([]) }));
vi.mock('../api/auth', () => ({ getMe: vi.fn().mockResolvedValue({ has_resume: true }) }));
vi.mock('../api/consent', () => ({
  getConsentStatus: vi.fn().mockResolvedValue({ consented: true }),
  postConsent: vi.fn(),
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AuthProvider>
          <ConsentProvider>
            <StartInterview />
          </ConsentProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

interface FakeTrack {
  stop: ReturnType<typeof vi.fn>;
}

function fakeStream(tracks: FakeTrack[]): MediaStream {
  return { getTracks: () => tracks } as unknown as MediaStream;
}

function setMediaDevices(value: unknown): void {
  Object.defineProperty(navigator, 'mediaDevices', { value, configurable: true });
}

function domError(name: string): DOMException {
  return new DOMException('x', name);
}

function micRow(): HTMLElement {
  const li = screen.getByText('Microphone').closest('li');
  if (!li) throw new Error('microphone row missing');
  return li;
}

describe('StartInterview device check', () => {
  const originalRtc = (globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection;

  beforeEach(() => {
    localStorage.clear();
    (globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection = class {};
  });

  afterEach(() => {
    (globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection = originalRtc;
    setMediaDevices(undefined);
  });

  it('starts honestly: nothing is claimed before the check runs', () => {
    renderPage();
    expect(screen.getAllByText('Not checked yet')).toHaveLength(3);
    expect(screen.queryByText('Ready')).not.toBeInTheDocument();
  });

  it('passes when the microphone opens, and stops every track it opened', async () => {
    const tracks = [{ stop: vi.fn() }, { stop: vi.fn() }];
    const getUserMedia = vi.fn().mockResolvedValue(fakeStream(tracks));
    setMediaDevices({ getUserMedia });
    renderPage();

    await userEvent.click(screen.getByRole('button', { name: 'Run device check' }));

    await waitFor(() => expect(micRow()).toHaveTextContent('Ready'));
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    tracks.forEach((t) => expect(t.stop).toHaveBeenCalledTimes(1));
    expect(screen.getByText('Supported')).toBeInTheDocument();
  });

  it('never asks for the camera', async () => {
    const getUserMedia = vi.fn().mockResolvedValue(fakeStream([]));
    setMediaDevices({ getUserMedia });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: 'Run device check' }));
    await waitFor(() => expect(getUserMedia).toHaveBeenCalled());
    expect(getUserMedia.mock.calls[0]?.[0]).not.toHaveProperty('video');
  });

  it('explains how to allow the microphone when permission is denied', async () => {
    setMediaDevices({ getUserMedia: vi.fn().mockRejectedValue(domError('NotAllowedError')) });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: 'Run device check' }));
    await waitFor(() => expect(micRow()).toHaveTextContent(/Microphone access is blocked/));
    expect(micRow()).toHaveTextContent('Needs attention');
    expect(micRow()).not.toHaveTextContent('Ready');
  });

  it('says so when no microphone is found', async () => {
    setMediaDevices({ getUserMedia: vi.fn().mockRejectedValue(domError('NotFoundError')) });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: 'Run device check' }));
    await waitFor(() => expect(micRow()).toHaveTextContent(/No microphone was found/));
  });

  it('does not test the microphone when the API is unavailable', async () => {
    setMediaDevices(undefined);
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: 'Run device check' }));
    await waitFor(() =>
      expect(screen.getByText(/cannot run a voice interview/)).toBeInTheDocument(),
    );
    expect(micRow()).toHaveTextContent(/Not checked, because this browser/);
    expect(micRow()).not.toHaveTextContent('Ready');
  });

  it('ignores a second click while a request is pending, then allows a re-run', async () => {
    let resolve: (s: MediaStream) => void = () => undefined;
    const getUserMedia = vi.fn().mockImplementation(
      () =>
        new Promise<MediaStream>((r) => {
          resolve = r;
        }),
    );
    setMediaDevices({ getUserMedia });
    renderPage();

    const button = screen.getByRole('button', { name: 'Run device check' });
    await userEvent.click(button);
    expect(screen.getByRole('button', { name: 'Checking…' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Checking…' }));
    expect(getUserMedia).toHaveBeenCalledTimes(1);

    resolve(fakeStream([]));
    await waitFor(() => expect(micRow()).toHaveTextContent('Ready'));
    await userEvent.click(screen.getByRole('button', { name: 'Run device check' }));
    expect(getUserMedia).toHaveBeenCalledTimes(2);
  });

  it('releases the microphone when the page unmounts mid-request', async () => {
    const track = { stop: vi.fn() };
    let resolve: (s: MediaStream) => void = () => undefined;
    setMediaDevices({
      getUserMedia: vi.fn().mockImplementation(
        () =>
          new Promise<MediaStream>((r) => {
            resolve = r;
          }),
      ),
    });
    const { unmount } = renderPage();
    await userEvent.click(screen.getByRole('button', { name: 'Run device check' }));
    unmount();
    resolve(fakeStream([track]));
    await waitFor(() => expect(track.stop).toHaveBeenCalledTimes(1));
  });
});
