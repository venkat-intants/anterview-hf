// readCookie (api/client.ts) used split('=')[1], which truncated a value that
// itself contains '=' (base64 padding, signed tokens). It is private, so it is
// observed through attemptRefresh, which sends the csrf_token cookie back as
// the X-CSRF-Token header.

import { describe, it, expect, vi, afterEach } from 'vitest';
import { attemptRefresh } from '../api/client';
import { clearToken } from '../api/tokenStore';

function clearCookie(name: string): void {
  document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
}

async function csrfHeaderSentFor(cookieValue: string): Promise<string | undefined> {
  document.cookie = `csrf_token=${cookieValue}; path=/`;
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({ access_token: 't', expires_in: 900, user_id: 'u', roles: ['candidate'] }),
  });
  vi.stubGlobal('fetch', fetchMock);
  await attemptRefresh('http://api.test');
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return (init.headers as Record<string, string>)['X-CSRF-Token'];
}

describe('readCookie via attemptRefresh', () => {
  afterEach(() => {
    clearCookie('csrf_token');
    clearCookie('other');
    clearToken();
    vi.unstubAllGlobals();
  });

  it('keeps a value that contains "="', async () => {
    expect(await csrfHeaderSentFor('abc==')).toBe('abc==');
  });

  it('keeps a value with "=" in the middle and at the end', async () => {
    expect(await csrfHeaderSentFor('a=b=c=')).toBe('a=b=c=');
  });

  it('still reads a plain value, and ignores a cookie whose name merely ends the same', async () => {
    document.cookie = 'x_csrf_token=decoy; path=/';
    expect(await csrfHeaderSentFor('plain')).toBe('plain');
    clearCookie('x_csrf_token');
  });
});
