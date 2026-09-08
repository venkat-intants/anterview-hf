// Tests for the landing page's light/dark theme switch.
//
// The landing page is the only surface with a light theme: the signed-in app is
// forced dark. So the two things worth pinning down are that the switch works,
// and that it stays scoped to the landing root — a regression that moved the
// attribute onto <html> would recolour the app behind the login, and nothing
// else in the suite would notice.
//
// Covers:
//   1. Light is the default for a first-time visitor (no stored choice).
//   2. Clicking Dark switches the landing root, and the choice is persisted.
//   3. A stored 'dark' choice is honoured on the next render.
//   4. The theme attribute never lands on <html> (the app's own theme is
//      `html.dark` + `html[data-theme]`, which this must not touch).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { I18nextProvider } from 'react-i18next';
import i18n from '../lib/i18n';
import Landing from '../pages/Landing';
import { LANDING_THEME_KEY } from '../landing/lib/useLandingTheme';

// Stub auth so Landing renders the marketing page instead of redirecting.
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

function renderLanding() {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter>
        <Landing />
      </MemoryRouter>
    </I18nextProvider>,
  );
}

/** The landing root is the element carrying the theme attribute. */
function themedRoot(): HTMLElement | null {
  return document.querySelector('[data-landing-theme]');
}

describe('Landing theme switch', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-landing-theme');
  });

  it('defaults to light for a visitor with no stored choice', () => {
    renderLanding();
    expect(themedRoot()).toHaveAttribute('data-landing-theme', 'light');
  });

  it('switches to dark and remembers the choice', async () => {
    const user = userEvent.setup();
    renderLanding();

    await user.click(screen.getByTestId('landing-theme-dark'));

    expect(themedRoot()).toHaveAttribute('data-landing-theme', 'dark');
    expect(localStorage.getItem(LANDING_THEME_KEY)).toBe('dark');
  });

  it('honours a stored dark choice on the next visit', () => {
    localStorage.setItem(LANDING_THEME_KEY, 'dark');
    renderLanding();
    expect(themedRoot()).toHaveAttribute('data-landing-theme', 'dark');
    expect(screen.getByTestId('landing-theme-dark')).toHaveAttribute('aria-checked', 'true');
  });

  it('never puts the landing theme on <html> — the app behind the login is unaffected', async () => {
    const user = userEvent.setup();
    renderLanding();

    await user.click(screen.getByTestId('landing-theme-dark'));

    expect(document.documentElement).not.toHaveAttribute('data-landing-theme');
  });
});
