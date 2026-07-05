// Landing — public marketing page.
//
// Renders the Anterview landing design (src/landing/*): a dark, aurora-lit,
// voice-first hero with full section-by-section marketing content. The design
// is self-contained (its root sets `bg-midnight` + dark tokens) so it does not
// depend on the app-wide light/dark theme.
//
// Authenticated users are redirected straight to /dashboard.

import { Navigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { LandingPage } from '@/landing/screens/Landing/LandingPage';

export default function Landing() {
  const { isAuthenticated, isInitializing } = useAuth();

  if (isInitializing) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-midnight">
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
