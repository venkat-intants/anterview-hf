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

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { AuthUser } from '../types/auth';
import { getToken, setToken, clearToken, subscribeToken } from '../api/tokenStore';
import { attemptRefresh } from '../api/client';
import { getMe } from '../api/auth';

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';
// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

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
  clearAuth: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // Mirror tokenStore into React state so components re-render on token changes.
  const [accessToken, setAccessToken] = useState<string | null>(getToken);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isInitializing, setIsInitializing] = useState(true);

  // Keep React state in sync when the store changes from outside React
  // (e.g. the 401-refresh path in apiClient writes directly to tokenStore).
  useEffect(() => {
    const unsub = subscribeToken((tok) => {
      setAccessToken(tok);
      if (!tok) setUser(null);
    });
    return unsub;
  }, []);

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
        if (refreshed) {
          const newToken = getToken();
          if (newToken) {
            try {
              const me = await getMe();
              setUser({
                user_id: me.user_id,
                full_name: me.full_name,
                email: me.email,
                roles: me.roles,
                must_change_password: me.must_change_password,
              });
            } catch {
              // Could not fetch profile — token still valid, user stays null
              // and the profile will be fetched by the page query.
            }
          }
        }
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
  const setAuth = useCallback((token: string, authUser: AuthUser) => {
    setToken(token);
    setAccessToken(token);
    setUser(authUser);
  }, []);

  const clearAuth = useCallback(() => {
    clearToken();
    setAccessToken(null);
    setUser(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      accessToken,
      user,
      isAuthenticated: accessToken !== null,
      isInitializing,
      setAuth,
      clearAuth,
    }),
    [accessToken, user, isInitializing, setAuth, clearAuth],
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
