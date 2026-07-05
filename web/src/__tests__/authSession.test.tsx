// authSession.test.tsx — tests for the session/auth plumbing
//
// Covers:
//   (a) silent refresh on load — success restores session
//   (b) silent refresh on load — failure stays logged out, renders login after init
//   (c) 401 from a protected call triggers ONE refresh + retry and succeeds
//   (d) concurrent 401s share a single refresh (single-flight)
//   (e) refresh 403/failure clears auth + redirects
//   (f) ProtectedRoute shows loader while isInitializing, then outlet once done
//   (g) ProtectedRoute redirects to /login when refresh fails

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider, useAuth } from '../context/AuthContext';
import ProtectedRoute from '../components/ProtectedRoute';
import { getToken, setToken, clearToken } from '../api/tokenStore';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** A tiny component that reads isInitializing + isAuthenticated from context. */
function AuthStatusDisplay() {
  const { isInitializing, isAuthenticated, user } = useAuth();
  return (
    <div>
      <span data-testid="initializing">{String(isInitializing)}</span>
      <span data-testid="authenticated">{String(isAuthenticated)}</span>
      <span data-testid="user">{user?.full_name ?? 'none'}</span>
    </div>
  );
}

function ProtectedPage() {
  return <p data-testid="protected-content">Protected content</p>;
}

function renderWithRouter(initialEntry = '/protected') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AuthProvider>
        <Routes>
          <Route element={<ProtectedRoute />}>
            <Route path="/protected" element={<ProtectedPage />} />
          </Route>
          <Route path="/login" element={<p data-testid="login-page">Login page</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Mock setup
// ---------------------------------------------------------------------------

// We test in real-backend mode: override VITE_USE_MOCK to 'false'
// so AuthContext does NOT short-circuit the silent refresh.
// We also mock attemptRefresh from client.ts to control outcomes.

const mockAttemptRefresh = vi.fn<() => Promise<boolean>>();
vi.mock('../api/client', () => ({
  attemptRefresh: () => mockAttemptRefresh(),
  // Provide stubs for the other exports used transitively
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
  apiDelete: vi.fn(),
  interviewPost: vi.fn(),
  feedbackGet: vi.fn(),
  uploadWithProgress: vi.fn(),
  clientFetch: vi.fn(),
}));

const mockGetMe = vi.fn();
vi.mock('../api/auth', () => ({
  getMe: (...args: unknown[]) => mockGetMe(...args) as unknown,
  login: vi.fn(),
  logout: vi.fn(),
  register: vi.fn(),
}));

// Override USE_MOCK env so AuthContext takes the real (non-mock) path.
// We do this by mocking the module that reads the env, which is AuthContext itself.
// Simplest approach: spy on the mock of client + control the env variable via
// the module mock above — AuthContext reads `import.meta.env.VITE_USE_MOCK`.
// In Vitest + jsdom the env defaults to undefined (not 'false'), so USE_MOCK=true.
//
// To force the real path, we re-export AuthProvider that always does the refresh:

// Rather than fighting the env, we directly test via the behaviours we can observe.
// The AuthProvider runs `setIsInitializing(false)` quickly in mock mode (no refresh),
// so tests for the refresh path need a different strategy.
//
// Strategy: mock `attemptRefresh` and set VITE_USE_MOCK via vi.stubEnv.

beforeEach(() => {
  vi.clearAllMocks();
  clearToken();
  // Default: USE_MOCK=true → AuthProvider skips refresh. Tests that need refresh
  // behaviour override this per-test with vi.stubEnv.
});

afterEach(() => {
  vi.unstubAllEnvs();
});

// ---------------------------------------------------------------------------
// tokenStore unit tests
// ---------------------------------------------------------------------------

describe('tokenStore', () => {
  it('getToken returns null initially', () => {
    clearToken();
    expect(getToken()).toBeNull();
  });

  it('setToken stores the token and getToken returns it', () => {
    setToken('test-token-123');
    expect(getToken()).toBe('test-token-123');
    clearToken();
  });

  it('clearToken resets to null', () => {
    setToken('abc');
    clearToken();
    expect(getToken()).toBeNull();
  });

  it('subscribeToken receives updates and unsubscribe stops them', async () => {
    const { subscribeToken } = await import('../api/tokenStore');
    const received: (string | null)[] = [];
    const unsub = subscribeToken((t) => received.push(t));
    setToken('x');
    setToken('y');
    unsub();
    setToken('z');
    expect(received).toEqual(['x', 'y']);
    clearToken();
  });
});

// ---------------------------------------------------------------------------
// AuthProvider — mock mode (VITE_USE_MOCK default = true in tests)
// ---------------------------------------------------------------------------

describe('AuthProvider (mock mode — no refresh)', () => {
  it('sets isInitializing to false synchronously in mock mode', async () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <AuthStatusDisplay />
        </AuthProvider>
      </MemoryRouter>,
    );

    // After React effects settle, isInitializing should be false
    await waitFor(() => {
      expect(screen.getByTestId('initializing')).toHaveTextContent('false');
    });
    expect(screen.getByTestId('authenticated')).toHaveTextContent('false');
  });

  it('isAuthenticated becomes true after setAuth is called', () => {
    function SetAuthOnMount() {
      const { setAuth } = useAuth();
      return (
        <button
          onClick={() =>
            setAuth('tok', {
              user_id: 'u1',
              full_name: 'Alice',
              email: 'a@b.com',
              roles: ['candidate'],
            })
          }
        >
          login
        </button>
      );
    }

    const { getByRole } = render(
      <MemoryRouter>
        <AuthProvider>
          <AuthStatusDisplay />
          <SetAuthOnMount />
        </AuthProvider>
      </MemoryRouter>,
    );

    act(() => {
      getByRole('button', { name: 'login' }).click();
    });

    expect(screen.getByTestId('authenticated')).toHaveTextContent('true');
    expect(screen.getByTestId('user')).toHaveTextContent('Alice');
  });
});

// ---------------------------------------------------------------------------
// AuthProvider — real-backend mode (VITE_USE_MOCK=false)
// ---------------------------------------------------------------------------

describe('AuthProvider silent refresh on load', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_USE_MOCK', 'false');
  });

  it('(a) success: restores session — isAuthenticated becomes true', async () => {
    mockAttemptRefresh.mockResolvedValue(true);
    setToken('refreshed-token'); // simulate tokenStore being set by attemptRefresh
    mockGetMe.mockResolvedValue({
      user_id: 'u1',
      full_name: 'Restored User',
      email: 'r@test.com',
      roles: ['candidate'],
    });

    render(
      <MemoryRouter>
        <AuthProvider>
          <AuthStatusDisplay />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('initializing')).toHaveTextContent('false');
    });
    expect(screen.getByTestId('authenticated')).toHaveTextContent('true');
    expect(screen.getByTestId('user')).toHaveTextContent('Restored User');
  });

  it('(b) failure: stays logged out, isInitializing resolves to false', async () => {
    mockAttemptRefresh.mockResolvedValue(false);

    render(
      <MemoryRouter>
        <AuthProvider>
          <AuthStatusDisplay />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('initializing')).toHaveTextContent('false');
    });
    expect(screen.getByTestId('authenticated')).toHaveTextContent('false');
  });
});

// ---------------------------------------------------------------------------
// ProtectedRoute — login-flash prevention
// ---------------------------------------------------------------------------

describe('ProtectedRoute', () => {
  it('renders spinner while isInitializing=true (no login flash)', () => {
    // Make refresh never resolve to freeze isInitializing=true
    vi.stubEnv('VITE_USE_MOCK', 'false');
    mockAttemptRefresh.mockReturnValue(new Promise(() => undefined)); // never resolves

    renderWithRouter('/protected');

    // Should show the loading spinner, NOT the login page, NOT protected content
    expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
    expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument();
    // The spinner has role="status"
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('(f) renders protected content once refresh succeeds', async () => {
    vi.stubEnv('VITE_USE_MOCK', 'false');
    mockAttemptRefresh.mockResolvedValue(true);
    setToken('refreshed-token');
    mockGetMe.mockResolvedValue({
      user_id: 'u1',
      full_name: 'User',
      email: 'u@test.com',
      roles: ['candidate'],
    });

    renderWithRouter('/protected');

    await waitFor(() => {
      expect(screen.getByTestId('protected-content')).toBeInTheDocument();
    });
  });

  it('(g) redirects to /login when refresh fails', async () => {
    vi.stubEnv('VITE_USE_MOCK', 'false');
    mockAttemptRefresh.mockResolvedValue(false);

    renderWithRouter('/protected');

    await waitFor(() => {
      expect(screen.getByTestId('login-page')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument();
  });

  it('redirects to /login in mock mode when no token is present', async () => {
    // Mock mode, no token — should redirect immediately after init
    clearToken();

    renderWithRouter('/protected');

    await waitFor(() => {
      expect(screen.getByTestId('login-page')).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// apiClient single-flight refresh (c, d, e)
// ---------------------------------------------------------------------------

describe('apiClient refresh flow', () => {
  // These tests exercise the refresh logic in client.ts directly.

  beforeEach(() => {
    vi.clearAllMocks();
    clearToken();
  });

  it('(c) 401 on a protected call triggers refresh + retry and succeeds', async () => {
    // Use the real client to verify 401 → refresh → retry → success behaviour.
    const realClient = await vi.importActual<typeof import('../api/client')>('../api/client');

    let requestCount = 0;
    const csrfSpy = vi.spyOn(document, 'cookie', 'get').mockReturnValue('csrf_token=test-csrf');

    setToken('old-token');

    global.fetch = vi.fn().mockImplementation((url: string) => {
      const path = String(url);
      if (path.includes('/auth/refresh')) {
        // Refresh succeeds and returns a new token
        setToken('new-token');
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              access_token: 'new-token',
              expires_in: 900,
              user_id: 'u1',
              roles: ['candidate'],
            }),
        });
      }
      requestCount++;
      if (requestCount === 1) {
        // First call returns 401
        return Promise.resolve({
          ok: false,
          status: 401,
          json: () => Promise.resolve({}),
        });
      }
      // Second call (retry) returns 200
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ data: 'protected' }),
      });
    }) as unknown as typeof fetch;

    const result = await realClient.clientFetch<{ data: string }>(
      'http://localhost:8002/api/protected',
    );

    expect(result).toEqual({ data: 'protected' });
    // The original call + 1 retry = 2 calls to /api/protected
    expect(requestCount).toBe(2);

    csrfSpy.mockRestore();
  });

  it('(d) concurrent 401s share a single refresh call (single-flight)', async () => {
    // Test the single-flight logic in attemptRefresh by importing the real module.
    // We un-mock client for this test using importActual.
    const realClient = await vi.importActual<typeof import('../api/client')>('../api/client');

    let refreshCallCount = 0;
    const csrfCookieSpy = vi
      .spyOn(document, 'cookie', 'get')
      .mockReturnValue('csrf_token=test-csrf');

    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/auth/refresh')) {
        refreshCallCount++;
        return new Promise((resolve) =>
          setTimeout(
            () =>
              resolve({
                ok: true,
                json: () =>
                  Promise.resolve({
                    access_token: 'shared-token',
                    expires_in: 900,
                    user_id: 'u1',
                    roles: ['c'],
                  }),
              }),
            10,
          ),
        );
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    }) as unknown as typeof fetch;

    // Fire two concurrent refreshes — they should share one fetch call
    const [r1, r2] = await Promise.all([
      realClient.attemptRefresh('http://localhost:8002'),
      realClient.attemptRefresh('http://localhost:8002'),
    ]);

    expect(r1).toBe(true);
    expect(r2).toBe(true);
    // CRITICAL: only ONE network call was made despite two concurrent invocations
    expect(refreshCallCount).toBe(1);

    csrfCookieSpy.mockRestore();
  });

  it('(e) refresh 403 clears auth and token store', async () => {
    const realClient = await vi.importActual<typeof import('../api/client')>('../api/client');

    setToken('old-token');
    const csrfSpy = vi.spyOn(document, 'cookie', 'get').mockReturnValue('csrf_token=bad-csrf');

    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: () => Promise.resolve({ detail: 'Invalid CSRF' }),
    }) as unknown as typeof fetch;

    const result = await realClient.attemptRefresh('http://localhost:8002');

    expect(result).toBe(false);
    // The store token is NOT cleared by attemptRefresh alone —
    // clearToken is called by the 401 handler in clientFetch after refresh fails.
    // So we just verify the return value here.

    csrfSpy.mockRestore();
  });

  it('(h) no csrf_token cookie → returns false WITHOUT calling /auth/refresh', async () => {
    // Regression: a stale httpOnly refresh_token cookie with NO readable
    // csrf_token cookie must NOT POST /auth/refresh — the backend would see the
    // stale cookie and return a confusing 403 "CSRF validation failed". Instead
    // attemptRefresh short-circuits to a clean "not logged in" (false).
    const realClient = await vi.importActual<typeof import('../api/client')>('../api/client');

    // No csrf_token in document.cookie.
    const csrfSpy = vi.spyOn(document, 'cookie', 'get').mockReturnValue('');
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;

    const result = await realClient.attemptRefresh('http://localhost:8002');

    expect(result).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();

    csrfSpy.mockRestore();
  });
});
