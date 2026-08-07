// RoleRoute — the single implementation behind ProtectedRoute, AdminRoute,
// HRRoute, SuperAdminRoute and PlatformOwnerRoute. Those five files remain as
// thin named wrappers so every call site in App.tsx reads as before; only the
// duplicated spinner/redirect body lives here now.
//
// These are UX guards, NOT the security boundary — every protected endpoint
// re-checks the caller's role server-side, so forcing a URL yields an empty
// console, not data. What the guards buy is (a) not routing a user into a
// console they cannot use and (b) not flashing /login while AuthProvider's
// silent-refresh probe is still in flight, which is why `isInitializing`
// renders a spinner rather than redirecting.
//
// Shaped as a LAYOUT route (renders <Outlet />) because that is how App.tsx
// mounts all five: <Route element={<HRRoute />}><Route path=… /></Route>.

import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

/**
 * Roles the guards can be parametrised with. A closed union rather than
 * `string` because a typo here silently changes navigation for a whole
 * console — the compiler should catch it, not a bug report.
 *
 * `admin` is the platform analytics role and sits OUTSIDE the
 * platform_owner → super_admin → hr_manager → candidate hierarchy.
 */
export type AppRole = 'platform_owner' | 'super_admin' | 'hr_manager' | 'admin' | 'candidate';

export interface RoleRouteProps {
  /**
   * Roles permitted through; a user passes when they hold ANY one of them
   * (roles are additive on an account — the platform owner also holds `admin`).
   *
   * Omit entirely for an authentication-only gate — that is ProtectedRoute.
   */
  roles?: readonly AppRole[];
}

export default function RoleRoute({ roles }: RoleRouteProps) {
  const { isAuthenticated, isInitializing, user } = useAuth();

  if (isInitializing) {
    return (
      <main
        className="min-h-screen flex items-center justify-center bg-gradient-to-br from-indigo-50 to-violet-50"
        aria-label="Loading"
      >
        <div
          className="h-10 w-10 animate-spin rounded-full border-4 border-indigo-600 border-t-transparent"
          role="status"
          aria-label="Checking session"
        />
      </main>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  // `user` is briefly null on an authenticated-but-profile-less session.
  // Treating that as "holds no roles" keeps the guard failing CLOSED, which is
  // exactly what each original did with its `!user?.roles.includes(…)` check.
  if (roles && !roles.some((role) => user?.roles.includes(role) ?? false)) {
    return <Navigate to="/dashboard" replace />;
  }

  return <Outlet />;
}
