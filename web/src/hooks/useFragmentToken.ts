// useFragmentToken — read a one-time secret from the URL #fragment, then remove
// it from the address bar.
//
// A fragment keeps the secret off the wire, out of access logs and out of
// Referer, but it stays in the address bar and in the browser history entry —
// readable by the next person at a shared machine. So: read it once and scrub
// it at once.
//
// WHERE IT LIVES AFTER THAT depends on the page, and the caller says which:
//
//   useFragmentToken()          memory only, for the component's lifetime. For
//                               a page that spends the token at once (verify
//                               email), where a reload has nothing to resume.
//   useFragmentToken(key)       memory AND this tab's sessionStorage under
//                               `key`. For a page somebody works on (a saved
//                               application, a multi-hour task, an offer, an
//                               invite, a password form): a reload, or the
//                               browser restoring the tab, would otherwise
//                               land on "this link no longer works" because the
//                               address bar no longer has the token in it
//                               (audit CA-H3). This is the pattern /exam has
//                               used since its own reload incident: per tab,
//                               gone when the tab closes, never in
//                               localStorage, a cookie or any other URL. The
//                               page calls `forgetFragmentToken(key)` once the
//                               link reaches a terminal state, so a spent
//                               credential does not sit in storage for the rest
//                               of the tab's life.
//
// The read is a PURE useState initialiser and the scrub is an effect, on
// purpose. React 18 StrictMode invokes initialisers twice and mounts effects
// twice: a scrub inside the initialiser would leave the second invocation with
// an empty hash (and an empty token), whereas an idempotent effect is harmless
// run twice, and the state set by the first render survives the simulated
// remount. Writing the same value to sessionStorage twice is equally harmless.
//
// Format: the whole fragment IS the token (`/path#<token>`), which is what
// every emailed link in the app uses today.

import { useEffect, useState } from 'react';

/**
 * The per-tab storage keys, one per page that keeps its token across a reload.
 * In one place so two pages can never share a key and read each other's link.
 */
export const FRAGMENT_TOKEN_KEYS = {
  draft: 'anthire.draft.token',
  task: 'anthire.task.token',
  offer: 'anthire.offer.token',
  invite: 'anthire.invite.token',
  activate: 'anthire.activate.token',
  resetPassword: 'anthire.reset.token',
} as const;

function readFragment(): string {
  if (typeof window === 'undefined') return '';
  return window.location.hash.replace(/^#/, '').trim();
}

/** Remove the fragment, keeping pathname, search and router state; no new history entry. */
export function scrubFragment(): void {
  if (typeof window === 'undefined' || !window.location.hash) return;
  const { pathname, search } = window.location;
  window.history.replaceState(window.history.state, '', `${pathname}${search}`);
}

function readToken(storageKey: string | undefined): string {
  const fromHash = readFragment();
  if (!storageKey) return fromHash;
  try {
    if (fromHash) {
      // A link opened afresh always wins over whatever an earlier link in this
      // tab left behind: it is the one the person just clicked.
      sessionStorage.setItem(storageKey, fromHash);
      return fromHash;
    }
    return sessionStorage.getItem(storageKey) ?? '';
  } catch {
    // Private mode, or storage blocked. The token is in state either way; only
    // surviving a reload is lost, and the emailed link still works.
    return fromHash;
  }
}

/** Drop a page's stored token once its link can no longer be used. */
export function forgetFragmentToken(storageKey: string): void {
  try {
    sessionStorage.removeItem(storageKey);
  } catch {
    // Blocked or unavailable: it was never written either.
  }
}

/**
 * The fragment token ('' when absent). Stable for the component's lifetime.
 * Pass `storageKey` to keep it for this tab so a reload does not lose it.
 */
export function useFragmentToken(storageKey?: string): string {
  const [token] = useState(() => readToken(storageKey));
  useEffect(() => {
    scrubFragment();
  }, []);
  return token;
}
