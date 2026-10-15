// authSync — tell the other tabs of this browser that the session changed.
//
// The access token lives in memory per tab, but the refresh cookie is shared by
// every tab. So signing out in one tab leaves the others looking signed in
// (until their token expires), and signing in as a different account in one tab
// makes every other tab's next token refresh mint THAT account's token under
// the old tab's user, roles and cache. This module is only the transport:
// BroadcastChannel, with a `storage`-event fallback where it is unavailable.
//
// A message carries the event type and, for login, the user id — NEVER a token
// or any credential. The id is only used to tell "same account" from "other".

export type AuthSyncEvent = { type: 'logout' } | { type: 'login'; userId: string };

const CHANNEL = 'anthire-auth';
// localStorage key used for the fallback. The value is a throwaway ping that is
// removed straight away; a `storage` event fires in OTHER tabs on both writes.
const FALLBACK_KEY = 'anthire.auth.sync';

function isEvent(value: unknown): value is AuthSyncEvent {
  if (typeof value !== 'object' || value === null) return false;
  const v = value as { type?: unknown; userId?: unknown };
  if (v.type === 'logout') return true;
  return v.type === 'login' && typeof v.userId === 'string' && v.userId.length > 0;
}

export interface AuthSync {
  publish: (event: AuthSyncEvent) => void;
  close: () => void;
}

/** Subscribe to auth events from OTHER tabs, and get a publisher for this one. */
export function openAuthSync(onEvent: (event: AuthSyncEvent) => void): AuthSync {
  if (typeof BroadcastChannel !== 'undefined') {
    const channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = (e: MessageEvent<unknown>) => {
      if (isEvent(e.data)) onEvent(e.data);
    };
    return {
      publish: (event) => channel.postMessage(event),
      close: () => channel.close(),
    };
  }

  const onStorage = (e: StorageEvent): void => {
    if (e.key !== FALLBACK_KEY || !e.newValue) return;
    try {
      const parsed: unknown = JSON.parse(e.newValue);
      if (typeof parsed === 'object' && parsed !== null) {
        const event = (parsed as { event?: unknown }).event;
        if (isEvent(event)) onEvent(event);
      }
    } catch {
      // Not ours / malformed — ignore.
    }
  };
  window.addEventListener('storage', onStorage);
  return {
    publish: (event) => {
      try {
        // `n` makes consecutive identical events distinct so each one fires.
        localStorage.setItem(FALLBACK_KEY, JSON.stringify({ event, n: Math.random() }));
        localStorage.removeItem(FALLBACK_KEY);
      } catch {
        // Storage blocked: other tabs simply are not told.
      }
    },
    close: () => window.removeEventListener('storage', onStorage),
  };
}
