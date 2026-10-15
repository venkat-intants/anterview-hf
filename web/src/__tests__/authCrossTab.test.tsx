// Cross-tab session consistency.
//
// The refresh cookie is shared by every tab; the access token and the React
// user/roles/query-cache are per tab. So (1) logging out in one tab must sign
// the others out and drop their cache, and (2) when a token refresh in this tab
// turns out to belong to a different account (another tab signed in as someone
// else), the tab must adopt the new identity and drop the old account's cache.
// Nothing sent between tabs may contain a token.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth } from '../context/AuthContext';
import { getToken, setToken, clearToken } from '../api/tokenStore';
import { getMe } from '../api/auth';
import type { AuthUser } from '../types/auth';

vi.mock('../api/client', () => ({ attemptRefresh: vi.fn(() => Promise.resolve(false)) }));
vi.mock('../api/auth', () => ({ getMe: vi.fn() }));

const SECRET = 'super-secret-access-token';

function person(id: string, roles = ['interviewer']): AuthUser {
  return { user_id: id, full_name: id, email: `${id}@x.test`, roles, must_change_password: false };
}

class FakeChannel {
  static instances: FakeChannel[] = [];
  static posted: unknown[] = [];
  onmessage: ((e: MessageEvent<unknown>) => void) | null = null;
  closed = false;
  constructor(public name: string) {
    FakeChannel.instances.push(this);
  }
  postMessage(data: unknown): void {
    FakeChannel.posted.push(data);
    for (const other of FakeChannel.instances) {
      if (other !== this && !other.closed && other.name === this.name) {
        other.onmessage?.({ data } as MessageEvent<unknown>);
      }
    }
  }
  close(): void {
    this.closed = true;
  }
}

function setup() {
  const qc = new QueryClient();
  let auth: ReturnType<typeof useAuth> | null = null;
  function Grab() {
    auth = useAuth();
    return null;
  }
  const utils = render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <Grab />
      </AuthProvider>
    </QueryClientProvider>,
  );
  return { qc, utils, get: () => auth! };
}

/** A second "tab": a channel on the same name that the test drives by hand. */
function otherTab(): FakeChannel {
  return new FakeChannel('anthire-auth');
}

beforeEach(() => {
  FakeChannel.instances = [];
  FakeChannel.posted = [];
  vi.stubGlobal('BroadcastChannel', FakeChannel);
  vi.mocked(getMe).mockReset();
  clearToken();
});
afterEach(() => {
  vi.unstubAllGlobals();
  clearToken();
  localStorage.clear();
});

describe('logout across tabs', () => {
  it('broadcasts logout when this tab signs out', () => {
    const { get } = setup();
    act(() => get().setAuth(SECRET, person('alice')));
    FakeChannel.posted = [];
    act(() => get().clearAuth());
    expect(FakeChannel.posted).toEqual([{ type: 'logout' }]);
  });

  it('signs this tab out and clears the query cache when another tab logs out', async () => {
    const { qc, get } = setup();
    act(() => get().setAuth(SECRET, person('alice')));
    qc.setQueryData(['hr', 'applicants'], [{ name: 'Asha' }]);
    expect(get().isAuthenticated).toBe(true);

    const tab = otherTab();
    act(() => tab.postMessage({ type: 'logout' }));

    await waitFor(() => expect(get().isAuthenticated).toBe(false));
    expect(get().user).toBeNull();
    expect(getToken()).toBeNull();
    expect(qc.getQueryData(['hr', 'applicants'])).toBeUndefined();
  });

  it('does not re-broadcast a logout it received (no ping-pong between tabs)', () => {
    const { get } = setup();
    act(() => get().setAuth(SECRET, person('alice')));
    const tab = otherTab();
    FakeChannel.posted = [];
    act(() => tab.postMessage({ type: 'logout' }));
    // Only the other tab's own message is on the wire.
    expect(FakeChannel.posted).toEqual([{ type: 'logout' }]);
  });

  it('ignores malformed or unknown messages', () => {
    const { get } = setup();
    act(() => get().setAuth(SECRET, person('alice')));
    const tab = otherTab();
    act(() => {
      tab.postMessage('logout');
      tab.postMessage({ type: 'login' }); // missing userId
      tab.postMessage({ type: 'nuke' });
      tab.postMessage(null);
    });
    expect(get().isAuthenticated).toBe(true);
  });

  it('closes its channel on unmount', () => {
    const { utils } = setup();
    const mine = FakeChannel.instances[0];
    utils.unmount();
    expect(mine?.closed).toBe(true);
  });
});

describe('login broadcast', () => {
  it('announces the account id, and never the token', () => {
    const { get } = setup();
    act(() => get().setAuth(SECRET, person('alice')));
    expect(FakeChannel.posted).toEqual([{ type: 'login', userId: 'alice' }]);
    expect(JSON.stringify(FakeChannel.posted)).not.toContain(SECRET);
  });

  it('does not carry the token on logout either', () => {
    const { get } = setup();
    act(() => get().setAuth(SECRET, person('alice')));
    act(() => get().clearAuth());
    expect(JSON.stringify(FakeChannel.posted)).not.toContain(SECRET);
  });
});

describe('identity check after a token refresh', () => {
  it("adopts the new account and drops the old cache when the refreshed token is someone else's", async () => {
    const { qc, get } = setup();
    act(() => get().setAuth(SECRET, person('alice', ['interviewer'])));
    qc.setQueryData(['interviewer', 'assignments'], [{ candidate_name: 'Asha' }]);
    vi.mocked(getMe).mockResolvedValue({
      user_id: 'bob',
      full_name: 'bob',
      email: 'bob@x.test',
      roles: ['hr_manager'],
      must_change_password: false,
    } as Awaited<ReturnType<typeof getMe>>);

    // What client.ts does after a silent refresh: write straight to the store.
    act(() => setToken('token-from-refresh'));

    await waitFor(() => expect(get().user?.user_id).toBe('bob'));
    expect(get().user?.roles).toEqual(['hr_manager']);
    expect(qc.getQueryData(['interviewer', 'assignments'])).toBeUndefined();
  });

  it('keeps the cache and the user object when the refresh is the same account', async () => {
    const { qc, get } = setup();
    act(() => get().setAuth(SECRET, person('alice')));
    const before = get().user;
    qc.setQueryData(['interviewer', 'assignments'], [{ candidate_name: 'Asha' }]);
    vi.mocked(getMe).mockResolvedValue({
      user_id: 'alice',
      full_name: 'alice',
      email: 'alice@x.test',
      roles: ['interviewer'],
      must_change_password: false,
    } as Awaited<ReturnType<typeof getMe>>);

    act(() => setToken('token-from-refresh'));
    await waitFor(() => expect(getMe).toHaveBeenCalledTimes(1));

    expect(get().user).toBe(before);
    expect(qc.getQueryData(['interviewer', 'assignments'])).toEqual([{ candidate_name: 'Asha' }]);
  });

  it('keeps the current identity when the profile fetch fails', async () => {
    const { qc, get } = setup();
    act(() => get().setAuth(SECRET, person('alice')));
    qc.setQueryData(['k'], 1);
    vi.mocked(getMe).mockRejectedValue(new Error('offline'));
    act(() => setToken('token-from-refresh'));
    await waitFor(() => expect(getMe).toHaveBeenCalled());
    expect(get().user?.user_id).toBe('alice');
    expect(qc.getQueryData(['k'])).toBe(1);
  });

  it("does not re-check the identity for this tab's own sign-in", () => {
    const { get } = setup();
    act(() => get().setAuth(SECRET, person('alice')));
    expect(getMe).not.toHaveBeenCalled();
  });
});

describe('storage-event fallback (no BroadcastChannel)', () => {
  beforeEach(() => {
    vi.stubGlobal('BroadcastChannel', undefined);
  });

  it('writes a token-free ping on logout', () => {
    const { get } = setup();
    act(() => get().setAuth(SECRET, person('alice')));
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    act(() => get().clearAuth());
    const writes = setItem.mock.calls.filter(([k]) => k === 'anthire.auth.sync');
    expect(writes).toHaveLength(1);
    expect(String(writes[0]?.[1])).toContain('logout');
    expect(String(writes[0]?.[1])).not.toContain(SECRET);
    // And the ping is removed straight away, so it never lingers in storage.
    expect(localStorage.getItem('anthire.auth.sync')).toBeNull();
    setItem.mockRestore();
  });

  it('signs out when a storage event from another tab says logout', async () => {
    const { qc, get } = setup();
    act(() => get().setAuth(SECRET, person('alice')));
    qc.setQueryData(['k'], 1);
    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', {
          key: 'anthire.auth.sync',
          newValue: JSON.stringify({ event: { type: 'logout' }, n: 0.5 }),
        }),
      );
    });
    await waitFor(() => expect(get().isAuthenticated).toBe(false));
    expect(qc.getQueryData(['k'])).toBeUndefined();
  });

  it('ignores storage events for other keys and garbage values', () => {
    const { get } = setup();
    act(() => get().setAuth(SECRET, person('alice')));
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: 'other', newValue: '{}' }));
      window.dispatchEvent(
        new StorageEvent('storage', { key: 'anthire.auth.sync', newValue: 'not json' }),
      );
      window.dispatchEvent(
        new StorageEvent('storage', { key: 'anthire.auth.sync', newValue: null }),
      );
    });
    expect(get().isAuthenticated).toBe(true);
  });
});
