import { StrictMode, type ReactNode } from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { forgetFragmentToken, useFragmentToken } from '../hooks/useFragmentToken';

function setUrl(url: string): void {
  window.history.replaceState({ idx: 3 }, '', url);
}

describe('useFragmentToken', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    setUrl('/');
  });

  it('returns the bare fragment as the token', () => {
    setUrl('/activate#abc123');
    const { result } = renderHook(() => useFragmentToken());
    expect(result.current).toBe('abc123');
  });

  it('returns an empty string when there is no fragment', () => {
    setUrl('/activate');
    const { result } = renderHook(() => useFragmentToken());
    expect(result.current).toBe('');
  });

  it('removes the fragment from the address bar, keeping path, search and router state', () => {
    setUrl('/reset-password?lang=hi#secret-token');
    renderHook(() => useFragmentToken());
    expect(window.location.hash).toBe('');
    expect(window.location.pathname).toBe('/reset-password');
    expect(window.location.search).toBe('?lang=hi');
    expect(window.history.state).toEqual({ idx: 3 });
  });

  it('adds no history entry (replaceState, never pushState)', () => {
    setUrl('/verify#tok');
    const push = vi.spyOn(window.history, 'pushState');
    const replace = vi.spyOn(window.history, 'replaceState');
    const before = window.history.length;
    renderHook(() => useFragmentToken());
    expect(push).not.toHaveBeenCalled();
    expect(replace).toHaveBeenCalled();
    expect(window.history.length).toBe(before);
  });

  it('keeps returning the token after the fragment is gone, across re-renders', () => {
    setUrl('/x#tok-1');
    const { result, rerender } = renderHook(() => useFragmentToken());
    expect(window.location.hash).toBe('');
    rerender();
    rerender();
    expect(result.current).toBe('tok-1');
  });

  it('is correct under React 18 StrictMode (double initialiser, double effect)', () => {
    setUrl('/x#strict-tok');
    const wrapper = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>;
    const { result, rerender } = renderHook(() => useFragmentToken(), { wrapper });
    expect(result.current).toBe('strict-tok');
    rerender();
    expect(result.current).toBe('strict-tok');
    expect(window.location.hash).toBe('');
  });

  it('never writes the token to storage or a cookie', () => {
    setUrl('/x#never-store-me');
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    renderHook(() => useFragmentToken());
    expect(setItem).not.toHaveBeenCalled();
    expect(document.cookie).not.toContain('never-store-me');
    expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain('never-store-me');
  });

  it('a genuinely new mount after the scrub sees no token', () => {
    setUrl('/x#once');
    renderHook(() => useFragmentToken());
    const second = renderHook(() => useFragmentToken());
    expect(second.result.current).toBe('');
  });
});

// Audit CA-H3: a page somebody works on must survive a reload. Same pattern as
// /exam: this tab's sessionStorage, never localStorage or a cookie.
describe('useFragmentToken(storageKey)', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    setUrl('/');
  });

  it('a reload (a new mount with the fragment gone) still has the token', () => {
    setUrl('/apply/draft#keep-me');
    const first = renderHook(() => useFragmentToken('k.draft'));
    expect(first.result.current).toBe('keep-me');
    expect(window.location.hash).toBe('');
    first.unmount();

    const reloaded = renderHook(() => useFragmentToken('k.draft'));
    expect(reloaded.result.current).toBe('keep-me');
  });

  it('keeps it in this tab only, never in localStorage or a cookie', () => {
    setUrl('/task#tab-only');
    renderHook(() => useFragmentToken('k.task'));
    expect(sessionStorage.getItem('k.task')).toBe('tab-only');
    expect(JSON.stringify({ ...localStorage })).not.toContain('tab-only');
    expect(document.cookie).not.toContain('tab-only');
  });

  it('a freshly opened link wins over one an earlier link left behind', () => {
    sessionStorage.setItem('k.offer', 'old-link');
    setUrl('/offer#new-link');
    const { result } = renderHook(() => useFragmentToken('k.offer'));
    expect(result.current).toBe('new-link');
    expect(sessionStorage.getItem('k.offer')).toBe('new-link');
  });

  it('forgetFragmentToken drops it, so a spent link does not linger', () => {
    setUrl('/offer#spent');
    renderHook(() => useFragmentToken('k.spent'));
    forgetFragmentToken('k.spent');
    const after = renderHook(() => useFragmentToken('k.spent'));
    expect(after.result.current).toBe('');
  });

  it('still returns the token when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    setUrl('/offer#no-storage');
    const { result } = renderHook(() => useFragmentToken('k.blocked'));
    expect(result.current).toBe('no-storage');
  });
});
