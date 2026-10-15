// sameOriginUrl — the open-redirect guard the candidate's own offer link
// (PH4-A3, GET /users/me/offers/{id}/link) is checked against before this
// app ever navigates to it.

import { describe, it, expect } from 'vitest';
import { appPath, downloadUrl, safeAvatarSrc, sameOriginUrl } from '../lib/safeUrl';

describe('sameOriginUrl', () => {
  it('accepts a same-origin link at the expected path', () => {
    expect(sameOriginUrl(`${window.location.origin}/offer#tok123`, '/offer')).toBe(
      `${window.location.origin}/offer#tok123`,
    );
  });

  it('rejects a cross-origin link', () => {
    expect(sameOriginUrl('https://evil.example.com/offer#tok123', '/offer')).toBeNull();
  });

  it('rejects a same-origin link to a different path', () => {
    expect(sameOriginUrl(`${window.location.origin}/not-offer`, '/offer')).toBeNull();
  });

  it('rejects a protocol-relative attempt to change host', () => {
    expect(sameOriginUrl('//evil.example.com/offer', '/offer')).toBeNull();
  });

  it('rejects an unparsable value', () => {
    expect(sameOriginUrl('not a url at all', '/offer')).toBeNull();
  });
});

describe('sameOriginUrl — forms that must never pass', () => {
  it.each([
    ['a javascript: URL', 'javascript:alert(1)'],
    ['userinfo naming our host', `https://${window.location.host}@evil.example.com/offer`],
    ['userinfo with a password', 'https://user:pass@evil.example.com/offer'],
    ['a backslash host', '\\\\evil.example.com/offer'],
    ['a look-alike subdomain', `https://${window.location.hostname}.evil.example.com/offer`],
    ['a data: URL', 'data:text/html,<p>x</p>'],
  ])('rejects %s', (_label, raw) => {
    expect(sameOriginUrl(raw, '/offer')).toBeNull();
  });
});

describe('downloadUrl', () => {
  it('opens an https signed link', () => {
    expect(downloadUrl('https://store.example.com/k?X-Amz-Signature=abc')).toBe(
      'https://store.example.com/k?X-Amz-Signature=abc',
    );
  });

  it.each([['javascript:alert(1)'], ['data:text/html,x'], ['/relative/path'], ['not a url']])(
    'refuses %s',
    (raw) => {
      expect(downloadUrl(raw)).toBeNull();
    },
  );
});

describe('appPath', () => {
  it.each([
    '/hr/applicants/abc',
    '/hr/applicants/abc?enrolment=1',
    '/hr/enrolments/e1/evidence?decision=3#top',
    '/',
  ])('accepts the same-app path %j', (raw) => {
    expect(appPath(raw)).toBe(raw);
  });

  it.each([
    'https://evil.example/x',
    'http://evil.example',
    'javascript:alert(1)',
    'mailto:a@b.c',
    '//evil.example/x',
    '/\\evil.example/x',
    '/hr/x\\y',
    '/\t/evil.example',
    '/\n/evil.example',
    '/\r/evil.example',
    '/hr/x\u0000',
    '/hr/x\u007f',
    'hr/applicants',
    '',
    ' /hr/x',
  ])('refuses %j', (raw) => {
    expect(appPath(raw)).toBeNull();
  });

  it('refuses non-strings', () => {
    expect(appPath(undefined)).toBeNull();
    expect(appPath(null)).toBeNull();
    expect(appPath(42)).toBeNull();
  });

  it('returns the caller-chosen fallback', () => {
    expect(appPath('//evil.example', '/hr')).toBe('/hr');
    expect(appPath(undefined, '/')).toBe('/');
    expect(appPath('/ok', '/hr')).toBe('/ok');
  });
});

describe('safeAvatarSrc', () => {
  const B64 = 'iVBORw0KGgoAAAANSUhEUg==';

  it.each(['png', 'jpeg', 'webp'])('accepts an inline %s data URI', (mime) => {
    const uri = `data:image/${mime};base64,${B64}`;
    expect(safeAvatarSrc(uri)).toBe(uri);
  });

  it('accepts exactly what the profile page downscaler produces (a JPEG data URL)', () => {
    const uri = `data:image/jpeg;base64,${'/9j/4AAQSkZJRgABAQ'.repeat(2000)}`;
    expect(uri.length).toBeLessThan(800_000); // backend length cap
    expect(safeAvatarSrc(uri)).toBe(uri);
  });

  it.each([
    'https://tracker.example/pixel.gif',
    'http://tracker.example/p.png',
    '//tracker.example/p.png',
    'javascript:alert(1)',
    'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=',
    'data:text/html;base64,PGgxPg==',
    'data:image/gif;base64,R0lGOD',
    'data:image/png;base64,',
    'data:image/png,raw',
    `data:image/png;base64,${B64}\n`,
    `data:image/png;base64,${B64}"onerror="x`,
    '',
  ])('refuses %j', (raw) => {
    expect(safeAvatarSrc(raw)).toBeNull();
  });

  it('refuses non-strings', () => {
    expect(safeAvatarSrc(null)).toBeNull();
    expect(safeAvatarSrc(undefined)).toBeNull();
  });
});
