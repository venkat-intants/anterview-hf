// Tests for the upload deadline on the XHR multipart path.
//
// `xhr.timeout` defaults to 0 (no limit), so a socket that dies AFTER send()
// fires neither `onload` nor `onerror`: the promise never settles and the
// upload UI is stuck at whatever percentage it reached until the page reloads.
//
// These assert the observable contract:
//   • a stalled upload rejects, with a message the user can act on;
//   • it does NOT reject early — the deadline scales with the payload, so the
//     largest permitted request (25 × 5 MB bulk applicants) still gets enough
//     time on a slow uplink;
//   • a completed upload is unaffected by the timer;
//   • a stall on the retry AFTER a 401 → refresh still settles, i.e. the
//     deadline and the refresh path do not cancel each other out.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { setToken, clearToken } from '../api/tokenStore';
import { uploadTimeoutMs } from '../api/client';

// No Toaster is mounted here and the session-expired path toasts.
vi.mock('../lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

/** What the fake transport should do with the Nth attempt. */
type Behaviour = number | 'stall';

interface XhrRecord {
  /** The deadline the client installed on this attempt, in ms. */
  timeout: number;
}

/**
 * Install a fake XMLHttpRequest that honours `timeout` the way a browser does:
 * a scripted 'stall' never answers, and the deadline fires through setTimeout
 * (so fake timers drive it) unless the response already arrived.
 */
function installXhr(script: Behaviour[]): XhrRecord[] {
  const records: XhrRecord[] = [];
  let attempt = 0;

  class FakeXhr {
    status = 0;
    responseText = '{}';
    timeout = 0;
    withCredentials = false;
    upload: { onprogress: ((e: ProgressEvent) => void) | null } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    ontimeout: (() => void) | null = null;

    open(): void {}
    setRequestHeader(): void {}

    send(): void {
      const index = attempt;
      attempt += 1;
      records.push({ timeout: this.timeout });

      const behaviour = script[index] ?? script[script.length - 1] ?? 200;
      let answered = false;

      if (behaviour !== 'stall') {
        this.status = behaviour;
        void Promise.resolve().then(() => {
          answered = true;
          this.onload?.();
        });
      }

      setTimeout(() => {
        if (!answered) this.ontimeout?.();
      }, this.timeout);
    }
  }

  global.XMLHttpRequest = FakeXhr as unknown as typeof XMLHttpRequest;
  return records;
}

/** The refresh endpoint's JSON response, as a fetch-shaped double. */
function makeRefreshResponse(ok: boolean): Response {
  return {
    ok,
    status: ok ? 200 : 401,
    headers: new Headers(),
    json: vi.fn().mockResolvedValue({
      access_token: 'refreshed-token',
      expires_in: 900,
      user_id: 'u-1',
      roles: ['candidate'],
    }),
  } as unknown as Response;
}

function makeForm(bytes = 8): FormData {
  const form = new FormData();
  form.append('file', new File([new Uint8Array(bytes)], 'cv.pdf', { type: 'application/pdf' }));
  return form;
}

/**
 * A FormData stand-in reporting `bytes` of file payload. Used instead of a real
 * Blob for the 125 MB case: uploadTimeoutMs only walks entries, and allocating
 * 125 MB in jsdom to assert arithmetic would be a needless CI cost.
 */
function formOfSize(bytes: number): FormData {
  const file = { size: bytes } as unknown as File;
  return {
    forEach(cb: (value: FormDataEntryValue, key: string, parent: FormData) => void): void {
      cb(file, 'files', this as unknown as FormData);
    },
  } as unknown as FormData;
}

const OrigXhr = global.XMLHttpRequest;
const MB = 1024 * 1024;

beforeEach(() => {
  setToken('a-token');
  document.cookie = 'csrf_token=csrf-abc';
});

afterEach(() => {
  vi.useRealTimers();
  global.XMLHttpRequest = OrigXhr;
  clearToken();
  document.cookie = 'csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT';
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('uploadTimeoutMs — the deadline is sized from the payload', () => {
  it('is finite and generous even for an empty body', () => {
    const base = uploadTimeoutMs(new FormData());
    expect(base).toBeGreaterThanOrEqual(60_000);
    expect(Number.isFinite(base)).toBe(true);
  });

  it('adds transfer time at 1 Mbps — 5 MB buys ~40 more seconds', () => {
    const delta = uploadTimeoutMs(formOfSize(5 * MB)) - uploadTimeoutMs(new FormData());
    expect(delta).toBeGreaterThanOrEqual(39_000);
    expect(delta).toBeLessThanOrEqual(41_000);
  });

  it('still clears the largest permitted request: 25 × 5 MB bulk applicants', () => {
    // 125 MB at 125 KB/s is ~17 minutes of transfer alone; aborting there would
    // fail a legitimate batch rather than a dead socket.
    expect(uploadTimeoutMs(formOfSize(25 * 5 * MB))).toBeGreaterThan(17 * 60_000);
  });

  it('ignores the accompanying text fields', () => {
    const form = new FormData();
    form.append('target_job_title', 'Senior Backend Engineer');
    form.append('target_jd_text', 'x'.repeat(5000));
    expect(uploadTimeoutMs(form)).toBe(uploadTimeoutMs(new FormData()));
  });
});

describe('uploadWithProgress — a stalled upload settles instead of hanging', () => {
  it('rejects with an actionable message once the deadline passes', async () => {
    const { uploadWithProgress } = await import('../api/client');
    const records = installXhr(['stall']);
    vi.useFakeTimers();

    const pending = uploadWithProgress('https://api.test/candidate/resume', makeForm());
    const rejection = expect(pending).rejects.toThrow(/timed out/i);

    await vi.advanceTimersByTimeAsync(uploadTimeoutMs(makeForm()) + 1);
    await rejection;
    expect(records[0]?.timeout).toBeGreaterThan(0);
  });

  it('does not abort a slow-but-alive upload before its deadline', async () => {
    const { uploadWithProgress } = await import('../api/client');
    installXhr(['stall']);
    vi.useFakeTimers();

    let settled = false;
    const pending = uploadWithProgress('https://api.test/candidate/resume', makeForm()).catch(
      () => {
        settled = true;
      },
    );

    await vi.advanceTimersByTimeAsync(uploadTimeoutMs(makeForm()) - 1000);
    expect(settled).toBe(false);

    await vi.advanceTimersByTimeAsync(2000);
    await pending;
    expect(settled).toBe(true);
  });

  it('leaves a completed upload alone when the deadline later elapses', async () => {
    const { uploadWithProgress } = await import('../api/client');
    installXhr([200]);
    vi.useFakeTimers();

    const result = await uploadWithProgress<Record<string, never>>(
      'https://api.test/candidate/resume',
      makeForm(),
    );

    // Advance well past the deadline: a fired ontimeout on a settled promise is
    // a no-op, but a mis-wired one would have rejected the awaited call above.
    await vi.advanceTimersByTimeAsync(60 * 60_000);
    expect(result).toEqual({});
  });
});

describe('uploadWithProgress — the deadline coexists with 401 → refresh → retry', () => {
  it('gives the retry a fresh deadline and still settles if that stalls too', async () => {
    const { uploadWithProgress } = await import('../api/client');
    const records = installXhr([401, 'stall']);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeRefreshResponse(true)));
    vi.useFakeTimers();

    const pending = uploadWithProgress('https://api.test/candidate/resume', makeForm());
    const rejection = expect(pending).rejects.toThrow(/timed out/i);

    // Let the 401 → refresh → retry run (all microtasks) before the clock moves.
    await vi.advanceTimersByTimeAsync(0);
    // Two attempts, each with its own deadline — the retry re-uploads the body
    // and must not inherit the first attempt's already-spent clock.
    expect(records).toHaveLength(2);

    await vi.advanceTimersByTimeAsync(uploadTimeoutMs(makeForm()) + 1);
    await rejection;
    expect(records[1]?.timeout).toBe(records[0]?.timeout);
  });

  it('a 401 answered inside the deadline still refreshes and succeeds', async () => {
    const { uploadWithProgress } = await import('../api/client');
    installXhr([401, 200]);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeRefreshResponse(true)));

    await expect(
      uploadWithProgress('https://api.test/candidate/resume', makeForm()),
    ).resolves.toEqual({});
  });
});
