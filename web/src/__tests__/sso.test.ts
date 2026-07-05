// Tests for the Google SSO api module (B-035)
import { describe, it, expect, vi, afterEach } from 'vitest';
import { completeGoogleLogin, googleLoginUrl } from '../api/sso';

describe('sso api', () => {
  const originalFetch = global.fetch;
  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('googleLoginUrl builds the initiate URL with an encoded return_url', () => {
    const url = googleLoginUrl('/dashboard');
    expect(url).toContain('/auth/sso/google/initiate');
    expect(url).toContain('return_url=%2Fdashboard');
  });

  it('completeGoogleLogin returns tokens on a 200 response', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          access_token: 'jwt-token',
          token_type: 'bearer',
          user_id: 'user-1',
        }),
    }) as unknown as typeof fetch;

    const res = await completeGoogleLogin('code123', 'state123');
    expect(res.access_token).toBe('jwt-token');
    expect(res.user_id).toBe('user-1');
  });

  it('completeGoogleLogin throws the server detail on a non-2xx response', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: () => Promise.resolve({ detail: 'INVALID_OR_EXPIRED_STATE' }),
    }) as unknown as typeof fetch;

    await expect(completeGoogleLogin('c', 's')).rejects.toThrow(
      'INVALID_OR_EXPIRED_STATE',
    );
  });
});
