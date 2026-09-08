// Tests for the app-wide light/dark mode.
//
// The load-bearing rule is the gate: the staff consoles and the live interview
// have no light design, so the visitor's preference must NOT reach them. If
// that broke, an HR manager who had once picked light on the marketing page
// would get a console painted in tokens its markup was never written for —
// white text on white cards — and no other test would catch it.
//
// Covers:
//   1. A themed route (candidate/auth/public) honours the stored mode.
//   2. A dark-only route (HR console, live interview) is forced dark.
//   3. Leaving the dark-only route restores the preference — it was overridden
//      for the render, not overwritten in storage.
//   4. `.dark` and `data-mode` never disagree (shadcn's `dark:` variants key
//      off the class, the palettes off the attribute).

import { describe, it, expect, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ThemeModeGate from '../components/ThemeModeGate';
import { MODE_KEY, isThemedRoute } from '../lib/useThemeMode';

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ThemeModeGate />
      <Routes>
        <Route path="*" element={<div />} />
      </Routes>
    </MemoryRouter>,
  );
}

function mode(): string | null {
  return document.documentElement.getAttribute('data-mode');
}

describe('theme mode gate', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-mode');
    document.documentElement.classList.remove('dark');
  });

  it('honours the stored mode on a themed route', () => {
    localStorage.setItem(MODE_KEY, 'dark');
    renderAt('/dashboard');
    expect(mode()).toBe('dark');
  });

  it('defaults to light on a themed route with no stored choice', () => {
    renderAt('/jobs');
    expect(mode()).toBe('light');
  });

  it.each(['/hr', '/admin/overview', '/platform', '/superadmin', '/interview/abc123'])(
    'forces dark on %s, which has no light design',
    (path) => {
      localStorage.setItem(MODE_KEY, 'light');
      renderAt(path);
      expect(mode()).toBe('dark');
      // The preference itself is untouched — it applies again on the way back.
      expect(localStorage.getItem(MODE_KEY)).toBe('light');
    },
  );

  it('keeps the .dark class and data-mode in agreement', () => {
    localStorage.setItem(MODE_KEY, 'dark');
    renderAt('/dashboard');
    expect(document.documentElement.classList.contains('dark')).toBe(true);

    localStorage.setItem(MODE_KEY, 'light');
    renderAt('/dashboard');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(mode()).toBe('light');
  });

  it('classifies routes: candidate and public in, staff and live interview out', () => {
    expect(isThemedRoute('/')).toBe(true);
    expect(isThemedRoute('/login')).toBe(true);
    expect(isThemedRoute('/scorecard/abc')).toBe(true);
    expect(isThemedRoute('/careers/acme')).toBe(true);
    expect(isThemedRoute('/hr/applicants')).toBe(false);
    expect(isThemedRoute('/admin/interviews')).toBe(false);
    // /interview/:id is the live session; /interview-invite is the public page.
    expect(isThemedRoute('/interview/abc')).toBe(false);
    expect(isThemedRoute('/interview-invite')).toBe(true);
  });
});
