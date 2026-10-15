// useScopedPersistKey — a localStorage key for a Collapsible that is per person.
//
// localStorage is per browser, not per user; two people sharing a machine would
// otherwise share each other's open/closed choices. The key is scoped by a SHORT
// NON-REVERSIBLE HASH of the signed-in user's id, never the id itself: the key
// names are readable by anything on the origin, and a UUID in them is an
// identifier in clear. (This is not a secret, just not an identifier.) Where
// there is no auth context the key falls back to "anon" rather than throwing,
// because a remembered disclosure is never worth a crash. Logout clears the
// user's keys (AuthContext.clearAuth, via lib/collapsibleKeys).

import { useEffect } from 'react';
import { hashUserId, removeCollapsibleKeys } from '@/lib/collapsibleKeys';
import { useSignedInStaff } from './useSignedInStaff';

export { clearCollapsibleKeysFor, hashUserId } from '@/lib/collapsibleKeys';

export function useScopedPersistKey(base: string): string {
  const { userId } = useSignedInStaff();
  // Best-effort, once per user: drop keys from before the hash.
  useEffect(() => {
    if (!userId) return;
    const legacy = `:${userId}`;
    removeCollapsibleKeys((k) => k.endsWith(legacy));
  }, [userId]);
  return `${base}:${userId ? hashUserId(userId) : 'anon'}`;
}
