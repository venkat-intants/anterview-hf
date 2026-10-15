// collapsibleKeys - the storage-key helpers behind remembered disclosures.
//
// Kept apart from the React hook so AuthContext (which clears them on logout)
// need not import a module that itself imports AuthContext.

const COLLAPSIBLE_PREFIX = 'anthire:collapsible:';

/** FNV-1a, 32 bit, base 36. Synchronous, tiny, and not reversible to the id. */
export function hashUserId(userId: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < userId.length; i += 1) {
    h ^= userId.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `u${h.toString(36)}`;
}

export function removeCollapsibleKeys(match: (key: string) => boolean): void {
  try {
    const doomed: string[] = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const k = window.localStorage.key(i);
      if (k && k.startsWith(COLLAPSIBLE_PREFIX) && match(k)) doomed.push(k);
    }
    for (const k of doomed) window.localStorage.removeItem(k);
  } catch {
    /* storage unavailable: nothing was remembered, nothing to clear */
  }
}

/** Forget every remembered disclosure of this user (called on logout). Also
 *  sweeps keys written before the hash, which carried the raw id. */
export function clearCollapsibleKeysFor(userId: string | null | undefined): void {
  if (!userId) return;
  const hashed = `:${hashUserId(userId)}`;
  const legacy = `:${userId}`;
  removeCollapsibleKeys((k) => k.endsWith(hashed) || k.endsWith(legacy));
}
