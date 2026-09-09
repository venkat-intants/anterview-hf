// Tests for the landing page's light/dark switch.
//
// The switch writes the SHARED app mode (lib/useThemeMode), so a choice made on
// the marketing page carries into login and the candidate pages. The landing
// root still stamps its own `data-landing-theme` for the `--lp-*` tokens, and
// that attribute must keep mirroring the mode — if the two drifted, the page
// would announce one theme and paint the other.
//
// Covers:
//   1. Light is the default for a first-time visitor (no stored choice).
//   2. Clicking Dark switches the landing root, and the choice is persisted
//      under the shared key.
//   3. A stored 'dark' choice is honoured on the next render.
//   4. The landing root's attribute matches the stored mode after a switch.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { I18nextProvider } from 'react-i18next';
import i18n from '../lib/i18n';
import Landing from '../pages/Landing';
import { MODE_KEY } from '../lib/useThemeMode';

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
    expect(localStorage.getItem(MODE_KEY)).toBe('dark');
  });

  it('honours a stored dark choice on the next visit', () => {
    localStorage.setItem(MODE_KEY, 'dark');
    renderLanding();
    expect(themedRoot()).toHaveAttribute('data-landing-theme', 'dark');
    expect(screen.getByTestId('landing-theme-dark')).toHaveAttribute('aria-checked', 'true');
  });

  it('keeps the landing root in step with the shared mode', async () => {
    const user = userEvent.setup();
    renderLanding();

    await user.click(screen.getByTestId('landing-theme-dark'));
    expect(themedRoot()).toHaveAttribute('data-landing-theme', localStorage.getItem(MODE_KEY));

    await user.click(screen.getByTestId('landing-theme-light'));
    expect(themedRoot()).toHaveAttribute('data-landing-theme', 'light');
    expect(localStorage.getItem(MODE_KEY)).toBe('light');
  });
});
