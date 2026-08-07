// Tests for the authenticated blob download path (FE-3).
//
// The two binary/streamed downloads (admin CSV export, HR XLSX question
// template) legitimately bypass clientFetch's JSON parsing, but used to bypass
// its refresh-on-401 too — so a token that expired mid-session turned the
// download into a dead button. These tests assert the OBSERVABLE outcome: the
// download succeeds across an expiry, and the retry carries the refreshed token.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { setToken, clearToken, getToken } from '../api/tokenStore';

// Sonner has no toaster mounted here; the session-expired path toasts.
vi.mock('../lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

/**
 * A binary-ish Response: `json()` REJECTS, so any accidental re-introduction of
 * JSON parsing on this path fails loudly instead of silently mangling a file.
 */
function makeBinaryResponse(status: number, body: string): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(),
    text: vi.fn().mockResolvedValue(body),
    blob: vi.fn().mockResolvedValue(new Blob([body])),
    json: vi.fn().mockRejectedValue(new SyntaxError('not JSON')),
  } as unknown as Response;
}

/** The refresh endpoint's JSON response. */
function makeRefreshResponse(ok: boolean, token = 'refreshed-token'): Response {
  return {
    ok,
    status: ok ? 200 : 401,
    headers: new Headers(),
    json: vi.fn().mockResolvedValue({
      access_token: token,
      expires_in: 900,
      user_id: 'u-1',
      roles: ['hr_manager'],
    }),
  } as unknown as Response;
}

function authHeaderOf(call: unknown): string | undefined {
  const [, init] = call as [string, RequestInit];
  return (init.headers as Record<string, string> | undefined)?.Authorization;
}

beforeEach(() => {
  setToken('expired-token');
  // attemptRefresh short-circuits without a readable csrf_token cookie — a
  // genuine logged-in session always has one.
  document.cookie = 'csrf_token=csrf-abc';
});

afterEach(() => {
  clearToken();
  document.cookie = 'csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT';
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('fetchBlobWithAuth — happy path', () => {
  it('returns the raw Response without parsing the body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeBinaryResponse(200, 'a,b,c')));

    const { fetchBlobWithAuth } = await import('../api/client');
    const res = await fetchBlobWithAuth('https://api.test/admin/export.csv');

    expect(res.status).toBe(200);
    expect(await res.text()).toBe('a,b,c');
  });

  it('sends the stored access token and the refresh cookie', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeBinaryResponse(200, 'x'));
    vi.stubGlobal('fetch', fetchMock);

    const { fetchBlobWithAuth } = await import('../api/client');
    await fetchBlobWithAuth('https://api.test/admin/export.csv');

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.credentials).toBe('include');
    expect(authHeaderOf(fetchMock.mock.calls[0])).toBe('Bearer expired-token');
  });

  it('omits Authorization entirely when no token is stored', async () => {
    clearToken();
    const fetchMock = vi.fn().mockResolvedValue(makeBinaryResponse(200, 'x'));
    vi.stubGlobal('fetch', fetchMock);

    const { fetchBlobWithAuth } = await import('../api/client');
    await fetchBlobWithAuth('https://api.test/admin/export.csv');

    expect(authHeaderOf(fetchMock.mock.calls[0])).toBeUndefined();
  });

  it('passes a non-401 error response through untouched for the caller to handle', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeBinaryResponse(403, '')));

    const { fetchBlobWithAuth } = await import('../api/client');
    const res = await fetchBlobWithAuth('https://api.test/admin/export.csv');

    expect(res.ok).toBe(false);
    expect(res.status).toBe(403);
  });
});

describe('fetchBlobWithAuth — token expired mid-session', () => {
  it('refreshes and retries once, returning the successful retry', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makeBinaryResponse(401, ''))
      .mockResolvedValueOnce(makeRefreshResponse(true))
      .mockResolvedValueOnce(makeBinaryResponse(200, 'a,b,c'));
    vi.stubGlobal('fetch', fetchMock);

    const { fetchBlobWithAuth } = await import('../api/client');
    const res = await fetchBlobWithAuth('https://api.test/admin/export.csv');

    expect(res.status).toBe(200);
    expect(await res.text()).toBe('a,b,c');
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('retries with the REFRESHED token, not the expired one', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makeBinaryResponse(401, ''))
      .mockResolvedValueOnce(makeRefreshResponse(true, 'brand-new-token'))
      .mockResolvedValueOnce(makeBinaryResponse(200, 'ok'));
    vi.stubGlobal('fetch', fetchMock);

    const { fetchBlobWithAuth } = await import('../api/client');
    await fetchBlobWithAuth('https://api.test/admin/export.csv');

    expect(authHeaderOf(fetchMock.mock.calls[0])).toBe('Bearer expired-token');
    expect(authHeaderOf(fetchMock.mock.calls[2])).toBe('Bearer brand-new-token');
    expect(getToken()).toBe('brand-new-token');
  });

  it('does not retry more than once — a second 401 is returned as-is', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makeBinaryResponse(401, ''))
      .mockResolvedValueOnce(makeRefreshResponse(true))
      .mockResolvedValueOnce(makeBinaryResponse(401, ''));
    vi.stubGlobal('fetch', fetchMock);

    const { fetchBlobWithAuth } = await import('../api/client');
    const res = await fetchBlobWithAuth('https://api.test/admin/export.csv');

    expect(res.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('throws (rather than returning the 401) when the session cannot be refreshed', async () => {
    // No csrf_token cookie ⇒ attemptRefresh short-circuits to "not logged in".
    // jsdom prints "Not implemented: navigation" on the stderr stream for the
    // intended `window.location.href = '/login'` bounce — expected noise, not a
    // failure; jsdom's location is unforgeable so it cannot be stubbed away.
    document.cookie = 'csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT';
    const fetchMock = vi.fn().mockResolvedValue(makeBinaryResponse(401, ''));
    vi.stubGlobal('fetch', fetchMock);

    const { fetchBlobWithAuth } = await import('../api/client');

    await expect(fetchBlobWithAuth('https://api.test/admin/export.csv')).rejects.toThrow(
      'Session expired',
    );
    expect(getToken()).toBeNull();
  });
});

describe('attemptRefresh — single-flight slot', () => {
  // Regression: the logged-out probe AuthProvider fires on mount short-circuits
  // on the missing csrf cookie WITHOUT awaiting, which used to leave the
  // single-flight slot holding an already-settled `false`. Every later refresh
  // then reused it, so the first token expiry after a successful login logged
  // the user straight back out.
  it('a short-circuited refresh does not pin later refreshes to its result', async () => {
    document.cookie = 'csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT';
    const { attemptRefresh } = await import('../api/client');

    await expect(attemptRefresh('https://api.test')).resolves.toBe(false);

    // The user logs in — a csrf cookie now exists and refresh should work.
    document.cookie = 'csrf_token=csrf-abc';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeRefreshResponse(true, 'fresh-token')));

    await expect(attemptRefresh('https://api.test')).resolves.toBe(true);
    expect(getToken()).toBe('fresh-token');
  });

  it('concurrent callers share one in-flight refresh request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeRefreshResponse(true, 'shared-token'));
    vi.stubGlobal('fetch', fetchMock);

    const { attemptRefresh } = await import('../api/client');
    const results = await Promise.all([
      attemptRefresh('https://api.test'),
      attemptRefresh('https://api.test'),
      attemptRefresh('https://api.test'),
    ]);

    expect(results).toEqual([true, true, true]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('downloadQuestionTemplate — survives an expiry at the call site', () => {
  beforeEach(() => {
    // jsdom implements neither object URLs nor anchor-triggered downloads.
    const urlShim = URL as unknown as {
      createObjectURL: (blob: Blob) => string;
      revokeObjectURL: (url: string) => void;
    };
    urlShim.createObjectURL = vi.fn().mockReturnValue('blob:mock');
    urlShim.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  });

  it('resolves after a 401 → refresh → retry instead of failing the download', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makeBinaryResponse(401, ''))
      .mockResolvedValueOnce(makeRefreshResponse(true))
      .mockResolvedValueOnce(makeBinaryResponse(200, 'xlsx-bytes'));
    vi.stubGlobal('fetch', fetchMock);

    const { downloadQuestionTemplate } = await import('../api/exams');

    await expect(downloadQuestionTemplate()).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('still surfaces a non-auth failure to the caller', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeBinaryResponse(500, '')));

    const { downloadQuestionTemplate } = await import('../api/exams');

    await expect(downloadQuestionTemplate()).rejects.toThrow('HTTP 500');
  });
});
