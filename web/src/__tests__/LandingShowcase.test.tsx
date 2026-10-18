// Tests for the landing page's product visuals.
//
// Covers:
//   1. The in-page product demo: one text alternative, a working pause control,
//      and the final composed frame (no timer, no control) under
//      prefers-reduced-motion.
//   2. Interviewer portraits fall back to initials when the image cannot load
//      (the Git LFS pointer case) instead of showing a broken image.
//   3. The page loads no video and no third-party image, and every image has
//      alt text and an explicit size.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nextProvider } from 'react-i18next';
import i18n from '../lib/i18n';
import Landing from '../pages/Landing';
import { ProductDemo } from '../landing/screens/Landing/ProductDemo';
import { PortraitTile } from '../landing/components/PortraitTile';
import { initialsOf } from '../landing/lib/initials';

vi.mock('../context/AuthContext', async () => {
  const actual = await vi.importActual<typeof import('../context/AuthContext')>('../context/AuthContext');
  return {
    ...actual,
    useAuth: vi.fn().mockReturnValue({
      isAuthenticated: false,
      isInitializing: false,
      user: null,
      accessToken: null,
      setAuth: vi.fn(),
      clearAuth: vi.fn(),
    }),
  };
});

function stubReducedMotion(matches: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: query.includes('prefers-reduced-motion') ? matches : false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

function renderLanding() {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter>
        <Landing />
      </MemoryRouter>
    </I18nextProvider>,
  );
}

describe('ProductDemo', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubReducedMotion(false);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('is labelled as a demo and has one text alternative', () => {
    render(<ProductDemo />);
    const fig = screen.getByRole('figure', { name: 'Product demo' });
    expect(fig.querySelector('figcaption')).toHaveTextContent(/invented content/i);
    // The moving stage is hidden from assistive tech; only the caption speaks.
    expect(fig.querySelector('[aria-hidden="true"].grid')).not.toBeNull();
  });

  it('plays, pauses and resumes from where it stopped', () => {
    render(<ProductDemo />);
    expect(screen.getByText('Anna is asking')).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(screen.getByText('You are answering')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Pause demo' }));
    expect(screen.getByRole('button', { name: 'Play demo' })).toHaveAttribute('aria-pressed', 'true');
    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(screen.getByText('You are answering')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Play demo' }));
    act(() => {
      vi.advanceTimersByTime(7000);
    });
    expect(screen.getByText('Anna is following up')).toBeInTheDocument();
  });

  it('pauses while the tab is hidden', () => {
    render(<ProductDemo />);
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(screen.getByText('You are answering')).toBeInTheDocument();
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
  });

  it('shows the finished frame, with no timer and no control, under reduced motion', () => {
    stubReducedMotion(true);
    render(<ProductDemo />);
    expect(screen.queryByRole('button', { name: /demo/i })).toBeNull();
    expect(screen.getByText('Interview complete')).toBeInTheDocument();
    expect(screen.getByText('78')).toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('PortraitTile', () => {
  it('turns into initials on the brand gradient when the image fails', () => {
    render(<PortraitTile src="/avatars/anna.jpg" alt="Anna, AI interviewer" name="Anna" width={240} height={300} />);
    const img = screen.getByAltText('Anna, AI interviewer');
    expect(img).toHaveAttribute('width', '240');
    expect(img).toHaveAttribute('height', '300');
    expect(img).toHaveAttribute('loading', 'lazy');

    fireEvent.error(img);

    expect(screen.queryByAltText('Anna, AI interviewer')).toBeNull();
    const tile = screen.getByRole('img', { name: 'Anna, AI interviewer' });
    expect(tile).toHaveTextContent('A');
  });

  it('loads the hero portrait eagerly and at high priority', () => {
    render(<PortraitTile src="/avatars/anna.jpg" alt="Anna" name="Anna" width={10} height={10} eager />);
    const img = screen.getByAltText('Anna');
    expect(img).toHaveAttribute('loading', 'eager');
    expect(img).toHaveAttribute('fetchpriority', 'high');
  });

  it('derives initials from letters only', () => {
    expect(initialsOf('Gloria (Warm)')).toBe('GW');
    expect(initialsOf('Raj')).toBe('R');
  });
});

describe('Landing media', () => {
  beforeEach(() => {
    localStorage.clear();
    stubReducedMotion(false);
  });

  it('has no video, and every image is same-origin with alt text and a size', () => {
    const { container } = renderLanding();
    expect(container.querySelectorAll('video')).toHaveLength(0);
    const imgs = [...container.querySelectorAll('img')];
    expect(imgs.length).toBeGreaterThan(6);
    for (const img of imgs) {
      expect(img.getAttribute('src') ?? '').toMatch(/^\//);
      expect(img.hasAttribute('alt')).toBe(true);
      expect(img.getAttribute('alt')).not.toBe('');
      expect(Number(img.getAttribute('width'))).toBeGreaterThan(0);
      expect(Number(img.getAttribute('height'))).toBeGreaterThan(0);
    }
  });

  it('lazy-loads every image except the one in the hero', () => {
    const { container } = renderLanding();
    const eager = [...container.querySelectorAll('img')].filter((i) => i.getAttribute('loading') !== 'lazy');
    expect(eager).toHaveLength(1);
    expect(eager[0]).toHaveAttribute('src', '/avatars/anna.jpg');
  });

  it('serves each screenshot in a light and a dark variant', () => {
    const { container } = renderLanding();
    const light = container.querySelectorAll('img[data-shot-variant="light"]');
    const dark = container.querySelectorAll('img[data-shot-variant="dark"]');
    expect(light.length).toBeGreaterThanOrEqual(4);
    expect(dark).toHaveLength(light.length);
  });
});

describe('Landing: a company hiring platform', () => {
  it('pitches hiring, not practice, campuses or government programmes', () => {
    renderLanding();
    const text = document.body.textContent ?? '';
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/hire faster/i);
    for (const word of [/practi[cs]e/i, /mock interview/i, /college/i, /skilling/i, /government/i, /naipunyam/i]) {
      expect(text).not.toMatch(word);
    }
    // Long dashes read as generated text; the page uses none.
    expect(text).not.toMatch(/[–—]/);
  });

  it('leads with the demo for companies; sign-up is for candidates', () => {
    renderLanding();
    const hero = screen.getByRole('heading', { level: 1 }).closest('section') as HTMLElement;
    const actions = hero.querySelectorAll('a');
    expect(actions[0]).toHaveTextContent('Book a demo');
    expect(hero).toHaveTextContent('Candidate sign up');
    expect(hero).toHaveTextContent(/companies are set up with our team/i);
  });
});
