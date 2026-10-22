// Audit CA-M27: only a same-app path is ever followed after sign-in.
import { describe, it, expect, beforeEach } from 'vitest';
import { rememberReturnPath, safeReturnPath, takeReturnPath } from '../lib/returnTo';

describe('safeReturnPath', () => {
  it.each(['/applications', '/scorecard/abc?tab=2', '/hr/openings/1'])('keeps %s', (p) => {
    expect(safeReturnPath(p)).toBe(p);
  });

  it.each([
    'https://evil.example/x',
    '//evil.example/x',
    '/\\evil.example',
    'applications',
    '',
    '/login',
    '/login?x=1',
    '/register',
    '/auth/google/callback?code=a',
    '/change-password',
    '/reset-password',
    '/ok\n//evil.example',
    42,
    null,
  ])('refuses %s', (p) => {
    expect(safeReturnPath(p)).toBeNull();
  });

  it('does not treat a page that only starts with an auth word as an auth page', () => {
    expect(safeReturnPath('/loginhelp')).toBe('/loginhelp');
  });
});

describe('remember / take', () => {
  beforeEach(() => sessionStorage.clear());

  it('hands the path back once', () => {
    rememberReturnPath('/applications');
    expect(takeReturnPath()).toBe('/applications');
    expect(takeReturnPath()).toBeNull();
  });

  it('never hands back an unsafe value planted in storage', () => {
    sessionStorage.setItem('intants:return-to', '//evil.example');
    expect(takeReturnPath()).toBeNull();
  });
});
