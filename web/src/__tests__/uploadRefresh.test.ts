// Tests for the XHR upload path's 401 → refresh → retry (FE-1).
//
// `uploadWithProgress` is XHR-based because `fetch` cannot report request-body
// progress, so it could not reuse `fetchBlobWithAuth`. It therefore had no
// refresh-on-401 at all: a candidate whose 15-minute access token expired
// mid-upload simply lost the upload, at the highest-friction step in the funnel.
//
// These assert the OBSERVABLE outcome and the shared-primitive property:
//   • a 401 followed by a successful refresh re-issues the POST exactly once,
//     carrying the REFRESHED token;
//   • two concurrent uploads that both 401 share ONE refresh request — the
//     single-flight guarantee a second refresh implementation would destroy;
//   • a second 401 after refreshing does not loop.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { setToken, clearToken, getToken } from '../api/tokenStore';

// No Toaster is mounted here and the session-expired path toasts.
vi.mock('../lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

// ---------------------------------------------------------------------------
// XHR double
// ---------------------------------------------------------------------------

interface XhrCall {
  url: string;
  authHeader: string | undefined;
  withCredentials: boolean;
  contentTypeSet: boolean;
}

interface XhrScript {
  /** Statuses to answer with, in order. The last entry repeats if exhausted. */
  statuses: number[];
  responseText?: string;
  /** Resolve the Nth attempt only when its gate is released (concurrency tests). */
  gate?: () => Promise<void>;
}

/**
 * Install a fake XMLHttpRequest that answers each `open()`/`send()` pair with
 * the next scripted status. Returns the recorded calls so a test can assert
 * how many POSTs went out and which token each carried.
 */
function installXhr(script: XhrScript): XhrCall[] {
  const calls: XhrCall[] = [];
  let attempt = 0;

  class FakeXhr {
    status = 0;
    responseText = script.responseText ?? '{}';
    withCredentials = false;
    upload: { onprogress: ((e: ProgressEvent) => void) | null } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;

    private url = '';
    private authHeader: string | undefined;
    private contentTypeSet = false;

    open(_method: string, url: string): void {
      this.url = url;
    }

    setRequestHeader(name: string, value: string): void {
      if (name === 'Authorization') this.authHeader = value;
      if (name.toLowerCase() === 'content-type') this.contentTypeSet = true;
    }

    send(): void {
      const index = attempt;
      attempt += 1;
      calls.push({
        url: this.url,
        authHeader: this.authHeader,
        withCredentials: this.withCredentials,
        contentTypeSet: this.contentTypeSet,
      });
      this.status = script.statuses[index] ?? script.statuses[script.statuses.length - 1] ?? 200;

      const fire = (): void => {
        this.onload?.();
      };
      if (script.gate) {
        void script.gate().then(fire);
      } else {
        void Promise.resolve().then(fire);
      }
    }
  }

  global.XMLHttpRequest = FakeXhr as unknown as typeof XMLHttpRequest;
  return calls;
}

/** The refresh endpoint's JSON response, as a fetch-shaped double. */
function makeRefreshResponse(ok: boolean, token = 'refreshed-token'): Response {
  return {
    ok,
    status: ok ? 200 : 401,
    headers: new Headers(),
    json: vi.fn().mockResolvedValue({
      access_token: token,
      expires_in: 900,
      user_id: 'u-1',
      roles: ['candidate'],
    }),
  } as unknown as Response;
}

const OrigXhr = global.XMLHttpRequest;

beforeEach(() => {
  setToken('expired-token');
  // attemptRefresh short-circuits without a readable csrf_token cookie — a
  // genuine logged-in session always has one.
  document.cookie = 'csrf_token=csrf-abc';
});

afterEach(() => {
  global.XMLHttpRequest = OrigXhr;
  clearToken();
  document.cookie = 'csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT';
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function makeForm(): FormData {
  const form = new FormData();
  form.append('file', new File(['%PDF-1.4'], 'cv.pdf', { type: 'application/pdf' }));
  return form;
}

describe('uploadWithProgress — token expires mid-upload', () => {
  it('refreshes and re-issues the upload once, resolving with the retry body', async () => {
    const calls = installXhr({
      statuses: [401, 200],
      responseText: JSON.stringify({ resume_s3_key: 'resumes/abc.pdf' }),
    });
    const fetchMock = vi.fn().mockResolvedValue(makeRefreshResponse(true));
    vi.stubGlobal('fetch', fetchMock);

    const { uploadWithProgress } = await import('../api/client');
    const result = await uploadWithProgress<{ resume_s3_key: string }>(
      'https://api.test/candidate/resume',
      makeForm(),
    );

    expect(result.resume_s3_key).toBe('resumes/abc.pdf');
    expect(calls).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('re-issues with the REFRESHED token, not the expired one', async () => {
    const calls = installXhr({ statuses: [401, 200] });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(makeRefreshResponse(true, 'brand-new-token')),
    );

    const { uploadWithProgress } = await import('../api/client');
    await uploadWithProgress('https://api.test/candidate/resume', makeForm());

    expect(calls[0]?.authHeader).toBe('Bearer expired-token');
    expect(calls[1]?.authHeader).toBe('Bearer brand-new-token');
    expect(getToken()).toBe('brand-new-token');
  });

  it('still lets the browser own the multipart boundary on the retry', async () => {
    // Setting Content-Type by hand omits the boundary and the server rejects
    // the body — a regression that would only show up on the retried upload.
    const calls = installXhr({ statuses: [401, 200] });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeRefreshResponse(true)));

    const { uploadWithProgress } = await import('../api/client');
    await uploadWithProgress('https://api.test/candidate/resume', makeForm());

    expect(calls.every((c) => !c.contentTypeSet)).toBe(true);
    expect(calls.every((c) => c.withCredentials)).toBe(true);
  });

  it('does not loop: a second 401 after refreshing surfaces as an error', async () => {
    const calls = installXhr({
      statuses: [401, 401],
      responseText: JSON.stringify({ detail: 'Not authenticated' }),
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeRefreshResponse(true)));

    const { uploadWithProgress } = await import('../api/client');

    await expect(
      uploadWithProgress('https://api.test/candidate/resume', makeForm()),
    ).rejects.toThrow('Not authenticated');
    expect(calls).toHaveLength(2);
  });

  it('clears the session and throws when the refresh itself fails', async () => {
    // jsdom prints "Not implemented: navigation" for the intended
    // `window.location.href = '/login'` bounce — expected noise, not a failure.
    const calls = installXhr({ statuses: [401] });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeRefreshResponse(false)));

    const { uploadWithProgress } = await import('../api/client');

    await expect(
      uploadWithProgress('https://api.test/candidate/resume', makeForm()),
    ).rejects.toThrow('Session expired');
    expect(getToken()).toBeNull();
    expect(calls).toHaveLength(1);
  });

  it('leaves non-401 failures exactly as they were — no refresh attempted', async () => {
    const calls = installXhr({
      statuses: [400],
      responseText: JSON.stringify({ detail: 'Only PDF files are accepted.' }),
    });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const { uploadWithProgress } = await import('../api/client');

    await expect(
      uploadWithProgress('https://api.test/candidate/resume', makeForm()),
    ).rejects.toThrow('Only PDF files are accepted.');
    expect(calls).toHaveLength(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('uploadWithProgress — shares the single-flight refresh', () => {
  it('two concurrent uploads that both 401 fire exactly ONE refresh', async () => {
    // Hold both first attempts open until each has been recorded, so the two
    // 401s are genuinely in flight together rather than serialised by luck.
    let release!: () => void;
    const bothStarted = new Promise<void>((resolve) => {
      release = resolve;
    });

    const calls: XhrCall[] = [];
    let attempt = 0;
    class FakeXhr {
      status = 0;
      responseText = '{}';
      withCredentials = false;
      upload: { onprogress: ((e: ProgressEvent) => void) | null } = { onprogress: null };
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      private authHeader: string | undefined;

      open(): void {}
      setRequestHeader(name: string, value: string): void {
        if (name === 'Authorization') this.authHeader = value;
      }
      send(): void {
        const index = attempt;
        attempt += 1;
        calls.push({
          url: '',
          authHeader: this.authHeader,
          withCredentials: this.withCredentials,
          contentTypeSet: false,
        });
        // Attempts 0 and 1 are the two originals; both 401. Later attempts are
        // the retries and succeed.
        this.status = index < 2 ? 401 : 200;
        if (index === 1) release();
        void (index < 2 ? bothStarted : Promise.resolve()).then(() => this.onload?.());
      }
    }
    global.XMLHttpRequest = FakeXhr as unknown as typeof XMLHttpRequest;

    const fetchMock = vi.fn().mockResolvedValue(makeRefreshResponse(true, 'one-shared-token'));
    vi.stubGlobal('fetch', fetchMock);

    const { uploadWithProgress } = await import('../api/client');
    await Promise.all([
      uploadWithProgress('https://api.test/candidate/resume', makeForm()),
      uploadWithProgress('https://api.test/hr/applicants', makeForm()),
    ]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(4);
    expect(calls[2]?.authHeader).toBe('Bearer one-shared-token');
    expect(calls[3]?.authHeader).toBe('Bearer one-shared-token');
  });
});

describe('uploadWithProgress — behaviour preserved from before FE-1', () => {
  it('reports upload progress as a percentage', async () => {
    const onProgress = vi.fn();
    class FakeXhr {
      status = 200;
      responseText = '{}';
      withCredentials = false;
      upload: { onprogress: ((e: ProgressEvent) => void) | null } = { onprogress: null };
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      open(): void {}
      setRequestHeader(): void {}
      send(): void {
        void Promise.resolve().then(() => {
          this.upload.onprogress?.({
            lengthComputable: true,
            loaded: 250,
            total: 1000,
          } as ProgressEvent);
          this.onload?.();
        });
      }
    }
    global.XMLHttpRequest = FakeXhr as unknown as typeof XMLHttpRequest;

    const { uploadWithProgress } = await import('../api/client');
    await uploadWithProgress('https://api.test/candidate/resume', makeForm(), onProgress);

    expect(onProgress).toHaveBeenCalledWith(25);
  });

  it('rejects with a network error when the transport fails', async () => {
    class FakeXhr {
      status = 0;
      responseText = '';
      withCredentials = false;
      upload: { onprogress: ((e: ProgressEvent) => void) | null } = { onprogress: null };
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      open(): void {}
      setRequestHeader(): void {}
      send(): void {
        void Promise.resolve().then(() => this.onerror?.());
      }
    }
    global.XMLHttpRequest = FakeXhr as unknown as typeof XMLHttpRequest;

    const { uploadWithProgress } = await import('../api/client');

    await expect(
      uploadWithProgress('https://api.test/candidate/resume', makeForm()),
    ).rejects.toThrow(/network error/i);
  });

  it('rejects with a clear message when a 2xx body is not JSON', async () => {
    installXhr({ statuses: [200], responseText: '<html>gateway</html>' });

    const { uploadWithProgress } = await import('../api/client');

    await expect(
      uploadWithProgress('https://api.test/candidate/resume', makeForm()),
    ).rejects.toThrow('Invalid response from server');
  });
});
