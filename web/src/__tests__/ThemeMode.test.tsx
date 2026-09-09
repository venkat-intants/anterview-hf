// Tests for the app-wide light/dark mode.
//
// The gate now works the other way round. The consoles were swept onto the
// mode tokens, so the preference reaches the WHOLE app and the exclusion list
// is one route: the live interview, pinned dark because it is a video surface.
//
// The load-bearing case is the boundary around that one route. `/interview/:id`
// is pinned; `/interview-invite` and `/interview/:id/complete` are ordinary
// pages that must NOT be. A prefix match would catch all three, which is why
// the check is exact-segment and why that is what these tests pin down.
//
// Covers:
//   1. Any ordinary route honours the stored mode — candidate AND staff.
//   2. The live interview is forced dark whatever the visitor chose.
//   3. Its neighbours by name are not caught by that rule.
//   4. The override does not overwrite the stored preference.
//   5. `.dark` and `data-mode` never disagree (shadcn's `dark:` variants key
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

  it('honours the stored mode on an ordinary route', () => {
    localStorage.setItem(MODE_KEY, 'dark');
    renderAt('/dashboard');
    expect(mode()).toBe('dark');
  });

  it('defaults to light with no stored choice', () => {
    renderAt('/jobs');
    expect(mode()).toBe('light');
  });

  it.each(['/hr', '/hr/applicants', '/admin/overview', '/platform', '/superadmin'])(
    'lets the staff console at %s follow the preference too',
    (path) => {
      localStorage.setItem(MODE_KEY, 'light');
      renderAt(path);
      expect(mode()).toBe('light');
    },
  );

  it('pins the live interview dark whatever the visitor chose', () => {
    localStorage.setItem(MODE_KEY, 'light');
    renderAt('/interview/abc123');
    expect(mode()).toBe('dark');
    // Overridden for the render, not overwritten — leaving restores the choice.
    expect(localStorage.getItem(MODE_KEY)).toBe('light');
  });

  it.each(['/interview-invite', '/interview/abc123/complete'])(
    'does not pin %s, which only looks like the live interview',
    (path) => {
      localStorage.setItem(MODE_KEY, 'light');
      renderAt(path);
      expect(mode()).toBe('light');
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

  it('classifies routes: everything in but the live interview itself', () => {
    for (const path of [
      '/', '/login', '/scorecard/abc', '/careers/acme',
      '/hr/applicants', '/admin/interviews', '/platform',
      '/interview-invite', '/interview/abc/complete',
    ]) {
      expect(isThemedRoute(path)).toBe(true);
    }
    expect(isThemedRoute('/interview/abc')).toBe(false);
  });
});
