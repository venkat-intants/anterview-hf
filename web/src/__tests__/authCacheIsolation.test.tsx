// Signing in as a different account drops the previous account's cached data
// (security audit W2). Query keys are not scoped by user, so without this a
// second person signing in on the same tab could briefly see the first's
// assignments and candidates.

import { describe, it, expect, vi } from 'vitest';
import { act, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth } from '../context/AuthContext';
import type { AuthUser } from '../types/auth';

vi.mock('../api/client', () => ({ attemptRefresh: vi.fn(() => Promise.resolve(null)) }));
vi.mock('../api/auth', () => ({ getMe: vi.fn() }));

function person(id: string): AuthUser {
  return { user_id: id, full_name: id, email: `${id}@x.test`, roles: ['interviewer'],
    must_change_password: false };
}

function setup() {
  const qc = new QueryClient();
  let auth: ReturnType<typeof useAuth> | null = null;
  function Grab() {
    auth = useAuth();
    return null;
  }
  render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <Grab />
      </AuthProvider>
    </QueryClientProvider>,
  );
  return { qc, setAuth: (u: AuthUser) => act(() => auth!.setAuth('tok', u)) };
}

describe('AuthProvider cache isolation', () => {
  it("drops cached queries when a different account signs in", () => {
    const { qc, setAuth } = setup();
    setAuth(person('alice'));
    qc.setQueryData(['interviewer', 'assignments'], [{ candidate_name: 'Asha' }]);
    setAuth(person('bob'));
    expect(qc.getQueryData(['interviewer', 'assignments'])).toBeUndefined();
  });

  it('keeps them on the first sign-in in a tab, which has no other account to hide', () => {
    const { qc, setAuth } = setup();
    qc.setQueryData(['public', 'jobs'], [{ title: 'Engineer' }]);
    setAuth(person('alice'));
    expect(qc.getQueryData(['public', 'jobs'])).toEqual([{ title: 'Engineer' }]);
  });

  it('keeps them when the same account re-authenticates (e.g. after a password change)', () => {
    const { qc, setAuth } = setup();
    setAuth(person('alice'));
    qc.setQueryData(['interviewer', 'assignments'], [{ candidate_name: 'Asha' }]);
    setAuth({ ...person('alice'), must_change_password: false });
    expect(qc.getQueryData(['interviewer', 'assignments'])).toEqual([{ candidate_name: 'Asha' }]);
  });
});
