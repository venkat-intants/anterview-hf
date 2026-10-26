// A candidate must never be left alone in a room with no interviewer (2026-10
// load test: 12.7 minutes observed). The watchdog asks the backend to dispatch
// again, then gives up visibly.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import {
  useInterviewerWatchdog,
  INTERVIEWER_JOIN_TIMEOUT_MS,
  MAX_REDISPATCH,
} from '../hooks/useInterviewerWatchdog';

const T = INTERVIEWER_JOIN_TIMEOUT_MS;

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

function setup(opts: { enabled?: boolean; present?: () => boolean } = {}) {
  const redispatch = vi.fn(() => Promise.resolve({ status: 'dispatched' }));
  const present = opts.present ?? (() => false);
  const hook = renderHook(
    (props: { enabled: boolean }) =>
      useInterviewerWatchdog({
        enabled: props.enabled,
        hasInterviewer: present,
        redispatch,
      }),
    { initialProps: { enabled: opts.enabled ?? true } },
  );
  return { ...hook, redispatch };
}

describe('useInterviewerWatchdog', () => {
  it('does nothing while the room is not connected', () => {
    const { result, redispatch } = setup({ enabled: false });
    act(() => {
      vi.advanceTimersByTime(T * 5);
    });
    expect(redispatch).not.toHaveBeenCalled();
    expect(result.current.status).toBe('waiting');
  });

  it('does not ask for anything before the timeout', () => {
    const { result, redispatch } = setup();
    act(() => {
      vi.advanceTimersByTime(T - 1);
    });
    expect(redispatch).not.toHaveBeenCalled();
    expect(result.current.status).toBe('waiting');
  });

  it('asks the backend to dispatch again when nobody has joined', () => {
    const { result, redispatch } = setup();
    act(() => {
      vi.advanceTimersByTime(T);
    });
    expect(redispatch).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe('retrying');
    expect(result.current.attempt).toBe(1);
  });

  it('retries a bounded number of times, then reports unavailable', () => {
    const { result, redispatch } = setup();
    for (let i = 0; i <= MAX_REDISPATCH; i += 1) {
      act(() => {
        vi.advanceTimersByTime(T);
      });
    }
    expect(redispatch).toHaveBeenCalledTimes(MAX_REDISPATCH);
    expect(result.current.status).toBe('unavailable');

    // And it stops: no endless polling of the backend.
    act(() => {
      vi.advanceTimersByTime(T * 10);
    });
    expect(redispatch).toHaveBeenCalledTimes(MAX_REDISPATCH);
  });

  it('does not ask again once the interviewer has joined', () => {
    const { result, redispatch } = setup();
    act(() => {
      vi.advanceTimersByTime(T);
    });
    expect(result.current.status).toBe('retrying');
    act(() => {
      result.current.markJoined();
    });
    act(() => {
      vi.advanceTimersByTime(T * 5);
    });
    expect(result.current.status).toBe('joined');
    expect(redispatch).toHaveBeenCalledTimes(1);
  });

  it('notices an interviewer that is already in the room when the timer fires', () => {
    const { result, redispatch } = setup({ present: () => true });
    act(() => {
      vi.advanceTimersByTime(T);
    });
    expect(result.current.status).toBe('joined');
    expect(redispatch).not.toHaveBeenCalled();
  });

  it('survives a failing redispatch request and keeps counting down', async () => {
    const redispatch = vi.fn(() => Promise.reject(new Error('503')));
    const { result } = renderHook(() =>
      useInterviewerWatchdog({ enabled: true, hasInterviewer: () => false, redispatch }),
    );
    for (let i = 0; i <= MAX_REDISPATCH; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(T);
      });
    }
    expect(result.current.status).toBe('unavailable');
  });

  it('reset() gives a fresh budget for a new connection attempt', () => {
    const { result, redispatch } = setup();
    for (let i = 0; i <= MAX_REDISPATCH; i += 1) {
      act(() => {
        vi.advanceTimersByTime(T);
      });
    }
    expect(result.current.status).toBe('unavailable');
    act(() => {
      result.current.reset();
    });
    expect(result.current.status).toBe('waiting');
    expect(result.current.attempt).toBe(0);
    redispatch.mockClear();
    act(() => {
      vi.advanceTimersByTime(T);
    });
    expect(redispatch).toHaveBeenCalledTimes(1);
  });

  it('stops at once, with limitReached, when the server refuses further re-dispatches', async () => {
    const redispatch = vi.fn(() => Promise.reject(new Error('429')));
    const { result } = renderHook(() =>
      useInterviewerWatchdog({
        enabled: true,
        hasInterviewer: () => false,
        redispatch,
        isFinalError: () => true,
      }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(T);
    });
    expect(result.current.status).toBe('unavailable');
    expect(result.current.limitReached).toBe(true);
    expect(redispatch).toHaveBeenCalledTimes(1);
  });

  it('a non-final failure does not set limitReached', async () => {
    const redispatch = vi.fn(() => Promise.reject(new Error('503')));
    const { result } = renderHook(() =>
      useInterviewerWatchdog({
        enabled: true,
        hasInterviewer: () => false,
        redispatch,
        isFinalError: () => false,
      }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(T);
    });
    expect(result.current.limitReached).toBe(false);
    expect(result.current.status).toBe('retrying');
  });

  it('stops the timers when the room disconnects', () => {
    const { rerender, redispatch } = setup();
    rerender({ enabled: false });
    act(() => {
      vi.advanceTimersByTime(T * 5);
    });
    expect(redispatch).not.toHaveBeenCalled();
  });
});
