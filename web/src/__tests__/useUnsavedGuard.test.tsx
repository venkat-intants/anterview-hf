// useUnsavedGuard - the browser Back button must ask before a dirty editor is
// left. beforeunload does not fire for an in-app history move and BrowserRouter
// cannot block one, so the hook parks a sentinel history entry while dirty.

import { describe, it, expect, vi, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { LEAVE_PROMPT, useUnsavedGuard } from '../components/bank/useUnsavedGuard';

afterEach(() => {
  vi.restoreAllMocks();
});

function pressBack(): void {
  act(() => {
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
}

describe('useUnsavedGuard - browser Back', () => {
  it('asks on Back while dirty, and stays dirty (re-arming) when declined', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const push = vi.spyOn(window.history, 'pushState');
    const { result } = renderHook(() => useUnsavedGuard());

    act(() => result.current.setDirty(true));
    expect(push).toHaveBeenCalledTimes(1); // the sentinel

    pressBack();
    expect(confirm).toHaveBeenCalledWith(LEAVE_PROMPT);
    expect(result.current.dirty).toBe(true);
    expect(push).toHaveBeenCalledTimes(2); // sentinel restored
  });

  it('lets the person go once they agree', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const { result } = renderHook(() => useUnsavedGuard());
    act(() => result.current.setDirty(true));
    pressBack();
    expect(result.current.dirty).toBe(false);
  });

  it('does not ask, and parks nothing, when nothing is unsaved', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const push = vi.spyOn(window.history, 'pushState');
    renderHook(() => useUnsavedGuard());
    pressBack();
    expect(confirm).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it('stops listening once the edit is saved', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const { result } = renderHook(() => useUnsavedGuard());
    act(() => result.current.setDirty(true));
    act(() => result.current.setDirty(false));
    pressBack();
    expect(confirm).not.toHaveBeenCalled();
  });
});
