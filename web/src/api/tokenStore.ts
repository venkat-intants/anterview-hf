// tokenStore — module-level in-memory access-token store.
//
// Lives OUTSIDE React state so non-React code (apiClient, interview-ws) can
// read the current token synchronously without hooks.
//
// The access token is NEVER written to localStorage/sessionStorage — that is
// intentional. The httpOnly refresh cookie is the durable credential; the
// access token is ephemeral and lives only in this module.

type Subscriber = (token: string | null) => void;

let _token: string | null = null;
const _subscribers = new Set<Subscriber>();

/** Return the current access token, or null if not authenticated. */
export function getToken(): string | null {
  return _token;
}

/** Store a new access token and notify all subscribers. */
export function setToken(token: string): void {
  _token = token;
  _subscribers.forEach((fn) => fn(_token));
}

/** Clear the stored token and notify all subscribers. */
export function clearToken(): void {
  _token = null;
  _subscribers.forEach((fn) => fn(null));
}

/**
 * Subscribe to token changes.
 * Returns an unsubscribe function — call it to stop receiving updates.
 */
export function subscribeToken(fn: Subscriber): () => void {
  _subscribers.add(fn);
  return () => {
    _subscribers.delete(fn);
  };
}
