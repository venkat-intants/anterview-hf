// Entry point — bootstraps React 18, QueryClient, Router, AuthProvider
// StrictMode import removed (disabled above)
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider } from 'react-i18next';
import { MotionConfig } from 'framer-motion';
import { AuthProvider } from './context/AuthContext';
import { ConsentProvider } from './context/ConsentContext';
import { Toaster } from './components/ui/sonner';
import i18n from './lib/i18n';
import App from './App';
import './index.css';
// Animate.css — utility entrance animations (e.g. animate__fadeIn on the hero).
import 'animate.css';
// Anterview landing design — fonts + scoped keyframes/animations.
// Imported AFTER index.css so its rules win on any name overlap.
import './landing/styles/anterview.css';
// Anterview app-wide design kit — av-* keyframes used by src/design primitives.
import './design/styles/anterview.css';

// Dev-only self-heal: a service worker registered by a previous `npm run build`
// keeps serving stale precached JS on localhost. In dev, vite-plugin-pwa emits
// NO new service worker, so the old one is never replaced and the UI gets stuck
// on an outdated bundle (e.g. new components silently not appearing). Unregister
// any service worker + drop its caches once, then reload so the dev server's
// fresh bundle loads. Guarded by import.meta.env.DEV → no-op in production, so
// the real PWA behaviour is unaffected.
if (import.meta.env.DEV && 'serviceWorker' in navigator) {
  void navigator.serviceWorker.getRegistrations().then(async (regs) => {
    if (regs.length === 0) return;
    await Promise.all(regs.map((r) => r.unregister()));
    if ('caches' in window) {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
    if (!sessionStorage.getItem('intants:sw-cleared')) {
      sessionStorage.setItem('intants:sw-cleared', '1');
      window.location.reload();
    }
  });
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Root element #root not found in document');
}

// NOTE: StrictMode intentionally DISABLED. Its dev-only double mount→unmount→
// remount cycle tore down and rebuilt the LiveKit room mid-connect (the
// connect/leave/reconnect flapping that prevented the WebRTC peer connection
// from ever stabilising — avatar never appeared). StrictMode is a dev aid only;
// removing it does not change production behaviour. Re-enable later once the
// LiveKit hook is hardened against double-invoke if desired.
createRoot(rootElement).render(
  <I18nextProvider i18n={i18n}>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <ConsentProvider>
            {/* Respect prefers-reduced-motion: collapses all framer-motion
                durations to 0 for users who opt out of animation (WCAG 2.3.3) */}
            <MotionConfig reducedMotion="user">
              <App />
            </MotionConfig>
          </ConsentProvider>
          {/* Sonner toast host — sibling of the provider tree so toasts
              survive even if a provider re-renders/unmounts a subtree */}
          <Toaster />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </I18nextProvider>,
);
