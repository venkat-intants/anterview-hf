// Vitest + RTL setup — loaded before each test file
import '@testing-library/jest-dom';
import { configure } from '@testing-library/react';

// Testing Library's async utilities have their OWN budget, and it is not the
// one vite.config.ts raises. `testTimeout: 15_000` is vitest's ceiling for a
// whole test; `findBy*` and `waitFor` give up after 1000 ms regardless, so on
// a loaded full-suite run a wait could never use more than a fifteenth of the
// budget that was deliberately widened for it. The mismatch showed up as
// "Unable to find an element" in whichever file lost the race that run.
//
// Well under testTimeout on purpose: a genuinely stuck wait still fails, and
// fails inside its own test rather than as a suite timeout.
configure({ asyncUtilTimeout: 5_000 });
// Initialise i18n once per test file so components using useTranslation render
// the English (fallback) text rather than raw translation keys. Each test file
// is module-isolated, so this re-inits to the default 'en' — no cross-file leak.
import '../lib/i18n';

// jsdom does not implement IntersectionObserver, which framer-motion's
// `whileInView` (used on the Landing page) calls during layout effects. Without
// a stub the component tree throws on mount. A no-op observer is enough for
// render/assertion tests — viewport callbacks simply never fire.
if (typeof globalThis.IntersectionObserver === 'undefined') {
  class IntersectionObserverStub {
    readonly root = null;
    readonly rootMargin = '';
    readonly thresholds: ReadonlyArray<number> = [];
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): IntersectionObserverEntry[] {
      return [];
    }
  }
  globalThis.IntersectionObserver =
    IntersectionObserverStub as unknown as typeof IntersectionObserver;
}

// No unit test may touch the network.
//
// Nothing stopped one. jsdom's fetch reaches whatever is actually listening, so
// a component whose API module was not mocked called the real data_gateway —
// and the result depended on the machine. On CI nothing listens on :8002, the
// call is refused, and the test passes. On a developer's box with the dev stack
// up, the same call answered 401 for the test's fake token, the API client did
// what it should (refresh, fail, clear the session, redirect to /login), and
// the component under test was signed out mid-assertion. Two files failed that
// way, locally only, for anyone running the app while running the tests.
//
// A plain function, not vi.fn(): a test file's `vi.resetAllMocks()` would strip
// a mock's implementation and leave fetch returning undefined, which fails as
// something unrecognisable instead of as this message. A test that needs fetch
// still overrides it for itself.
globalThis.fetch = ((input: RequestInfo | URL): Promise<Response> => {
  const url =
    typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
  return Promise.reject(
    new Error(
      `Unmocked network call in a unit test: ${url}\n` +
        'Mock the src/api/ module this comes from (vi.mock), or stub fetch in the test itself.',
    ),
  );
}) as typeof fetch;
