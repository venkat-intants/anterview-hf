// useExamCamera — getUserMedia acquisition for on-device exam proctoring.
// Every path must degrade gracefully: no camera, denied permission, or no
// getUserMedia API at all must never throw and must never block the exam.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useExamCamera } from '../pages/exam/useExamCamera';

function stubGetUserMedia(impl: (...a: unknown[]) => Promise<MediaStream>) {
  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia: vi.fn(impl) },
    configurable: true,
  });
}

function fakeStream(): MediaStream {
  const track = { stop: vi.fn() } as unknown as MediaStreamTrack;
  return { getTracks: () => [track] } as unknown as MediaStream;
}

describe('useExamCamera', () => {
  afterEach(() => {
    // jsdom has no mediaDevices by default — restore that baseline so one
    // test's stub never leaks into the next.
    Object.defineProperty(navigator, 'mediaDevices', {
      value: undefined,
      configurable: true,
    });
    vi.clearAllMocks();
  });

  it('does nothing while disabled', () => {
    const { result } = renderHook(() => useExamCamera({ enabled: false }));
    expect(result.current.available).toBe(false);
    expect(result.current.settled).toBe(false);
    expect(result.current.denied).toBe(false);
  });

  it('degrades gracefully when the browser has no getUserMedia at all', async () => {
    Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true });
    const { result } = renderHook(() => useExamCamera({ enabled: true }));
    await waitFor(() => expect(result.current.settled).toBe(true));
    expect(result.current.denied).toBe(true);
    expect(result.current.available).toBe(false);
  });

  it('degrades gracefully when getUserMedia rejects (permission denied)', async () => {
    stubGetUserMedia(() => Promise.reject(new Error('NotAllowedError')));
    const { result } = renderHook(() => useExamCamera({ enabled: true }));
    await waitFor(() => expect(result.current.settled).toBe(true));
    expect(result.current.denied).toBe(true);
    expect(result.current.available).toBe(false);
  });

  it('attaches the stream and reports available on success', async () => {
    const stream = fakeStream();
    stubGetUserMedia(() => Promise.resolve(stream));
    const { result } = renderHook(() => useExamCamera({ enabled: true }));
    await waitFor(() => expect(result.current.available).toBe(true));
    expect(result.current.denied).toBe(false);
    expect(result.current.settled).toBe(true);
  });

  it('stops every track when disabled again (never leaves the camera running)', async () => {
    const stopSpy = vi.fn();
    const track = { stop: stopSpy } as unknown as MediaStreamTrack;
    const stream = { getTracks: () => [track] } as unknown as MediaStream;
    stubGetUserMedia(() => Promise.resolve(stream));

    const { result, rerender } = renderHook(({ enabled }) => useExamCamera({ enabled }), {
      initialProps: { enabled: true },
    });
    await waitFor(() => expect(result.current.available).toBe(true));

    rerender({ enabled: false });
    expect(stopSpy).toHaveBeenCalled();
  });
});
