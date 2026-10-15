// useSignedInStaff — who is signed in, without requiring a provider.
//
// A few components only want to NAME the signed-in person ("recorded as decided
// by you, Priya") or scope a stored preference to them. Neither is worth a
// crash where there is no AuthProvider (a unit test, a public page), so this
// returns nulls instead of throwing.

import { useAuth } from '@/context/AuthContext';

export function useSignedInStaff(): { userId: string | null; name: string | null } {
  try {
    // The hook call is unconditional; only its throw is swallowed.
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const { user } = useAuth();
    return { userId: user?.user_id ?? null, name: user?.full_name ?? null };
  } catch {
    return { userId: null, name: null };
  }
}
