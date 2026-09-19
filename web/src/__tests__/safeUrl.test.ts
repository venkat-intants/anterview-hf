// sameOriginUrl — the open-redirect guard the candidate's own offer link
// (PH4-A3, GET /users/me/offers/{id}/link) is checked against before this
// app ever navigates to it.

import { describe, it, expect } from 'vitest';
import { downloadUrl, sameOriginUrl } from '../lib/safeUrl';

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
