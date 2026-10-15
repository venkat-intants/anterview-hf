// Landing — public marketing page.
//
// Renders the AntHire landing design (src/landing/*): the same paper-and-ink
// identity as the signed-in app, with a hero, a short proof band, benefit
// sections and a footer. Its root sets `data-landing-theme` (light | dark),
// which mirrors the app-wide mode, and every `--lp-*` colour is an alias of an
// app token — so the page and the product cannot drift apart.
//
// Authenticated users are redirected straight to /dashboard.

import { Navigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { LandingPage } from '@/landing/screens/Landing/LandingPage';
import { readLandingTheme } from '@/landing/lib/useLandingTheme';

export default function Landing() {
  const { isAuthenticated, isInitializing } = useAuth();

  if (isInitializing) {
    return (
      <main data-landing-theme={readLandingTheme()} className="flex min-h-screen items-center justify-center bg-[var(--lp-canvas)]">
        <div
          className="h-8 w-8 animate-spin rounded-full border-4 border-[var(--lp-accent)] border-t-transparent motion-reduce:animate-none"
          role="status"
          aria-label="Loading"
        />
      </main>
    );
  }

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  return <LandingPage />;
}
