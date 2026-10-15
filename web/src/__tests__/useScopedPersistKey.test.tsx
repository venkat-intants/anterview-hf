// useScopedPersistKey - collapsible preferences are keyed by a short
// non-reversible hash of the user id, never the id, and are cleared on logout.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

const auth = vi.hoisted(() => ({ user: { user_id: '3f2b8c1e-aaaa-4bbb-8ccc-1234567890ab' } as { user_id: string } | null }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ user: auth.user }) }));

import {
  clearCollapsibleKeysFor,
  hashUserId,
  useScopedPersistKey,
} from '../hooks/useScopedPersistKey';

const ID = '3f2b8c1e-aaaa-4bbb-8ccc-1234567890ab';
const P = 'anthire:collapsible:';

beforeEach(() => {
  localStorage.clear();
  auth.user = { user_id: ID };
});

describe('useScopedPersistKey', () => {
  it('keys on a hash, never the raw user id', () => {
    const { result } = renderHook(() => useScopedPersistKey('hub-activity'));
    expect(result.current).toBe(`hub-activity:${hashUserId(ID)}`);
    expect(result.current).not.toContain(ID);
    expect(result.current).not.toContain('3f2b8c1e');
  });

  it('is stable per user, different between users, and falls back to anon', () => {
    expect(hashUserId(ID)).toBe(hashUserId(ID));
    expect(hashUserId(ID)).not.toBe(hashUserId('someone-else'));
    auth.user = null;
    const { result } = renderHook(() => useScopedPersistKey('x'));
    expect(result.current).toBe('x:anon');
  });

  it('sweeps keys written before the hash (raw id) on first load', () => {
    localStorage.setItem(`${P}hub-activity:${ID}`, '1');
    localStorage.setItem(`${P}hub-activity:other`, '1');
    renderHook(() => useScopedPersistKey('hub-activity'));
    expect(localStorage.getItem(`${P}hub-activity:${ID}`)).toBeNull();
    expect(localStorage.getItem(`${P}hub-activity:other`)).toBe('1');
  });
});

describe('clearCollapsibleKeysFor (logout)', () => {
  it('removes only the signing-out user\'s collapsible keys', () => {
    localStorage.setItem(`${P}a:${hashUserId(ID)}`, '1');
    localStorage.setItem(`${P}b:${ID}`, '0');
    localStorage.setItem(`${P}a:${hashUserId('other')}`, '1');
    localStorage.setItem('unrelated', 'keep');
    clearCollapsibleKeysFor(ID);
    expect(localStorage.getItem(`${P}a:${hashUserId(ID)}`)).toBeNull();
    expect(localStorage.getItem(`${P}b:${ID}`)).toBeNull();
    expect(localStorage.getItem(`${P}a:${hashUserId('other')}`)).toBe('1');
    expect(localStorage.getItem('unrelated')).toBe('keep');
  });
});
