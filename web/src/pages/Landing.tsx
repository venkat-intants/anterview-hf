// Landing — public marketing page.
//
// Renders the Anterview landing design (src/landing/*): an aurora-lit,
// voice-first hero with full section-by-section marketing content. The design
// is self-contained — its root sets `data-landing-theme` (light | dark, the
// visitor's choice, defaulting to light) and every colour resolves against the
// tokens scoped to it — so it neither depends on nor disturbs the app's own
// forced-dark theme.
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
          className="h-8 w-8 animate-spin rounded-full border-4 border-electric border-t-transparent"
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
