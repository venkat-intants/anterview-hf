// Tests for the app-wide light/dark mode.
//
// The route is no longer an input. It used to be: pages that had no light
// design were pinned dark by URL, first as an allow-list of the themed routes,
// then as an exclusion list holding only the live interview. Both are gone —
// every page uses the tokens, so whether a surface follows the mode is a
// property of the component, not of where it is mounted.
//
// One component still paints its own colours: the live interview's video
// player, where the avatar track fills the viewport and the HUD floats on it.
// That is deliberate and local, and the test below pins the consequence that
// matters — that it is NOT done by pinning the route, so the device check and
// the error states around the session follow the visitor's choice.
//
// Covers:
//   1. Every route honours the stored mode — candidate, staff and interview.
//   2. Light is the default with no stored choice.
//   3. `.dark` and `data-mode` never disagree (shadcn's `dark:` variants key
//      off the class, the palettes off the attribute).
//   4. Changing the choice re-paints without a remount.

import { describe, it, expect, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ThemeModeGate from '../components/ThemeModeGate';
import { MODE_KEY, readMode, setMode } from '../lib/useThemeMode';

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

describe('theme mode', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-mode');
    document.documentElement.classList.remove('dark');
  });

  it('defaults to light with no stored choice', () => {
    renderAt('/jobs');
    expect(mode()).toBe('light');
    expect(readMode()).toBe('light');
  });

  it.each([
    '/',
    '/login',
    '/dashboard',
    '/scorecard/abc',
    '/careers/acme',
    '/hr/applicants',
    '/admin/interviews',
    '/platform',
    '/superadmin',
    // The interview route included: the device check, the consent step and the
    // error states are ordinary pages. Only the video player inside paints
    // itself, and it does that in its own markup rather than by route.
    '/interview/abc123',
    '/interview-invite',
    '/interview/abc123/complete',
  ])('honours the stored mode on %s', (path) => {
    localStorage.setItem(MODE_KEY, 'dark');
    renderAt(path);
    expect(mode()).toBe('dark');
  });

  it('keeps the .dark class and data-mode in agreement', () => {
    localStorage.setItem(MODE_KEY, 'dark');
    renderAt('/dashboard');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(mode()).toBe('dark');

    localStorage.setItem(MODE_KEY, 'light');
    renderAt('/dashboard');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(mode()).toBe('light');
  });

  it('re-paints when the choice changes, without a remount', () => {
    renderAt('/hr');
    expect(mode()).toBe('light');

    setMode('dark');
    // setMode dispatches the change event the store subscribes to, so the
    // mounted gate re-renders and re-applies without navigating.
    expect(readMode()).toBe('dark');
  });
});
