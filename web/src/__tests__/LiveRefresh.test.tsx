// A5 — a new notification refreshes the views its event made stale.

import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { queriesToRefresh, REFRESH_ON, useLiveRefresh } from '../lib/liveRefresh';

describe('queriesToRefresh', () => {
  it('refreshes exam results and everything downstream when an exam is submitted', () => {
    const keys = queriesToRefresh(['exam_submitted']);
    expect(keys).toContainEqual(['hr', 'exam']);
    expect(keys).toContainEqual(['hr', 'pipeline']);
    expect(keys).toContainEqual(['hr', 'decision-queue']);
    // The attention panel does not poll — this is how it learns anything.
    expect(keys).toContainEqual(['hr-attention']);
  });

  it('refreshes the interviews page when an interview completes', () => {
    expect(queriesToRefresh(['interview_completed'])).toContainEqual(['hr', 'interviews']);
  });

  it("refreshes the candidate's own applications for their events", () => {
    expect(queriesToRefresh(['results_ready'])).toContainEqual(['my-applications']);
    expect(queriesToRefresh(['interview_invite'])).toContainEqual(['my-applications']);
  });

  it('asks for each view once however many events touch it', () => {
    const keys = queriesToRefresh(['exam_submitted', 'interview_completed', 'exam_submitted']);
    const ids = keys.map((k) => JSON.stringify(k));
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('refreshes nothing for a kind it does not know', () => {
    // A new backend event must be safe to ship before this map learns it.
    expect(queriesToRefresh(['some_future_kind', 'welcome'])).toEqual([]);
  });

  it('covers every event the backend announces to HR', () => {
    for (const kind of [
      'exam_submitted',
      'interview_completed',
      'link_expired',
      'bulk_upload',
      'interview_no_show',
    ]) {
      expect(REFRESH_ON[kind]?.length).toBeGreaterThan(0);
    }
  });
});

describe('useLiveRefresh', () => {
  function setup() {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries').mockResolvedValue(undefined);
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    type Items = { id: string; kind: string }[] | undefined;
    const hook = renderHook(({ items }: { items: Items }) => useLiveRefresh(items), {
      wrapper,
      initialProps: { items: undefined as Items },
    });
    const invalidated = () =>
      invalidate.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
    return { hook, invalidate, invalidated };
  }

  it('does nothing for the notifications already there on first load', () => {
    const { hook, invalidate } = setup();
    hook.rerender({ items: [{ id: 'n1', kind: 'exam_submitted' }] });
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('refreshes for a notification that arrives afterwards', () => {
    const { hook, invalidated } = setup();
    hook.rerender({ items: [{ id: 'n1', kind: 'welcome' }] });
    hook.rerender({
      items: [{ id: 'n2', kind: 'interview_completed' }, { id: 'n1', kind: 'welcome' }],
    });
    expect(invalidated()).toContain(JSON.stringify(['hr', 'interviews']));
  });

  it('does not refresh again for the same notification on the next poll', () => {
    const { hook, invalidate } = setup();
    hook.rerender({ items: [] });
    hook.rerender({ items: [{ id: 'n2', kind: 'exam_submitted' }] });
    const after = invalidate.mock.calls.length;
    expect(after).toBeGreaterThan(0);
    hook.rerender({ items: [{ id: 'n2', kind: 'exam_submitted' }] });
    expect(invalidate.mock.calls.length).toBe(after);
  });
});
