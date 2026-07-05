// Vitest + RTL setup — loaded before each test file
import '@testing-library/jest-dom';
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
