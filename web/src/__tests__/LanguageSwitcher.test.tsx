// Tests for LanguageSwitcher component and i18n integration.
// Covers:
//   1. Switcher opens menu and shows all 3 language options.
//   2. Clicking हिंदी calls i18n.changeLanguage('hi').
//   3. Clicking తెలుగు calls i18n.changeLanguage('te').
//   4. EN option is aria-checked when language is en.
//   5. Landing page renders its (hardcoded) English hero headline.
//   6. After switching to 'hi' via the switcher, i18n reflects the new language
//      and the trigger button shows the Hindi label.
//
// Design notes (aurora UI redesign, commit 6dbf60e):
//   - LanguageSwitcher is now a Globe trigger button (aria-label = t('lang.label')
//     = "UI language") that opens a role="menu" dropdown.
//   - Language items inside the menu have role="menuitemradio" and
//     aria-label = t('lang.<code>') ("English" / "हिंदी" / "తెలుగు").
//   - Active item has aria-checked="true" (not aria-pressed).
//   - The menu must be opened by clicking the trigger before querying items.
//   - The Landing page (LandingPage.tsx) uses hardcoded English copy; it does
//     NOT consume i18n keys, so headline assertions test the actual DOM text.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { I18nextProvider } from 'react-i18next';
import i18n from '../lib/i18n';
import LanguageSwitcher from '../components/LanguageSwitcher';
import Landing from '../pages/Landing';
import { AuthProvider } from '../context/AuthContext';

// ── Switcher isolation tests ──────────────────────────────────────────────────

function renderSwitcher() {
  return render(
    <I18nextProvider i18n={i18n}>
      <LanguageSwitcher />
    </I18nextProvider>,
  );
}

describe('LanguageSwitcher', () => {
  beforeEach(async () => {
    // Reset to English before each test
    await i18n.changeLanguage('en');
  });

  it('renders three language options: EN, हिंदी, తెలుగు inside the dropdown menu', async () => {
    const user = userEvent.setup();
    renderSwitcher();

    // The trigger button is the Globe button labelled "UI language"
    const trigger = screen.getByRole('button', { name: /ui language/i });
    await user.click(trigger);

    // All three items appear as menuitemradio once the menu opens
    expect(screen.getByRole('menuitemradio', { name: /english/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitemradio', { name: /हिंदी/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitemradio', { name: /తెలుగు/i })).toBeInTheDocument();
  });

  it('calls changeLanguage("hi") when हिंदी is clicked', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(i18n, 'changeLanguage');
    renderSwitcher();

    // Open the menu first
    await user.click(screen.getByRole('button', { name: /ui language/i }));
    await user.click(screen.getByRole('menuitemradio', { name: /हिंदी/i }));

    expect(spy).toHaveBeenCalledWith('hi');
    spy.mockRestore();
  });

  it('calls changeLanguage("te") when తెలుగు is clicked', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(i18n, 'changeLanguage');
    renderSwitcher();

    // Open the menu first
    await user.click(screen.getByRole('button', { name: /ui language/i }));
    await user.click(screen.getByRole('menuitemradio', { name: /తెలుగు/i }));

    expect(spy).toHaveBeenCalledWith('te');
    spy.mockRestore();
  });

  it('marks EN as aria-checked when language is en', async () => {
    const user = userEvent.setup();
    renderSwitcher();

    // Open the menu to reveal the items
    await user.click(screen.getByRole('button', { name: /ui language/i }));

    const enItem = screen.getByRole('menuitemradio', { name: /english/i });
    expect(enItem).toHaveAttribute('aria-checked', 'true');
  });
});

// ── Integration: Landing page + LanguageSwitcher ──────────────────────────────

// Stub auth context so Landing doesn't redirect
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

function renderLandingWithSwitcher() {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter>
        <AuthProvider>
          <LanguageSwitcher />
          <Landing />
        </AuthProvider>
      </MemoryRouter>
    </I18nextProvider>,
  );
}

describe('Landing page i18n integration', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('shows English headline by default', async () => {
    renderLandingWithSwitcher();
    // The aurora-redesigned Landing hero headline is hardcoded English copy.
    // The h1 reads "Talk to an AI interviewer. / Get hired faster."
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /talk to an ai interviewer/i }),
      ).toBeInTheDocument();
    });
  });

  it('calls changeLanguage("hi") and trigger shows Hindi label after switching to hi', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(i18n, 'changeLanguage');
    renderLandingWithSwitcher();

    // Open the Globe dropdown (trigger aria-label = "UI language" in English)
    await user.click(screen.getByRole('button', { name: /ui language/i }));

    // Click the Hindi option
    await user.click(screen.getByRole('menuitemradio', { name: /हिंदी/i }));

    // i18n.changeLanguage must have been called with 'hi'
    expect(spy).toHaveBeenCalledWith('hi');

    // After switching, t('lang.label') returns the Hindi translation "UI भाषा"
    // and the trigger's visible span now shows "हिंदी".
    await waitFor(() => {
      // The trigger now carries the Hindi aria-label
      const trigger = screen.getByRole('button', { name: /ui भाषा/i });
      expect(trigger).toHaveTextContent('हिंदी');
    });

    spy.mockRestore();
  });
});
