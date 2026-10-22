// AuthContext — in-memory token storage (never localStorage/sessionStorage).
// Provides useAuth() hook to all child components.
//
// On mount, performs a silent refresh: POST /auth/refresh with the httpOnly
// refresh cookie + CSRF double-submit header. On success the session is
// restored seamlessly. On failure the user stays logged out. Either way
// isInitializing becomes false and ProtectedRoute can make a routing decision.
//
// The in-memory access token is mirrored from tokenStore so that non-React
// code (apiClient, interview-ws) can read it synchronously.

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { QueryClientContext } from '@tanstack/react-query';
import type { AuthUser } from '../types/auth';
import { getToken, setToken, clearToken, subscribeToken } from '../api/tokenStore';
import { attemptRefresh } from '../api/client';
import { getMe } from '../api/auth';
import { openAuthSync, type AuthSync } from '../lib/authSync';
import { clearCollapsibleKeysFor } from '../lib/collapsibleKeys';
import { clearFragmentTokens } from '../hooks/useFragmentToken';

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';
// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

/** How many times the mount-time profile fetch may fail before the session is
 *  treated as unusable. One retry absorbs a cold container's 502/timeout,
 *  which is the common case on the Space. */
const PROFILE_FETCH_ATTEMPTS = 2;
const PROFILE_RETRY_MS = 600;

interface AuthContextValue {
  accessToken: string | null;
  user: AuthUser | null;
  isAuthenticated: boolean;
  /**
   * True while the provider is running the silent-refresh probe on mount.
   * ProtectedRoute renders a loading state instead of redirecting to /login
   * while this is true.
   */
  isInitializing: boolean;
  setAuth: (token: string, user: AuthUser) => void;
  /**
   * Merge fresh profile fields into the cached session user.
   *
   * Needed because the session user is written once at login: anything that
   * edits the profile afterwards (onboarding writes `full_name`) would
   * otherwise leave the shell greeting them by the name they just replaced.
   */
  updateUser: (patch: Partial<AuthUser>) => void;
  clearAuth: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // Mirror tokenStore into React state so components re-render on token changes.
  const [accessToken, setAccessToken] = useState<string | null>(getToken);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isInitializing, setIsInitializing] = useState(true);
  // Optional, so the provider still works where no QueryClientProvider wraps it.
  const queryClient = useContext(QueryClientContext);
  // The last account this tab was signed in as. Deliberately NOT reset on
  // logout: a session can end without passing through clearAuth (a failed
  // refresh only nulls the token), and the next sign-in must still know the
  // cache belongs to somebody else.
  const lastUserIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (user) lastUserIdRef.current = user.user_id;
  }, [user]);

  // Mirrors of state for callbacks that must not re-subscribe on every change.
  const userRef = useRef<AuthUser | null>(null);
  useEffect(() => {
    userRef.current = user;
  }, [user]);
  // The token this tab's own setAuth just wrote: the store subscriber must not
  // treat it as an EXTERNAL refresh and re-check the identity it just set.
  const ownTokenRef = useRef<string | null>(null);
  const syncRef = useRef<AuthSync | null>(null);
  const reconcilingRef = useRef(false);

  // After a token refresh the refresh COOKIE is shared by every tab, so the new
  // token may belong to a different account than the one this tab's `user`,
  // roles and query cache describe (another tab signed in as someone else).
  // Re-read the profile; if the account changed, drop the cache and adopt it.
  const reconcileIdentity = useCallback(async () => {
    const current = userRef.current;
    if (!current || reconcilingRef.current) return;
    reconcilingRef.current = true;
    try {
      const me = await getMe();
      if (me.user_id !== current.user_id) {
        queryClient?.getQueryCache().clear();
        lastUserIdRef.current = me.user_id;
      }
      setUser((prev) => {
        const next: AuthUser = {
          user_id: me.user_id,
          full_name: me.full_name,
          email: me.email,
          roles: me.roles,
          must_change_password: me.must_change_password,
        };
        // Same account and nothing changed: keep the object so consumers do
        // not re-render on every routine refresh.
        return prev &&
          prev.user_id === next.user_id &&
          prev.full_name === next.full_name &&
          prev.email === next.email &&
          prev.must_change_password === next.must_change_password &&
          prev.roles.join(',') === next.roles.join(',')
          ? prev
          : next;
      });
    } catch {
      // Offline or a transient failure: keep the identity we have. The next
      // refresh checks again, and a revoked session surfaces as a 401 anyway.
    } finally {
      reconcilingRef.current = false;
    }
  }, [queryClient]);

  // Keep React state in sync when the store changes from outside React
  // (e.g. the 401-refresh path in apiClient writes directly to tokenStore).
  useEffect(() => {
    const unsub = subscribeToken((tok) => {
      setAccessToken(tok);
      if (!tok) {
        setUser(null);
        return;
      }
      if (tok !== ownTokenRef.current && !USE_MOCK) void reconcileIdentity();
    });
    return unsub;
  }, [reconcileIdentity]);

  // Cross-tab session sync. Never carries a token (see lib/authSync).
  useEffect(() => {
    const sync = openAuthSync((event) => {
      if (event.type !== 'logout') return;
      // Another tab signed out: this tab's token is now for a revoked session.
      // Its emailed-link credentials go too: sign-out ends this machine's use.
      clearFragmentTokens();
      ownTokenRef.current = null;
      clearToken();
      setAccessToken(null);
      setUser(null);
      queryClient?.getQueryCache().clear();
    });
    syncRef.current = sync;
    return () => {
      sync.close();
      syncRef.current = null;
    };
  }, [queryClient]);

  // Silent refresh on mount — attempts to exchange the httpOnly refresh cookie
  // for a fresh access token before rendering protected routes.
  useEffect(() => {
    if (USE_MOCK) {
      // In mock mode there are no real cookies. Treat the session as
      // authenticated only if a token is already in the store (set during
      // the current SPA session via login/register). If none, stay logged out.
      setIsInitializing(false);
      return;
    }

    void (async () => {
      try {
        const refreshed = await attemptRefresh(API_BASE);
        if (!refreshed || getToken() === null) return;

        for (let attempt = 1; attempt <= PROFILE_FETCH_ATTEMPTS; attempt += 1) {
          try {
            const me = await getMe();
            setUser({
              user_id: me.user_id,
              full_name: me.full_name,
              email: me.email,
              roles: me.roles,
              must_change_password: me.must_change_password,
            });
            return;
          } catch {
            if (attempt < PROFILE_FETCH_ATTEMPTS) {
              await new Promise((resolve) => setTimeout(resolve, PROFILE_RETRY_MS));
            }
          }
        }

        // Every attempt failed. Keeping the token would leave an authenticated
        // but role-less session, and the route guards read roles from HERE, not
        // from a page query: an hr_manager would be bounced to the candidate
        // dashboard on every attempt to reach /hr, and `must_change_password`
        // would read `undefined` and wave a bootstrap password straight into
        // the shell. Dropping the token fails closed and sends them to /login,
        // which they can recover from without a manual reload.
        clearToken();
        setAccessToken(null);
        setUser(null);
      } finally {
        setIsInitializing(false);
      }
    })();
    // Run exactly once on mount — no dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // setAuth/clearAuth set React state DIRECTLY (not via the subscriber): they are
  // called from React event handlers and must not depend on the subscribe()
  // effect having committed yet. The subscriber exists only to mirror EXTERNAL
  // store writes (e.g. the apiClient 401-refresh path) back into React state.
  const setAuth = useCallback(
    (token: string, authUser: AuthUser) => {
      // Query keys are not scoped by user (['interviewer', 'assignments'],
      // ['hr', ...]), so a different account signing in on the same tab would
      // otherwise be shown the previous one's cached data until it refetched.
      // Only on a SWITCH between two accounts: the first sign-in in a tab has
      // nobody's data to hide, and clearing then would pull queries out from
      // under pages already mounted. Only queries are dropped: the mutation
      // that is signing in is still running, and clearing the mutation cache
      // would pull it out from under its own onSuccess.
      if (lastUserIdRef.current !== null && lastUserIdRef.current !== authUser.user_id) {
        queryClient?.getQueryCache().clear();
      }
      lastUserIdRef.current = authUser.user_id;
      ownTokenRef.current = token;
      setToken(token);
      setAccessToken(token);
      setUser(authUser);
      syncRef.current?.publish({ type: 'login', userId: authUser.user_id });
    },
    [queryClient],
  );

  const updateUser = useCallback((patch: Partial<AuthUser>) => {
    setUser((prev) => (prev ? { ...prev, ...patch } : prev));
  }, []);

  const clearAuth = useCallback(() => {
    // Remembered open/closed choices belong to the person who made them.
    clearCollapsibleKeysFor(userRef.current?.user_id);
    // So do the emailed-link credentials this tab kept (draft, offer, task,
    // invite, exam): on a shared machine they would otherwise outlive the
    // session, including across a reopened tab.
    clearFragmentTokens();
    ownTokenRef.current = null;
    clearToken();
    setAccessToken(null);
    setUser(null);
    syncRef.current?.publish({ type: 'logout' });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      accessToken,
      user,
      isAuthenticated: accessToken !== null,
      isInitializing,
      setAuth,
      updateUser,
      clearAuth,
    }),
    [accessToken, user, isInitializing, setAuth, updateUser, clearAuth],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return ctx;
}
