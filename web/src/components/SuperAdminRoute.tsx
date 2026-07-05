// SuperAdminRoute — requires the 'super_admin' role (a company's super admin,
// one per company). Non-super-admins are redirected to /dashboard;
// unauthenticated to /login. (Platform-wide governance lives behind
// PlatformOwnerRoute.)

import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function SuperAdminRoute() {
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

  if (!isAuthenticated) return <Navigate to="/login" replace />;
  if (!user?.roles.includes('super_admin')) return <Navigate to="/dashboard" replace />;
  return <Outlet />;
}
