import { StrictMode, type ReactNode } from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useFragmentToken } from '../hooks/useFragmentToken';

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
