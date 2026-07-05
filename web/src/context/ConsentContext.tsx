// ConsentContext — DPDP Act 2023 consent gate
// Fetches GET /consent/status on mount (after login); caches result in state.
// Exposes consented, loading, refresh, and recordConsent to the component tree.

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { getConsentStatus, postConsent } from '../api/consent';
import { useAuth } from './AuthContext';

interface ConsentContextValue {
  /** null = not yet checked (initial load); true/false = known state */
  consented: boolean | null;
  loading: boolean;
  /** Re-fetch GET /consent/status from the server */
  refresh: () => void;
  /** Call POST /consent then refresh — throws on network/API error */
  recordConsent: () => Promise<void>;
}

const ConsentContext = createContext<ConsentContextValue | null>(null);

export function ConsentProvider({ children }: { children: React.ReactNode }) {
  const { accessToken, isAuthenticated } = useAuth();

  // null = unknown (pre-fetch); boolean = known
  const [consented, setConsented] = useState<boolean | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  const fetchStatus = useCallback(async () => {
    if (!accessToken) {
      // Not authenticated — reset to unknown
      setConsented(null);
      return;
    }
    setLoading(true);
    try {
      const status = await getConsentStatus(accessToken);
      setConsented(status.consented);
    } catch {
      // On fetch failure treat as unknown so we re-prompt rather than silently skip
      setConsented(null);
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  // Re-run whenever authentication state changes (login / logout)
  useEffect(() => {
    if (isAuthenticated) {
      void fetchStatus();
    } else {
      // Logged out — clear consent state
      setConsented(null);
    }
  }, [isAuthenticated, fetchStatus]);

  const refresh = useCallback(() => {
    void fetchStatus();
  }, [fetchStatus]);

  const recordConsent = useCallback(async (): Promise<void> => {
    if (!accessToken) throw new Error('Not authenticated');
    await postConsent(accessToken);
    // Re-fetch so consented flips to true and callers can react
    await fetchStatus();
  }, [accessToken, fetchStatus]);

  const value = useMemo<ConsentContextValue>(
    () => ({ consented, loading, refresh, recordConsent }),
    [consented, loading, refresh, recordConsent],
  );

  return <ConsentContext.Provider value={value}>{children}</ConsentContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useConsent(): ConsentContextValue {
  const ctx = useContext(ConsentContext);
  if (!ctx) {
    throw new Error('useConsent must be used within a ConsentProvider');
  }
  return ctx;
}
