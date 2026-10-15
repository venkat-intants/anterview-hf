// Collapsible and ProgressiveList — the one disclosure behind every activity,
// history and timeline section.
//
// What matters: it is a real disclosure (button, aria-expanded, aria-controls,
// a labelled region), closed content costs nothing, the choice is remembered
// when storage works and the section still works when it does not, motion
// respects the reader's preference, and a long list shows its first few then
// offers the rest.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Collapsible, ProgressiveList } from '../design/components/collapsible';

const KEY = 'anthire:collapsible:';

function reduced(matches: boolean): void {
  window.matchMedia = vi.fn().mockImplementation((q: string) => ({
    matches: matches && q.includes('prefers-reduced-motion'),
    media: q,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

beforeEach(() => {
  localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  // @ts-expect-error — put jsdom back to "no matchMedia"
  delete window.matchMedia;
});

describe('Collapsible — a real disclosure', () => {
  it('is a button in a heading that names the region it controls', () => {
    render(
      <Collapsible title="Activity" count={3} noun="event">
        <p>body</p>
      </Collapsible>,
    );
    const button = screen.getByRole('button', { name: /Activity/ });
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(button.closest('h3')).not.toBeNull();
    const region = document.getElementById(button.getAttribute('aria-controls') as string);
    expect(region).not.toBeNull();
    expect(region?.getAttribute('role')).toBe('region');
    expect(region?.getAttribute('aria-labelledby')).toBe(button.id);
  });

  it('opens and closes, and renders nothing while closed', async () => {
    const user = userEvent.setup();
    render(
      <Collapsible title="Activity">
        <p>the body</p>
      </Collapsible>,
    );
    expect(screen.queryByText('the body')).toBeNull();

    await user.click(screen.getByRole('button', { name: /Activity/ }));
    expect(screen.getByRole('button', { name: /Activity/ }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText('the body')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /Activity/ }));
    expect(screen.queryByText('the body')).toBeNull();
  });

  it('opens with Enter and with Space', async () => {
    const user = userEvent.setup();
    render(
      <Collapsible title="Activity">
        <p>the body</p>
      </Collapsible>,
    );
    screen.getByRole('button', { name: /Activity/ }).focus();
    await user.keyboard('{Enter}');
    expect(screen.getByText('the body')).toBeTruthy();
    await user.keyboard(' ');
    expect(screen.queryByText('the body')).toBeNull();
  });

  it('honours defaultOpen', () => {
    render(
      <Collapsible title="Activity" defaultOpen>
        <p>the body</p>
      </Collapsible>,
    );
    expect(screen.getByText('the body')).toBeTruthy();
  });

  it('shows count and the newest item while closed, and not once open', async () => {
    const user = userEvent.setup();
    render(
      <Collapsible title="Activity" count={12} noun="event" latest="Vikram Singh moved to AI interview, 2h ago">
        <p>the body</p>
      </Collapsible>,
    );
    expect(screen.getByTestId('collapsible-summary').textContent).toBe(
      '12 events · latest: Vikram Singh moved to AI interview, 2h ago',
    );
    await user.click(screen.getByRole('button', { name: /Activity/ }));
    expect(screen.queryByTestId('collapsible-summary')).toBeNull();
  });

  it('says "1 event", not "1 events", and omits a latest it does not have', () => {
    render(
      <Collapsible title="Activity" count={1} noun="event">
        <p>x</p>
      </Collapsible>,
    );
    expect(screen.getByTestId('collapsible-summary').textContent).toBe('1 event');
  });

  it('uses an irregular plural when given one: "3 entries"', () => {
    render(
      <Collapsible title="History" count={3} noun="entry" nounPlural="entries">
        <p>x</p>
      </Collapsible>,
    );
    expect(screen.getByTestId('collapsible-summary').textContent).toBe('3 entries');
  });

  it('keeps header controls outside the toggle', async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(
      <Collapsible title="History" actions={<button onClick={onClick}>Evidence trail</button>}>
        <p>x</p>
      </Collapsible>,
    );
    await user.click(screen.getByRole('button', { name: 'Evidence trail' }));
    expect(onClick).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /History/ }).getAttribute('aria-expanded')).toBe('false');
  });

  it('has a 40px hit target on the toggle', () => {
    render(
      <Collapsible title="Activity">
        <p>x</p>
      </Collapsible>,
    );
    expect(screen.getByRole('button', { name: /Activity/ }).className).toMatch(/min-h-10/);
  });
});

describe('Collapsible — remembers the choice', () => {
  it('stores it under the key and restores it on the next mount', async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <Collapsible title="Activity" persistKey="t:u1">
        <p>the body</p>
      </Collapsible>,
    );
    await user.click(screen.getByRole('button', { name: /Activity/ }));
    expect(localStorage.getItem(`${KEY}t:u1`)).toBe('1');
    unmount();

    render(
      <Collapsible title="Activity" persistKey="t:u1">
        <p>the body</p>
      </Collapsible>,
    );
    expect(screen.getByText('the body')).toBeTruthy();
  });

  it('a stored "closed" beats defaultOpen', () => {
    localStorage.setItem(`${KEY}t:u1`, '0');
    render(
      <Collapsible title="Activity" persistKey="t:u1" defaultOpen>
        <p>the body</p>
      </Collapsible>,
    );
    expect(screen.queryByText('the body')).toBeNull();
  });

  it('keeps one person\'s choice apart from another\'s', () => {
    localStorage.setItem(`${KEY}t:u1`, '1');
    render(
      <Collapsible title="Activity" persistKey="t:u2">
        <p>the body</p>
      </Collapsible>,
    );
    expect(screen.queryByText('the body')).toBeNull();
  });

  it('still works when storage throws on read and on write', async () => {
    const user = userEvent.setup();
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    render(
      <Collapsible title="Activity" persistKey="t:u1">
        <p>the body</p>
      </Collapsible>,
    );
    await user.click(screen.getByRole('button', { name: /Activity/ }));
    expect(screen.getByText('the body')).toBeTruthy();
  });

  it('writes nothing without a key', async () => {
    const user = userEvent.setup();
    render(
      <Collapsible title="Activity">
        <p>x</p>
      </Collapsible>,
    );
    await user.click(screen.getByRole('button', { name: /Activity/ }));
    expect(localStorage.length).toBe(0);
  });
});

describe('Collapsible — motion', () => {
  it('is instant under prefers-reduced-motion: reduce', async () => {
    reduced(true);
    const user = userEvent.setup();
    render(
      <Collapsible title="Activity">
        <p>the body</p>
      </Collapsible>,
    );
    await user.click(screen.getByRole('button', { name: /Activity/ }));
    const region = screen.getByRole('region');
    expect(region.className).toMatch(/grid-rows-\[1fr\]/);
    await user.click(screen.getByRole('button', { name: /Activity/ }));
    expect(screen.queryByText('the body')).toBeNull();
  });

  it('keeps the content mounted while it animates shut, then removes it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      reduced(false);
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(
        <Collapsible title="Activity" defaultOpen>
          <p>the body</p>
        </Collapsible>,
      );
      await user.click(screen.getByRole('button', { name: /Activity/ }));
      // Closing: aria says closed at once, the body is still there for the transition.
      expect(screen.getByRole('button', { name: /Activity/ }).getAttribute('aria-expanded')).toBe('false');
      expect(screen.getByText('the body')).toBeTruthy();
      act(() => {
        vi.advanceTimersByTime(300);
      });
      expect(screen.queryByText('the body')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('declares its transition as motion-reduce:transition-none', () => {
    render(
      <Collapsible title="Activity">
        <p>x</p>
      </Collapsible>,
    );
    expect(screen.getByRole('region', { hidden: true }).className).toMatch(/motion-reduce:transition-none/);
  });
});

describe('ProgressiveList', () => {
  const items = Array.from({ length: 12 }, (_, i) => ({ id: `i${i}`, label: `Event ${i + 1}` }));
  const renderList = (list = items) =>
    render(
      <ProgressiveList
        items={list}
        initial={5}
        noun="events"
        getKey={(x) => x.id}
        renderItem={(x) => <li>{x.label}</li>}
      />,
    );

  it('shows the first five and offers the rest', async () => {
    const user = userEvent.setup();
    renderList();
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
    const more = screen.getByRole('button', { name: 'Show all 12 events' });
    await user.click(more);
    expect(screen.getAllByRole('listitem')).toHaveLength(12);
    await user.click(screen.getByRole('button', { name: 'Show fewer' }));
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
  });

  it('shows a short list whole, with no button', () => {
    renderList(items.slice(0, 5));
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
    expect(screen.queryByRole('button')).toBeNull();
  });
});
