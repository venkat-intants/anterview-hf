// sameOriginUrl — the open-redirect guard the candidate's own offer link
// (PH4-A3, GET /users/me/offers/{id}/link) is checked against before this
// app ever navigates to it.

import { describe, it, expect } from 'vitest';
import { sameOriginUrl } from '../lib/safeUrl';

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
